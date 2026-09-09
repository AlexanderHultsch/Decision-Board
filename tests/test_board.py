#!/usr/bin/env python3
"""Tests for TR-4 AI Board (spec chapter 9, FR-3.1..FR-3.6): the six isolated
member calls, the synthesis call over what they produced, and the plain-text
rendering - the model itself mocked at the ``AiProvider`` boundary, the same
pattern as ``tests/test_briefing.py``.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from decisionboard.agent.provider import AiNotConfiguredError, AiProvider, AiResult  # noqa: E402
from decisionboard import board  # noqa: E402
from _roles_fixture import CLASSIC, make_roles  # noqa: E402

# The board is whoever has a filled role profile in the roles folder
# (section 3.4); the repository ships none, so the tests write six.
import tempfile as _tempfile  # noqa: E402
_ROLES_DIR = make_roles(Path(_tempfile.mkdtemp()) / "roles")
MEMBERS = CLASSIC


def _member_response(view: str = "a view", risks="a risk", recommendation: str = "a recommendation") -> str:
    return json.dumps({"view": view, "risks": risks, "recommendation": recommendation})


SYNTHESIS_RESPONSE = json.dumps({
    "overall_recommendation": "Go with option B",
    "decisive_criterion": "Cost",
    "counter_arguments": ["Option A is faster"],
    "what_would_change_it": "A firm supplier quote",
    "disagreements": ["Finance and Manufacturing disagree on ramp-up cost"],
})


class FakeProvider(AiProvider):
    """A stand-in for a real model: returns queued response texts in call
    order, recording every ``(task, prompt)`` pair it was given - the same
    pattern as ``tests/test_briefing.py``'s ``FakeProvider``, extended with a
    queue since one board run makes several calls that each need their own
    canned response."""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list[tuple[str, str]] = []

    def complete(self, task: str, prompt: str) -> AiResult:
        self.calls.append((task, prompt))
        text = self.texts.pop(0) if self.texts else "{}"
        return AiResult(
            text=text, provider="fake", model="fake-model",
            input_tokens=10, output_tokens=5, duration_seconds=0.01,
        )


class TimedFakeProvider(AiProvider):
    """A ``FakeProvider`` variant for the concurrency tests: replies from a
    per-member sleep table (keyed by the ``Member: <name>`` line each member
    prompt carries) and records, for every call, which member it was for,
    the full prompt text, and start/end timestamps - enough to assert on
    both ordering and overlap. Calls are appended under a lock since
    several threads call ``complete`` at once."""

    def __init__(self, sleep_by_member: dict[str, float], response_by_member: dict[str, str] | None = None):
        self.sleep_by_member = sleep_by_member
        self.response_by_member = response_by_member or {}
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    @staticmethod
    def _member_from_prompt(prompt: str) -> str:
        for line in prompt.splitlines():
            if line.startswith("Member: "):
                return line[len("Member: "):]
        return ""

    def complete(self, task: str, prompt: str) -> AiResult:
        member = self._member_from_prompt(prompt)
        start = time.monotonic()
        time.sleep(self.sleep_by_member.get(member, 0))
        end = time.monotonic()
        with self._lock:
            self.calls.append({"member": member, "prompt": prompt, "start": start, "end": end})
        text = self.response_by_member.get(member, _member_response(view=member)) if member else SYNTHESIS_RESPONSE
        return AiResult(
            text=text, provider="fake", model="fake-model",
            input_tokens=10, output_tokens=5, duration_seconds=end - start,
        )


class RaisingMemberProvider(AiProvider):
    """Raises for exactly one named member and answers normally for the
    rest, including the synthesis call - to check that one member's call
    failing does not cancel the other five."""

    def __init__(self, raise_for: str):
        self.raise_for = raise_for

    def complete(self, task: str, prompt: str) -> AiResult:
        if f"Member: {self.raise_for}" in prompt:
            raise RuntimeError("provider unavailable")
        text = SYNTHESIS_RESPONSE if "## Assessments" in prompt else _member_response()
        return AiResult(
            text=text, provider="fake", model="fake-model",
            input_tokens=10, output_tokens=5, duration_seconds=0.01,
        )


class TestRunBoard(unittest.TestCase):
    def setUp(self):
        self.config = {"provider": {"models": {"board": "test/model"}}, "knowledge": {"roles_folder": str(_ROLES_DIR)}}

    def test_a_full_run_makes_one_call_per_member_plus_one_synthesis_call(self):
        provider = FakeProvider([_member_response() for _ in MEMBERS] + [SYNTHESIS_RESPONSE])
        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(provider.calls), len(MEMBERS) + 1)
        self.assertEqual(result.llm_calls, len(MEMBERS) + 1)
        self.assertEqual(len(result.assessments), len(MEMBERS))
        self.assertEqual({a.member for a in result.assessments}, set(MEMBERS))

    def test_no_member_prompt_contains_another_members_answer(self):
        """FR-3.3a: members are polled in isolation. A model writing the
        sixth assessment that could see the five already written would
        converge towards them, smoothing away the disagreement FR-3.6
        exists to surface. This pins that each member's prompt carries only
        the FR-3.1 input and that member's name - never another member's
        response text. Now that the six calls run concurrently, this is a
        structural property rather than a conventional one: all six prompts
        are built before any of the six calls is dispatched, so there is no
        point in time at which a later prompt could be constructed from an
        earlier answer."""
        marker = "MARKER-ONLY-FINANCE-SHOULD-SAY-THIS"
        responses = [_member_response(view=marker)] + [_member_response() for _ in MEMBERS[1:]]
        responses.append(SYNTHESIS_RESPONSE)
        provider = FakeProvider(responses)
        board.run_board(self.config, provider, topic="Dual-source the connector?")

        member_prompts = [prompt for _task, prompt in provider.calls[:len(MEMBERS)]]
        for prompt in member_prompts[1:]:
            self.assertNotIn(marker, prompt)

    def test_a_member_with_unparsable_json_is_recorded_and_the_rest_still_run(self):
        responses = ["this is not json"] + [_member_response() for _ in MEMBERS[1:]]
        responses.append(SYNTHESIS_RESPONSE)
        provider = FakeProvider(responses)
        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(result.failed_members), 1)
        self.assertIn("Finance", result.failed_members[0])
        self.assertEqual(len(result.assessments), len(MEMBERS) - 1)

    def test_a_member_missing_the_risks_key_is_treated_the_same_way(self):
        bad_response = json.dumps({"view": "a view", "recommendation": "a recommendation"})
        responses = [bad_response] + [_member_response() for _ in MEMBERS[1:]]
        responses.append(SYNTHESIS_RESPONSE)
        provider = FakeProvider(responses)
        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(result.failed_members), 1)
        self.assertIn("Finance", result.failed_members[0])
        self.assertEqual(len(result.assessments), len(MEMBERS) - 1)

    def test_fewer_than_two_assessments_skips_the_synthesis(self):
        responses = [_member_response()] + ["not json"] * (len(MEMBERS) - 1)
        provider = FakeProvider(responses)
        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(result.assessments), 1)
        self.assertIn("nothing to consolidate", result.synthesis.lower())
        self.assertEqual(result.llm_calls, len(MEMBERS))
        self.assertIsNone(result.ai_result)

    def test_assessments_come_back_in_board_members_order_even_when_calls_finish_out_of_order(self):
        """AP-1: the Python layer's output is deterministic. The six calls
        run concurrently and are free to finish in any order - here the
        last member submitted (KPI Check) is given the shortest sleep and
        returns first, and the first member submitted (Finance) is given
        the longest sleep and returns last - but ``assessments`` must still
        come back in ``BOARD_MEMBERS`` order, not completion order, or a
        board run could not be compared with itself between runs."""
        sleeps = {member: i * 0.03 for i, member in enumerate(reversed(MEMBERS))}
        responses = {member: _member_response(view=member) for member in MEMBERS}
        provider = TimedFakeProvider(sleeps, responses)

        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual([a.member for a in result.assessments], list(MEMBERS))

    def test_one_member_raising_still_yields_five_assessments_in_order(self):
        provider = RaisingMemberProvider(raise_for="Manufacturing")

        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(result.assessments), len(MEMBERS) - 1)
        self.assertEqual(len(result.failed_members), 1)
        self.assertIn("Manufacturing", result.failed_members[0])
        self.assertEqual(
            [a.member for a in result.assessments],
            [member for member in MEMBERS if member != "Manufacturing"],
        )

    def test_the_six_member_calls_actually_overlap_in_time(self):
        """This is the test that would fail if someone reverted to a
        sequential loop - every other assertion in this file would still
        pass whether the six calls run one after another or concurrently.
        Only overlapping start/end windows prove they really run in
        parallel."""
        sleeps = {member: 0.05 for member in MEMBERS}
        provider = TimedFakeProvider(sleeps)

        board.run_board(self.config, provider, topic="Dual-source the connector?")

        member_calls = [call for call in provider.calls if call["member"]]
        self.assertEqual(len(member_calls), len(MEMBERS))
        overlap = any(
            a["start"] < b["end"] and b["start"] < a["end"]
            for i, a in enumerate(member_calls)
            for b in member_calls[i + 1:]
        )
        self.assertTrue(overlap, "no two member calls overlapped in time - looks sequential")

    def test_without_a_provider_it_raises_ai_not_configured(self):
        with self.assertRaises(AiNotConfiguredError):
            board.run_board(self.config, None, topic="Dual-source the connector?")

    def test_without_a_board_model_configured_it_raises_ai_not_configured(self):
        config = {"provider": {"models": {"board": None}}}
        provider = FakeProvider([_member_response()])
        with self.assertRaises(AiNotConfiguredError):
            board.run_board(config, provider, topic="Dual-source the connector?")

    def test_a_config_without_paths_workbook_does_not_raise(self):
        """AI-2/NFR-10 - the board is the one run that works without a
        workbook (``self.config`` here carries no ``paths`` section at all),
        and audit logging must not change that."""
        provider = FakeProvider([_member_response() for _ in MEMBERS] + [SYNTHESIS_RESPONSE])
        result = board.run_board(self.config, provider, topic="Dual-source the connector?")

        self.assertEqual(len(result.assessments), len(MEMBERS))


class TestAskFollowUp(unittest.TestCase):
    def setUp(self):
        self.config = {"provider": {"models": {"board": "test/model"}}, "knowledge": {"roles_folder": str(_ROLES_DIR)}}
        self.board_result = board.BoardResult(
            topic="Dual-source the connector?",
            assessments=[
                board.MemberAssessment(
                    member="Finance", view="MARKER-FINANCE-VIEW", risks="Budget overrun",
                    recommendation="Do not dual-source",
                ),
                board.MemberAssessment(
                    member="HW Engineering", view="Reduces supply risk", risks="Requalification effort",
                    recommendation="Dual-source",
                ),
            ],
            synthesis="Overall recommendation: proceed with caution",
            failed_members=[], ai_result=None, llm_calls=7,
        )

    def test_ask_follow_up_makes_exactly_one_synthesis_call(self):
        """The owner decided a follow-up costs one call, not seven: the six
        members are never polled again for a follow-up, only the synthesis
        is re-run."""
        provider = FakeProvider(["the answer"])
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        board.ask_follow_up(self.config, provider, conversation, "What about cost?")

        self.assertEqual(len(provider.calls), 1)
        task, prompt = provider.calls[0]
        self.assertEqual(task, board.TASK_BOARD)
        self.assertIn("## Assessments", prompt)
        self.assertNotIn("## Member (FR-3.3a)", prompt)

    def test_the_follow_up_prompt_contains_the_original_assessments(self):
        """The members are not re-polled, so their first answers are the
        only material a follow-up ever has - this is why board_members.md
        demands depth from the start."""
        provider = FakeProvider(["the answer"])
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        board.ask_follow_up(self.config, provider, conversation, "What about cost?")

        _task, prompt = provider.calls[0]
        self.assertIn("MARKER-FINANCE-VIEW", prompt)

    def test_a_second_follow_up_prompt_contains_the_first_question_and_answer_in_order(self):
        provider = FakeProvider(["first answer", "second answer"])
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        board.ask_follow_up(self.config, provider, conversation, "first question")
        board.ask_follow_up(self.config, provider, conversation, "second question")

        _task, prompt = provider.calls[1]
        question_index = prompt.index("first question")
        answer_index = prompt.index("first answer")
        self.assertGreater(answer_index, question_index)

    def test_conversation_turns_grows_by_one_per_call(self):
        provider = FakeProvider(["first answer", "second answer"])
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        board.ask_follow_up(self.config, provider, conversation, "first question")
        self.assertEqual(conversation.turns, [("first question", "first answer")])
        self.assertEqual(conversation.llm_calls, 1)

        board.ask_follow_up(self.config, provider, conversation, "second question")
        self.assertEqual(
            conversation.turns,
            [("first question", "first answer"), ("second question", "second answer")],
        )
        self.assertEqual(conversation.llm_calls, 2)

    def test_a_blank_question_raises_value_error(self):
        provider = FakeProvider(["the answer"])
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        with self.assertRaises(ValueError):
            board.ask_follow_up(self.config, provider, conversation, "   ")

    def test_without_a_provider_it_raises_ai_not_configured(self):
        conversation = board.BoardConversation(result=self.board_result, turns=[])

        with self.assertRaises(AiNotConfiguredError):
            board.ask_follow_up(self.config, None, conversation, "a question")


class TestMemberChoiceApplicabilityAndRetry(unittest.TestCase):
    """Decided 9 September 2026: Alex chooses the members asked, a member
    may say the topic does not touch it, and a call that used tools
    instead of answering is retried once."""

    def setUp(self):
        self.config = {"provider": {"models": {"board": "fake/m"}}, "knowledge": {"roles_folder": str(_ROLES_DIR)}}

    def test_only_the_chosen_members_are_asked(self):
        chosen = MEMBERS[:2]
        provider = FakeProvider([_member_response()] * 2 + [SYNTHESIS_RESPONSE])
        result = board.run_board(self.config, provider, topic="T", members=chosen)
        member_prompts = [p for _t, p in provider.calls if "## Member (FR-3.3a)" in p]
        self.assertEqual(len(member_prompts), 2)
        self.assertEqual([a.member for a in result.assessments], list(chosen))
        self.assertEqual(result.llm_calls, 3)

    def test_choosing_nobody_on_the_board_raises(self):
        with self.assertRaises(ValueError):
            board.run_board(self.config, FakeProvider([]), topic="T", members=["Nobody"])

    def test_a_member_may_say_the_topic_does_not_touch_it(self):
        texts = [json.dumps({"applies": False, "view": "- No hardware part changes.", "risks": [],
                             "recommendation": "- No position: not affected."})]
        texts += [_member_response(risks=["r1", "r2"])] * (len(MEMBERS) - 1) + [SYNTHESIS_RESPONSE]
        provider = FakeProvider(texts)
        result = board.run_board(self.config, provider, topic="T")
        first = result.assessments[0]
        self.assertFalse(first.applies)
        self.assertTrue(all(a.applies for a in result.assessments[1:]))
        self.assertEqual(result.assessments[1].risks, "- r1\n- r2")     # one bullet per risk
        synthesis_prompt = provider.calls[-1][1]
        self.assertIn('"applies": false', synthesis_prompt)

    def test_a_tool_only_failure_is_retried_once_without_tools(self):
        class ToolsFirst(FakeProvider):
            def __init__(self):
                super().__init__([])
                self.failed_once = False

            def complete(self, task, prompt):
                self.calls.append((task, prompt))
                if "## Member (FR-3.3a)" in prompt and not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError("opencode run produced no answer text. It called tool(s) instead of answering: glob.")
                text = SYNTHESIS_RESPONSE if "## Assessments" in prompt else _member_response()
                return AiResult(text=text, provider="fake", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)

        provider = ToolsFirst()
        result = board.run_board(self.config, provider, topic="T")
        self.assertEqual(result.failed_members, [])
        self.assertEqual(len(result.assessments), len(MEMBERS))
        self.assertEqual(result.llm_calls, len(MEMBERS) + 2)          # one retry
        retried = [p for _t, p in provider.calls if p.startswith("IMPORTANT: your previous attempt")]
        self.assertEqual(len(retried), 1)

    def test_other_failures_are_not_retried(self):
        class Broken(FakeProvider):
            def complete(self, task, prompt):
                self.calls.append((task, prompt))
                if "## Member (FR-3.3a)" in prompt:
                    raise RuntimeError("connection refused")
                return AiResult(text=SYNTHESIS_RESPONSE, provider="fake", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)

        provider = Broken([])
        result = board.run_board(self.config, provider, topic="T")
        self.assertEqual(len(result.failed_members), len(MEMBERS))
        self.assertEqual(len(provider.calls), len(MEMBERS))


class TestFollowUpWithMembers(unittest.TestCase):
    def setUp(self):
        self.config = {"provider": {"models": {"board": "fake/m"}}, "knowledge": {"roles_folder": str(_ROLES_DIR)}}
        roles = board.load_board(self.config).profiles
        self.conversation = board.BoardConversation(
            result=board.BoardResult(
                topic="Source the soft tooling now?",
                assessments=[board.MemberAssessment(member=m, view=f"- {m} view", risks="- r", recommendation="- wait")
                             for m in MEMBERS],
                synthesis="Overall recommendation: wait", failed_members=[], ai_result=None, llm_calls=7),
            turns=[], roles=roles,
            inputs={"topic": "Source the soft tooling now?", "context": "SOP fixed", "options": ["now", "later"],
                    "constraints": []},
            conduct="Be concrete.", member_data={MEMBERS[0]: "| KPI | MG0 |"}, project="Dual DCDC")

    def test_members_asked_again_get_their_profile_earlier_answer_and_the_question(self):
        again = MEMBERS[:2]
        follow_up_json = json.dumps({"answer": "- Then source now.", "reasons": ["Hardware: lead time"],
                                     "recommendation_now": "Source now.", "disagreements": []})
        provider = FakeProvider([_member_response(view="- changed my mind")] * 2 + [follow_up_json])
        turn = board.ask_follow_up_full(self.config, provider, self.conversation, "What if tooling is free?", again)
        self.assertEqual(turn.llm_calls, 3)
        self.assertEqual([a.member for a in turn.assessments], list(again))
        member_prompts = [p for _t, p in provider.calls[:2]]
        for member, prompt in zip(again, member_prompts):
            self.assertIn(f"Member: {member}", prompt)
            self.assertIn("## Role profile", prompt)
            self.assertIn("## Your earlier assessment", prompt)
            self.assertIn(f"- {member} view", prompt)
            self.assertIn("Be concrete.", prompt)
            self.assertIn("What if tooling is free?", prompt)
            self.assertIn("Project: Dual DCDC.", prompt)
        self.assertIn("| KPI | MG0 |", member_prompts[0])
        synthesis_prompt = provider.calls[2][1]
        self.assertIn("## Member answers to the new question", synthesis_prompt)
        self.assertIn("changed my mind", synthesis_prompt)
        self.assertEqual(turn.data["recommendation_now"], "Source now.")
        self.assertIn("Then source now.", turn.answer)
        self.assertEqual(self.conversation.turns[-1][0], "What if tooling is free?")

    def test_no_members_means_one_call_and_plain_text_is_kept_as_is(self):
        provider = FakeProvider(["Because of the lead time."])
        turn = board.ask_follow_up_full(self.config, provider, self.conversation, "Why?")
        self.assertEqual(turn.llm_calls, 1)
        self.assertIsNone(turn.data)
        self.assertEqual(turn.answer, "Because of the lead time.")
        self.assertNotIn("## Member answers", provider.calls[0][1])

    def test_a_member_that_fails_on_the_follow_up_is_named_and_the_rest_answer(self):
        class OneBroken(FakeProvider):
            def complete(self, task, prompt):
                self.calls.append((task, prompt))
                if f"Member: {MEMBERS[0]}" in prompt:
                    raise RuntimeError("boom")
                text = "not json" if "## Assessments" in prompt else _member_response()
                return AiResult(text=text, provider="fake", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)

        turn = board.ask_follow_up_full(self.config, OneBroken([]), self.conversation, "Why?", MEMBERS[:2])
        self.assertEqual(len(turn.failed_members), 1)
        self.assertIn(MEMBERS[0], turn.failed_members[0])
        self.assertEqual([a.member for a in turn.assessments], [MEMBERS[1]])
        self.assertEqual(turn.answer, "not json")


class TestRender(unittest.TestCase):
    def test_render_produces_one_table_section_per_assessment_and_the_synthesis(self):
        result = board.BoardResult(
            topic="Dual-source the connector?",
            assessments=[
                board.MemberAssessment(
                    member="Finance", view="Adds cost", risks="Budget overrun",
                    recommendation="Do not dual-source",
                ),
                board.MemberAssessment(
                    member="HW Engineering", view="Reduces supply risk", risks="Requalification effort",
                    recommendation="Dual-source",
                ),
            ],
            synthesis="Overall recommendation: proceed with caution",
            failed_members=["KPI Check: response did not parse as JSON with view/risks/recommendation"],
            ai_result=None,
            llm_calls=3,
        )
        text = board.render(result)

        self.assertIn("Finance", text)
        self.assertIn("Adds cost", text)
        self.assertIn("HW Engineering", text)
        self.assertIn("Reduces supply risk", text)
        self.assertIn("Overall recommendation: proceed with caution", text)
        self.assertIn("KPI Check", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
