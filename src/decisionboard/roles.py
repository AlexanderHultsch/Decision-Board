"""The board's personality: one role profile per member, kept in the vault
(spec section 3.4, decided 8 September 2026).

A role profile is a Markdown note in ``<vault>/Roles/`` - character,
skills, KPIs, vocabulary, how the member assesses and what it pushes back
on. It is read fresh on every board run, never cached, so an edit in
Obsidian is in force on the next question. It is not part of the ranked
knowledge selection (``knowledge.py`` skips the folder): a member's profile
is mandatory context for that member's call, not a note that competes for
the token budget.

Each member receives only its own profile (FR-3.3a: a member never sees
another member's material); the synthesis receives every member's
one-line ``perspective`` so it can weigh who said what.

The repository ships one example profile per member under ``roles/``. When
the vault has no profile for a member, that example is used and the run
reports it as ``built-in``, so a half-filled Roles folder is visible, never
silent.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .knowledge import _front_matter

DEFAULT_SUBFOLDER = "Roles"
BUILTIN_DIR = Path(__file__).resolve().parents[2] / "roles"
MEMBERS = ("Finance", "HW Engineering", "Mechanical Engineering",
           "Manufacturing", "SW Engineering", "KPI Check")


class RolesUnavailable(RuntimeError):
    """The roles folder is configured but cannot be read."""


@dataclass(frozen=True)
class RoleProfile:
    member: str
    title: str
    perspective: str      # one line, goes to the synthesis
    body: str             # Markdown without front matter, goes to the member
    source: str           # "vault" or "built-in"
    path: Path | None


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _read_profile(path: Path, member: str, source: str) -> RoleProfile:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _front_matter(text)
    title = meta.get("title") if isinstance(meta.get("title"), str) else ""
    perspective = meta.get("perspective") if isinstance(meta.get("perspective"), str) else ""
    body = _strip_front_matter(text).strip()
    if not perspective:
        # first non-heading paragraph line, so a note without front matter still has one
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                perspective = line
                break
    return RoleProfile(member=member, title=title or member, perspective=perspective,
                       body=body, source=source, path=path)


def _profile_files(folder: Path) -> dict[str, Path]:
    """Member -> file. A file claims a member through ``member:`` in its front
    matter, or through its file name; the front matter wins."""
    found: dict[str, Path] = {}
    for path in sorted(folder.glob("*.md")):
        if not path.is_file():
            continue
        try:
            meta = _front_matter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        claimed = meta.get("member") if isinstance(meta.get("member"), str) else ""
        member = claimed or path.stem
        for known in MEMBERS:
            if member.strip().lower() == known.lower() and known not in found:
                found[known] = path
    return found


def roles_folder(config: dict) -> Path | None:
    vault = _config(config, "knowledge.vault_path")
    if not vault:
        return None
    sub = _config(config, "knowledge.roles_subfolder") or DEFAULT_SUBFOLDER
    return Path(str(vault)).expanduser() / str(sub)


def load_roles(config: dict) -> dict[str, RoleProfile]:
    """Every member's profile, from the vault where it has one and from the
    shipped examples where it has not. Always six entries."""
    folder = roles_folder(config)
    from_vault: dict[str, Path] = {}
    if folder is not None:
        if folder.exists() and not folder.is_dir():
            raise RolesUnavailable(f"roles folder is not a folder: {folder}")
        if folder.is_dir():
            from_vault = _profile_files(folder)
    builtin = _profile_files(BUILTIN_DIR) if BUILTIN_DIR.is_dir() else {}
    profiles: dict[str, RoleProfile] = {}
    for member in MEMBERS:
        if member in from_vault:
            profiles[member] = _read_profile(from_vault[member], member, "vault")
        elif member in builtin:
            profiles[member] = _read_profile(builtin[member], member, "built-in")
        else:
            profiles[member] = RoleProfile(member=member, title=member, perspective="", body="",
                                           source="built-in", path=None)
    return profiles


def summary(profiles: dict[str, RoleProfile]) -> dict[str, object]:
    vault = [m for m, p in profiles.items() if p.source == "vault"]
    return {
        "from_vault": vault,
        "built_in": [m for m in profiles if m not in vault],
        "folder": str(roles_folder_from(profiles)) if vault else None,
    }


def roles_folder_from(profiles: dict[str, RoleProfile]) -> Path | None:
    for profile in profiles.values():
        if profile.source == "vault" and profile.path is not None:
            return profile.path.parent
    return None


def install_examples(folder: Path, *, only_missing: bool = True) -> list[Path]:
    """Copies the shipped example profiles into ``folder`` (created if
    needed). Existing files are never overwritten unless ``only_missing`` is
    False. Returns what was written."""
    folder.mkdir(parents=True, exist_ok=True)
    present = _profile_files(folder) if only_missing else {}
    written: list[Path] = []
    for member, source in _profile_files(BUILTIN_DIR).items():
        if member in present:
            continue
        target = folder / source.name
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
