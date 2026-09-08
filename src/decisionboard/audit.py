"""The board's audit trail: every run, appended to
``audit_log.jsonl`` (AI-2).

An append-only JSON Lines file, not a database or a workbook sheet:

* **Appending cannot corrupt what is already there.** A rewrite-the-whole-
  file format would put every prior row at risk on a failed write. One
  line, opened in append mode, cannot.
* **The shape is not fixed by a column layout.** A run's counts do not
  have to be flattened into a description string to fit a sheet that has
  no column for them (AI-2).

The board has no class C-E actions and needs no approval token (decision
0005), so only the run log survives the move from the source project's
governance audit trail - not the action/approval logging that trail also
carried.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_LOG_NAME = "audit_log.jsonl"


def audit_log_path(audit_folder: Path | str) -> Path:
    return Path(audit_folder) / AUDIT_LOG_NAME


def read_entries(audit_folder: Path | str, limit: int | None = None) -> list[dict[str, Any]]:
    """The audit trail, oldest first, or the last ``limit`` entries.

    A line that does not parse is returned as ``{"malformed": <the line>}``
    rather than skipped: a trail with a hole in it must show the hole (NFR-8).
    """
    path = audit_log_path(audit_folder)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit is not None:
        lines = lines[-limit:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"malformed": line})
    return entries


def _append(audit_folder: Path | str, entry: dict[str, Any]) -> Path:
    folder = Path(audit_folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / AUDIT_LOG_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def log_run(
    run_name: str,
    *,
    audit_folder: Path | str,
    pc_name: str | None = None,
    duration_seconds: float,
    counts: dict[str, int] | None = None,
    provider: str | None = None,
    model: str | None = None,
    tokens: int | None = None,
) -> Path | None:
    """Log one completed run (AI-2, NFR-10).

    Duration and the run's counts are structured fields, not a packed
    description string. The description keeps the one-line greppable form
    regardless, since that is what a person scanning the trail reads.

    Logging must never take a run down: a run that did its work and then
    could not write its own audit entry has still done its work, so any
    failure here - including no ``audit_folder`` being configured at all -
    becomes ``None`` rather than an exception the caller has to handle.
    """
    if not audit_folder:
        return None
    parts = [run_name, f"duration={duration_seconds:.1f}s"]
    for key, value in (counts or {}).items():
        parts.append(f"{key}={value}")
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_class": "B",
        "target": "board",
        "description": " ".join(parts),
        "decision": "completed",
        "actor": run_name,
        "pc_name": pc_name,
        "provider": provider,
        "model": model,
        "tokens": tokens,
        "duration_seconds": round(duration_seconds, 3),
        "counts": counts or {},
    }
    try:
        return _append(audit_folder, entry)
    except OSError:
        return None
