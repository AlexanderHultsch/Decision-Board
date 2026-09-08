"""The board's membership and personality: role profiles read from one
folder (spec section 3.4, decided 8 September 2026).

There is no list of members anywhere in this repository. Whoever has a
role profile in the roles folder sits on the board; the profile is the
member's character, skills, KPIs, vocabulary, how it assesses and what it
pushes back on. Profiles are read fresh on every board run, never cached,
so an edit in Obsidian is in force on the next question.

**One folder**, chosen by the user (``knowledge.roles_folder``), else a
folder in the vault whose name starts with "Roles" (``Roles``,
``Roles&Responsibilities``, ...) or reads "R&R". No folder, no board: the
repository ships no member profiles, only the generic conduct note and a
template (``roles/``), which the wizard installs into the folder.

**What is the same for every member** - character, how to answer, how to
treat the roles under one's responsibility - is one note in the same
folder, ``kind: conduct`` in its front matter (installed as
``_Board member conduct.md``). It is prepended to every member's profile.
Notes whose name starts with ``_`` or whose front matter says
``kind: template`` are never members. A member whose profile has a heading
and nothing else is *not filled yet*: it is left off the board and reported,
so an empty note never produces an empty opinion.

**One file per role, or one file with several roles.** A file whose front
matter names a ``member:`` is one role; so is a file with at most one
level-one heading, named after the file. A file with two or more
level-one headings is several roles, one per heading, and ``key: value``
lines directly under a heading (``perspective``, ``icon``, ``color``,
``short``, ``order``, ``title``) are that role's metadata.

Each member receives only its own profile (FR-3.3a); the synthesis
receives one line per member.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .knowledge import _front_matter

DEFAULT_SUBFOLDER = "Roles"
SUPPORT_DIR = Path(__file__).resolve().parents[2] / "roles"
CONDUCT_NAME = "_Board member conduct.md"
TEMPLATE_NAME = "_Template - one member.md"
MIN_MEMBERS = 2
_MIN_BODY_CHARS = 40   # below this a profile is "not filled yet"

ICONS = ("dollar", "chip", "gear", "factory", "code", "target", "scale", "people",
         "shield", "truck", "flask", "chart", "layers", "person")
_ICON_KEYWORDS = (
    ("dollar", ("finance", "cost", "budget", "control", "money", "commercial")),
    ("chip", ("hw", "hardware", "electr", "electronic")),
    ("gear", ("mech",)),
    ("factory", ("manufact", "production", "plant", "operations")),
    ("code", ("sw", "software")),
    ("target", ("kpi", "quality", "goal", "score")),
    ("scale", ("legal", "compliance", "contract")),
    ("people", ("hr", "people", "customer", "sales", "marketing")),
    ("shield", ("safety", "security", "risk")),
    ("truck", ("logistic", "supply", "purchas", "procure")),
    ("flask", ("test", "validation", "lab", "research", "system")),
    ("layers", ("config", "integration", "release", "bom")),
    ("chart", ("strateg", "market", "business", "program", "project")),
)
# File-name prefixes that name the folder's purpose, not the member:
# "R&R Hardware.md" is the member "Hardware".
_NAME_PREFIXES = ("r&r", "r & r", "r_r", "rr", "roles and responsibilities", "roles & responsibilities", "role", "roles")
PALETTE = ("#15803d", "#2563eb", "#d97706", "#7c3aed", "#0f766e", "#db2777",
           "#b91c1c", "#4f46e5", "#0891b2", "#65a30d", "#9333ea", "#ea580c")
_META_KEYS = ("member", "title", "perspective", "icon", "color", "short", "order", "kind")


class RolesUnavailable(RuntimeError):
    """The roles folder cannot be read, or defines fewer than two members."""


@dataclass
class Board:
    profiles: dict[str, "RoleProfile"]
    conduct: str                       # generic text prepended to every profile ("" if none)
    conduct_path: Path | None
    skipped: list[tuple[str, str]]     # (member, reason) - e.g. not filled yet
    folder: Path
    source: str


@dataclass(frozen=True)
class RoleProfile:
    member: str
    title: str
    perspective: str      # one line, goes to the synthesis
    body: str             # Markdown without front matter, goes to the member
    source: str           # "configured", "vault" or "built-in"
    path: Path | None
    icon: str = "person"
    color: str = PALETTE[0]
    short: str = ""
    order: int = 999


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _first_paragraph_line(body: str) -> str:
    """The first content line, cut to its first sentence: the one line the
    synthesis sees about a member."""
    for line in body.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if line and not line.startswith("#"):
            match = re.match(r"^(.{20,}?[.!?])(\s|$)", line)
            sentence = match.group(1) if match else line
            return sentence[:200].rstrip(" ,:;")
    return ""


def _member_from_stem(stem: str) -> str:
    name = stem.strip()
    lowered = name.lower()
    for prefix in sorted(_NAME_PREFIXES, key=len, reverse=True):
        if lowered.startswith(prefix) and len(name) > len(prefix):
            rest = name[len(prefix):]
            if rest[:1] in (" ", "_", "-", ":"):
                return rest.lstrip(" _-:").strip() or name
    return name


def _first_heading(body: str) -> str:
    match = _H1.search(body)
    return match.group(1).strip() if match else ""


_H1 = re.compile(r"^# +(.+?)\s*$", re.MULTILINE)


def _meta_str(meta: dict, key: str) -> str:
    value = meta.get(key)
    return value.strip() if isinstance(value, str) else ""


def _guess_icon(name: str) -> str:
    lowered = name.lower()
    for icon, keywords in _ICON_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return icon
    return "person"


def _make_profile(member: str, meta: dict, body: str, source: str, path: Path | None) -> RoleProfile:
    icon = _meta_str(meta, "icon").lower()
    order_text = _meta_str(meta, "order")
    try:
        order = int(order_text) if order_text else 999
    except ValueError:
        order = 999
    return RoleProfile(
        member=member,
        title=_meta_str(meta, "title") or member,
        perspective=_meta_str(meta, "perspective") or _first_paragraph_line(body),
        body=body.strip(),
        source=source,
        path=path,
        icon=icon if icon in ICONS else _guess_icon(member),
        color=_meta_str(meta, "color") or "",
        short=_meta_str(meta, "short") or "",
        order=order,
    )


_KEY_LINE = re.compile(r"^(member|title|perspective|icon|color|short|order|kind):\s*(.*)$", re.IGNORECASE)


def parse_file(path: Path, source: str) -> list[RoleProfile]:
    """Every role a file defines: one, or several (see module docstring)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _front_matter(text)
    body = _strip_front_matter(text)
    headings = list(_H1.finditer(body))
    claimed = _meta_str(meta, "member")
    if claimed or len(headings) <= 1:
        # One member per file: the file name names the member (a heading is
        # a title, and a copied heading must not merge two files into one
        # member); "R&R Hardware.md" is "Hardware".
        member = claimed or _member_from_stem(path.stem)
        if not _meta_str(meta, "title"):
            meta = {**meta, "title": _first_heading(body) or member}
        return [_make_profile(member, meta, body, source, path)]

    profiles: list[RoleProfile] = []
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = body[start:end]
        section_meta: dict = {}
        remaining: list[str] = []
        lines = section.splitlines()
        seen_content = False
        for line in lines:
            key_match = _KEY_LINE.match(line.strip()) if not seen_content else None
            if key_match:
                section_meta[key_match.group(1).lower()] = key_match.group(2).strip()
                continue
            if line.strip():
                seen_content = True
            remaining.append(line)
        member = _meta_str(section_meta, "member") or match.group(1).strip()
        section_meta.setdefault("order", str(index))
        profiles.append(_make_profile(member, section_meta, "\n".join(remaining), source, path))
    return profiles


def detect_folder(vault: Path) -> Path | None:
    """A roles folder inside the vault by name: ``Roles``,
    ``Roles&Responsibilities``, ``Roles & Responsibilities``, ``R&R``..."""
    if not vault.is_dir():
        return None
    try:
        for child in sorted(vault.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            name = child.name.lower().replace(" ", "")
            if name.startswith("role") or name in ("r&r", "randr", "rnr"):
                return child
    except OSError:
        return None
    return None


def resolve_folder(config: dict) -> tuple[Path | None, str]:
    """Where the profiles come from and why: the configured folder, else a
    roles folder detected in the vault, else nothing."""
    configured = _config(config, "knowledge.roles_folder")
    if configured:
        return Path(str(configured)).expanduser(), "configured"
    vault = _config(config, "knowledge.vault_path")
    if vault:
        detected = detect_folder(Path(str(vault)).expanduser())
        if detected is not None:
            return detected, "vault"
    return None, "none"


def _is_member_file(path: Path) -> bool:
    name = path.name.lower()
    return path.is_file() and not name.startswith("_") and not name.startswith("readme")


def _kind(path: Path) -> str:
    try:
        return _meta_str(_front_matter(path.read_text(encoding="utf-8", errors="replace")), "kind").lower()
    except OSError:
        return ""


def _filled(profile: RoleProfile) -> bool:
    """A profile with a heading and nothing under it is not filled yet."""
    content = "\n".join(line for line in profile.body.splitlines() if not line.strip().startswith("#"))
    return len(content.strip()) >= _MIN_BODY_CHARS


def load_folder(folder: Path, source: str) -> tuple[list[RoleProfile], list[tuple[str, str]]]:
    profiles: list[RoleProfile] = []
    skipped: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.md")):
        if not _is_member_file(path) or _kind(path) in ("conduct", "template"):
            continue
        try:
            for profile in parse_file(path, source):
                if _filled(profile):
                    profiles.append(profile)
                else:
                    skipped.append((profile.member, f"not filled yet ({path.name})"))
        except OSError as exc:
            raise RolesUnavailable(f"role profile could not be read: {path} ({exc})") from exc
    return profiles, skipped


def load_conduct(folder: Path) -> tuple[str, Path | None]:
    """The generic conduct note: front matter ``kind: conduct``, else the
    file named ``CONDUCT_NAME``. Its body, front matter stripped."""
    candidates = [p for p in sorted(folder.glob("*.md")) if p.is_file() and _kind(p) == "conduct"]
    if not candidates and (folder / CONDUCT_NAME).is_file():
        candidates = [folder / CONDUCT_NAME]
    if not candidates:
        return "", None
    path = candidates[0]
    return _strip_front_matter(path.read_text(encoding="utf-8", errors="replace")).strip(), path


def load_board(config: dict) -> Board:
    """The board: every filled member profile in ``order`` then file order,
    plus the conduct note and the members left off. Raises
    ``RolesUnavailable`` when there is no folder, it cannot be read, or it
    holds fewer than two filled profiles - a board of one is not a board."""
    folder, source = resolve_folder(config)
    if folder is None:
        raise RolesUnavailable("no roles folder chosen - the board has no members")
    if not folder.is_dir():
        raise RolesUnavailable(f"roles folder not found: {folder}")
    parsed, skipped = load_folder(folder, source)
    ordered = sorted(enumerate(parsed), key=lambda item: (item[1].order, item[0]))
    profiles: dict[str, RoleProfile] = {}
    for index, (_, profile) in enumerate(ordered):
        if profile.member in profiles:
            continue   # first definition wins; a duplicate name is one member
        color = profile.color or PALETTE[index % len(PALETTE)]
        short = profile.short or _short_name(profile.title)
        profiles[profile.member] = RoleProfile(**{**profile.__dict__, "color": color, "short": short})
    if len(profiles) < MIN_MEMBERS:
        detail = f"; not filled yet: {', '.join(m for m, _ in skipped)}" if skipped else ""
        raise RolesUnavailable(
            f"roles folder defines {len(profiles)} filled member profile(s), at least {MIN_MEMBERS} "
            f"are needed: {folder}{detail}"
        )
    conduct, conduct_path = load_conduct(folder)
    return Board(profiles=profiles, conduct=conduct, conduct_path=conduct_path,
                 skipped=skipped, folder=folder, source=source)


def load_roles(config: dict) -> dict[str, RoleProfile]:
    """The members only - see ``load_board``."""
    return load_board(config).profiles


def _short_name(title: str) -> str:
    words = title.split()
    if len(words) == 1:
        return title[:14]
    first = words[0]
    return (first if len(first) > 3 else " ".join(words[:2]))[:14]


def folder_of(profiles: dict[str, RoleProfile]) -> Path | None:
    for profile in profiles.values():
        if profile.path is not None:
            return profile.path.parent
    return None


def summary(board: Board) -> dict[str, object]:
    profiles = board.profiles
    return {
        "members": list(profiles),
        "count": len(profiles),
        "source": board.source,
        "folder": str(board.folder),
        "files": sorted({p.path.name for p in profiles.values() if p.path is not None}),
        "conduct": board.conduct_path.name if board.conduct_path else None,
        "skipped": [{"member": member, "reason": reason} for member, reason in board.skipped],
    }


def member_meta(profiles: dict[str, RoleProfile]) -> list[dict[str, str]]:
    """What the interface needs to draw a member."""
    return [
        {"name": p.member, "title": p.title, "short": p.short, "icon": p.icon,
         "color": p.color, "perspective": p.perspective}
        for p in profiles.values()
    ]


def members_in(folder: Path) -> list[str]:
    """Filled members the folder defines (nothing about the conduct note)."""
    if not folder.is_dir():
        return []
    return [profile.member for profile in load_folder(folder, "configured")[0]]


def install_support_files(folder: Path) -> list[Path]:
    """Copies the generic conduct note and the profile template into
    ``folder`` (created if needed). Nothing is ever overwritten, and no
    member is created: members are the user's to write."""
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in (CONDUCT_NAME, TEMPLATE_NAME):
        source = SUPPORT_DIR / name
        target = folder / name
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)
            written.append(target)
    return written


def _config(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if node not in ("", None) else None
