"""AI Board orchestration (docs/spec.md chapter 9, FR-3.1..FR-3.6).

The board's members are whoever has a role profile in the roles folder
(section 3.4, ``roles.py``) - there is no member list in code. Each
produces a separate, clearly attributed assessment (FR-3.3): view, risks,
recommendation. FR-3.3a is a deliberate exception to AP-3 (one trigger,
one batched run): members are polled in isolation, one model call per
member, so a call never contains another member's answer. Batching all
members into one call is cheaper and would satisfy AP-3, but a model
writing the last assessment can see the ones it has already written and
converges towards them - the disagreement FR-3.6 exists to surface would
be smoothed away before anyone could read it. One board run is therefore
N+1 calls: one per member plus one synthesis call over the collected
assessments (FR-3.4).

Nothing here degrades to a partial answer without a provider - N
perspectives minus a model is not a board, so ``run_board`` raises rather
than reporting "not configured" and carrying on.

The module renders nothing but the tables and prose FR-3.5 asks Python,
not the model, to produce - ``render`` turns a ``BoardResult`` into text;
the CLI (``cli.py``) is the only place that prints it.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .agent.prompts import load_prompt
from .agent.provider import TASK_BOARD, AiNotConfiguredError, AiProvider, AiResult, is_configured
from .audit import log_run
from .knowledge import kpi_notes
from .roles import Board, RoleProfile, load_board


def _get(config: dict, dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node not in ("", None) else default


@dataclass(frozen=True)
class MemberAssessment:
    member: str
    view: str
    risks: str
    recommendation: str


@dataclass
class BoardResult:
    topic: str
    assessments: list[MemberAssessment]
    synthesis: str
    failed_members: list[str]     # member plus why, short strings
    ai_result: AiResult | None    # the synthesis call's AiResult, for AI-2
    llm_calls: int = 0
    synthesis_data: dict[str, Any] | None = None   # the parsed synthesis JSON, for the HMI


@dataclass
class BoardConversation:
    """A follow-up conversation attached to one completed ``BoardResult``
    (the owner's decision: "die Nachfrage reicht in der Synthese"). Lives for
    the duration of one ``board`` CLI invocation only - nothing here is
    written to disk or resumed across invocations."""
    result: BoardResult
    turns: list[tuple[str, str]]   # (question, answer), in order
    llm_calls: int = 0             # follow-up calls only; run_board counts its own
    roles: dict[str, RoleProfile] | None = None   # the profiles the run used


def _member_prompt(
    topic: str, context: str, options: tuple[str, ...], constraints: tuple[str, ...], member: str,
    role: RoleProfile | None = None, conduct: str = "", kpi_data: str = "",
) -> str:
    """The prompt for one member's call.

    ``load_prompt("board_members")`` already describes the single-member
    contract (isolation, response shape) FR-3.3a requires; this appends
    what that file cannot know in advance - which member this call is for,
    that member's role profile from the vault (section 3.4: its character,
    skills, KPIs and vocabulary, and only its own), and the FR-3.1 input."""
    lines = [
        load_prompt("board_members"),
        "",
        "## Member (FR-3.3a)",
        "",
        f"Member: {member}",
    ]
    if conduct:
        lines += ["", "## Board member conduct (section 3.4, the same for every member)", "", conduct]
    if role is not None and role.body:
        lines += ["", "## Role profile (section 3.4)", ""]
        if role.roles:
            lines.append(
                f"This member is the {role.member} swim lane. It speaks as its highest-ranked role, "
                f"{role.title} (level {role.level}), and answers for every role in the swim lane. "
                "Roles by rank - a lower level number is a higher rank and carries more weight where "
                "roles would disagree:"
            )
            lines.extend(f"- level {r.level}: {r.name}" for r in role.roles)
            lines.append("")
        lines.append(
            "This profile is authoritative for this call: assess from these responsibilities, "
            "against these KPIs, in this vocabulary."
        )
        lines += ["", role.body]
    if kpi_data:
        lines += [
            "",
            "## KPI data from the knowledge network (section 3.4)",
            "",
            "The values behind the targets this member is judged on, as recorded in the "
            "knowledge network. Baseline MG0 unless the note says otherwise. Judge every "
            "option against these numbers, name the delta, and quote the date a value was "
            "recorded whenever you use it.",
            "",
            kpi_data,
        ]
    elif role is not None and role.body:
        lines += [
            "",
            "## KPI data from the knowledge network (section 3.4)",
            "",
            "No KPI note for this member is in the knowledge network yet. Where a target "
            "would decide the answer, say that its value is not recorded and state the "
            "assumption you use instead.",
        ]
    lines += [
        "",
        "## Input (FR-3.1)",
        "",
        f"Topic: {topic}",
    ]
    if context:
        lines.append(f"Context: {context}")
    if options:
        lines.append("Options under consideration:")
        lines.extend(f"- {option}" for option in options)
    if constraints:
        lines.append("Hard constraints:")
        lines.extend(f"- {constraint}" for constraint in constraints)
    return "\n".join(lines)


def _member_questions(text: str) -> list[str] | None:
    """The questions a member returned instead of an assessment, if that
    is what it did (the ``{"status": "questions"}`` shape). Since the
    clarifier step in front of the board, members are told not to do this;
    when one does anyway, the questions are recorded against that member
    rather than lost."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("status") == "questions":
        questions = data.get("questions")
        return [str(item) for item in questions] if isinstance(questions, list) else []
    return None


def _parse_member_response(text: str) -> dict[str, str] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not all(key in data for key in ("view", "risks", "recommendation")):
        return None
    risks = data["risks"]
    if isinstance(risks, list):
        risks = "; ".join(str(item) for item in risks)
    return {
        "view": str(data["view"]),
        "risks": str(risks),
        "recommendation": str(data["recommendation"]),
    }


def _synthesis_prompt(
    assessments: list[MemberAssessment], roles: dict[str, RoleProfile] | None = None
) -> str:
    payload = [
        {
            "member": assessment.member,
            "view": assessment.view,
            "risks": assessment.risks,
            "recommendation": assessment.recommendation,
        }
        for assessment in assessments
    ]
    prompt = load_prompt("board_synthesis")
    if roles:
        # One line per member (section 3.4): the synthesis weighs who said
        # what; it never receives a member's full profile.
        prompt += "\n\n## Board members\n\n" + "\n".join(
            f"- {role.title}: {role.perspective}" for role in roles.values() if role.perspective
        )
    return prompt + "\n\n## Assessments\n\n" + json.dumps(payload, indent=2)


def _synthesis_text(data: dict[str, Any]) -> str:
    lines = [
        f"Overall recommendation: {data.get('overall_recommendation', '')}",
        f"Decisive criterion: {data.get('decisive_criterion', '')}",
    ]
    counter_arguments = data.get("counter_arguments") or []
    if counter_arguments:
        lines.append("Counter-arguments:")
        lines.extend(f"  - {item}" for item in counter_arguments)
    lines.append(f"What would change it: {data.get('what_would_change_it', '')}")
    disagreements = data.get("disagreements") or []
    if disagreements:
        lines.append("Disagreements (FR-3.6):")
        lines.extend(f"  - {item}" for item in disagreements)
    else:
        lines.append("Disagreements (FR-3.6): none stated.")
    return "\n".join(lines)


def run_board(
    config: dict,
    provider: AiProvider | None,
    *,
    topic: str,
    context: str = "",
    options: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    on_member: Callable[[str, str], None] | None = None,
    roles: dict[str, RoleProfile] | None = None,
    board: Board | None = None,
    member_data: dict[str, str] | None = None,
) -> BoardResult:
    """One AI Board run (FR-3.1..FR-3.6): one isolated call per member, then
    one synthesis call over what they produced. ``roles`` is the board
    (section 3.4); when not given it is read from the configuration's roles
    folder - fresh, on this run.

    ``on_member(member, state)`` is called from the worker threads as each
    member starts (``"running"``) and finishes (``"done"`` or ``"failed"``),
    so a front end can show progress. It carries no answer text - member
    isolation (FR-3.3a) is not weakened by a progress callback.

    Raises ``AiNotConfiguredError`` when no provider is given or
    ``provider.models.board`` has no model configured - unlike the other
    three triggers, the board does not degrade to a partial answer without a
    model (perspectives minus a model is not a board).

    A member whose response does not parse as JSON, or is missing one of
    ``view``/``risks``/``recommendation``, is recorded in ``failed_members``
    and left out of ``assessments`` - the other members still run. With
    fewer than two assessments a synthesis is skipped rather than produced
    over too little to synthesise, and that is stated plainly in
    ``synthesis`` rather than attempted anyway."""
    start = time.monotonic()
    if provider is None or not is_configured(config, TASK_BOARD):
        raise AiNotConfiguredError(
            "AI Board has no model configured - set provider.models.board"
        )

    if board is None and roles is None:
        board = load_board(config)
    if board is not None:
        roles = board.profiles
    conduct = board.conduct if board is not None else ""
    members = tuple(roles)
    if member_data is None:
        # The KPI data each member is judged against, attached deterministically
        # (AP-1) whatever the question - never left to the ranked selection.
        member_data = kpi_notes(config, members)
    audit_folder = _get(config, "runtime.audit_folder")
    pc_name = _get(config, "storage.pc_name", "")

    def _log(result: BoardResult) -> BoardResult:
        # There is no data store in this repository (decision 0005) - the
        # board is the only run there is, so it logs unconditionally rather
        # than skipping when a store is absent.
        ai_result = result.ai_result
        log_run(
            "board",
            audit_folder=audit_folder, pc_name=pc_name,
            duration_seconds=time.monotonic() - start,
            counts={
                "assessments": len(result.assessments),
                "failed_members": len(result.failed_members),
                "llm_calls": result.llm_calls,
                "over_token_limit": 1 if ai_result is not None and ai_result.over_token_limit else 0,
            },
            provider=ai_result.provider if ai_result is not None else None,
            model=ai_result.model if ai_result is not None else None,
            tokens=ai_result.total_tokens if ai_result is not None else None,
        )
        return result

    assessments: list[MemberAssessment] = []
    failed_members: list[str] = []

    # FR-3.3a: one call per member, in isolation - every prompt is built
    # here, before any call is dispatched, so it is structurally
    # impossible, not merely conventional, for one member's prompt to
    # contain another member's answer. Polling the calls concurrently
    # below does not weaken that isolation, it strengthens it: none of the
    # calls can see another's response, because none of them has
    # produced one yet when they are submitted.
    prompts = [
        _member_prompt(topic, context, options, constraints, member, roles[member], conduct,
                       member_data.get(member, ""))
        for member in members
    ]

    def _notify(member: str, state: str) -> None:
        if on_member is not None:
            try:
                on_member(member, state)
            except Exception:   # a progress display must never take a run down
                pass

    def _call(member: str, prompt: str) -> AiResult:
        _notify(member, "running")
        try:
            result = provider.complete(TASK_BOARD, prompt)
        except Exception:
            _notify(member, "failed")
            raise
        _notify(member, "done" if _parse_member_response(result.text) is not None else "failed")
        return result

    results: list[AiResult | None] = [None] * len(members)
    errors: list[BaseException | None] = [None] * len(members)
    with ThreadPoolExecutor(max_workers=len(members)) as executor:
        futures = [
            executor.submit(_call, member, prompt) for member, prompt in zip(members, prompts)
        ]
        for index, future in enumerate(futures):
            try:
                results[index] = future.result()
            except Exception as exc:
                errors[index] = exc

    llm_calls = len(members)

    for member, result, error in zip(members, results, errors):
        if error is not None:
            failed_members.append(f"{member}: {error}")
            continue
        parsed = _parse_member_response(result.text)
        if parsed is None:
            questions = _member_questions(result.text)
            if questions is not None:
                asked = " | ".join(questions) if questions else "(none listed)"
                failed_members.append(f"{member}: asked questions instead of assessing: {asked}")
            else:
                failed_members.append(
                    f"{member}: response did not parse as JSON with view/risks/recommendation"
                )
            continue
        assessments.append(MemberAssessment(member=member, **parsed))

    if len(assessments) < 2:
        return _log(BoardResult(
            topic=topic,
            assessments=assessments,
            synthesis=(
                "Synthesis skipped - fewer than two members produced an assessment "
                "(a synthesis of one view is not a synthesis)."
            ),
            failed_members=failed_members,
            ai_result=None,
            llm_calls=llm_calls,
        ))

    ai_result = provider.complete(TASK_BOARD, _synthesis_prompt(assessments, roles))
    llm_calls += 1

    try:
        synthesis_data = json.loads(ai_result.text)
    except json.JSONDecodeError:
        synthesis_data = None

    if isinstance(synthesis_data, dict):
        synthesis = _synthesis_text(synthesis_data)
    else:
        synthesis = f"Synthesis response did not parse as JSON: {ai_result.text}"
        synthesis_data = None

    return _log(BoardResult(
        topic=topic,
        assessments=assessments,
        synthesis=synthesis,
        failed_members=failed_members,
        ai_result=ai_result,
        llm_calls=llm_calls,
        synthesis_data=synthesis_data,
    ))


def _follow_up_prompt(
    assessments: list[MemberAssessment], turns: list[tuple[str, str]], question: str,
    roles: dict[str, RoleProfile] | None = None,
) -> str:
    lines = [_synthesis_prompt(assessments, roles), "", "## Conversation so far"]
    if turns:
        for turn_question, turn_answer in turns:
            lines.append(f"Q: {turn_question}")
            lines.append(f"A: {turn_answer}")
    else:
        lines.append("(none yet)")
    lines.append("")
    lines.append("## New question")
    lines.append(question)
    return "\n".join(lines)


def ask_follow_up(
    config: dict, provider: AiProvider | None, conversation: BoardConversation, question: str
) -> str:
    """One follow-up turn on an already-completed board run.

    The owner decided a follow-up costs one call, not seven: only the
    synthesis prompt is re-run, over the original assessments plus the
    conversation so far - the members are never polled again for this
    topic. That is exactly why ``board_members.md`` requires each member's
    first answer to be self-contained and substantive: it is the only
    material any follow-up will ever have to work with.

    Raises ``AiNotConfiguredError`` under the same conditions ``run_board``
    does, and ``ValueError`` for a blank question."""
    if not question.strip():
        raise ValueError("a follow-up question must not be blank")
    if provider is None or not is_configured(config, TASK_BOARD):
        raise AiNotConfiguredError(
            "AI Board has no model configured - set provider.models.board"
        )

    prompt = _follow_up_prompt(conversation.result.assessments, conversation.turns, question,
                               conversation.roles)
    ai_result = provider.complete(TASK_BOARD, prompt)
    answer = ai_result.text

    conversation.turns.append((question, answer))
    conversation.llm_calls += 1
    return answer


def render_follow_up(question: str, answer: str) -> str:
    """FR-3.5-style plain text for one follow-up turn - no markdown, matching
    ``render``'s style."""
    return f"Q: {question}\nA: {answer}"


def render(result: BoardResult) -> str:
    """FR-3.5: one plain-text table per member, columns View / Risks /
    Recommendation, then the synthesis as prose below - produced here in
    Python from structured output, never asked of the model. Fixed-width
    label column, no markdown."""
    lines: list[str] = []
    for assessment in result.assessments:
        lines.append(assessment.member)
        lines.append("-" * len(assessment.member))
        lines.append(f"{'View':<16}{assessment.view}")
        lines.append(f"{'Risks':<16}{assessment.risks}")
        lines.append(f"{'Recommendation':<16}{assessment.recommendation}")
        lines.append("")

    if result.failed_members:
        lines.append("Failed member(s) - the rest still ran:")
        for entry in result.failed_members:
            lines.append(f"  {entry}")
        lines.append("")

    lines.append("Synthesis")
    lines.append("-" * len("Synthesis"))
    lines.append(result.synthesis)
    return "\n".join(lines)
