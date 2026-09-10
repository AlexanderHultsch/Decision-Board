"""Prompt library: prompts as versioned files, separate from tool code.

Versioning is Git's job, not this module's - a prompt file's history is its
commit history, same as any other tracked file. What this module adds is the
separation itself: prompt text lives in ``.md`` files next to the code that
uses it rather than as string literals inside it, so a prompt can be
reviewed and changed without touching Python.

Since the restructuring of 10 September 2026 the prompts live with their
owners: the board's under ``agents/board/prompts``, Ask the vault's under
``agents/ask/prompts``, the knowledge pick and the summaries under
``knowledge/prompts``, the memory proposal under ``memory/prompts``. A new
agent adds its folder to ``PROMPT_DIRS`` and nothing else.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIRS: tuple[Path, ...] = (
    _ROOT / "agents" / "board" / "prompts",
    _ROOT / "agents" / "ask" / "prompts",
    _ROOT / "knowledge" / "prompts",
    _ROOT / "memory" / "prompts",
)


def prompt_names() -> tuple[str, ...]:
    """Every prompt on disk, by name, sorted."""
    names = {path.stem for folder in PROMPT_DIRS if folder.is_dir() for path in folder.glob("*.md")}
    return tuple(sorted(names))


PROMPT_NAMES: tuple[str, ...] = prompt_names()


def load_prompt(name: str) -> str:
    """Reads the prompt text for ``name`` from ``<name>.md`` in one of the
    prompt folders."""
    for folder in PROMPT_DIRS:
        path = folder / f"{name}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise ValueError(f"unknown prompt {name!r}; available prompts: {', '.join(prompt_names())}")
