"""Enrich the vault for the second-generation selection (spec section
5.1, decided 10 September 2026): ``phases`` and ``aliases`` on the task
pages, derived from the vault itself, and a ``summary`` on every page,
written by the model and marked as such.

Python derives what it can (AP-1): the phases come from the task table on
the VPDS overview, the aliases from the ``task`` property when the table's
name differs from the title. The model writes only the summaries, from the page text, and
every summary is prefixed so nobody mistakes it for official text. Nothing
is written before Alex has seen the proposals and said yes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from programmind.ai.prompts import load_prompt
from programmind.ai.provider import TASK_BOARD, AiProvider
from programmind.knowledge.knowledge import Note, _front_matter, estimate_tokens

SUMMARY_PREFIX = "AI summary, not official."
SUMMARY_BATCH = 8             # pages per model call
SUMMARY_CHARS = 6000          # characters of a page the model sees
_PHASE_COLUMNS = ["PURSUIT", "MP0", "MP1", "MP2", "MP3", "MP4", "MP5", "MP6", "MP7", "MP8", "MP9", "MP10"]


@dataclass
class Change:
    path: str
    key: str                  # the property
    value: Any                # a string or a list
    old: Any = None


@dataclass
class Proposal:
    changes: list[Change] = field(default_factory=list)
    calls: int = 0
    problems: list[str] = field(default_factory=list)


def task_phases(overview: Note) -> dict[str, tuple[str, ...]]:
    """Task name -> phases active, read from the overview's task table: a
    non-empty cell under a phase column marks the task active in it."""
    result: dict[str, tuple[str, ...]] = {}
    for line in overview.body.splitlines():
        if not line.startswith("| ") or line.startswith("| Task ") or re.match(r"^\|\s*-", line):
            continue
        # Cells split at ``|`` but not at the ``\|`` inside a table link.
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 13:
            continue
        name = re.sub(r"\[\[[^\]|]+\\\|([^\]]+)\]\]", r"\1", cells[0]).strip()
        phases = tuple(_PHASE_COLUMNS[i] for i, cell in enumerate(cells[1:13]) if cell)
        if name and phases:
            result[name] = phases
    return result


def task_aliases(note: Note, task: str) -> tuple[str, ...]:
    """Aliases Python can defend: the task table's shorter name when it
    differs from the title, and the aliases already there. Abbreviations
    are not aliases: the Abbreviations page is their one place, and the
    selection reads that page's links (decided 10 September 2026)."""
    title = note.title.replace("VPDS_", "").strip()
    aliases = list(note.aliases)
    if task and task.lower() != title.lower() and task not in aliases:
        aliases.append(task)
    return tuple(aliases)


def propose(notes: list[Note], provider: AiProvider | None, *, summaries: bool = True, refresh: bool = False) -> Proposal:
    """What the enrichment would write: phases and aliases for task pages,
    summaries for pages without one (all pages with ``refresh``). Model
    calls happen here, in batches; nothing is written."""
    proposal = Proposal()
    overview = next((n for n in notes if n.path.stem == "VPDS_Overview"), None)
    phases_by_task = task_phases(overview) if overview else {}
    for note in notes:
        meta = _front_matter(note.body)
        task = meta.get("task") if isinstance(meta.get("task"), str) else ""
        if task:
            phases = phases_by_task.get(task)
            if phases and tuple(phases) != note.phases:
                proposal.changes.append(Change(note.relative, "phases", list(phases), list(note.phases)))
            elif not phases:
                proposal.problems.append(f"{note.relative}: task '{task}' is not a row of the task table")
            aliases = task_aliases(note, task)
            if aliases != note.aliases:
                proposal.changes.append(Change(note.relative, "aliases", list(aliases), list(note.aliases)))
    if summaries:
        wanted = [n for n in notes if refresh or not n.summary]
        if wanted and provider is None:
            proposal.problems.append(f"{len(wanted)} page(s) need a summary but no model is configured")
        elif wanted:
            for start in range(0, len(wanted), SUMMARY_BATCH):
                batch = wanted[start:start + SUMMARY_BATCH]
                written = _summaries(provider, batch)
                proposal.calls += 1
                for note in batch:
                    text = written.get(note.relative)
                    if text:
                        while text.lower().startswith(SUMMARY_PREFIX.lower()):
                            text = text[len(SUMMARY_PREFIX):].strip()          # the model echoed the marker: once is enough
                        proposal.changes.append(Change(note.relative, "summary", f"{SUMMARY_PREFIX} {text}", note.summary))
                    else:
                        proposal.problems.append(f"{note.relative}: the model returned no summary")
    return proposal


def _summaries(provider: AiProvider, batch: list[Note]) -> dict[str, str]:
    lines = [load_prompt("summaries"), "", "## Pages", ""]
    for note in batch:
        body = re.sub(r"^summary:.*\n", "", note.body, count=1, flags=re.M)[:SUMMARY_CHARS]   # the old summary is not the page
        lines += [f"### {note.relative}", "", body, ""]
    result = provider.complete(TASK_BOARD, "\n".join(lines))
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        text = re.sub(r"\s+", " ", str(value)).strip()
        if text:
            cleaned[str(key).strip()] = text[:400]
    return cleaned


def set_property(text: str, key: str, value: Any) -> str:
    """The page text with ``key: value`` set in its front matter, replacing
    the key where it is (inline or as a block list), inserting it before
    ``updated`` (else before the closing ``---``) otherwise, creating the front matter when there is
    none. Lists are written inline (as a block list when an item holds a
    comma), a string with a colon is quoted."""
    block: list[str] = []
    if isinstance(value, (list, tuple)):
        if any("," in str(v) for v in value):
            # An inline list splits at commas when read back; an item with
            # a comma in it needs the block form.
            block = [f"  - {v}" for v in value]
            rendered = ""
        else:
            rendered = "[" + ", ".join(str(v) for v in value) + "]"
    else:
        rendered = str(value)
        if ":" in rendered or rendered.startswith(("[", "{", "'", '"')) or rendered.endswith(" "):
            rendered = '"' + rendered.replace('"', "'") + '"'
    line = "\n".join([f"{key}: {rendered}".rstrip()] + block)
    if not text.startswith("---"):
        return f"---\n{line}\n---\n{text}"
    end = text.find("\n---", 3)
    if end == -1:
        return f"---\n{line}\n---\n{text}"
    head, tail = text[4:end], text[end:]
    lines = head.split("\n")
    out, skipping, done = [], False, False
    for raw in lines:
        if skipping and re.match(r"^\s*-\s+", raw):
            continue
        skipping = False
        if re.match(rf"^{re.escape(key)}\s*:", raw, flags=re.I):
            out.append(line)
            done = True
            skipping = True
            continue
        out.append(raw)
    if not done:
        # A new key goes in front of ``updated`` so that one stays last.
        at = next((i for i, raw in enumerate(out) if re.match(r"^updated\s*:", raw, flags=re.I)), len(out))
        if key.lower() == "updated":
            at = len(out)
        out.insert(at, line)
    return "---\n" + "\n".join(out) + tail


def apply(vault: Path, proposal: Proposal, today: str) -> list[Path]:
    """Write the proposal into the vault, one file at a time, ``updated``
    set on every page touched except KPI pages. Returns the files written."""
    by_path: dict[str, list[Change]] = {}
    for change in proposal.changes:
        by_path.setdefault(change.path, []).append(change)
    written: list[Path] = []
    for relative, changes in by_path.items():
        path = vault / relative
        text = path.read_text(encoding="utf-8-sig")
        for change in changes:
            text = set_property(text, change.key, change.value)
        if _front_matter(text).get("kind") != "kpi":
            # On a KPI page ``updated`` is the date of the numbers, which a
            # summary does not refresh; the board reads it for staleness.
            text = set_property(text, "updated", today)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def describe(proposal: Proposal) -> str:
    lines = []
    for change in proposal.changes:
        old = f" (was {change.old})" if change.old else ""
        value = change.value if not isinstance(change.value, list) else "[" + ", ".join(change.value) + "]"
        lines.append(f"- {change.path}: {change.key}: {value}{old}")
    for problem in proposal.problems:
        lines.append(f"! {problem}")
    if proposal.calls:
        lines.append(f"({proposal.calls} model call(s) made for the summaries)")
    return "\n".join(lines) if lines else "Nothing to change."
