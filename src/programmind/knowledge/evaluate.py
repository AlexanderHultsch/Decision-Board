"""The knowledge evaluation (spec section 5.1, decided 10 September
2026): a set of real questions, each with the pages a good answer must
use, run through the selection, and a hit rate per mode - so a change to
the ranking is judged by a number, not by feel.

The set lives in ``tests/knowledge_eval/questions.json``: a list of
``{"question": ..., "expected": [pages], "members": {member: [pages]}}``.
``expected`` pages must reach at least one member, in full or as a one
line summary; ``members`` pages must reach that member.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from programmind.knowledge import knowledge

from programmind.knowledge import picker
from programmind.ai.provider import AiProvider
from programmind.agents.board.board import role_terms
from programmind.agents.board.roles import load_board

DEFAULT_SET = Path(__file__).resolve().parents[3] / "tests" / "knowledge_eval" / "questions.json"


@dataclass
class Outcome:
    question: str
    hits: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    member_misses: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        total = len(self.hits) + len(self.misses)
        return len(self.hits) / total if total else 1.0


def load_set(path: Path = DEFAULT_SET) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [q for q in data if isinstance(q, dict) and q.get("question")]


def _norm(page: str) -> str:
    page = page.replace("\\", "/").strip()
    return page if page.endswith(".md") else page + ".md"


def evaluate(config: dict, questions: list[dict[str, Any]], *, selection: str = "python",
             provider: AiProvider | None = None, budget: int | None = None) -> list[Outcome]:
    board = load_board(config)
    terms = {m: role_terms(role) for m, role in board.profiles.items()}
    outcomes: list[Outcome] = []
    for item in questions:
        question = str(item["question"])
        picks = None
        if selection == "ai" and provider is not None:
            cands = knowledge.candidates(config, question, terms)
            result = picker.pick(provider, question, {m: role.perspective for m, role in board.profiles.items()}, cands)
            picks = result.picks if result.ok else None
        blocks = knowledge.gather_for_members(config, question, terms, token_budget=budget, picks=picks)
        reached: dict[str, set[str]] = {}
        for member, sel in blocks.items():
            reached[member] = {n.relative for n in sel.notes} | {n.relative for n in sel.briefs}
        anyone = set().union(*reached.values()) if reached else set()
        outcome = Outcome(question=question)
        for page in item.get("expected") or []:
            (outcome.hits if _norm(page) in {p for p in anyone} else outcome.misses).append(_norm(page))
        for member, pages in (item.get("members") or {}).items():
            for page in pages:
                if _norm(page) in reached.get(member, set()):
                    outcome.hits.append(f"{member}: {_norm(page)}")
                else:
                    outcome.misses.append(f"{member}: {_norm(page)}")
                    outcome.member_misses.append(f"{member}: {_norm(page)}")
        outcomes.append(outcome)
    return outcomes


def report(outcomes: list[Outcome], selection: str) -> str:
    lines = [f"Knowledge evaluation, selection: {selection}"]
    total_hits = sum(len(o.hits) for o in outcomes)
    total = sum(len(o.hits) + len(o.misses) for o in outcomes)
    for o in outcomes:
        lines.append(f"- {o.rate:4.0%}  {o.question[:70]}")
        for miss in o.misses:
            lines.append(f"      missed: {miss}")
    lines.append(f"Hit rate: {total_hits}/{total}" + (f" = {total_hits / total:.0%}" if total else ""))
    return "\n".join(lines)
