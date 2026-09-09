#!/usr/bin/env python3
"""End-to-end tests for the browser interface's API (spec section 10): a
real ``http.server`` on a free local port, the model mocked at the
``AiProvider`` boundary, the flow driven exactly as the page drives it."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

sys.path.insert(0, str(REPO_ROOT / "tests"))
from decisionboard.agent.provider import AiProvider, AiResult  # noqa: E402
from decisionboard.server import create_http_server  # noqa: E402
from decisionboard import clarify as clarify_mod  # noqa: E402
from _roles_fixture import CLASSIC, make_roles  # noqa: E402

MEMBER_COUNT = len(CLASSIC)

CLARIFIER = json.dumps({"topic": "Rework or switch", "context": "SOP is fixed.", "options": ["Rework", "Switch"],
                        "constraints": ["SOP cannot move"], "questions": ["What is the budget?"]})
SYNTHESIS = json.dumps({"overall_recommendation": "Rework", "decisive_criterion": "Time", "counter_arguments": ["Cost"],
                        "what_would_change_it": "A quote", "disagreements": ["Finance vs Manufacturing"]})
PROPOSAL = json.dumps({"path": "Decisions/Rework.md", "title": "Rework decision", "tags": ["tooling"], "body": "Decided: rework."})
CLEAR = json.dumps({"topic": "Rework or switch", "context": "SOP is fixed. Budget 200k.", "options": ["Rework", "Switch"],
                    "constraints": ["SOP cannot move"], "questions": [], "clear": True})
FOLLOW_UP = json.dumps({"answer": "- Because time is the decisive criterion.", "reasons": ["Hardware: DV date"],
                        "recommendation_now": "Rework, unchanged.", "disagreements": []})


class RoutingFakeProvider(AiProvider):
    """Answers by what the prompt is for - the markers each prompt builder
    puts in - so one provider serves the whole flow."""

    def __init__(self):
        self.prompts: list[str] = []
        self.lock = threading.Lock()

    def complete(self, task: str, prompt: str) -> AiResult:
        with self.lock:
            self.prompts.append(prompt)
        if "## Question from Alex" in prompt:
            text = CLEAR if "## Clarification so far" in prompt else CLARIFIER
        elif "## Members to assess" in prompt or "## Members to ask again" in prompt:
            names = [line.split("### Member: ", 1)[1].strip() for line in prompt.splitlines() if line.startswith("### Member: ")]
            entries = [{"member": n, "applies": True, "view": f"- {n} combined view", "risks": ["r"], "recommendation": "- Rework",
                        "facts_from_network": [{"fact": "Tooling is late", "source": "Tooling.md"}]} for n in names]
            text = json.dumps({"members": entries, "synthesis": json.loads(SYNTHESIS),
                               "follow_up": {"answer": "- Combined follow-up.", "reasons": [], "recommendation_now": "Rework.", "disagreements": []}})
        elif "## Vault outline" in prompt:
            text = PROPOSAL
        elif "## Your earlier assessment" in prompt:
            member = prompt.split("Member: ", 1)[1].splitlines()[0]
            text = json.dumps({"applies": member != "Finance", "view": f"- {member} again",
                               "risks": ["r2"], "recommendation": "- Rework, still"})
        elif "## New question" in prompt:
            text = FOLLOW_UP
        elif "## Assessments" in prompt:
            text = SYNTHESIS
        elif "Member: " in prompt:
            member = prompt.split("Member: ", 1)[1].splitlines()[0]
            text = json.dumps({"view": f"{member} view", "risks": ["r1"], "recommendation": "Rework",
                               "facts_from_network": [{"fact": "Tooling is late", "source": "Tooling.md"},
                                                      {"fact": "MG3 is in March", "source": "Gates.md"}],
                               "own_judgement": ["a new supplier needs 10 weeks"]})
        else:
            text = "{}"
        return AiResult(text=text, provider="fake", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)


class TestServerFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.vault = Path(cls.tmp.name) / "vault"
        cls.vault.mkdir()
        (cls.vault / "Tooling.md").write_text("---\ntitle: Tooling\ntags: [tooling]\n---\nTooling is late.\n", encoding="utf-8")
        make_roles(cls.vault / "Roles&Responsibilities")
        cls.config_path = Path(cls.tmp.name) / "config.local.json"
        cls.config = {"provider": {"models": {"board": "fake/m"}}, "knowledge": {"vault_path": str(cls.vault), "token_budget": 6000}}
        cls.config_path.write_text(json.dumps(cls.config), encoding="utf-8")
        cls.provider = RoutingFakeProvider()
        cls.httpd, cls.board_server = create_http_server(cls.config, cls.config_path, port=0, provider=cls.provider)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def wait_for(self, session_id, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, state = self.call("GET", f"/api/sessions/{session_id}")
            if predicate(state):
                return state
            time.sleep(0.02)
        self.fail(f"timed out waiting; last phase {state['phase']} error {state['error']}")

    def test_index_and_static_files_are_served(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertIn(b"Decision Board", response.read())
        for name in ("app.js", "style.css"):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/{name}", timeout=10) as response:
                self.assertEqual(response.status, 200)
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/static/../server.py")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(request, timeout=10)

    def test_project_round_trips_and_the_vault_projects_are_listed(self):
        (self.vault / "Dual DCDC.md").write_text("---\nkind: project\n---\nThe project.\n", encoding="utf-8")
        try:
            status, view = self.call("POST", "/api/config", {"project": "Dual DCDC"})
            self.assertEqual(status, 200)
            self.assertEqual(view["project"], "Dual DCDC")
            self.assertIn("Dual DCDC", view["projects"])
            self.assertEqual(self.config["knowledge"]["project"], "Dual DCDC")
        finally:
            self.call("POST", "/api/config", {"project": ""})
            (self.vault / "Dual DCDC.md").unlink()

    def test_config_is_read_and_written(self):
        status, view = self.call("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(view["model"], "fake/m")
        self.assertTrue(view["knowledge_status"]["ok"])
        self.assertEqual(view["knowledge_status"]["notes"], 1)
        status, view = self.call("POST", "/api/config", {"token_budget": 3000, "theme": "dark"})
        self.assertEqual(status, 200)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["knowledge"]["token_budget"], 3000)
        self.assertEqual(saved["ui"]["theme"], "dark")
        self.call("POST", "/api/config", {"token_budget": 6000})

    def test_blank_question_is_rejected(self):
        status, body = self.call("POST", "/api/sessions", {"question": "  "})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_full_flow_from_question_to_written_note(self):
        first_prompt = len(self.provider.prompts)   # other tests share the provider
        status, state = self.call("POST", "/api/sessions", {"question": "Rework the tooling or switch supplier?"})
        self.assertEqual(status, 201)
        sid = state["id"]

        state = self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.assertEqual(state["clarification"]["questions"], ["What is the budget?"])
        self.assertEqual(state["knowledge"]["selected"], 1)
        self.assertEqual(state["knowledge"]["notes"], ["Tooling.md"])
        clarifier_prompt = [p for p in self.provider.prompts[first_prompt:] if "## Question from Alex" in p][0]
        self.assertIn("Tooling is late.", clarifier_prompt)

        status, state = self.call("POST", f"/api/sessions/{sid}/answers", {"answers": ["200k"]})
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "clarifying")           # the clarifier looks again
        state = self.wait_for(sid, lambda s: s["phase"] == "confirm")
        self.assertEqual(len(state["rounds"]), 1)
        self.assertEqual(state["llm_calls"], 2)                  # two clarifier calls
        self.assertIn("Q: What is the budget?\nA: 200k", state["inputs"]["context"])
        self.assertIn("Budget 200k.", state["inputs"]["context"])   # the second round's context won
        self.assertEqual(sorted(state["roles"]["members"]), sorted(CLASSIC))

        chosen = CLASSIC[:3]
        status, state = self.call("POST", f"/api/sessions/{sid}/run", {
            "topic": "Rework or switch (edited)", "context": state["inputs"]["context"],
            "options": ["Rework", "Switch"], "constraints": ["SOP cannot move"], "members": chosen})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: s["phase"] == "result")
        self.assertEqual(state["result"]["topic"], "Rework or switch (edited)")
        self.assertEqual(sorted(state["selected_members"]), sorted(chosen))
        self.assertEqual(sorted(a["member"] for a in state["result"]["assessments"]), sorted(chosen))
        self.assertEqual(state["result"]["synthesis_data"]["overall_recommendation"], "Rework")
        self.assertEqual(state["result"]["sources"]["network"], ["Tooling.md"])   # the note the board sent
        self.assertEqual(len(state["result"]["sources"]["unverified"]), len(chosen))   # Gates.md was never sent
        self.assertEqual(state["result"]["assessments"][0]["sources"][0]["verified"], True)
        self.assertEqual(state["result"]["assessments"][0]["sources"][1]["verified"], False)
        self.assertEqual(sorted(state["members"]), sorted(chosen))
        self.assertTrue(all(v == "done" for v in state["members"].values()))
        self.assertEqual(sorted(state["partial"]), sorted(chosen))     # the early answers, one per member
        self.assertEqual(state["partial"][chosen[0]]["view"], f"{chosen[0]} view")
        self.assertEqual(state["llm_calls"], 2 + len(chosen) + 1)   # clarifier x2 + members + synthesis

        # Members received the knowledge and the clarification, never the clarifier's questions as a task.
        member_prompts = [p for p in self.provider.prompts[first_prompt:] if "Member: " in p]
        self.assertEqual(len(member_prompts), len(chosen))
        self.assertTrue(all("Tooling is late." in p for p in member_prompts))
        self.assertTrue(all("A: 200k" in p for p in member_prompts))
        self.assertFalse(any("## Question from Alex" in p for p in member_prompts))

        calls_before = state["llm_calls"]
        status, state = self.call("POST", f"/api/sessions/{sid}/follow-up", {"question": "Why?"})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: not s["busy"])
        self.assertIn("Because time is the decisive criterion.", state["turns"][0]["answer"])
        self.assertEqual(state["turns"][0]["data"]["recommendation_now"], "Rework, unchanged.")
        self.assertEqual(state["turns"][0]["assessments"], [])
        self.assertEqual(state["llm_calls"], calls_before + 1)     # one call: nobody asked again

        asked_again = chosen[:2]
        status, state = self.call("POST", f"/api/sessions/{sid}/follow-up",
                                  {"question": "And if tooling is free?", "members": asked_again})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: not s["busy"])
        turn = state["turns"][1]
        self.assertEqual(sorted(a["member"] for a in turn["assessments"]), sorted(asked_again))
        self.assertEqual(state["llm_calls"], calls_before + 1 + len(asked_again) + 1)
        again_prompts = [p for p in self.provider.prompts[first_prompt:] if "## Your earlier assessment" in p]
        self.assertEqual(len(again_prompts), len(asked_again))
        self.assertTrue(all("## Role profile" in p and "And if tooling is free?" in p for p in again_prompts))
        finance = [a for a in turn["assessments"] if a["member"] == "Finance"]
        if finance:
            self.assertFalse(finance[0]["applies"])

        status, state = self.call("POST", f"/api/sessions/{sid}/close", {"remember": True})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: s["phase"] == "proposal")
        self.assertEqual(state["proposal"]["path"], "Decisions/Rework.md")
        self.assertEqual(state["proposal"]["mode"], "create")
        self.assertIn("Decided: rework.", state["proposal"]["preview"])
        self.assertFalse((self.vault / "Decisions" / "Rework.md").exists())   # nothing written yet

        status, state = self.call("POST", f"/api/sessions/{sid}/memory", {
            "path": "Decisions/Rework.md", "title": "Rework decision", "tags": ["tooling"], "body": "Decided: rework (edited)."})
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "written")
        written = (self.vault / "Decisions" / "Rework.md").read_text(encoding="utf-8")
        self.assertIn("Decided: rework (edited).", written)
        self.assertIn("tags: [tooling, decision-board]", written)

    def test_close_without_remembering_writes_nothing(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})   # straight to the board
        state = self.wait_for(sid, lambda s: s["phase"] == "confirm")
        self.assertEqual(state["llm_calls"], 1)
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "context": "", "options": [], "constraints": []})
        self.wait_for(sid, lambda s: s["phase"] == "result")
        status, state = self.call("POST", f"/api/sessions/{sid}/close", {"remember": False})
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "closed")

    def test_unreachable_vault_is_an_error_not_a_silent_run(self):
        original = self.config["knowledge"]["vault_path"]
        self.config["knowledge"]["vault_path"] = str(Path(self.tmp.name) / "missing")
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
            state = self.wait_for(state["id"], lambda s: s["phase"] == "error")
            self.assertIn("knowledge source not found", state["error"])
            self.assertIn("Options", state["error"])
        finally:
            self.config["knowledge"]["vault_path"] = original

    def test_the_round_limit_sends_the_question_to_the_board(self):
        keep = self.provider.complete
        # A clarifier that never finds the question clear.
        self.provider.complete = lambda task, prompt: (
            AiResult(text=CLARIFIER, provider="fake", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)
            if "## Question from Alex" in prompt else keep(task, prompt))
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
            sid = state["id"]
            for round_number in range(1, clarify_mod.MAX_ROUNDS + 1):
                state = self.wait_for(sid, lambda s: s["phase"] in ("questions", "confirm"))
                if state["phase"] == "confirm":
                    break
                self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [f"answer {round_number}"]})
            state = self.wait_for(sid, lambda s: s["phase"] == "confirm")
            self.assertEqual(len(state["rounds"]), state["max_rounds"])
            self.assertIn("A: answer 3", state["inputs"]["context"])
        finally:
            self.provider.complete = keep

    def test_choosing_no_known_member_is_refused(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        self.wait_for(sid, lambda s: s["phase"] == "confirm")
        status, body = self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": ["Nobody"]})
        self.assertEqual(status, 400)
        self.assertIn("at least one member", body["error"])

    def test_combined_mode_is_one_call_for_the_board_and_one_for_a_follow_up(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        state = self.wait_for(sid, lambda s: s["phase"] == "confirm")
        before = state["llm_calls"]
        chosen = CLASSIC[:3]
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": chosen, "mode": "combined"})
        state = self.wait_for(sid, lambda s: s["phase"] == "result")
        self.assertEqual(state["mode"], "combined")
        self.assertEqual(state["result"]["mode"], "combined")
        self.assertEqual(state["llm_calls"], before + 1)
        self.assertEqual(sorted(a["member"] for a in state["result"]["assessments"]), sorted(chosen))
        self.assertEqual(state["result"]["sources"]["network"], ["Tooling.md"])
        self.call("POST", f"/api/sessions/{sid}/follow-up", {"question": "Why?", "members": chosen[:2], "mode": "combined"})
        state = self.wait_for(sid, lambda s: not s["busy"])
        self.assertEqual(state["llm_calls"], before + 2)
        self.assertEqual(state["turns"][0]["mode"], "combined")
        self.assertEqual(sorted(a["member"] for a in state["turns"][0]["assessments"]), sorted(chosen[:2]))
        self.assertIn("Combined follow-up.", state["turns"][0]["answer"])

    def test_the_estimate_counts_exact_calls_and_learns_the_overhead_from_real_calls(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Rework the tooling?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        self.wait_for(sid, lambda s: s["phase"] == "confirm")
        chosen = CLASSIC[:3]
        status, est = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "mode": "individual", "budget": 2000})
        self.assertEqual(status, 200)
        self.assertEqual(est["calls"], 4)                                  # three members plus the synthesis
        self.assertEqual(est["overhead_learned_from"], 1)                   # the clarifier call is the only real one so far
        self.assertEqual(est["overhead_per_call"], 0)                       # the fake reports 1 token in: overhead floored at 0
        self.assertEqual(sorted(est["members"]), sorted(chosen))
        self.assertIn("Tooling.md", est["members"][chosen[0]])
        self.assertGreater(est["tokens_in"], 4 * 1000)                      # four prompts of a few thousand characters
        status, combined = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "mode": "combined", "budget": 2000})
        self.assertEqual(combined["calls"], 1)
        self.assertLess(combined["tokens_in"], est["tokens_in"])
        status, none = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "budget": 0})
        self.assertEqual(none["members"][chosen[0]], [])
        # after a real run the overhead is learned from the recorded calls (the fake reports 1 token in)
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Rework?", "members": chosen, "budget": 2000})
        state = self.wait_for(sid, lambda s: s["phase"] == "result")
        self.assertEqual(state["budget"], 2000)
        self.assertEqual(sorted(state["member_knowledge_paths"]), sorted(chosen))
        self.call("POST", f"/api/sessions/{sid}/back")
        status, est2 = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "budget": 2000})
        self.assertGreater(est2["overhead_learned_from"], 1)               # the members and the synthesis were recorded too
        self.assertEqual(est2["overhead_per_call"], 0)

    def test_projects_come_from_the_home_page_and_scope_the_notes(self):
        (self.vault / "Sister.md").write_text("---\nkind: project\nprojects: [Sister]\n---\nSister project tooling note.\n", encoding="utf-8")
        (self.vault / "Dual.md").write_text("---\nkind: project\nprojects: [Dual DCDC]\n---\nDual project tooling note.\n", encoding="utf-8")
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Tooling?", "projects": ["Dual DCDC"]})
            sid = state["id"]
            self.assertEqual(state["projects"], ["Dual DCDC"])
            state = self.wait_for(sid, lambda s: s["phase"] == "questions")
            self.assertIn("Dual.md", state["knowledge"]["notes"])
            self.assertNotIn("Sister.md", state["knowledge"]["notes"])
            _, both = self.call("POST", "/api/sessions", {"question": "Tooling?", "projects": ["Dual DCDC", "Sister"]})
            both = self.wait_for(both["id"], lambda s: s["phase"] == "questions")
            self.assertIn("Sister.md", both["knowledge"]["notes"])
            self.assertIn("Dual.md", both["knowledge"]["notes"])
        finally:
            (self.vault / "Sister.md").unlink(); (self.vault / "Dual.md").unlink()

    def test_manual_picks_are_sent_on_top_of_the_budget_and_exclusions_never(self):
        (self.vault / "Manual.md").write_text("# Manual\n\nA note nobody would rank for this question.\n", encoding="utf-8")
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Rework the tooling?"})
            sid = state["id"]
            self.wait_for(sid, lambda s: s["phase"] == "questions")
            self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
            self.wait_for(sid, lambda s: s["phase"] == "confirm")
            chosen = CLASSIC[:2]
            _, plain = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "budget": 40, "outline": True})
            self.assertTrue(any(n["path"] == "Manual.md" for n in plain["outline"]))
            self.assertNotIn("Manual.md", plain["members"][chosen[0]])           # a 40-token budget: Tooling.md only
            _, picked = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": chosen, "budget": 40, "extra": ["Manual.md"], "exclude": ["Tooling.md"]})
            self.assertIn("Manual.md", picked["members"][chosen[0]])
            self.assertNotIn("Tooling.md", picked["members"][chosen[0]])
            self.assertGreater(picked["forced_tokens"], 0)
            self.assertTrue(any(s["forced"] for s in picked["sections"][chosen[0]]))
            self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Rework?", "members": chosen, "budget": 40, "extra": ["Manual.md"], "exclude": ["Tooling.md"]})
            state = self.wait_for(sid, lambda s: s["phase"] == "result")
            self.assertEqual(state["extra"], ["Manual.md"])
            self.assertEqual(state["member_knowledge_paths"][chosen[0]], ["Manual.md"])
        finally:
            (self.vault / "Manual.md").unlink()

    def test_a_member_not_asked_at_first_can_be_asked_in_a_follow_up(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        self.wait_for(sid, lambda s: s["phase"] == "confirm")
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": CLASSIC[:2]})
        self.wait_for(sid, lambda s: s["phase"] == "result")
        later = CLASSIC[2]
        first_prompt = len(self.provider.prompts)
        self.call("POST", f"/api/sessions/{sid}/follow-up", {"question": "And you?", "members": [later]})
        state = self.wait_for(sid, lambda s: not s["busy"])
        self.assertEqual([a["member"] for a in state["turns"][0]["assessments"]], [later])
        prompt = [p for p in self.provider.prompts[first_prompt:] if f"Member: {later}" in p][0]
        self.assertIn("you did not produce an assessment in the first round", prompt)
        self.assertIn("Tooling is late.", prompt)               # its knowledge block was built on demand

    def test_back_returns_to_the_questions_with_the_answers_kept(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": ["my answer"], "final": True})
        state = self.wait_for(sid, lambda s: s["phase"] == "confirm")
        status, state = self.call("POST", f"/api/sessions/{sid}/back")
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "questions")
        self.assertEqual(state["clarification"]["questions"], ["What is the budget?"])
        self.assertEqual(state["answers"], ["my answer"])
        self.assertEqual(state["rounds"], [])

    def test_back_after_a_failed_run_returns_to_confirm_with_the_choice_kept(self):
        keep = self.provider.complete
        self.provider.complete = lambda task, prompt: (_ for _ in ()).throw(RuntimeError("gateway down")) \
            if "## Assessments" in prompt else keep(task, prompt)
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
            sid = state["id"]
            self.wait_for(sid, lambda s: s["phase"] == "questions")
            self.call("POST", f"/api/sessions/{sid}/answers", {"answers": ["x"], "final": True})
            self.wait_for(sid, lambda s: s["phase"] == "confirm")
            chosen = CLASSIC[:2]
            self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Edited topic", "context": "c", "options": [], "constraints": [], "members": chosen})
            state = self.wait_for(sid, lambda s: s["phase"] == "error")
            self.assertIn("gateway down", state["error"])
        finally:
            self.provider.complete = keep
        status, state = self.call("POST", f"/api/sessions/{sid}/back")
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "confirm")
        self.assertIsNone(state["error"])
        self.assertEqual(state["inputs"]["topic"], "Edited topic")
        self.assertEqual(sorted(state["selected_members"]), sorted(chosen))
        self.assertEqual(sorted(state["members"]), sorted(chosen))
        # and the board can be run again from there
        status, _ = self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Edited topic", "members": chosen})
        self.assertEqual(status, 200)
        self.wait_for(sid, lambda s: s["phase"] == "result")

    def test_back_while_the_board_works_stops_it_and_returns_to_confirm(self):
        import time as _time
        keep = self.provider.complete

        def slow(task, prompt):
            if "## Member (FR-3.3a)" in prompt:
                _time.sleep(0.6)
            return keep(task, prompt)
        self.provider.complete = slow
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
            sid = state["id"]
            self.wait_for(sid, lambda s: s["phase"] == "questions")
            self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
            self.wait_for(sid, lambda s: s["phase"] == "confirm")
            baseline = threading.active_count()
            self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": CLASSIC[:2]})
            state = self.wait_for(sid, lambda s: s["phase"] == "running")
            self.assertTrue(state["nav"]["back"])
            status, state = self.call("POST", f"/api/sessions/{sid}/back")
            self.assertEqual(status, 200)
            self.assertEqual(state["phase"], "confirm")
            deadline = _time.monotonic() + 8
            while threading.active_count() > baseline and _time.monotonic() < deadline:
                _time.sleep(0.1)                               # the stopped worker threads finish in the background
            _, state = self.call("GET", f"/api/sessions/{sid}")
            self.assertEqual(state["phase"], "confirm")        # their late result was discarded
            self.assertIsNone(state["result"])
            # and the board runs again from there
            self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": CLASSIC[:2]})
            state = self.wait_for(sid, lambda s: s["phase"] == "result")
            self.assertEqual(len(state["result"]["assessments"]), 2)
        finally:
            self.provider.complete = keep

    def test_back_from_the_result_and_forward_again(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        self.wait_for(sid, lambda s: s["phase"] == "confirm")
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Anything", "members": CLASSIC[:2]})
        self.wait_for(sid, lambda s: s["phase"] == "result")
        status, state = self.call("POST", f"/api/sessions/{sid}/back")
        self.assertEqual(state["phase"], "confirm")
        self.assertTrue(state["nav"]["forward"])
        self.assertIsNotNone(state["result"])
        status, state = self.call("POST", f"/api/sessions/{sid}/forward")
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "result")
        self.assertFalse(state["nav"]["forward"])
        status, _ = self.call("POST", f"/api/sessions/{sid}/forward")
        self.assertEqual(status, 409)
        status, state = self.call("POST", f"/api/sessions/{sid}/abandon")
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "closed")

    def test_back_with_nothing_behind_it_is_refused(self):
        original = self.config["knowledge"]["vault_path"]
        self.config["knowledge"]["vault_path"] = str(Path(self.tmp.name) / "missing")
        try:
            _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
            sid = state["id"]
            self.wait_for(sid, lambda s: s["phase"] == "error")
        finally:
            self.config["knowledge"]["vault_path"] = original
        status, body = self.call("POST", f"/api/sessions/{sid}/back")
        self.assertEqual(status, 409)
        self.assertIn("your question is kept", body["error"])

    def test_only_this_machines_page_may_talk_to_the_server(self):
        def raw(method, path, headers, body=b""):
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body or None, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code
        self.assertEqual(raw("GET", "/api/config", {"Host": "evil.example.com"}), 403)                 # DNS rebinding
        self.assertEqual(raw("GET", "/api/config", {"Host": f"127.0.0.1:{self.port + 1}"}), 403)      # another port
        self.assertEqual(raw("POST", "/api/sessions", {"Host": f"127.0.0.1:{self.port}", "Origin": "http://evil.example.com",
                                                        "Content-Type": "application/json"}, b'{"question": "x"}'), 403)
        self.assertEqual(raw("POST", "/api/sessions", {"Host": f"127.0.0.1:{self.port}", "Content-Type": "text/plain"},
                             b'{"question": "x"}'), 415)                                                # a "simple" cross-origin POST
        self.assertEqual(raw("POST", "/api/sessions", {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json",
                                                        "Content-Length": "abc"}, b"{}"), 400)
        self.assertEqual(raw("GET", "/api/config", {"Host": f"localhost:{self.port}"}), 200)

    def test_actions_out_of_order_are_refused(self):
        _, state = self.call("POST", "/api/sessions", {"question": "Anything?"})
        sid = state["id"]
        status, body = self.call("POST", f"/api/sessions/{sid}/run", {"topic": "x"})
        self.assertEqual(status, 409)
        status, body = self.call("POST", f"/api/sessions/{sid}/follow-up", {"question": "x"})
        self.assertEqual(status, 409)
        status, body = self.call("GET", "/api/sessions/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
