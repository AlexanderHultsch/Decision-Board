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
import re
import threading
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
    impact: str = ""          # the dependency chain into this member's area, one bullet per step
    # Where the material came from (decided 9 September 2026): facts the member
    # took from the knowledge net, each with its note and Python's verdict on
    # whether that note was sent and carries the wording; and the member's own
    # judgement, one bullet each. Anything not in ``sources`` is the model's.
    sources: tuple[dict[str, Any], ...] = ()
    judgement: str = ""
    flags: tuple[str, ...] = ()   # Python's warnings on this entry, e.g. "generic" in the combined mode


@dataclass
class BoardResult:
    topic: str
    assessments: list[MemberAssessment]
    synthesis: str
    failed_members: list[str]     # member plus why, short strings
    ai_result: AiResult | None    # the synthesis call's AiResult, for AI-2
    llm_calls: int = 0
    synthesis_data: dict[str, Any] | None = None   # the parsed synthesis JSON, for the HMI
    sources: dict[str, Any] = field(default_factory=dict)   # network notes used, unverified citations, judgement count
    mode: str = "individual"      # "individual": one call per member; "combined": one call for all


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
    sent_notes: dict[str, str] = field(default_factory=dict)   # note path -> body, what the members received


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
    impact = data.get("impact", "")
    if isinstance(impact, list):
        impact = "\n".join(f"- {item}" for item in (str(i).strip() for i in impact) if item)
    facts: list[dict[str, Any]] = []
    for item in data.get("facts_from_network") or []:
        if isinstance(item, dict):
            fact, source = str(item.get("fact", "")).strip(), str(item.get("source", "")).strip()
        else:
            fact, source = str(item).strip(), ""
        if fact:
            facts.append({"fact": fact, "source": source})
    judgement = data.get("own_judgement", "")
    if isinstance(judgement, list):
        judgement = "\n".join(f"- {item}" for item in (str(i).strip() for i in judgement) if item)
    return {
        "view": str(data["view"]),
        "risks": str(risks),
        "recommendation": str(data["recommendation"]),
        "applies": bool(applies),
        "impact": str(impact or ""),
        "sources": tuple(facts),
        "judgement": str(judgement or ""),
    }


_SOURCE_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "have", "will", "which", "about",
                     "their", "there", "than", "then", "were", "been", "into", "also", "only", "when", "what"}


def _note_key(path: str) -> str:
    name = path.replace("\\", "/").strip().strip("[]").lower()
    return name[:-3] if name.endswith(".md") else name


def verify_sources(facts: list[dict[str, Any]] | tuple[dict[str, Any], ...], sent: dict[str, str]) -> list[dict[str, Any]]:
    """Python's verdict on every fact a member says it took from the
    knowledge net (AP-1). The cited note must be one the board actually
    sent - by path, file name or title - and some distinctive word of the
    fact must appear in that note. A fact that fails is kept and flagged,
    never dropped: the reader sees what the model claimed and what held."""
    by_key: dict[str, tuple[str, str]] = {}
    for path, body in sent.items():
        key = _note_key(path)
        by_key[key] = (path, body)
        by_key[key.rsplit("/", 1)[-1]] = (path, body)
    verdicts: list[dict[str, Any]] = []
    for item in facts:
        fact, source = str(item.get("fact", "")), str(item.get("source", ""))
        hit = by_key.get(_note_key(source)) or by_key.get(_note_key(source).rsplit("/", 1)[-1])
        if hit is None:
            verdicts.append({"fact": fact, "source": source, "verified": False,
                             "note": "not among the notes sent to this member" if source else "no note named"})
            continue
        path, body = hit
        lowered = body.lower()
        words = [w for w in re.findall(r"[\w][\w.,%-]{3,}", fact.lower()) if w.strip(".,%-") not in _SOURCE_STOPWORDS]
        found = [w for w in words if w in lowered]
        if words and not found:
            verdicts.append({"fact": fact, "source": path, "verified": False,
                             "note": "the note was sent, but none of the fact's words appear in it"})
        else:
            verdicts.append({"fact": fact, "source": path, "verified": True, "note": ""})
    return verdicts


def _kpi_note_bodies(member_data_text: str) -> dict[str, str]:
    """The KPI notes inside one member's data block, by the ``### path``
    headings ``knowledge.kpi_notes`` writes."""
    paths = re.findall(r"(?m)^### (.+\.md)\s*$", member_data_text or "")
    return {path: member_data_text for path in paths}


def _summarise_sources(assessments: list[MemberAssessment]) -> dict[str, Any]:
    network: list[str] = []
    unverified: list[str] = []
    judgement = 0
    for a in assessments:
        for s in a.sources:
            if s.get("verified"):
                if s["source"] not in network:
                    network.append(s["source"])
            else:
                unverified.append(f"{a.member}: \"{s.get('fact', '')}\" -> {s.get('source') or '(no note)'}: {s.get('note', '')}")
        judgement += len([line for line in a.judgement.splitlines() if line.strip()])
    return {"network": network, "unverified": unverified, "judgement_count": judgement}


def _synthesis_prompt(
    assessments: list[MemberAssessment], roles: dict[str, RoleProfile] | None = None
) -> str:
    payload = [
        {
            "member": assessment.member,
            "applies": assessment.applies,
            "view": assessment.view,
            "impact": assessment.impact,
            "risks": assessment.risks,
            "recommendation": assessment.recommendation,
            "facts_from_network": [
                {"fact": s["fact"], "source": s["source"], "verified_by_python": bool(s.get("verified"))}
                for s in assessment.sources
            ],
            "own_judgement": assessment.judgement,
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
    rests = data.get("rests_on_judgement") or []
    if rests:
        lines.append("Rests on judgement (not in the knowledge net):")
        lines.extend(f"  - {item}" for item in rests)
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
    on_assessment: Callable[[MemberAssessment], None] | None = None,
    board: Board | None = None,
    member_data: dict[str, str] | None = None,
    members: tuple[str, ...] | list[str] | None = None,
    sent_notes: dict[str, str] | None = None,
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
    early: dict[str, MemberAssessment] = {}
    early_lock = threading.Lock()

    def _assessment(member: str, parsed: dict[str, Any]) -> MemberAssessment:
        sent = dict(sent_notes or {})
        sent.update(_kpi_note_bodies(member_data.get(member, "")))
        parsed["sources"] = tuple(verify_sources(parsed["sources"], sent))
        return MemberAssessment(member=member, **parsed)

    def _call(member: str, prompt: str) -> AiResult:
        _notify(member, "running")
        try:
            result, calls = _complete_with_retry(provider, prompt)
        except Exception:
            _notify(member, "failed")
            raise
        retries[0] += calls - 1
        parsed = _parse_member_response(result.text)
        if parsed is not None:
            # Decided 9 September 2026: an answer can be read as soon as it
            # is in, while the others still think. It is verified here, once,
            # and handed out; the final list below reuses it. Isolation
            # (FR-3.3a) is untouched - it goes to Alex, never to a member.
            assessment = _assessment(member, parsed)
            with early_lock:
                early[member] = assessment
            if on_assessment is not None:
                try:
                    on_assessment(assessment)
                except Exception:   # a progress display must never take a run down
                    pass
        _notify(member, "done" if parsed is not None else "failed")
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
        if member in early:
            assessments.append(early[member])
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
        assessments.append(_assessment(member, parsed))

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
            sources=_summarise_sources(assessments),
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
        sources=_summarise_sources(assessments),
    ))


def _input_block(topic: str, context: str, options: tuple[str, ...], constraints: tuple[str, ...]) -> list[str]:
    lines = ["## Input (FR-3.1)", "", f"Topic: {topic}"]
    if context:
        lines.append(f"Context: {context}")
    if options:
        lines.append("Options under consideration:")
        lines.extend(f"- {option}" for option in options)
    if constraints:
        lines.append("Hard constraints:")
        lines.extend(f"- {constraint}" for constraint in constraints)
    return lines


def _member_section(member: str, role: RoleProfile | None, kpi_data: str) -> list[str]:
    """One member's material inside the combined prompt: the same profile
    and KPI block the single call gets, under the member's heading."""
    lines = [f"### Member: {member}", ""]
    if role is not None and role.roles:
        lines.append(f"This member is the {role.member} swim lane. It speaks as {role.title} (level {role.level}) "
                     "and answers for every role in the swim lane. Roles by rank:")
        lines.extend(f"- level {r.level}: {r.name}" for r in role.roles)
        lines.append("")
    if role is not None and role.body:
        lines += [role.body, ""]
    if kpi_data:
        lines += ["#### KPI data from the knowledge network", "", kpi_data, ""]
    else:
        lines += ["#### KPI data from the knowledge network", "",
                  "No KPI note for this member is in the knowledge network yet.", ""]
    return lines


def _combined_prompt(
    topic: str, context: str, options: tuple[str, ...], constraints: tuple[str, ...],
    roles: dict[str, RoleProfile], conduct: str, member_data: dict[str, str], project: str,
) -> str:
    lines = [load_prompt("board_combined"), ""]
    if project:
        lines += [f"Project: {project}. This question belongs to this project.", ""]
    if conduct:
        lines += ["## Board member conduct (the same for every member)", "", conduct, ""]
    lines += ["## Members to assess, in this order", ""]
    lines.append(", ".join(roles))
    lines.append("")
    for member, role in roles.items():
        lines += _member_section(member, role, member_data.get(member, ""))
    lines += _input_block(topic, context, options, constraints)
    return "\n".join(lines)


_GENERIC_MIN_TERMS = 1


def _role_terms(role: RoleProfile | None) -> set[str]:
    """Distinctive words from the sections that make a role its own: the
    targets it is judged on, the process tasks that name it, its title."""
    if role is None:
        return set()
    text = role.title + " "
    body = role.body
    for heading in ("## Targets I am judged on", "## Process", "## What I protect when I cannot have everything"):
        start = body.find(heading)
        if start == -1:
            continue
        end = body.find("\n## ", start + len(heading))
        text += body[start:end if end != -1 else None] + " "
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z&-]{4,}", text)}
    return words - _SOURCE_STOPWORDS - {"targets", "judged", "process", "protect", "cannot", "everything",
                                        "member", "network", "knowledge", "values", "baseline", "record",
                                        "tasks", "official", "owner", "process", "writes", "before", "answering",
                                        "question", "touches", "defines", "points", "there", "every", "phase",
                                        "itself", "pages", "affected", "swimlanes", "section", "level"}


def _entry_flags(entry: dict[str, Any], role: RoleProfile | None) -> tuple[str, ...]:
    """Python's check that a combined entry was written from the member's
    own profile: it must name at least one of the role's own terms."""
    terms = _role_terms(role)
    if not terms:
        return ()
    text = " ".join(str(entry.get(k, "")) for k in ("view", "impact", "risks", "recommendation", "own_judgement")).lower()
    hits = [t for t in terms if t in text]
    return () if len(hits) >= _GENERIC_MIN_TERMS else ("generic: names none of this role's measures or tasks",)


def _parse_combined(text: str, members: tuple[str, ...]) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    """The combined answer: entries by member (raw, parsed like a single
    answer), the synthesis object and the follow-up object, if present."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}, None, None
    if not isinstance(data, dict):
        return {}, None, None
    wanted = {m.lower(): m for m in members}
    entries: dict[str, dict[str, Any]] = {}
    for item in data.get("members") or []:
        if not isinstance(item, dict):
            continue
        name = wanted.get(str(item.get("member", "")).strip().lower())
        if name is None or name in entries:
            continue
        parsed = _parse_member_response(json.dumps(item))
        if parsed is not None:
            entries[name] = parsed
    synthesis = data.get("synthesis") if isinstance(data.get("synthesis"), dict) else None
    follow_up = data.get("follow_up") if isinstance(data.get("follow_up"), dict) else None
    return entries, synthesis, follow_up


def run_board_combined(
    config: dict,
    provider: AiProvider | None,
    *,
    topic: str,
    context: str = "",
    options: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    on_member: Callable[[str, str], None] | None = None,
    board: Board | None = None,
    member_data: dict[str, str] | None = None,
    members: tuple[str, ...] | list[str] | None = None,
    sent_notes: dict[str, str] | None = None,
    on_assessment: Callable[[MemberAssessment], None] | None = None,
) -> BoardResult:
    """The combined form (decided 9 September 2026): one call writes every
    chosen member's assessment and the synthesis. Everything the single
    calls receive is in this one prompt; Python checks that every member
    came back, that each entry names its own role's terms, and every
    citation, as in ``run_board``. Cheaper by a factor of the member count;
    the entries influence each other, which the interface says."""
    start = time.monotonic()
    if provider is None or not is_configured(config, TASK_BOARD):
        raise AiNotConfiguredError("AI Board has no model configured - set provider.models.board")
    if board is None:
        board = load_board(config)
    roles = board.profiles
    if members:
        chosen = {str(m).strip().lower() for m in members}
        roles = {name: role for name, role in roles.items() if name.lower() in chosen}
        if not roles:
            raise ValueError("none of the chosen members is on the board")
    names = tuple(roles)
    if member_data is None:
        member_data = kpi_notes(config, names)
    project = active_project(config) or ""
    prompt = _combined_prompt(topic, context, options, constraints, roles, board.conduct, member_data, project)

    def _notify(state: str) -> None:
        if on_member is not None:
            for member in names:
                try:
                    on_member(member, state)
                except Exception:
                    pass

    _notify("running")
    try:
        ai_result, calls = _complete_with_retry(provider, prompt)
    except Exception:
        _notify("failed")
        raise
    entries, synthesis_data, _follow = _parse_combined(ai_result.text, names)
    assessments: list[MemberAssessment] = []
    failed: list[str] = []
    for member in names:
        parsed = entries.get(member)
        if parsed is None:
            failed.append(f"{member}: no entry in the combined answer")
            if on_member is not None:
                on_member(member, "failed")
            continue
        sent = dict(sent_notes or {})
        sent.update(_kpi_note_bodies(member_data.get(member, "")))
        parsed["sources"] = tuple(verify_sources(parsed["sources"], sent))
        parsed["flags"] = _entry_flags(parsed, roles.get(member))
        assessment = MemberAssessment(member=member, **parsed)
        assessments.append(assessment)
        if on_assessment is not None:
            try:
                on_assessment(assessment)
            except Exception:
                pass
        if on_member is not None:
            on_member(member, "done")
    if not entries and not synthesis_data:
        failed = [f"{m}: the combined answer did not parse as JSON with members and synthesis" for m in names]
    if synthesis_data is not None:
        synthesis = _synthesis_text(synthesis_data)
    elif assessments:
        synthesis = "The combined answer carried no synthesis object; the member entries stand on their own."
    else:
        synthesis = "No member produced an assessment, so there is nothing to consolidate."
    result = BoardResult(
        topic=topic, assessments=assessments, synthesis=synthesis, failed_members=failed,
        ai_result=ai_result, llm_calls=calls, synthesis_data=synthesis_data,
        sources=_summarise_sources(assessments), mode="combined",
    )
    log_run(
        "board", audit_folder=_get(config, "runtime.audit_folder"), pc_name=_get(config, "storage.pc_name", ""),
        duration_seconds=time.monotonic() - start,
        counts={"assessments": len(assessments), "failed_members": len(failed), "llm_calls": calls,
                "over_token_limit": 1 if ai_result.over_token_limit else 0, "combined": 1},
        provider=ai_result.provider, model=ai_result.model, tokens=ai_result.total_tokens,
    )
    return result


def _combined_follow_up_prompt(conversation: BoardConversation, chosen: list[str], question: str) -> str:
    inputs = conversation.inputs
    roles = {m: (conversation.roles or {}).get(m) for m in chosen}
    lines = [load_prompt("board_combined"), ""]
    if conversation.project:
        lines += [f"Project: {conversation.project}.", ""]
    if conversation.conduct:
        lines += ["## Board member conduct (the same for every member)", "", conversation.conduct, ""]
    lines += ["## Members to ask again, in this order", "", ", ".join(chosen), ""]
    for member, role in roles.items():
        lines += _member_section(member, role, conversation.member_data.get(member, ""))
        earlier = next((a for a in conversation.result.assessments if a.member == member), None)
        lines += ["#### Earlier assessment of this member", ""]
        lines.append(json.dumps({"applies": earlier.applies, "view": earlier.view, "impact": earlier.impact,
                                 "risks": earlier.risks, "recommendation": earlier.recommendation}, indent=2)
                     if earlier else "(none in the first round)")
        lines.append("")
    lines += _input_block(str(inputs.get("topic", conversation.result.topic)), str(inputs.get("context", "")),
                          tuple(inputs.get("options", ())), tuple(inputs.get("constraints", ())))
    lines += ["", "## Board recommendation so far", "", conversation.result.synthesis, "", "## Conversation so far"]
    if conversation.turns:
        for q, a in conversation.turns:
            lines += [f"Q: {q}", f"A: {a}"]
    else:
        lines.append("(none yet)")
    lines += ["", "## New question", "", question]
    return "\n".join(lines)


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
            {"member": a.member, "applies": a.applies, "view": a.view, "impact": a.impact, "risks": a.risks,
             "recommendation": a.recommendation,
             "facts_from_network": [{"fact": s["fact"], "source": s["source"], "verified_by_python": bool(s.get("verified"))}
                                    for s in a.sources],
             "own_judgement": a.judgement}
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
        lines.append(json.dumps({"applies": earlier.applies, "view": earlier.view, "impact": earlier.impact,
                                 "risks": earlier.risks, "recommendation": earlier.recommendation}, indent=2))
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
    members: tuple[str, ...] | list[str] = (), mode: str = "individual",
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
    if chosen and mode == "combined":
        # One call: every chosen member's answer and the board's answer together.
        ai_result, calls = _complete_with_retry(provider, _combined_follow_up_prompt(conversation, chosen, question))
        entries, _synthesis, data = _parse_combined(ai_result.text, tuple(chosen))
        for member in chosen:
            parsed = entries.get(member)
            if parsed is None:
                failed.append(f"{member}: no entry in the combined answer")
                continue
            sent = dict(conversation.sent_notes)
            sent.update(_kpi_note_bodies(conversation.member_data.get(member, "")))
            parsed["sources"] = tuple(verify_sources(parsed["sources"], sent))
            parsed["flags"] = _entry_flags(parsed, (conversation.roles or {}).get(member))
            member_answers.append(MemberAssessment(member=member, **parsed))
        if isinstance(data, dict) and "answer" in data:
            answer = _follow_up_text(data)
        else:
            data = None
            answer = ai_result.text
        conversation.turns.append((question, answer))
        conversation.llm_calls += calls
        return FollowUp(question=question, answer=answer, data=data, assessments=member_answers,
                        failed_members=failed, llm_calls=calls)
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
            sent = dict(conversation.sent_notes)
            sent.update(_kpi_note_bodies(conversation.member_data.get(member, "")))
            parsed["sources"] = tuple(verify_sources(parsed["sources"], sent))
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
        if assessment.sources:
            lines.append(f"{'From the net':<16}" + "; ".join(
                f"{s['fact']} [{s['source']}{'' if s.get('verified') else ', NOT VERIFIED: ' + s.get('note', '')}]"
                for s in assessment.sources))
        if assessment.judgement:
            lines.append(f"{'Own judgement':<16}{assessment.judgement}")
        for flag in assessment.flags:
            lines.append(f"{'Check':<16}{flag}")
        lines.append("")

    if result.sources:
        lines.append("Sources")
        lines.append("-------")
        lines.append("Knowledge net notes used (verified): " + (", ".join(result.sources.get("network", [])) or "none"))
        lines.append(f"Statements from the members' own judgement: {result.sources.get('judgement_count', 0)}")
        for item in result.sources.get("unverified", []):
            lines.append(f"  NOT VERIFIED: {item}")
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
