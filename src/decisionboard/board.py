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
from dataclasses import dataclass, field
from typing import Any, Callable

from .agent.prompts import load_prompt
from .agent.provider import TASK_BOARD, AiNotConfiguredError, AiProvider, AiResult, is_configured
from .audit import log_run
from .knowledge import active_project, kpi_notes
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
    applies: bool = True      # False: the member said, with reasons, that the topic does not touch it


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
    # What a member needs to be asked again (decided 9 September 2026:
    # a follow-up may go back to chosen members): the input the run used,
    # the conduct note, the KPI data per member and the project.
    inputs: dict[str, Any] = field(default_factory=dict)
    conduct: str = ""
    member_data: dict[str, str] = field(default_factory=dict)
    project: str = ""


@dataclass
class FollowUp:
    """One follow-up turn: the board's answer, structured where the model
    kept the shape, and the answers of the members asked again (if any)."""
    question: str
    answer: str                                   # plain text, always
    data: dict[str, Any] | None = None            # the parsed JSON answer, for the HMI
    assessments: list[MemberAssessment] = field(default_factory=list)
    failed_members: list[str] = field(default_factory=list)
    llm_calls: int = 0


def _member_prompt(
    topic: str, context: str, options: tuple[str, ...], constraints: tuple[str, ...], member: str,
    role: RoleProfile | None = None, conduct: str = "", kpi_data: str = "", project: str = "",
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
    if project:
        lines += [
            f"Project: {project}. This question belongs to this project. The knowledge and KPI "
            "data below were selected for it; pages of other projects were left out.",
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


def _parse_member_response(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not all(key in data for key in ("view", "risks", "recommendation")):
        return None
    risks = data["risks"]
    if isinstance(risks, list):
        # One bullet per risk: the interface shows lines starting with "- "
        # as a list, the same way it shows view and recommendation.
        risks = "\n".join(f"- {item}" for item in (str(r).strip() for r in risks) if item)
    applies = data.get("applies", True)
    if isinstance(applies, str):
        applies = applies.strip().lower() not in ("false", "no", "0")
    return {
        "view": str(data["view"]),
        "risks": str(risks),
        "recommendation": str(data["recommendation"]),
        "applies": bool(applies),
    }


def _synthesis_prompt(
    assessments: list[MemberAssessment], roles: dict[str, RoleProfile] | None = None
) -> str:
    payload = [
        {
            "member": assessment.member,
            "applies": assessment.applies,
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
    not_affected = data.get("not_affected") or []
    if not_affected:
        lines.append("Not affected:")
        lines.extend(f"  - {item}" for item in not_affected)
    return "\n".join(lines)


_TOOL_ONLY_MARKERS = ("no answer text", "called tool(s) instead of answering")
_RETRY_PREFIX = (
    "IMPORTANT: your previous attempt at this call used tools and returned no text. "
    "You have no tools. Do not read, search or list anything. Reply with the JSON object only.\n\n"
)


def _is_tool_only_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TOOL_ONLY_MARKERS)


def _complete_with_retry(provider: AiProvider, prompt: str) -> tuple[AiResult, int]:
    """One member call, retried once when the model called tools instead of
    answering (seen 9 September 2026: Manufacturing ran ``glob`` and
    produced no text). Returns the result and the number of calls made."""
    try:
        return provider.complete(TASK_BOARD, prompt), 1
    except Exception as exc:
        if not _is_tool_only_failure(exc):
            raise
    return provider.complete(TASK_BOARD, _RETRY_PREFIX + prompt), 2


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
    members: tuple[str, ...] | list[str] | None = None,
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
    if members:
        # Decided 9 September 2026: Alex chooses which members are asked;
        # a topic need not concern every swim lane.
        chosen = {str(m).strip().lower() for m in members}
        roles = {name: role for name, role in roles.items() if name.lower() in chosen}
        if not roles:
            raise ValueError("none of the chosen members is on the board")
    members = tuple(roles)
    if member_data is None:
        # The KPI data each member is judged against, attached deterministically
        # (AP-1) whatever the question - never left to the ranked selection.
        member_data = kpi_notes(config, members)
    project = active_project(config) or ""
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
                       member_data.get(member, ""), project)
        for member in members
    ]

    def _notify(member: str, state: str) -> None:
        if on_member is not None:
            try:
                on_member(member, state)
            except Exception:   # a progress display must never take a run down
                pass

    retries = [0]

    def _call(member: str, prompt: str) -> AiResult:
        _notify(member, "running")
        try:
            result, calls = _complete_with_retry(provider, prompt)
        except Exception:
            _notify(member, "failed")
            raise
        retries[0] += calls - 1
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

    llm_calls = len(members) + retries[0]

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
                "Only one member answered, so there is nothing to consolidate: its assessment is the answer."
                if len(assessments) == 1 else
                "No member produced an assessment, so there is nothing to consolidate."
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
    member_answers: list[MemberAssessment] | None = None,
) -> str:
    lines = [_synthesis_prompt(assessments, roles), "", "## Conversation so far"]
    if turns:
        for turn_question, turn_answer in turns:
            lines.append(f"Q: {turn_question}")
            lines.append(f"A: {turn_answer}")
    else:
        lines.append("(none yet)")
    if member_answers:
        lines += ["", "## Member answers to the new question", ""]
        lines.append(json.dumps([
            {"member": a.member, "applies": a.applies, "view": a.view, "risks": a.risks,
             "recommendation": a.recommendation}
            for a in member_answers
        ], indent=2))
    lines.append("")
    lines.append("## New question")
    lines.append(question)
    return "\n".join(lines)


def _member_follow_up_prompt(conversation: BoardConversation, member: str, question: str) -> str:
    """The prompt for one member asked again: its original call, then its
    earlier assessment, the board's recommendation so far, the conversation
    and the new question (board_members.md, "Follow-up turn")."""
    inputs = conversation.inputs
    role = (conversation.roles or {}).get(member)
    lines = [_member_prompt(
        str(inputs.get("topic", conversation.result.topic)), str(inputs.get("context", "")),
        tuple(inputs.get("options", ())), tuple(inputs.get("constraints", ())),
        member, role, conversation.conduct, conversation.member_data.get(member, ""), conversation.project,
    )]
    earlier = next((a for a in conversation.result.assessments if a.member == member), None)
    lines += ["", "## Your earlier assessment", ""]
    if earlier is not None:
        lines.append(json.dumps({"applies": earlier.applies, "view": earlier.view, "risks": earlier.risks,
                                 "recommendation": earlier.recommendation}, indent=2))
    else:
        lines.append("(you did not produce an assessment in the first round)")
    lines += ["", "## Board recommendation so far", "", conversation.result.synthesis, "", "## Conversation so far"]
    if conversation.turns:
        for turn_question, turn_answer in conversation.turns:
            lines.append(f"Q: {turn_question}")
            lines.append(f"A: {turn_answer}")
    else:
        lines.append("(none yet)")
    lines += ["", "## New question", "", question]
    return "\n".join(lines)


def _follow_up_text(data: dict[str, Any]) -> str:
    lines = [str(data.get("answer", "")).strip()]
    reasons = data.get("reasons") or []
    if reasons:
        lines.append("Reasons:")
        lines.extend(f"  - {item}" for item in reasons)
    if data.get("recommendation_now"):
        lines.append(f"Recommendation now: {data['recommendation_now']}")
    disagreements = data.get("disagreements") or []
    if disagreements:
        lines.append("Disagreements:")
        lines.extend(f"  - {item}" for item in disagreements)
    return "\n".join(line for line in lines if line)


def ask_follow_up_full(
    config: dict, provider: AiProvider | None, conversation: BoardConversation, question: str,
    members: tuple[str, ...] | list[str] = (),
) -> FollowUp:
    """One follow-up turn on a completed board run.

    With ``members`` empty this is the one-call form: the synthesis prompt
    is re-run over the original assessments and the conversation so far.
    With members named (decided 9 September 2026), each of them is asked
    again, in isolation, with its earlier assessment and the conversation
    in front of it, and the synthesis then answers over those new answers:
    one call per member plus one.

    Raises ``AiNotConfiguredError`` under the same conditions ``run_board``
    does, and ``ValueError`` for a blank question."""
    if not question.strip():
        raise ValueError("a follow-up question must not be blank")
    if provider is None or not is_configured(config, TASK_BOARD):
        raise AiNotConfiguredError(
            "AI Board has no model configured - set provider.models.board"
        )
    chosen = [m for m in (conversation.roles or {}) if m.lower() in {str(x).strip().lower() for x in members}]
    member_answers: list[MemberAssessment] = []
    failed: list[str] = []
    calls = 0
    if chosen:
        prompts = {member: _member_follow_up_prompt(conversation, member, question) for member in chosen}
        results: dict[str, AiResult | BaseException] = {}
        with ThreadPoolExecutor(max_workers=len(chosen)) as executor:
            futures = {member: executor.submit(_complete_with_retry, provider, prompts[member]) for member in chosen}
            for member, future in futures.items():
                try:
                    result, made = future.result()
                    results[member] = result
                    calls += made
                except Exception as exc:
                    results[member] = exc
                    calls += 1
        for member in chosen:
            outcome = results[member]
            if isinstance(outcome, BaseException):
                failed.append(f"{member}: {outcome}")
                continue
            parsed = _parse_member_response(outcome.text)
            if parsed is None:
                failed.append(f"{member}: response did not parse as JSON with view/risks/recommendation")
                continue
            member_answers.append(MemberAssessment(member=member, **parsed))

    prompt = _follow_up_prompt(conversation.result.assessments, conversation.turns, question,
                               conversation.roles, member_answers or None)
    ai_result = provider.complete(TASK_BOARD, prompt)
    calls += 1
    try:
        data = json.loads(ai_result.text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "answer" in data:
        answer = _follow_up_text(data)
    else:
        data = None
        answer = ai_result.text
    conversation.turns.append((question, answer))
    conversation.llm_calls += calls
    return FollowUp(question=question, answer=answer, data=data, assessments=member_answers,
                    failed_members=failed, llm_calls=calls)


def ask_follow_up(
    config: dict, provider: AiProvider | None, conversation: BoardConversation, question: str,
    members: tuple[str, ...] | list[str] = (),
) -> str:
    """``ask_follow_up_full`` for callers that want the text only (the CLI)."""
    return ask_follow_up_full(config, provider, conversation, question, members).answer


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
