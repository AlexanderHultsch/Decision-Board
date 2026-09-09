"""The board's knowledge source: an Obsidian vault read from disk (spec
section 5).

A vault is a folder of Markdown files. Nothing here knows or cares whether
Obsidian is installed - the board reads ``.md`` files under the configured
folder and nothing else. Selection is deterministic Python (AP-1): every
note is split at its headings, the sections are ranked by how many of the
question's terms - and, for a member's own block, the member's own terms -
appear in the note's title, tags, file name, the heading and the section
text, and the best-ranked sections are packed into a token budget. KPI
notes (``kind: kpi``) and the roles folder are never part of that ranking:
they are attached to their member deterministically.

The model never lists or reads files itself. It receives what this module
selected, as one text block per member (FR-3.7).
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DEFAULT_TOKEN_BUDGET = 6000
_CHARS_PER_TOKEN = 4          # a rough, deliberately conservative estimate
_SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
MAX_NOTE_BYTES = 2 * 1024 * 1024   # a note larger than this is skipped, not read into every prompt
_STOPWORDS = {
    # English
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "our", "you", "your", "can", "should",
    "would", "could", "what", "which", "when", "where", "how", "why", "into",
    "than", "then", "them", "they", "there", "these", "those", "will", "about",
    "also", "any", "all", "one", "two", "does", "did", "been", "being", "its",
    "option", "options", "board", "decide", "decision", "question", "please",
    # German
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einen", "einem", "einer", "ist", "sind", "wir", "ihr", "sie", "nicht",
    "mit", "von", "für", "auf", "aus", "bei", "nach", "über", "auch", "wie",
    "was", "wenn", "dann", "noch", "kann", "soll", "sollen", "werden", "wird",
    "haben", "hat", "sein", "zum", "zur", "als", "sich", "uns", "dass",
}


class KnowledgeUnavailable(RuntimeError):
    """The configured vault folder cannot be read. The board does not run
    without its knowledge source once one is configured (owner decision,
    8 September 2026): the error is shown, never silently worked around."""


@dataclass(frozen=True)
class Note:
    path: Path              # absolute
    relative: str           # vault-relative, forward slashes, for display
    title: str
    tags: tuple[str, ...]
    body: str               # full file text, front matter included
    kind: str = ""          # front matter ``kind``: "kpi" marks a KPI data note
    member: tuple[str, ...] = ()   # front matter ``affected_swimlanes`` (``member`` still accepted): who the note is attached to
    projects: tuple[str, ...] = ()  # front matter ``projects``: which projects the note belongs to; empty means all


@dataclass(frozen=True)
class Section:
    """One part of a note, split at its headings (decided 9 September 2026:
    the budget buys the sections that match the question, not whole
    pages). A short note is one section."""
    note: Note
    heading: str          # the heading line without its hashes; "" for the opening part or a whole note
    body: str             # the section's text, heading line included
    index: int = 0        # position within the note, for document order and for a unique id

    @property
    def relative(self) -> str:
        return self.note.relative


@dataclass
class KnowledgeSelection:
    vault_path: Path | None
    project: str | None = None                            # the project the notes were filtered to
    notes: list[Note] = field(default_factory=list)      # selected, in rank order
    sections: list[Section] = field(default_factory=list)   # the sections sent, in rank order
    sent: dict[str, str] = field(default_factory=dict)   # note path -> the text of it that was sent
    forced_tokens: int = 0                                # what the manual picks added on top of the budget
    total_notes: int = 0
    tokens: int = 0                                       # estimate for ``text``
    truncated: bool = False                               # a note was cut to fit
    text: str = ""                                        # the block sent to the model

    @property
    def relative_paths(self) -> list[str]:
        return [note.relative for note in self.notes]


def estimate_tokens(text: str) -> int:
    return estimate_tokens_for(len(text))


def estimate_tokens_for(chars: int) -> int:
    return (chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _front_matter(text: str) -> dict[str, str | list[str]]:
    """The minimal YAML this tool needs: ``title:``, ``tags:`` and the like, either
    inline (``tags: [a, b]`` / ``tags: a, b``) or as a ``- item`` list. No
    YAML library - standard library only, and Obsidian front matter in the
    wild is rarely more than this."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n")
    result: dict[str, str | list[str]] = {}
    current_key: str | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        list_item = re.match(r"^\s*-\s+(.*)$", line)
        if list_item and current_key is not None:
            items = result.setdefault(current_key, [])
            if isinstance(items, list):
                items.append(list_item.group(1).strip().strip("'\""))
            continue
        # Keys may carry spaces ("Part of Decision Board AI: true" is a
        # checkbox property in Obsidian); they are read lower-cased.
        key_value = re.match(r"^([A-Za-z_][\w -]*?):\s*(.*)$", line)
        if not key_value:
            continue
        key, value = key_value.group(1).lower(), key_value.group(2).strip()
        current_key = key
        if value == "":
            result[key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        else:
            result[key] = value.strip("'\"")
    return result


def _load_note(vault: Path, path: Path) -> Note:
    body = path.read_text(encoding="utf-8-sig", errors="replace")    # -sig: a BOM must not void the front matter
    meta = _front_matter(body)
    title = meta.get("title")
    if not isinstance(title, str) or not title:
        title = path.stem
    tags_raw = meta.get("tags", [])
    if isinstance(tags_raw, str):
        tags = tuple(tag.strip().lstrip("#") for tag in tags_raw.split(",") if tag.strip())
    else:
        tags = tuple(str(tag).lstrip("#") for tag in tags_raw)
    relative = path.relative_to(vault).as_posix()
    kind = meta.get("kind") if isinstance(meta.get("kind"), str) else ""
    # ``affected_swimlanes`` is the key (decided 9 September 2026: a page says
    # which swim lanes it affects); ``member`` is read as well for old notes.
    member_raw = meta.get("affected_swimlanes", meta.get("member", ()))
    if isinstance(member_raw, str):
        members = [m.strip() for m in member_raw.split(",") if m.strip()]
    else:
        members = [str(m).strip() for m in member_raw if str(m).strip()]
    lead = meta.get("lead_swimlane")
    if isinstance(lead, str) and lead.strip() and lead.strip() not in members:
        members.insert(0, lead.strip())     # the lead is affected by definition
    members = tuple(members)
    # ``projects`` (decided 9 September 2026): a page that belongs to one or
    # more projects lists them; a page without the property is common to
    # every project (the process, the roles, the guide). ``project`` is read
    # as well for a page written with the singular.
    projects_raw = meta.get("projects", meta.get("project", ()))
    if isinstance(projects_raw, str):
        projects = tuple(p.strip() for p in projects_raw.split(",") if p.strip())
    else:
        projects = tuple(str(p).strip() for p in projects_raw if str(p).strip())
    return Note(path=path, relative=relative, title=title, tags=tags, body=body,
                kind=str(kind).lower(), member=members, projects=projects)


# Notes already read, per vault: path -> (mtime_ns, size, Note). A second
# walk in the same process only stats the files and re-reads what changed,
# so "read fresh on every question" stays true at the cost of a stat per
# file instead of a read (the estimate on the confirm screen walks the
# vault on every slider move).
_VAULT_CACHE: dict[str, dict[str, tuple[int, int, Note]]] = {}
_VAULT_CACHE_LOCK = threading.Lock()


def load_vault(vault_path: Path | str, *, skip_subfolders: tuple[str, ...] = ()) -> list[Note]:
    """Every ``.md`` note under ``vault_path``, Obsidian's own folders
    skipped, plus any top-level ``skip_subfolders`` (the Roles folder: a
    member's profile is mandatory context for that member, section 3.4,
    not a note competing for the budget). Symbolic links are not followed
    and a note over ``MAX_NOTE_BYTES`` is left out: the vault is the
    boundary of what reaches a prompt. Raises ``KnowledgeUnavailable`` when
    the folder cannot be read."""
    vault = Path(vault_path)
    skip_parts = [tuple(part.lower() for part in Path(name).parts) for name in skip_subfolders if name]
    if not vault.exists():
        raise KnowledgeUnavailable(f"knowledge source not found: {vault}")
    if not vault.is_dir():
        raise KnowledgeUnavailable(f"knowledge source is not a folder: {vault}")
    key = str(vault.resolve())
    with _VAULT_CACHE_LOCK:
        cached = dict(_VAULT_CACHE.get(key, {}))
    notes: list[Note] = []
    fresh: dict[str, tuple[int, int, Note]] = {}
    try:
        for root, dirs, files in os.walk(vault, followlinks=False):
            root_path = Path(root)
            rel_parts = root_path.relative_to(vault).parts
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")
                             and not (root_path / d).is_symlink())
            lowered = tuple(part.lower() for part in rel_parts)
            if any(lowered[:len(skip)] == skip for skip in skip_parts if skip):
                dirs[:] = []
                continue
            for name in sorted(files):
                if not name.lower().endswith(".md"):
                    continue
                path = root_path / name
                if path.is_symlink():
                    continue
                stat = path.stat()
                if stat.st_size > MAX_NOTE_BYTES:
                    continue
                relative = path.relative_to(vault).as_posix()
                entry = cached.get(relative)
                if entry is not None and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size:
                    note = entry[2]
                else:
                    note = _load_note(vault, path)
                fresh[relative] = (stat.st_mtime_ns, stat.st_size, note)
                notes.append(note)
    except OSError as exc:
        raise KnowledgeUnavailable(f"knowledge source could not be read: {vault} ({exc})") from exc
    with _VAULT_CACHE_LOCK:
        _VAULT_CACHE[key] = fresh
    notes.sort(key=lambda n: n.relative)
    return notes


def _project_list(project) -> list[str]:
    """One project, several as a list, or several in one string separated by
    commas or semicolons; empty means all."""
    if project is None:
        return []
    if isinstance(project, (list, tuple, set)):
        return [str(p).strip() for p in project if str(p).strip()]
    return [p.strip() for p in re.split(r"[,;]", str(project)) if p.strip()]


def for_project(notes: list[Note], project) -> list[Note]:
    """The notes that apply to the project(s): every note without a
    ``projects`` property (common to all projects) plus those that name
    one of them. No active project: every note. Names match
    case-insensitively."""
    wanted = {p.lower() for p in _project_list(project)}
    if not wanted:
        return list(notes)
    return [n for n in notes if not n.projects or any(p.lower() in wanted for p in n.projects)]


def project_names(notes: list[Note]) -> list[str]:
    """Every project the vault knows, for a project picker: the title of
    each ``kind: project`` page plus every name a ``projects`` property
    uses, sorted, deduplicated case-insensitively (first spelling wins)."""
    seen: dict[str, str] = {}
    for note in notes:
        names = [note.title] if note.kind == "project" else []
        names.extend(note.projects)
        for name in names:
            key = name.strip().lower()
            if key and key not in seen:
                seen[key] = name.strip()
    return sorted(seen.values(), key=str.lower)


def active_projects(config: dict) -> list[str]:
    """``knowledge.project`` as a list (decided 9 September 2026: a question
    may concern several projects); empty means every page."""
    return _project_list(_config_value(config, "knowledge.project"))


def active_project(config: dict) -> str | None:
    """The active project(s) as one string for a prompt line, or ``None``."""
    projects = active_projects(config)
    return ", ".join(projects) if projects else None


def list_projects(config: dict) -> list[str]:
    """``project_names`` for the configured vault; empty when no vault is
    configured or it cannot be read (a picker, not a gate)."""
    vault_path = _config_value(config, "knowledge.vault_path")
    if not vault_path:
        return []
    vault = Path(str(vault_path)).expanduser()
    try:
        notes = load_vault(vault, skip_subfolders=_roles_inside(config, vault))
    except KnowledgeUnavailable:
        return []
    return project_names(notes)


_SECTION_MIN_CHARS = 1200     # a note shorter than this is one section
_SECTION_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")


def split_sections(note: Note) -> list[Section]:
    """The note split at its level-one to level-three headings. The front
    matter stays with the opening part. Each section starts with a line
    naming the note and the heading, so a section read alone still says
    where it belongs."""
    text = note.body.strip()
    if len(text) < _SECTION_MIN_CHARS:
        return [Section(note=note, heading="", body=text)]
    lines = text.splitlines()
    sections: list[Section] = []
    current: list[str] = []
    heading = ""
    content_seen = False          # anything beyond the front matter in ``current``
    in_front_matter = lines[:1] == ["---"]
    in_fence = False              # a "# line" inside a code block is code, not a heading
    for index, line in enumerate(lines):
        if in_front_matter:
            current.append(line)
            if index > 0 and line.strip() == "---":
                in_front_matter = False
            continue
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _SECTION_HEADING.match(line)
        if match and content_seen:
            sections.append(Section(note=note, heading=heading, body="\n".join(current).strip(), index=len(sections)))
            current, heading, content_seen = [], match.group(2).strip(), False
        elif match:
            heading = match.group(2).strip()
        current.append(line)
        if line.strip():
            content_seen = True
    if any(l.strip() for l in current):
        sections.append(Section(note=note, heading=heading, body="\n".join(current).strip(), index=len(sections)))
    return sections or [Section(note=note, heading="", body=text)]


def score_section(section: Section, terms: list[str], lowered: "_Lowered | None" = None) -> int:
    if not terms:
        return 0
    low = lowered or _Lowered.of(section)
    score = 0
    for term in terms:
        if term in low.title or term in low.name:
            score += 5
        if low.heading and term in low.heading:
            score += 4
        if term in low.tags:
            score += 5
        score += min(low.body.count(term), 5)
    return score


@dataclass(frozen=True)
class _Lowered:
    """A section's searchable text, lower-cased once: ranking runs per
    member, and lower-casing a whole vault eight times is waste."""
    title: str
    name: str
    heading: str
    tags: str
    body: str

    @staticmethod
    def of(section: Section) -> "_Lowered":
        note = section.note
        return _Lowered(note.title.lower(), note.path.stem.lower(), section.heading.lower(),
                        " ".join(note.tags).lower(), section.body.lower())


@dataclass
class _Prepared:
    """The vault split once for several selections: the sections, their
    lower-cased text and each note's mtime."""
    sections: list[Section]
    lowered: dict[int, _Lowered]
    mtimes: dict[str, float]

    @staticmethod
    def of(notes: list[Note]) -> "_Prepared":
        sections = [section for note in notes for section in split_sections(note)]
        mtimes: dict[str, float] = {}
        for note in notes:
            try:
                mtimes[note.relative] = note.path.stat().st_mtime
            except OSError:
                mtimes[note.relative] = 0.0
        return _Prepared(sections, {id(s): _Lowered.of(s) for s in sections}, mtimes)


FORCED_CAP_TOKENS = 12000     # manual picks on top of the budget stop here, whatever the slider says


def select_sections(
    notes: list[Note], question: str, token_budget: int = DEFAULT_TOKEN_BUDGET,
    *, extra_terms: list[str] | tuple[str, ...] = (), pinned: list[Note] | None = None,
    extra: list[str] | tuple[str, ...] = (), exclude: list[str] | tuple[str, ...] = (),
    prepared: "_Prepared | None" = None,
) -> KnowledgeSelection:
    """Rank every section of every note against the question - and, for a
    member's own block, against ``extra_terms`` (the member's targets,
    process tasks and title) - and pack the best into ``token_budget``.
    ``pinned`` notes (the project page) go first whatever their score,
    within a share of the budget. Everything fits: everything is sent. A
    section too big for what is left is cut to fit, once."""
    terms = query_terms(question)
    member_terms = [t for t in (str(x).lower().strip() for x in extra_terms) if len(t) >= 4 and t not in terms]
    forced = {str(x) for x in extra}
    banned = {str(x) for x in exclude}
    prep = prepared or _Prepared.of(notes)
    all_sections = [section for section in prep.sections
                    if section_id(section) not in banned and section.relative not in banned]
    pinned_paths = {n.relative for n in (pinned or [])}

    def is_forced(section: Section) -> bool:
        return section_id(section) in forced or section.relative in forced

    def rank(section: Section) -> tuple:
        pin = 0 if is_forced(section) else 1 if section.relative in pinned_paths else 2
        low = prep.lowered.get(id(section))
        score = score_section(section, terms, low) * 2 + score_section(section, member_terms, low)
        return (pin, -score, -prep.mtimes.get(section.relative, 0.0), section.relative, section.index)

    ranked = sorted(all_sections, key=rank)
    selection = KnowledgeSelection(vault_path=None, total_notes=len(notes))
    if not notes:
        return selection
    remaining = token_budget - estimate_tokens("## Knowledge from the vault\n\n")
    pinned_share = (token_budget // 4) // max(len(pinned_paths), 1)   # per pinned note, so no project page starves another
    chosen: list[tuple[Section, str]] = []
    pinned_used: dict[str, int] = {}
    cut_once = False
    for section in ranked:
        label = f"### {section.relative}" + (f" - {section.heading}" if section.heading else "")
        chunk = f"{label}\n{section.body}\n\n"
        cost = estimate_tokens(chunk)
        if is_forced(section):
            if selection.forced_tokens + cost > FORCED_CAP_TOKENS:
                selection.truncated = True      # the picks alone would overflow the model; the rest is dropped
                continue
            chosen.append((section, chunk))    # a manual pick is sent whole, on top of the budget
            selection.forced_tokens += cost
            continue
        if section.relative in pinned_paths and pinned_used.get(section.relative, 0) + cost > pinned_share and chosen:
            continue          # a project page may not eat the whole budget
        if cost <= remaining:
            chosen.append((section, chunk))
            remaining -= cost
            if section.relative in pinned_paths:
                pinned_used[section.relative] = pinned_used.get(section.relative, 0) + cost
            continue
        if remaining < 50:
            break             # the budget is spent
        room_chars = remaining * _CHARS_PER_TOKEN - len(label) - 40
        if room_chars > 200 and not cut_once:
            # This section does not fit whole: cut it once to what is left,
            # then keep looking for smaller sections that still fit.
            cut = section.body[:room_chars]
            chosen.append((section, f"{label}\n{cut}\n[... cut to fit the token budget]\n\n"))
            selection.truncated = True
            cut_once = True
            remaining = 0
            break
        continue              # too big: a smaller, lower-ranked section may still fit
    # Sections of one note stay together, in the note's own order, under the
    # note's first appearance in the ranking.
    order: list[str] = []
    by_note: dict[str, list[tuple[Section, str]]] = {}
    for section, chunk in chosen:
        if section.relative not in by_note:
            order.append(section.relative)
            by_note[section.relative] = []
        by_note[section.relative].append((section, chunk))
    parts: list[str] = []
    for relative in order:
        items = sorted(by_note[relative], key=lambda item: item[0].index)
        note_text = "".join(chunk for _s, chunk in items)
        parts.append(note_text)
        selection.notes.append(items[0][0].note)
        selection.sections.extend(section for section, _c in items)
        selection.sent[relative] = "".join(section.body + "\n" for section, _c in items)
    selection.text = ("## Knowledge from the vault\n\n" + "".join(parts)).rstrip() if parts else ""
    selection.tokens = estimate_tokens(selection.text)
    return selection


def query_terms(question: str) -> list[str]:
    words = re.findall(r"[\w][\w'-]{2,}", question.lower())
    seen: list[str] = []
    for word in words:
        if word in _STOPWORDS or word.isdigit() or word in seen:
            continue
        seen.append(word)
    return seen


def select_notes(
    notes: list[Note], question: str, token_budget: int = DEFAULT_TOKEN_BUDGET
) -> KnowledgeSelection:
    """The selection for one question with no member in view: see
    ``select_sections``."""
    return select_sections(notes, question, token_budget)


def _knowledge_notes(config: dict, projects=None) -> tuple[Path | None, list[str], list[Note]]:
    vault_path = _config_value(config, "knowledge.vault_path")
    if not vault_path:
        return None, [], []
    vault = Path(str(vault_path)).expanduser()
    chosen = _project_list(projects) if projects is not None else active_projects(config)
    notes = [n for n in for_project(load_vault(vault, skip_subfolders=_roles_inside(config, vault)), chosen)
             if n.kind != "kpi"]
    return vault, chosen, notes


def _pinned(notes: list[Note], projects: list[str]) -> list[Note]:
    """The project page(s): the common core every member receives."""
    wanted = {p.lower() for p in projects}
    if not wanted:
        return []
    return [n for n in notes if n.kind == "project"
            and (n.title.strip().lower() in wanted or any(p.lower() in wanted for p in n.projects))]


def section_id(section: Section) -> str:
    """``path#heading`` for a section, the path alone for a whole note; a
    repeated heading gets its position appended so the ids stay unique."""
    if not section.heading:
        return section.relative if section.index == 0 else f"{section.relative}#{section.index}"
    same = [s for s in split_sections(section.note) if s.heading == section.heading]
    if len(same) > 1 and same[0].index != section.index:
        return f"{section.relative}#{section.heading}#{section.index}"
    return f"{section.relative}#{section.heading}"


def outline(config: dict, projects=None) -> list[dict]:
    """Every note and its sections, with ids and token sizes, for the
    manual picks under the estimate (decided 9 September 2026)."""
    vault, _chosen, notes = _knowledge_notes(config, projects)
    if vault is None:
        return []
    result = []
    for note in notes:
        sections = split_sections(note)
        result.append({"path": note.relative, "title": note.title, "kind": note.kind,
                       "sections": [{"id": section_id(s),
                                     "heading": s.heading or ("(whole note)" if len(sections) == 1 else "(opening)"),
                                     "tokens": estimate_tokens(s.body)} for s in sections]})
    return result


def gather(config: dict, question: str, *, token_budget: int | None = None, projects=None) -> KnowledgeSelection:
    """The knowledge block for ``question`` under the configured source.

    No source configured: an empty selection, the board runs on the
    question alone. A source configured but unreadable: ``KnowledgeUnavailable``."""
    vault, chosen, notes = _knowledge_notes(config, projects)
    if vault is None:
        return KnowledgeSelection(vault_path=None)
    budget = token_budget or _config_value(config, "knowledge.token_budget") or DEFAULT_TOKEN_BUDGET
    selection = select_sections(notes, question, int(budget), pinned=_pinned(notes, chosen))
    selection.vault_path = vault
    selection.project = ", ".join(chosen) if chosen else None
    return selection


def gather_for_members(
    config: dict, question: str, member_terms: dict[str, list[str] | tuple[str, ...] | set[str]],
    *, token_budget: int | None = None, projects=None,
    extra: list[str] | tuple[str, ...] = (), exclude: list[str] | tuple[str, ...] = (),
) -> dict[str, KnowledgeSelection]:
    """One knowledge block per member (decided 9 September 2026): the
    sections ranked by the question and by the member's own terms, so each
    member receives what concerns it, the project page first for all.
    ``extra`` section ids are sent to every member on top of the budget;
    ``exclude`` ids are never sent. The vault is read once. No source
    configured: empty selections."""
    vault, chosen, notes = _knowledge_notes(config, projects)
    budget = int(token_budget or _config_value(config, "knowledge.token_budget") or DEFAULT_TOKEN_BUDGET)
    result: dict[str, KnowledgeSelection] = {}
    pinned = _pinned(notes, chosen)
    prepared = _Prepared.of(notes) if vault is not None else None      # split and lower-case the vault once
    for member, terms in member_terms.items():
        if vault is None:
            result[member] = KnowledgeSelection(vault_path=None)
            continue
        selection = select_sections(notes, question, budget, extra_terms=tuple(terms), pinned=pinned,
                                    extra=extra, exclude=exclude, prepared=prepared)
        selection.vault_path = vault
        selection.project = ", ".join(chosen) if chosen else None
        result[member] = selection
    return result


KPI_TOKEN_CAP = 2500       # per member; a KPI note is a table, not a chapter
KPI_STALE_DAYS = 30        # beyond this the age is called out, not just stated


def _updated_on(note: "Note") -> date | None:
    meta = _front_matter(note.body)
    raw = meta.get("updated")
    if not isinstance(raw, str):
        return None
    for shape in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), shape).date()
        except ValueError:
            continue
    return None


def _freshness(note: "Note", today: date, stale_days: int) -> str:
    """One line above a KPI note saying how old it is. Python computes the
    age (AP-1) so a member can never quote a number without its date."""
    updated = _updated_on(note)
    if updated is None:
        return ("Last updated: not recorded in this note. Treat every value in it as an "
                "assumption and say so.")
    age = (today - updated).days
    if age < 0:
        return (f"Last updated {updated.isoformat()}, which is in the future: check the date before "
                "you rely on a value from it.")
    when = f"Last updated {updated.isoformat()}, {age} day(s) ago."
    if age > stale_days:
        return (f"{when} **This is older than {stale_days} days: say so before you rely on a value "
                f"from it, and name what would have to be re-checked.**")
    return when


def kpi_notes(config: dict, members: list[str] | tuple[str, ...], *, today: date | None = None, projects=None) -> dict[str, str]:
    """The KPI data block for each member (spec 3.4, decided 9 September
    2026): every note in the vault whose front matter says ``kind: kpi`` and
    lists the member under ``affected_swimlanes`` is attached to that
    member's call, always, whatever the question - the role says which KPI,
    the network holds the number. With ``knowledge.project`` set, only the
    notes of that project (and notes without a ``projects`` property) count,
    so a second project's gates never reach this project's board.
    Members without a note get no block; the role profile tells them to say
    the target is not in the network yet. Raises ``KnowledgeUnavailable`` as
    ``gather`` does."""
    vault_path = _config_value(config, "knowledge.vault_path")
    if not vault_path:
        return {}
    vault = Path(str(vault_path)).expanduser()
    notes = [n for n in for_project(load_vault(vault, skip_subfolders=_roles_inside(config, vault)),
                                    _project_list(projects) if projects is not None else active_projects(config))
             if n.kind == "kpi"]
    wanted = {m.lower(): m for m in members}
    stale_days = int(_config_value(config, "knowledge.kpi_stale_days") or KPI_STALE_DAYS)
    day = today or date.today()
    blocks: dict[str, list[str]] = {}
    for note in notes:
        for named in note.member:
            member = wanted.get(named.lower())
            if member is None:
                continue
            chunk = f"### {note.relative}\n{_freshness(note, day, stale_days)}\n\n{note.body.strip()}"
            blocks.setdefault(member, []).append(chunk)
    result: dict[str, str] = {}
    limit = KPI_TOKEN_CAP * _CHARS_PER_TOKEN
    for member, chunks in blocks.items():
        # Whole notes only: a KPI table cut mid-row, or a note without its
        # freshness line, is exactly the "number without its date" this
        # module exists to prevent.
        kept: list[str] = []
        used = 0
        for chunk in chunks:
            if used + len(chunk) > limit and kept:
                break
            kept.append(chunk[:limit] if len(chunk) > limit else chunk)
            used += len(chunk)
        omitted = len(chunks) - len(kept)
        text = "\n\n".join(kept)
        if omitted:
            text += f"\n\n[{omitted} further KPI note(s) omitted: over the KPI budget]"
        result[member] = text
    return result


def _roles_inside(config: dict, vault: Path) -> tuple[str, ...]:
    """The roles folder as a vault-relative path when it lies inside the
    vault (section 3.4: profiles are not knowledge notes), else nothing."""
    from .roles import resolve_folder   # local import: roles imports this module
    folder, _ = resolve_folder(config)
    if folder is None:
        return ()
    try:
        return (folder.resolve().relative_to(vault.resolve()).as_posix(),)
    except (ValueError, OSError):
        return ()


def vault_outline(vault_path: Path | str, *, max_folders: int = 200, max_titles: int = 400) -> str:
    """A compact description of the vault for the memory proposal call
    (``memory_writer``): its folders and note titles, capped so a large
    vault does not become the prompt."""
    notes = load_vault(vault_path)
    folders = sorted({str(Path(note.relative).parent.as_posix()) for note in notes} - {"."})
    lines = ["Folders:"]
    lines.extend(f"- {folder}/" for folder in folders[:max_folders])
    if len(folders) > max_folders:
        lines.append(f"- ... and {len(folders) - max_folders} more")
    lines.append("")
    lines.append("Existing notes (path: title):")
    for note in notes[:max_titles]:
        lines.append(f"- {note.relative}: {note.title}")
    if len(notes) > max_titles:
        lines.append(f"- ... and {len(notes) - max_titles} more")
    return "\n".join(lines)


def _config_value(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if node not in ("", None) else None
