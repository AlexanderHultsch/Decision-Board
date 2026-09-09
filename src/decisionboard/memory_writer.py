"""Writing a closed topic into the vault - proposed by a model call,
written only after Alex confirms (AP-4, spec section 5).

When Alex closes a topic and says yes to remembering it, one model call
receives the topic, its inputs, the synthesis, the follow-up conversation
and an outline of the vault (folders and existing note titles) and proposes
where the note belongs and what it should say. Python then validates the
path (inside the vault, a ``.md`` file, no traversal), decides whether the
write creates a new note or appends to an existing one, and renders the
front matter itself. Nothing touches the disk until ``write_note`` is called
with what Alex saw and confirmed - and Alex can edit both path and body
before that.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .agent.prompts import load_prompt
from .agent.provider import TASK_BOARD, AiProvider, AiResult
from .knowledge import vault_outline

DEFAULT_SUBFOLDER = "Decision Board"


@dataclass
class MemoryProposal:
    path: str            # vault-relative, forward slashes
    title: str
    tags: list[str]
    body: str            # Markdown body, no front matter
    mode: str            # "create" or "append"
    ai_result: AiResult | None = None
    parse_error: str | None = None


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9äöüÄÖÜß ]+", " ", text).strip()
    slug = re.sub(r"\s+", " ", slug)
    return slug[:80] or "Decision"


def proposal_prompt(
    *, topic: str, inputs: dict, synthesis: dict | str, turns: list[tuple[str, str]], outline: str
) -> str:
    lines = [
        load_prompt("memory_proposal"),
        "",
        "## Vault outline",
        "",
        outline,
        "",
        "## Topic",
        "",
        f"Topic: {topic}",
        f"Context: {inputs.get('context', '')}",
    ]
    if inputs.get("options"):
        lines.append("Options under consideration:")
        lines.extend(f"- {option}" for option in inputs["options"])
    if inputs.get("constraints"):
        lines.append("Hard constraints:")
        lines.extend(f"- {constraint}" for constraint in inputs["constraints"])
    lines += ["", "## Synthesis", ""]
    lines.append(json.dumps(synthesis, indent=2, ensure_ascii=False) if isinstance(synthesis, dict) else str(synthesis))
    lines += ["", "## Follow-up conversation", ""]
    if turns:
        for question, answer in turns:
            lines.append(f"Q: {question}")
            lines.append(f"A: {answer}")
    else:
        lines.append("(none)")
    return "\n".join(lines)


def safe_relative_path(vault: Path, proposed: str, fallback_title: str) -> str:
    """A vault-relative ``.md`` path that cannot escape the vault."""
    candidate = (proposed or "").strip().replace("\\", "/").lstrip("/")
    if not candidate.lower().endswith(".md"):
        candidate = f"{DEFAULT_SUBFOLDER}/{_slug(fallback_title)}.md" if not candidate else candidate + ".md"
    # Sanitise first, then drop ".." - the other way round, '..:' would be
    # cleaned into '..' after the filter had run.
    parts = [re.sub(r'[<>:"|?*\x00]', "", part).strip() for part in candidate.split("/")]
    parts = [part for part in parts if part not in ("", ".", "..")]
    if not parts:
        parts = [DEFAULT_SUBFOLDER, f"{_slug(fallback_title)}.md"]
    fallback = f"{DEFAULT_SUBFOLDER}/{_slug(fallback_title)}.md"
    for relative in ("/".join(parts), fallback):
        if _inside(vault, relative):
            return relative
    raise ValueError(f"no safe path inside the vault for {proposed!r}")


def _inside(vault: Path, relative: str) -> bool:
    """Whether ``vault/relative`` resolves - symbolic links followed - to a
    place inside the vault."""
    try:
        resolved = (vault / relative).resolve()
        return vault.resolve() in resolved.parents
    except (OSError, ValueError):
        return False


def parse_proposal(text: str, vault: Path, topic: str) -> MemoryProposal:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        relative = safe_relative_path(vault, "", topic)
        return MemoryProposal(
            path=relative, title=topic, tags=["decision-board"],
            body=f"# {topic}\n\n(The proposal did not parse. Write the note by hand.)",
            mode="append" if (vault / relative).exists() else "create",
            parse_error="memory proposal did not parse as JSON",
        )
    title = str(data.get("title") or topic).strip()
    tags = [str(tag).strip().lstrip("#") for tag in (data.get("tags") or []) if str(tag).strip()]
    if "decision-board" not in tags:
        tags.append("decision-board")
    relative = safe_relative_path(vault, str(data.get("path") or ""), title)
    body = str(data.get("body") or "").strip()
    mode = "append" if (vault / relative).exists() else "create"
    return MemoryProposal(path=relative, title=title, tags=tags, body=body, mode=mode)


def propose(
    provider: AiProvider, vault: Path | str, *, topic: str, inputs: dict,
    synthesis: dict | str, turns: list[tuple[str, str]],
) -> MemoryProposal:
    vault_dir = Path(vault)
    outline = vault_outline(vault_dir)
    prompt = proposal_prompt(topic=topic, inputs=inputs, synthesis=synthesis, turns=turns, outline=outline)
    ai_result = provider.complete(TASK_BOARD, prompt)
    proposal = parse_proposal(ai_result.text, vault_dir, topic)
    proposal.ai_result = ai_result
    return proposal


def render_note(title: str, tags: list[str], body: str, *, today: date | None = None) -> str:
    """Front matter plus body - Python's job, never the model's (AP-1)."""
    created = (today or date.today()).isoformat()
    if "decision-board" not in tags:   # every note the board wrote stays findable
        tags = [*tags, "decision-board"]
    tag_list = ", ".join(tags)
    front = ["---", f"title: {json.dumps(title, ensure_ascii=False)}", f"tags: [{tag_list}]",
             f"created: {created}", "source: decision-board", "---", ""]
    return "\n".join(front) + body.strip() + "\n"


def preview(proposal: MemoryProposal) -> str:
    """Exactly the text ``write_note`` will put on disk for this proposal.
    An appended section carries its tags as a line, since the existing
    note's front matter is never touched."""
    if proposal.mode == "append":
        tags = f"Tags: {', '.join(proposal.tags)}\n\n" if proposal.tags else ""
        return f"\n\n---\n\n## {proposal.title}\n\n{tags}{proposal.body.strip()}\n"
    return render_note(proposal.title, proposal.tags, proposal.body)


def write_note(vault: Path | str, proposal: MemoryProposal) -> Path:
    """Writes the confirmed proposal. Creates the folder if needed; appends
    when the note already exists, never overwrites."""
    vault_dir = Path(vault)
    relative = safe_relative_path(vault_dir, proposal.path, proposal.title)
    target = vault_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        proposal.mode = "append"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(preview(proposal))
    else:
        proposal.mode = "create"
        target.write_text(preview(proposal), encoding="utf-8")
    return target
