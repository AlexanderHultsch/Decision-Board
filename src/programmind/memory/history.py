"""The work every agent keeps (spec 11.1, decision 9, 10 September 2026).

One JSON file per piece of work, in one folder next to the local config,
never in the vault: a board topic and an Ask the vault thread lie side by
side and are told apart by their ``kind`` (``board``, ``ask``). The folder
is ``config/history/``; a folder named in ``server.history_folder`` (or, as
it was called until this step, ``server.threads_folder``) wins over it.

Nothing here knows what a topic or a thread holds: each agent writes its own
dictionary and reads it back. A file that does not parse is skipped, never
deleted - a half-written file must not cost the rest of the history.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z0-9]{6,32}$")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _config_value(config: dict, dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def history_folder(config: dict, config_path: Path | None) -> Path:
    """Where the work is kept: ``server.history_folder``, else the older
    ``server.threads_folder`` (a folder Alex chose himself stays where it
    is), else ``history/`` next to ``config.local.json``.

    The default folder was ``threads/`` until the shell of 10 September
    2026; when only that one exists it is renamed once, so the threads
    written before the rename are still listed. A rename that fails (the
    folder is open, or read-only) leaves the old folder alone and the new
    one is created empty."""
    configured = _config_value(config, "server.history_folder") or _config_value(config, "server.threads_folder")
    if configured:
        return Path(str(configured)).expanduser()
    base = config_path.parent if config_path is not None else Path.cwd() / "config"
    folder = base / "history"
    threads = base / "threads"
    if not folder.exists() and threads.is_dir():
        try:
            threads.rename(folder)
        except OSError:
            return threads
    return folder


class HistoryStore:
    """One JSON file per record in ``folder``; the folder is created on the
    first write."""

    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)

    def path(self, record_id: str) -> Path:
        if not _ID.match(record_id):
            raise ValueError(f"bad record id: {record_id!r}")
        return self.folder / f"{record_id}.json"

    def save(self, record_id: str, data: dict[str, Any]) -> Path:
        """Written to a temporary file of its own and moved into place, so a
        reader never meets half a record and two writers of the same record
        never meet in the same temporary file (seen 10 September 2026: the
        answer to a request and the background step that finished it saved
        the same topic at the same moment, and the file on disk was left
        truncated)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path(record_id)
        tmp = self.folder / f"{record_id}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return path

    def load(self, record_id: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path(record_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def delete(self, record_id: str) -> bool:
        try:
            self.path(record_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Every readable record, of one ``kind`` when asked for. Unsorted;
        the caller decides the order."""
        if not self.folder.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in self.folder.glob("*.json"):
            if not _ID.match(path.stem):
                continue
            data = self.load(path.stem)
            if data is None:
                continue
            data.setdefault("id", path.stem)
            if kind is None or data.get("kind") == kind:
                found.append(data)
        return found
