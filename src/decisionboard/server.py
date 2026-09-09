"""The local HTTP server behind the browser interface (spec section 10).

Standard library only: ``http.server`` bound to ``127.0.0.1``, one HTML
page with its script and stylesheet served from ``web/``, and a small JSON
API the page polls. No framework, no build step, no dependency - the
decision of 8 September 2026 allows dependencies that run on any company
machine, and none is needed for this.

Every model call runs in a background thread and the page polls the
session's state once a second, so the member avatars fill in as each
member returns. The board itself is ``board.run_board`` - the same function
the CLI calls; this module adds the clarifier in front of it, the
knowledge block from the vault, the follow-up loop, and the confirmed
memory write on close.

Sessions live in memory for the life of the server process. Nothing is
written to disk except the audit entry ``run_board`` already writes and
the one vault note Alex confirms on closing a topic.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agent.opencode_client import opencode_config_problem
from . import clarify as clarify_mod
from . import knowledge as knowledge_mod
from . import memory_writer
from . import roles as roles_mod
from .agent.provider import AiNotConfiguredError, AiProvider, AiResult, build_provider
from .board import BoardConversation, ask_follow_up_full, run_board, run_board_combined

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8765

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _get(config: dict, dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node not in ("", None) else default


def _set(config: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _call_label(prompt: str, phase: str) -> tuple[str, str]:
    """What a model call was for, read from the markers each prompt builder
    puts in (the same markers the tests route on): a step name and, for a
    member call, the member. Deterministic, no model involved."""
    if "## Question from Alex" in prompt:
        return ("clarifier", "")
    if "## Vault outline" in prompt:
        return ("memory proposal", "")
    if "## Members to ask again" in prompt:
        return ("follow-up, combined", "")
    if "## Members to assess" in prompt:
        return ("board, combined", "")
    if "## Your earlier assessment" in prompt:
        return ("follow-up, member", prompt.split("Member: ", 1)[1].splitlines()[0] if "Member: " in prompt else "")
    if "## New question" in prompt:
        return ("follow-up, board", "")
    if "## Assessments" in prompt:
        return ("synthesis", "")
    if "## Member (FR-3.3a)" in prompt or "Member: " in prompt:
        return ("member", prompt.split("Member: ", 1)[1].splitlines()[0] if "Member: " in prompt else "")
    return (phase or "call", "")


class RecordingProvider(AiProvider):
    """Wraps the real provider for one session and writes every call's
    tokens and duration into the session's statistics (decided
    9 September 2026: how much did this question cost, step by step)."""

    def __init__(self, inner: AiProvider, session: "Session") -> None:
        self._inner = inner
        self._session = session

    def complete(self, task: str, prompt: str) -> AiResult:
        started = time.monotonic()
        step, member = _call_label(prompt, self._session.phase)
        try:
            result = self._inner.complete(task, prompt)
        except Exception as exc:
            self._session.record_call(step, member, None, None, time.monotonic() - started, error=str(exc)[:120])
            raise
        duration = result.duration_seconds if result.duration_seconds else time.monotonic() - started
        self._session.record_call(step, member, result.input_tokens, result.output_tokens, duration)
        return result


class Session:
    """One topic, from the typed question to the closed topic."""

    def __init__(self, question: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.lock = threading.Lock()
        self.question = question
        self.started = time.time()
        self.calls: list[dict[str, Any]] = []          # every model call: step, member, tokens, seconds
        self.marks: list[dict[str, Any]] = [{"phase": "started", "at": self.started}]   # phase changes, for wall time
        self.phase = "clarifying"
        self.error: str | None = None
        self.busy = False
        self.knowledge: dict[str, Any] = {"vault_path": None, "selected": 0, "total": 0, "tokens": 0,
                                          "truncated": False, "notes": []}
        self.knowledge_text = ""
        self.sent_notes: dict[str, str] = {}              # note path -> body, what the members receive
        self.roles: dict[str, Any] = {"members": [], "count": 0, "source": "", "folder": None, "files": []}
        self.member_meta: list[dict[str, str]] = []
        self.clarification: clarify_mod.Clarification | None = None
        self.answers: list[str] = []
        self.rounds: list[dict[str, list[str]]] = []      # every clarification round: questions and answers
        self.inputs: dict[str, Any] = {}
        self.selected_members: list[str] = []             # the members Alex chose to ask
        self.members: dict[str, str] = {}
        self.partial: dict[str, dict[str, Any]] = {}      # answers already in while the others think
        self.mode = "individual"                          # "individual" or "combined" (decided 9 September 2026)
        self.result: dict[str, Any] | None = None
        self.conversation: BoardConversation | None = None
        self.turns: list[dict[str, str]] = []
        self.llm_calls = 0
        self.proposal: memory_writer.MemoryProposal | None = None
        self.written_path: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            clarification = None
            if self.clarification is not None:
                clarification = {
                    "topic": self.clarification.topic,
                    "context": self.clarification.context,
                    "options": list(self.clarification.options),
                    "constraints": list(self.clarification.constraints),
                    "questions": list(self.clarification.questions),
                    "parse_error": self.clarification.parse_error,
                }
            proposal = None
            if self.proposal is not None:
                proposal = {
                    "path": self.proposal.path,
                    "title": self.proposal.title,
                    "tags": list(self.proposal.tags),
                    "body": self.proposal.body,
                    "mode": self.proposal.mode,
                    "parse_error": self.proposal.parse_error,
                    "preview": memory_writer.preview(Path(self.knowledge["vault_path"] or "."), self.proposal),
                }
            return deepcopy({
                "id": self.id,
                "question": self.question,
                "phase": self.phase,
                "busy": self.busy,
                "error": self.error,
                "knowledge": self.knowledge,
                "roles": self.roles,
                "member_meta": self.member_meta,
                "clarification": clarification,
                "answers": self.answers,
                "rounds": self.rounds,
                "max_rounds": clarify_mod.MAX_ROUNDS,
                "inputs": self.inputs,
                "selected_members": self.selected_members,
                "members": self.members,
                "partial": self.partial,
                "mode": self.mode,
                "stats": {"calls": self.calls, "marks": self.marks, "started": self.started},
                "result": self.result,
                "turns": self.turns,
                "llm_calls": self.llm_calls,
                "proposal": proposal,
                "written_path": self.written_path,
            })

    def fail(self, message: str) -> None:
        with self.lock:
            self.phase = "error"
            self.error = message
            self.busy = False
            self.marks.append({"phase": "error", "at": time.time()})

    def mark(self, phase: str) -> None:
        """Under the caller's lock or not - appending is atomic enough."""
        self.marks.append({"phase": phase, "at": time.time()})

    def record_call(self, step: str, member: str, input_tokens: int | None, output_tokens: int | None,
                    seconds: float, error: str | None = None) -> None:
        with self.lock:
            self.calls.append({
                "n": len(self.calls) + 1, "step": step, "member": member, "phase": self.phase,
                "input_tokens": input_tokens, "output_tokens": output_tokens, "seconds": round(seconds, 1),
                "error": error, "at": time.time(),
            })


class BoardServer:
    """State and behaviour behind the API; the HTTP handler only routes."""

    def __init__(self, config: dict, config_path: Path | None, *, provider: AiProvider | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self._provider_override = provider
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    # -- provider and config ------------------------------------------------

    def provider(self) -> AiProvider:
        if self._provider_override is not None:
            return self._provider_override
        provider = build_provider(self.config)
        if provider is None:
            raise AiNotConfiguredError(
                "No model configured. Open Options and set the model string (provider.models.board)."
            )
        return provider

    def config_view(self) -> dict[str, Any]:
        vault_path = _get(self.config, "knowledge.vault_path")
        status: dict[str, Any] = {"configured": bool(vault_path), "ok": False, "notes": 0, "error": None}
        if vault_path:
            try:
                vault_dir = Path(str(vault_path)).expanduser()
                status["notes"] = len(knowledge_mod.load_vault(
                    vault_dir, skip_subfolders=knowledge_mod._roles_inside(self.config, vault_dir)))
                status["ok"] = True
            except knowledge_mod.KnowledgeUnavailable as exc:
                status["error"] = str(exc)
        return {
            "config_path": str(self.config_path) if self.config_path else None,
            "vault_path": vault_path or "",
            "project": knowledge_mod.active_project(self.config) or "",
            "projects": knowledge_mod.list_projects(self.config),
            "token_budget": _get(self.config, "knowledge.token_budget", knowledge_mod.DEFAULT_TOKEN_BUDGET),
            "model": _get(self.config, "provider.models.board", "") or "",
            "token_limit": _get(self.config, "provider.token_limits.board"),
            "audit_folder": _get(self.config, "runtime.audit_folder", "") or "",
            "auto_approve": bool(_get(self.config, "provider.opencode.auto_approve", True)),
            "opencode_config": _get(self.config, "provider.opencode.config_file", "") or "",
            "theme": _get(self.config, "ui.theme", "system") or "system",
            "knowledge_status": status,
            "roles_folder": _get(self.config, "knowledge.roles_folder", "") or "",
            "roles_status": self._roles_status(),
        }

    def _roles_status(self) -> dict[str, Any]:
        folder, origin = roles_mod.resolve_folder(self.config)
        try:
            board = roles_mod.load_board(self.config)
        except roles_mod.RolesUnavailable as exc:
            return {"error": str(exc), "members": [], "count": 0, "source": origin,
                    "folder": str(folder) if folder else None, "files": [], "member_meta": [], "skipped": []}
        info = roles_mod.summary(board)
        info["error"] = None
        info["member_meta"] = roles_mod.member_meta(board.profiles)
        return info

    def _install_target(self) -> Path:
        configured = _get(self.config, "knowledge.roles_folder")
        if configured:
            return Path(str(configured)).expanduser()
        vault = _get(self.config, "knowledge.vault_path")
        if vault:
            vault_dir = Path(str(vault)).expanduser()
            return roles_mod.detect_folder(vault_dir) or (vault_dir / roles_mod.DEFAULT_SUBFOLDER)
        raise ApiError(400, "Choose a roles folder (or a knowledge source) first.")

    def install_roles(self) -> dict[str, Any]:
        folder = self._install_target()
        try:
            written = roles_mod.install_support_files(folder)
        except OSError as exc:
            raise ApiError(500, f"Could not write to the roles folder: {exc}")
        status = self._roles_status()
        status["written"] = [str(path) for path in written]
        status["target"] = str(folder)
        return status

    def update_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "vault_path": ("knowledge.vault_path", str),
            "project": ("knowledge.project", str),
            "token_budget": ("knowledge.token_budget", int),
            "model": ("provider.models.board", str),
            "token_limit": ("provider.token_limits.board", lambda v: None if v in ("", None) else int(v)),
            "audit_folder": ("runtime.audit_folder", str),
            "auto_approve": ("provider.opencode.auto_approve", bool),
            "opencode_config": ("provider.opencode.config_file", str),
            "roles_folder": ("knowledge.roles_folder", str),
            "theme": ("ui.theme", str),
        }
        for key, (dotted, cast) in mapping.items():
            if key in changes:
                try:
                    value = cast(changes[key]) if changes[key] is not None else None
                except (TypeError, ValueError):
                    raise ApiError(400, f"{key}: not a valid value")
                if isinstance(value, str):
                    value = value.strip()
                if key == "opencode_config" and value:
                    problem = opencode_config_problem(value)
                    if problem:
                        raise ApiError(400, f"OpenCode configuration file {problem}")
                _set(self.config, dotted, value)
        if self.config_path is not None:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.config_view()

    # -- sessions -------------------------------------------------------------

    def start_session(self, question: str) -> Session:
        question = (question or "").strip()
        if not question:
            raise ApiError(400, "Type a question first.")
        session = Session(question)
        with self.lock:
            self.sessions[session.id] = session
        self._spawn(session, self._clarify, session)
        return session

    @staticmethod
    def _spawn(session: Session, target, *args) -> None:
        """A background step whose crash must show up as the session's error,
        never as a page polling 'clarifying' forever."""
        def run() -> None:
            try:
                target(*args)
            except Exception as exc:   # noqa: BLE001 - the whole point is to surface anything
                session.fail(f"internal error: {exc!r}")
        threading.Thread(target=run, daemon=True).start()

    def get_session(self, session_id: str) -> Session:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise ApiError(404, "Unknown session - start a new topic.")
        return session

    def _clarify(self, session: Session) -> None:
        try:
            selection = knowledge_mod.gather(self.config, session.question)
        except knowledge_mod.KnowledgeUnavailable as exc:
            session.fail(f"{exc}. Check the knowledge source in Options.")
            return
        try:
            board = roles_mod.load_board(self.config)
        except roles_mod.RolesUnavailable as exc:
            session.fail(f"{exc}. Choose the roles folder in Options.")
            return
        try:
            kpi = knowledge_mod.kpi_notes(self.config, list(board.profiles))
        except knowledge_mod.KnowledgeUnavailable:
            kpi = {}
        with session.lock:
            session.roles = roles_mod.summary(board)
            session.roles["kpi_members"] = sorted(kpi)
            session.member_meta = roles_mod.member_meta(board.profiles)
            session.members = {member: "pending" for member in board.profiles}
            session.knowledge = {
                "vault_path": str(selection.vault_path) if selection.vault_path else None,
                "project": selection.project or "",
                "selected": len(selection.notes),
                "total": selection.total_notes,
                "tokens": selection.tokens,
                "truncated": selection.truncated,
                "notes": selection.relative_paths,
            }
            session.knowledge_text = selection.text
            session.sent_notes = {note.relative: note.body for note in selection.notes}
        try:
            provider = RecordingProvider(self.provider(), session)
            clarification = clarify_mod.clarify(provider, session.question, session.knowledge_text)
        except Exception as exc:   # any provider failure ends the session visibly
            session.fail(str(exc))
            return
        with session.lock:
            session.clarification = clarification
            session.llm_calls += 1
            session.phase = "questions"
            session.mark("questions")

    def answer(self, session: Session, answers: list[str], *, final: bool = False) -> None:
        """Alex's answers to the current round. Unless ``final`` (the "ask
        the board now" button) or the round limit is reached, the clarifier
        looks again and either asks more or declares the question clear
        (decided 9 September 2026: clarification loops until clear)."""
        with session.lock:
            # "confirm" is accepted too: the page's Back button returns to
            # the last round's questions and re-submits them.
            if session.phase not in ("questions", "confirm") or session.clarification is None:
                raise ApiError(409, "The board is not waiting for answers right now.")
            session.answers = [str(item) for item in answers]
            current = {"questions": list(session.clarification.questions), "answers": session.answers}
            if session.phase == "confirm" and session.rounds:
                session.rounds[-1] = current
            else:
                session.rounds.append(current)
            if final or len(session.rounds) >= clarify_mod.MAX_ROUNDS or session.phase == "confirm":
                self._to_confirm(session)
                return
            session.phase = "clarifying"
        self._spawn(session, self._clarify_more, session)

    def _to_confirm(self, session: Session) -> None:
        """Under the session lock: the FR-3.1 input from every round."""
        clarification = session.clarification
        rounds = [(r["questions"], r["answers"]) for r in session.rounds]
        session.inputs = {
            "topic": clarification.topic,
            "context": clarify_mod.merge_rounds(clarification.context, rounds),
            "options": list(clarification.options),
            "constraints": list(clarification.constraints),
        }
        session.phase = "confirm"
        session.mark("confirm")

    def _clarify_more(self, session: Session) -> None:
        rounds = [(r["questions"], r["answers"]) for r in session.rounds]
        try:
            clarification = clarify_mod.clarify(RecordingProvider(self.provider(), session), session.question,
                                                session.knowledge_text, rounds)
        except Exception as exc:
            session.fail(str(exc))
            return
        with session.lock:
            session.llm_calls += 1
            # Keep what the earlier round extracted where the new one is thin.
            if not clarification.context and session.clarification is not None:
                clarification.context = session.clarification.context
            if not clarification.options and session.clarification is not None:
                clarification.options = list(session.clarification.options)
            if not clarification.constraints and session.clarification is not None:
                clarification.constraints = list(session.clarification.constraints)
            session.clarification = clarification
            session.answers = []
            if clarification.questions:
                session.phase = "questions"
                session.mark("questions")
            else:
                self._to_confirm(session)

    def run(self, session: Session, inputs: dict[str, Any]) -> None:
        with session.lock:
            if session.phase != "confirm":
                raise ApiError(409, "The board is not ready to run.")
            topic = str(inputs.get("topic") or session.inputs.get("topic") or "").strip()
            if not topic:
                raise ApiError(400, "The topic must not be empty.")
            session.inputs = {
                "topic": topic,
                "context": str(inputs.get("context") or "").strip(),
                "options": [str(o).strip() for o in inputs.get("options") or [] if str(o).strip()],
                "constraints": [str(c).strip() for c in inputs.get("constraints") or [] if str(c).strip()],
            }
            session.selected_members = self._chosen_members(session, inputs.get("members"))
            session.mode = "combined" if str(inputs.get("mode") or "").lower() == "combined" else "individual"
            session.phase = "running"
            session.mark("running")
            session.members = {member: "pending" for member in session.selected_members}
        self._spawn(session, self._run_board, session)

    @staticmethod
    def _chosen_members(session: Session, wanted: Any) -> list[str]:
        """The members Alex ticked, in board order; all of them when the
        request names none. At least one is required."""
        on_board = list(session.roles.get("members") or [])
        if not on_board:
            on_board = list(session.members)
        if not isinstance(wanted, list) or not wanted:
            return on_board
        chosen = {str(m).strip().lower() for m in wanted}
        selected = [m for m in on_board if m.lower() in chosen]
        if not selected:
            raise ApiError(400, "Choose at least one member to ask.")
        return selected

    def _run_board(self, session: Session) -> None:
        def on_member(member: str, state: str) -> None:
            with session.lock:
                session.members[member] = state
                if all(s in ("done", "failed") for s in session.members.values()) and session.phase != "synthesising":
                    session.phase = "synthesising"
                    session.mark("synthesising")

        def on_assessment(assessment) -> None:
            with session.lock:
                session.partial[assessment.member] = asdict(assessment)

        inputs = session.inputs
        context = inputs["context"]
        if session.knowledge_text:
            context = f"{context}\n\n{session.knowledge_text}" if context else session.knowledge_text
        try:
            # Section 3.4: read fresh on every run, never cached - an edit in
            # Obsidian is in force on the next question.
            board = roles_mod.load_board(self.config)
            selected = [m for m in board.profiles if m in session.selected_members] or list(board.profiles)
            profiles = {m: board.profiles[m] for m in selected}
            try:
                member_data = knowledge_mod.kpi_notes(self.config, selected)
            except knowledge_mod.KnowledgeUnavailable:
                member_data = {}
            with session.lock:
                session.roles = roles_mod.summary(board)
                session.member_meta = roles_mod.member_meta(board.profiles)
                session.selected_members = selected
                session.members = {member: "pending" for member in selected}
                session.partial = {}
            runner = run_board_combined if session.mode == "combined" else run_board
            result = runner(
                self.config, RecordingProvider(self.provider(), session),
                topic=inputs["topic"], context=context,
                options=tuple(inputs["options"]), constraints=tuple(inputs["constraints"]),
                on_member=on_member, board=board, member_data=member_data, members=selected,
                sent_notes=session.sent_notes, on_assessment=on_assessment,
            )
        except Exception as exc:
            session.fail(str(exc))
            return
        with session.lock:
            session.conversation = BoardConversation(
                result=result, turns=[], roles=profiles,
                inputs={"topic": inputs["topic"], "context": context,
                        "options": list(inputs["options"]), "constraints": list(inputs["constraints"])},
                conduct=board.conduct, member_data=member_data,
                project=knowledge_mod.active_project(self.config) or "",
                sent_notes=session.sent_notes,
            )
            session.result = {
                "topic": result.topic,
                "assessments": [asdict(a) for a in result.assessments],
                "synthesis": result.synthesis,
                "synthesis_data": result.synthesis_data,
                "failed_members": list(result.failed_members),
                "llm_calls": result.llm_calls,
                "sources": result.sources,
                "mode": result.mode,
            }
            session.llm_calls += result.llm_calls
            session.phase = "result"
            session.mark("result")

    def follow_up(self, session: Session, question: str, members: Any = None, mode: Any = None) -> None:
        """A follow-up. ``members`` names the members to ask again (decided
        9 September 2026); empty or missing means the one-call form over
        the original assessments."""
        question = (question or "").strip()
        with session.lock:
            if session.phase != "result" or session.conversation is None:
                raise ApiError(409, "There is no open board result to ask about.")
            if session.busy:
                raise ApiError(409, "The board is still answering the previous question.")
            if not question:
                raise ApiError(400, "Type a question first.")
            chosen: list[str] = []
            if isinstance(members, list) and members:
                wanted = {str(m).strip().lower() for m in members}
                chosen = [m for m in session.members if m.lower() in wanted]
            follow_mode = "combined" if str(mode or "").lower() == "combined" else "individual"
            session.busy = True
            session.mark("follow-up")
            session.turns.append({"question": question, "answer": "", "pending": True, "members": chosen,
                                  "mode": follow_mode, "data": None, "assessments": [], "failed_members": []})
        self._spawn(session, self._follow_up, session, question, chosen, follow_mode)

    def _follow_up(self, session: Session, question: str, chosen: list[str], follow_mode: str = "individual") -> None:
        try:
            turn = ask_follow_up_full(self.config, RecordingProvider(self.provider(), session), session.conversation,
                                      question, chosen, follow_mode)
        except Exception as exc:
            with session.lock:
                session.turns[-1] = {"question": question, "answer": f"Could not answer: {exc}", "pending": False,
                                     "error": True, "members": chosen, "mode": follow_mode, "data": None,
                                     "assessments": [], "failed_members": []}
                session.busy = False
            return
        with session.lock:
            session.turns[-1] = {
                "question": question, "answer": turn.answer, "pending": False, "members": chosen, "mode": follow_mode,
                "data": turn.data, "assessments": [asdict(a) for a in turn.assessments],
                "failed_members": list(turn.failed_members),
            }
            session.llm_calls += turn.llm_calls
            session.busy = False
            session.mark("follow-up answered")

    def back(self, session: Session) -> None:
        """After an error (or from the confirm screen): return to the last
        step Alex can edit, with everything he typed still there (decided
        9 September 2026: nothing typed is lost to an error). The confirm
        screen when the input was already assembled, else the last round of
        questions with its answers; with nothing to go back to, 409 and the
        page starts over with the question kept."""
        with session.lock:
            if session.busy:
                raise ApiError(409, "The board is still working.")
            if session.phase in ("result", "proposal", "proposing", "written", "closed"):
                raise ApiError(409, "This topic has a result; ask back or close it.")
            if session.phase == "error" and session.inputs and session.clarification is not None:
                session.phase = "confirm"
                session.error = None
                session.members = {member: "pending" for member in session.selected_members}
                return
            if session.phase in ("error", "confirm") and session.rounds and session.clarification is not None:
                last = session.rounds.pop()
                # The clarifier may have replaced the questions before failing;
                # the questions Alex answered are the ones to show again.
                session.clarification.questions = list(last["questions"])
                session.answers = list(last["answers"])
                session.phase = "questions"
                session.error = None
                return
            raise ApiError(409, "Nothing to go back to - start over; your question is kept.")

    def close(self, session: Session, remember: bool) -> None:
        with session.lock:
            if session.phase != "result":
                raise ApiError(409, "There is no open topic to close.")
            if session.busy:
                raise ApiError(409, "Wait for the board's answer before closing.")
            if not remember:
                session.phase = "closed"
                return
            if not session.knowledge.get("vault_path"):
                raise ApiError(400, "No knowledge source is configured - set the vault folder in Options first.")
            session.phase = "proposing"
            session.busy = True
        self._spawn(session, self._propose, session)

    def _propose(self, session: Session) -> None:
        try:
            proposal = memory_writer.propose(
                RecordingProvider(self.provider(), session), Path(session.knowledge["vault_path"]),
                topic=session.inputs["topic"], inputs=session.inputs,
                synthesis=(session.result or {}).get("synthesis_data") or (session.result or {}).get("synthesis", ""),
                turns=[(t["question"], t["answer"]) for t in session.turns if not t.get("pending")],
            )
        except Exception as exc:
            with session.lock:
                session.phase = "result"
                session.busy = False
                session.error = f"Could not propose a memory entry: {exc}"
            return
        with session.lock:
            session.proposal = proposal
            session.llm_calls += 1
            session.phase = "proposal"
            session.busy = False
            session.error = None

    def write_memory(self, session: Session, edited: dict[str, Any]) -> Path:
        with session.lock:
            if session.phase != "proposal" or session.proposal is None:
                raise ApiError(409, "There is no memory proposal to write.")
            vault = Path(session.knowledge["vault_path"])
            proposal = session.proposal
            proposal.path = str(edited.get("path") or proposal.path)
            proposal.title = str(edited.get("title") or proposal.title).strip() or session.inputs["topic"]
            if "tags" in edited:
                proposal.tags = [str(t).strip().lstrip("#") for t in edited["tags"] if str(t).strip()]
            if "body" in edited:
                proposal.body = str(edited["body"])
            if not proposal.body.strip():
                raise ApiError(400, "The note body must not be empty.")
            try:
                target = memory_writer.write_note(vault, proposal)
            except OSError as exc:
                raise ApiError(500, f"Could not write the note: {exc}")
            session.written_path = str(target)
            session.phase = "written"
            return target

    def discard_memory(self, session: Session) -> None:
        with session.lock:
            if session.phase != "proposal":
                raise ApiError(409, "There is no memory proposal to discard.")
            session.phase = "closed"


def pick_folder(initial: str = "", *, kind: str = "folder", title: str = "") -> str | None:
    """Opens the native folder (or, with ``kind="file"``, file) dialog in a
    separate Python process (tkinter is not safe to drive from a server
    thread) and returns the chosen path, or ``None`` if the dialog was
    cancelled or tkinter is unavailable."""
    script = (
        "import sys\n"
        "try:\n"
        "    import tkinter as tk\n"
        "    from tkinter import filedialog\n"
        "except Exception:\n"
        "    sys.exit(3)\n"
        "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
        "if sys.argv[2] == 'file':\n"
        "    path = filedialog.askopenfilename(title='Choose the OpenCode configuration (opencode.json)', "
        "initialdir=sys.argv[1] or None, filetypes=[('JSON', '*.json'), ('All files', '*.*')])\n"
        "else:\n"
        "    path = filedialog.askdirectory(title=sys.argv[3] or 'Choose a folder', "
        "initialdir=sys.argv[1] or None, mustexist=True)\n"
        "root.destroy()\n"
        "sys.stdout.write(path or '')\n"
    )
    if initial and kind == "file":
        initial = str(Path(initial).expanduser().parent)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, initial, kind,
             title or "Choose the knowledge source (Obsidian vault)"],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    path = completed.stdout.strip()
    return path or None


def make_handler(server: BoardServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DecisionBoard/1.0"

        def log_message(self, format: str, *args: Any) -> None:   # noqa: A002 - stdlib signature
            return   # quiet by default; errors surface as JSON responses

        # -- helpers --------------------------------------------------------

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(400, "Request body is not valid JSON.")
            return data if isinstance(data, dict) else {}

        def _static(self, name: str) -> None:
            path = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in path.parents or not path.is_file():
                self._json(404, {"error": "not found"})
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        # -- routing --------------------------------------------------------

        def do_GET(self) -> None:   # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    self._static("index.html")
                elif path.startswith("/static/"):
                    self._static(path[len("/static/"):])
                elif path == "/api/config":
                    self._json(200, server.config_view())
                elif path.startswith("/api/sessions/"):
                    session = server.get_session(path.split("/")[3])
                    self._json(200, session.snapshot())
                else:
                    self._json(404, {"error": "not found"})
            except ApiError as exc:
                self._json(exc.status, {"error": exc.message})

        def do_POST(self) -> None:   # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
                if path == "/api/config":
                    self._json(200, server.update_config(body))
                elif path == "/api/pick-folder":
                    chosen = pick_folder(str(body.get("initial") or ""), title=str(body.get("title") or ""))
                    self._json(200, {"path": chosen})
                elif path == "/api/roles/install":
                    self._json(200, server.install_roles())
                elif path == "/api/pick-file":
                    chosen = pick_folder(str(body.get("initial") or ""), kind="file")
                    self._json(200, {"path": chosen})
                elif path == "/api/sessions":
                    session = server.start_session(str(body.get("question") or ""))
                    self._json(201, session.snapshot())
                elif path.startswith("/api/sessions/"):
                    parts = path.split("/")
                    session = server.get_session(parts[3])
                    action = parts[4] if len(parts) > 4 else ""
                    if action == "answers":
                        server.answer(session, list(body.get("answers") or []), final=bool(body.get("final")))
                    elif action == "run":
                        server.run(session, body)
                    elif action == "follow-up":
                        server.follow_up(session, str(body.get("question") or ""), body.get("members"), body.get("mode"))
                    elif action == "back":
                        server.back(session)
                    elif action == "close":
                        server.close(session, bool(body.get("remember")))
                    elif action == "memory":
                        server.write_memory(session, body)
                    elif action == "discard-memory":
                        server.discard_memory(session)
                    else:
                        raise ApiError(404, "unknown action")
                    self._json(200, session.snapshot())
                else:
                    self._json(404, {"error": "not found"})
            except ApiError as exc:
                self._json(exc.status, {"error": exc.message})

    return Handler


def create_http_server(
    config: dict, config_path: Path | None, *, port: int = DEFAULT_PORT, provider: AiProvider | None = None,
) -> tuple[ThreadingHTTPServer, BoardServer]:
    board_server = BoardServer(config, config_path, provider=provider)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(board_server))
    httpd.daemon_threads = True
    return httpd, board_server


def serve(config: dict, config_path: Path | None, *, port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    """Runs the server until Ctrl+C. Local only: it binds to 127.0.0.1 and
    nothing else (owner decision, 8 September 2026)."""
    try:
        httpd, _ = create_http_server(config, config_path, port=port)
    except OSError as exc:
        print(f"Cannot listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Decision Board is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
