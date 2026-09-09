"""The board's knowledge source: an Obsidian vault read from disk (spec
section 5).

A vault is a folder of Markdown files. Nothing here knows or cares whether
Obsidian is installed - the board reads ``.md`` files under the configured
folder and nothing else. Selection is deterministic Python (AP-1): notes are
ranked by how many of the question's terms appear in their title, tags,
file name and body, and the best-ranked notes are packed into a configured
token budget. When the whole vault fits into the budget, the whole vault is
sent - ranking then only decides the order.

The model never lists or reads files itself. It receives what this module
selected, as one text block appended to the ``Context`` input every member
already gets (FR-3.7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DEFAULT_TOKEN_BUDGET = 6000
_CHARS_PER_TOKEN = 4          # a rough, deliberately conservative estimate
_SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
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


@dataclass
class KnowledgeSelection:
    vault_path: Path | None
    project: str | None = None                            # the project the notes were filtered to
    notes: list[Note] = field(default_factory=list)      # selected, in rank order
    total_notes: int = 0
    tokens: int = 0                                       # estimate for ``text``
    truncated: bool = False                               # a note was cut to fit
    text: str = ""                                        # the block sent to the model

    @property
    def relative_paths(self) -> list[str]:
        return [note.relative for note in self.notes]


def estimate_tokens(text: str) -> int:
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _front_matter(text: str) -> dict[str, str | list[str]]:
    """The minimal YAML this tool needs: ``title:`` and ``tags:``, either
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
        key_value = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
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
    body = path.read_text(encoding="utf-8", errors="replace")
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


def load_vault(vault_path: Path | str, *, skip_subfolders: tuple[str, ...] = ()) -> list[Note]:
    """Every ``.md`` note under ``vault_path``, Obsidian's own folders
    skipped, plus any top-level ``skip_subfolders`` (the Roles folder: a
    member's profile is mandatory context for that member, section 3.4,
    not a note competing for the budget). Raises ``KnowledgeUnavailable``
    when the folder cannot be read."""
    vault = Path(vault_path)
    skip_parts = [tuple(part.lower() for part in Path(name).parts) for name in skip_subfolders if name]
    if not vault.exists():
        raise KnowledgeUnavailable(f"knowledge source not found: {vault}")
    if not vault.is_dir():
        raise KnowledgeUnavailable(f"knowledge source is not a folder: {vault}")
    notes: list[Note] = []
    try:
        for path in sorted(vault.rglob("*.md")):
            parts = path.relative_to(vault).parts
            if any(part in _SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
                continue
            lowered = tuple(part.lower() for part in parts[:-1])
            if any(lowered[:len(skip)] == skip for skip in skip_parts if skip):
                continue
            if not path.is_file():
                continue
            notes.append(_load_note(vault, path))
    except OSError as exc:
        raise KnowledgeUnavailable(f"knowledge source could not be read: {vault} ({exc})") from exc
    return notes


def for_project(notes: list[Note], project: str | None) -> list[Note]:
    """The notes that apply to ``project``: every note without a
    ``projects`` property (common to all projects) plus those that name it.
    No active project: every note. Names match case-insensitively."""
    if not project or not str(project).strip():
        return list(notes)
    wanted = str(project).strip().lower()
    return [n for n in notes if not n.projects or any(p.lower() == wanted for p in n.projects)]


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


def active_project(config: dict) -> str | None:
    """``knowledge.project`` when set; the project every question is about."""
    value = _config_value(config, "knowledge.project")
    return str(value).strip() if value is not None and str(value).strip() else None


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


def query_terms(question: str) -> list[str]:
    words = re.findall(r"[\w][\w'-]{2,}", question.lower())
    seen: list[str] = []
    for word in words:
        if word in _STOPWORDS or word.isdigit() or word in seen:
            continue
        seen.append(word)
    return seen


def score_note(note: Note, terms: list[str]) -> int:
    if not terms:
        return 0
    title = note.title.lower()
    name = note.path.stem.lower()
    tags = " ".join(note.tags).lower()
    body = note.body.lower()
    score = 0
    for term in terms:
        if term in title or term in name:
            score += 5
        if term in tags:
            score += 5
        score += min(body.count(term), 5)
    return score


def select_notes(
    notes: list[Note], question: str, token_budget: int = DEFAULT_TOKEN_BUDGET
) -> KnowledgeSelection:
    """Rank ``notes`` against ``question`` and pack them into
    ``token_budget``. Everything fits: everything is sent. Otherwise the
    best-ranked notes are sent, and the last one is cut to fit if that is
    the only way to use the remaining budget."""
    terms = query_terms(question)
    ranked = sorted(
        notes,
        key=lambda note: (-score_note(note, terms), -note.path.stat().st_mtime if note.path.exists() else 0, note.relative),
    )
    selection = KnowledgeSelection(vault_path=None, total_notes=len(notes))
    if not notes:
        return selection

    header = "## Knowledge from the vault\n\n"
    parts: list[str] = []
    remaining = token_budget - estimate_tokens(header)
    for note in ranked:
        chunk = f"### {note.relative}\n{note.body.strip()}\n\n"
        cost = estimate_tokens(chunk)
        if cost <= remaining:
            parts.append(chunk)
            selection.notes.append(note)
            remaining -= cost
            continue
        # Cut this note to what is left, but only if that leaves room for
        # more than a heading - otherwise stop here.
        room_chars = max(remaining, 0) * _CHARS_PER_TOKEN - len(f"### {note.relative}\n") - 40
        if room_chars > 200:
            cut = note.body.strip()[:room_chars]
            parts.append(f"### {note.relative}\n{cut}\n[... cut to fit the token budget]\n\n")
            selection.notes.append(note)
            selection.truncated = True
        break

    selection.text = (header + "".join(parts)).rstrip() if parts else ""
    selection.tokens = estimate_tokens(selection.text)
    return selection


def gather(config: dict, question: str) -> KnowledgeSelection:
    """The knowledge block for ``question`` under the configured source.

    No source configured: an empty selection, the board runs on the
    question alone. A source configured but unreadable: ``KnowledgeUnavailable``."""
    vault_path = _config_value(config, "knowledge.vault_path")
    budget = _config_value(config, "knowledge.token_budget") or DEFAULT_TOKEN_BUDGET
    if not vault_path:
        return KnowledgeSelection(vault_path=None)
    vault = Path(str(vault_path)).expanduser()
    project = active_project(config)
    notes = [n for n in for_project(load_vault(vault, skip_subfolders=_roles_inside(config, vault)), project)
             if n.kind != "kpi"]
    selection = select_notes(notes, question, int(budget))
    selection.vault_path = vault
    selection.project = project
    return selection


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
    when = f"Last updated {updated.isoformat()}, {age} day(s) ago."
    if age > stale_days:
        return (f"{when} **This is older than {stale_days} days: say so before you rely on a value "
                f"from it, and name what would have to be re-checked.**")
    return when


def kpi_notes(config: dict, members: list[str] | tuple[str, ...], *, today: date | None = None) -> dict[str, str]:
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
                                    active_project(config)) if n.kind == "kpi"]
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
    for member, chunks in blocks.items():
        text = "\n\n".join(chunks)
        limit = KPI_TOKEN_CAP * _CHARS_PER_TOKEN
        if len(text) > limit:
            text = text[:limit] + "\n[... cut to fit the KPI budget]"
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
