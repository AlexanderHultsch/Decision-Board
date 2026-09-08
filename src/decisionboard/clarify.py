"""The clarifier: one model call in front of the board (FR-3.2 as decided
on 8 September 2026).

Alex types one free-text question. The clarifier reads it, together with
the knowledge block selected for it, and returns two things: the four FR-3.1
inputs it can extract (topic, context, options, constraints) and at least
one question whose answer would most change the board's recommendation.
The clarifier is independent of the six members and its questions never
reach them - the members receive only the aligned input, after Alex has
answered and confirmed.

Deterministic parts stay in Python (AP-1): the response is parsed here, the
minimum of one question is enforced here, and Alex's answers are folded
into the context by string assembly, not by a second model call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .agent.prompts import load_prompt
from .agent.provider import TASK_BOARD, AiProvider, AiResult

FALLBACK_QUESTION = (
    "What outcome would you consider a failure for this decision, and is "
    "there anything the board should know that is not in your question?"
)


@dataclass
class Clarification:
    topic: str
    context: str
    options: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    ai_result: AiResult | None = None
    parse_error: str | None = None


def clarifier_prompt(question: str, knowledge_text: str) -> str:
    lines = [load_prompt("clarifier"), "", "## Question from Alex", "", question.strip()]
    if knowledge_text:
        lines += ["", knowledge_text]
    return "\n".join(lines)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [line.strip("- ").strip() for line in value.splitlines() if line.strip("- ").strip()]
    return []


def parse_clarification(text: str, question: str) -> Clarification:
    """The clarifier's JSON, with Python's guarantees applied: the topic is
    never empty (it falls back to the question itself) and there is always
    at least one question (``FALLBACK_QUESTION`` when the model returned
    none - the minimum of one is a rule, not a hope)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return Clarification(
            topic=question.strip(), context="", questions=[FALLBACK_QUESTION],
            parse_error="clarifier response did not parse as JSON",
        )
    questions = _as_list(data.get("questions"))
    if not questions:
        questions = [FALLBACK_QUESTION]
    topic = str(data.get("topic") or "").strip() or question.strip()
    return Clarification(
        topic=topic,
        context=str(data.get("context") or "").strip(),
        options=_as_list(data.get("options")),
        constraints=_as_list(data.get("constraints")),
        questions=questions,
    )


def clarify(provider: AiProvider, question: str, knowledge_text: str = "") -> Clarification:
    """One clarifier call. Raises whatever the provider raises."""
    ai_result = provider.complete(TASK_BOARD, clarifier_prompt(question, knowledge_text))
    clarification = parse_clarification(ai_result.text, question)
    clarification.ai_result = ai_result
    return clarification


def merge_answers(clarification: Clarification, answers: list[str]) -> str:
    """The context the board receives: the clarifier's extracted context
    plus every question/answer pair, verbatim. A skipped question is
    recorded as skipped, so the members know it was asked and not
    answered rather than never asked."""
    lines = [clarification.context.strip()] if clarification.context.strip() else []
    pairs = []
    for index, question in enumerate(clarification.questions):
        answer = answers[index].strip() if index < len(answers) and answers[index] else ""
        pairs.append(f"Q: {question}\nA: {answer if answer else '(not answered)'}")
    if pairs:
        lines.append("Clarification with Alex before the board was asked:")
        lines.extend(pairs)
    return "\n\n".join(lines)
