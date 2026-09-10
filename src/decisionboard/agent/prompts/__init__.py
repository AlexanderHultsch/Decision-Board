"""Prompt library: prompts as versioned files, separate from tool code.

Versioning is Git's job, not this module's - a prompt file's history is its
commit history, same as any other tracked file. What this module adds is the
separation itself: prompt text lives in ``.md`` files next to the task-type
constants in ``provider.py`` rather than as string literals inside the tool
code that calls the model, so a prompt can be reviewed and changed without
touching Python.

Each file here is named after the task type it belongs to (see
``TASK_*`` constants in ``provider.py``) and holds the full instruction text
sent to the model for that task, contact lists and other per-run context
excluded - those are supplied by the caller at call time, not baked into the
file.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_NAMES: tuple[str, ...] = (
    "ask",
    "board_combined",
    "board_members",
    "board_synthesis",
    "clarifier",
    "knowledge_pick",
    "memory_proposal",
    "summaries",
)

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Reads the prompt text for ``name`` from ``<name>.md`` in this directory."""
    if name not in PROMPT_NAMES:
        raise ValueError(
            f"unknown prompt {name!r}; available prompts: {', '.join(PROMPT_NAMES)}"
        )
    path = _PROMPTS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"prompt {name!r} is registered but its file is missing: {path}") from None
