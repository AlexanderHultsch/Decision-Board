"""The board's membership and personality: role profiles read from one
folder (spec section 3.4, decided 8 September 2026).

There is no list of members anywhere in this repository. Whoever has a
role profile in the roles folder sits on the board; the profile is the
member's character, skills, KPIs, vocabulary, how it assesses and what it
pushes back on. Profiles are read fresh on every board run, never cached,
so an edit in Obsidian is in force on the next question.

**One folder**, chosen by the user (``knowledge.roles_folder``), else
``<vault>/Roles`` when that exists, else the examples shipped under
``roles/`` in the repository - which is the only fallback, and it is files,
not code.

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
BUILTIN_DIR = Path(__file__).resolve().parents[2] / "roles"
MIN_MEMBERS = 2

ICONS = ("dollar", "chip", "gear", "factory", "code", "target", "scale", "people",
         "shield", "truck", "flask", "chart", "person")
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
    ("flask", ("test", "validation", "lab", "research")),
    ("chart", ("strateg", "market", "business", "program", "project")),
)
PALETTE = ("#15803d", "#2563eb", "#d97706", "#7c3aed", "#0f766e", "#db2777",
           "#b91c1c", "#4f46e5", "#0891b2", "#65a30d", "#9333ea", "#ea580c")
_META_KEYS = ("member", "title", "perspective", "icon", "color", "short", "order")


class RolesUnavailable(RuntimeError):
    """The roles folder cannot be read, or defines fewer than two members."""


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
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


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


_H1 = re.compile(r"^# +(.+?)\s*$", re.MULTILINE)
_KEY_LINE = re.compile(r"^(member|title|perspective|icon|color|short|order):\s*(.*)$", re.IGNORECASE)


def parse_file(path: Path, source: str) -> list[RoleProfile]:
    """Every role a file defines: one, or several (see module docstring)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _front_matter(text)
    body = _strip_front_matter(text)
    headings = list(_H1.finditer(body))
    claimed = _meta_str(meta, "member")
    if claimed or len(headings) <= 1:
        member = claimed or path.stem
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


def resolve_folder(config: dict) -> tuple[Path, str]:
    """Where the profiles come from and why: the configured folder, else
    ``<vault>/Roles`` if it exists, else the shipped examples."""
    configured = _config(config, "knowledge.roles_folder")
    if configured:
        return Path(str(configured)).expanduser(), "configured"
    vault = _config(config, "knowledge.vault_path")
    if vault:
        candidate = Path(str(vault)).expanduser() / DEFAULT_SUBFOLDER
        if candidate.is_dir():
            return candidate, "vault"
    return BUILTIN_DIR, "built-in"


def load_folder(folder: Path, source: str) -> list[RoleProfile]:
    profiles: list[RoleProfile] = []
    for path in sorted(folder.glob("*.md")):
        if path.is_file() and not path.name.lower().startswith("readme"):
            try:
                profiles.extend(parse_file(path, source))
            except OSError as exc:
                raise RolesUnavailable(f"role profile could not be read: {path} ({exc})") from exc
    return profiles


def load_roles(config: dict) -> dict[str, RoleProfile]:
    """The board: every member with a profile, in ``order`` then file order.
    Raises ``RolesUnavailable`` for a configured folder that cannot be read
    or defines fewer than two members - a board of one is not a board."""
    folder, source = resolve_folder(config)
    if not folder.is_dir():
        raise RolesUnavailable(f"roles folder not found: {folder}")
    parsed = load_folder(folder, source)
    ordered = sorted(enumerate(parsed), key=lambda item: (item[1].order, item[0]))
    profiles: dict[str, RoleProfile] = {}
    for index, (_, profile) in enumerate(ordered):
        if profile.member in profiles:
            continue   # first definition wins; a duplicate name is one member
        color = profile.color or PALETTE[index % len(PALETTE)]
        short = profile.short or _short_name(profile.title)
        profiles[profile.member] = RoleProfile(**{**profile.__dict__, "color": color, "short": short})
    if len(profiles) < MIN_MEMBERS:
        raise RolesUnavailable(
            f"roles folder defines {len(profiles)} member(s), at least {MIN_MEMBERS} are needed: {folder}"
        )
    return profiles


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


def summary(profiles: dict[str, RoleProfile]) -> dict[str, object]:
    folder = folder_of(profiles)
    source = next(iter(profiles.values())).source if profiles else "built-in"
    return {
        "members": list(profiles),
        "count": len(profiles),
        "source": source,
        "folder": str(folder) if folder else None,
        "files": sorted({p.path.name for p in profiles.values() if p.path is not None}),
    }


def member_meta(profiles: dict[str, RoleProfile]) -> list[dict[str, str]]:
    """What the interface needs to draw a member."""
    return [
        {"name": p.member, "title": p.title, "short": p.short, "icon": p.icon,
         "color": p.color, "perspective": p.perspective}
        for p in profiles.values()
    ]


def members_in(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return [profile.member for profile in load_folder(folder, "configured")]


def install_examples(folder: Path, *, only_missing: bool = True) -> list[Path]:
    """Copies the shipped example profiles into ``folder`` (created if
    needed). A file is skipped when the folder already defines that member
    or already has a file of that name; nothing is ever overwritten."""
    folder.mkdir(parents=True, exist_ok=True)
    present = set(members_in(folder)) if only_missing else set()
    written: list[Path] = []
    for path in sorted(BUILTIN_DIR.glob("*.md")):
        if path.name.lower().startswith("readme"):
            continue
        members = [p.member for p in parse_file(path, "built-in")]
        target = folder / path.name
        if target.exists() or any(member in present for member in members):
            continue
        shutil.copyfile(path, target)
        written.append(target)
    return written


def _config(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if node not in ("", None) else None
