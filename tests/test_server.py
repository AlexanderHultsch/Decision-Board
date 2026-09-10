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
from programmind.ai.provider import AiProvider, AiResult  # noqa: E402
from programmind.shell.server import create_http_server, site_name  # noqa: E402
from programmind.agents.board import clarify as clarify_mod  # noqa: E402
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
# Ask the vault (spec 10): one source that was sent, one that was not - Python must drop the second.
ASK = json.dumps({"answer": "- Tooling is late.",
                  "sources": [{"path": "Tooling.md", "heading": "", "why": "says so"},
                              {"path": "Invented.md", "heading": "", "why": "made up"}],
                  "gaps": ["the new date"], "decision_question": False})


class RoutingFakeProvider(AiProvider):
    """Answers by what the prompt is for - the markers each prompt builder
    puts in - so one provider serves the whole flow."""

    def __init__(self):
        self.prompts: list[str] = []
        self.lock = threading.Lock()
        self.pick_answer: str | None = None     # a fixed answer to the pick prompt, for the fallback test
        self.delay = 0.0                        # seconds every call sleeps, for the "busy" tests

    def complete(self, task: str, prompt: str) -> AiResult:
        with self.lock:
            self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        if "## Question to the vault" in prompt:
            text = ASK
        elif "## Candidate sections" in prompt:
            if self.pick_answer is not None:
                text = self.pick_answer
            else:
                members = [line[4:].strip() for line in prompt.splitlines() if line.startswith("### ")]
                ids = [line.split("- id: ", 1)[1].split(" | ", 1)[0] for line in prompt.splitlines() if line.startswith("- id: ")]
                text = json.dumps({"members": [{"member": m, "full": ids[:1], "brief": ids[1:2],
                                                "reasons": {i: f"{m} needs it" for i in ids[:2]}} for m in members]})
        elif "## Question from Alex" in prompt:
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
        cls.config = {"provider": {"models": {"board": "fake/m"}},
                      "knowledge": {"vault_path": str(cls.vault), "token_budget": 6000, "selection": "python"}}
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
        for path in ("/", "/board", "/board/abc123", "/ask", "/ask/abc123"):
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertIn(b"Program Mind", response.read())
        for name in ("shell.js", "style.css", "agents/board/board.js", "agents/ask/ask.js"):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/{name}", timeout=10) as response:
                self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/agents/board/../../shell/server.py", timeout=10)
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
        self.assertIn(state["phase"], ("clarifying", "confirm"))   # the clarifier looks again (and may already be done)
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


class TestKnowledgePick(TestServerFlow):
    """Spec 5.1: the AI-assisted pick on the confirm screen, its fallback,
    the shared core once in the combined form, the statistics row."""

    def _to_confirm(self, selection=None):
        _, state = self.call("POST", "/api/sessions", {"question": "Rework the tooling before MG4?"})
        sid = state["id"]
        self.wait_for(sid, lambda s: s["phase"] == "questions")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""], "final": True})
        self.wait_for(sid, lambda s: s["phase"] == "confirm")
        if selection:
            self.call("POST", f"/api/sessions/{sid}/pick", {"selection": selection})
        return sid

    def test_the_pick_runs_on_the_confirm_screen_and_leads_each_members_block(self):
        sid = self._to_confirm("ai")
        state = self.wait_for(sid, lambda s: s["pick_state"] in ("done", "failed"))
        self.assertEqual(state["pick_state"], "done", state["pick_error"])
        self.assertEqual(state["selection"], "ai")
        first = state["picks"][CLASSIC[0]]["full"][0]           # the fake picks the first candidate
        status, est = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": list(CLASSIC[:2]), "budget": 2000})
        self.assertEqual(est["calls"], 3)                        # the pick is made: two members plus the synthesis
        self.assertEqual(est["picked_by"][CLASSIC[0]], "model")
        self.assertEqual(est["reasons"][CLASSIC[0]][first], f"{CLASSIC[0]} needs it")
        self.assertTrue(any(s["reason"] for s in est["sections"][CLASSIC[0]]))
        self.assertIn("split", est)
        self.call("POST", f"/api/sessions/{sid}/run", {"topic": "Rework?", "members": list(CLASSIC[:2]), "budget": 2000, "mode": "combined"})
        state = self.wait_for(sid, lambda s: s["phase"] == "result")
        combined = [p for p in self.provider.prompts if "## Members to assess" in p][-1]
        self.assertEqual(combined.count("shared by every member"), 1 if "shared by every member" in combined else 0)
        self.assertIn(CLASSIC[0], state["knowledge_split"])
        steps = [c["step"] for c in state["stats"]["calls"]]
        self.assertIn("knowledge pick", steps)

    def test_a_bad_pick_keeps_the_python_ranking_and_says_so(self):
        self.provider.pick_answer = "{}"
        try:
            sid = self._to_confirm("ai")
            state = self.wait_for(sid, lambda s: s["pick_state"] in ("done", "failed"))
            self.assertEqual(state["pick_state"], "failed")
            self.assertIn("members", state["pick_error"])
            _, est = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": list(CLASSIC[:2]), "budget": 2000})
            self.assertEqual(est["picked_by"][CLASSIC[0]], "python")
            self.assertEqual(est["calls"], 3)                    # a failed pick is not redone: two members, synthesis
            self.assertIn("Tooling.md", est["members"][CLASSIC[0]])
        finally:
            self.provider.pick_answer = None

    def test_new_inputs_make_a_new_pick_and_a_running_pick_is_counted_once(self):
        sid = self._to_confirm("ai")
        state = self.wait_for(sid, lambda s: s["pick_state"] in ("done", "failed"))
        self.assertEqual(state["pick_state"], "done")
        picks_before = len([p for p in self.provider.prompts if "## Candidate sections" in p])
        _, est = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": list(CLASSIC[:2]), "budget": 2000})
        self.assertEqual(est["calls"], 3)                        # the pick is done: not counted again
        # Back to the questions, answer again: the confirm screen has new inputs and picks again.
        self.call("POST", f"/api/sessions/{sid}/back")
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": ["changed answer"], "final": True})
        state = self.wait_for(sid, lambda s: s["phase"] == "confirm" and s["pick_state"] in ("done", "failed"))
        self.assertEqual(state["pick_state"], "done")
        self.assertEqual(len([p for p in self.provider.prompts if "## Candidate sections" in p]), picks_before + 1)
        self.assertIn("changed answer", [p for p in self.provider.prompts if "## Candidate sections" in p][-1])

    def test_python_only_makes_no_pick_call(self):
        sid = self._to_confirm("python")
        _, state = self.call("GET", f"/api/sessions/{sid}")
        self.assertEqual(state["pick_state"], "idle")
        _, est = self.call("POST", f"/api/sessions/{sid}/estimate", {"members": list(CLASSIC[:2]), "budget": 2000})
        self.assertEqual(est["calls"], 3)
        self.assertNotIn("knowledge pick", [p["label"] for p in est["per_call"]])


class TestSiteName(unittest.TestCase):
    """Spec 9.5, decision 21: the site is named under .localhost, and only
    that one name is accepted next to localhost and 127.0.0.1."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = {"provider": {"models": {"board": "fake/m"}}, "knowledge": {"vault_path": ""},
                      "server": {"site_name": "ai"}}
        cls.httpd, cls.board_server = create_http_server(cls.config, None, port=0, provider=RoutingFakeProvider())
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def raw(self, method, path, headers, body=b""):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body or None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_site_name_is_cleaned_and_defaults_to_mind(self):
        self.assertEqual(site_name({}), "mind")          # spec 11.1, decision 1
        self.assertEqual(site_name({"server": {"site_name": "My Site!"}}), "mysite")
        self.assertEqual(site_name({"server": {"site_name": "!!"}}), "mind")

    def test_only_the_named_host_under_localhost_is_accepted(self):
        self.assertEqual(self.raw("GET", "/api/config", {"Host": f"ai.localhost:{self.port}"}), 200)
        self.assertEqual(self.raw("GET", "/api/config", {"Host": f"evil.localhost:{self.port}"}), 403)
        self.assertEqual(self.raw("GET", "/api/config", {"Host": f"ai.localhost:{self.port + 1}"}), 403)

    def test_only_the_named_origin_under_localhost_is_accepted(self):
        json_headers = {"Host": f"ai.localhost:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.raw("POST", "/api/sessions", {**json_headers, "Origin": f"http://ai.localhost:{self.port}"},
                                  b'{"question": "x"}'), 201)
        self.assertEqual(self.raw("POST", "/api/sessions", {**json_headers, "Origin": f"http://evil.localhost:{self.port}"},
                                  b'{"question": "x"}'), 403)


class TestAskThreads(unittest.TestCase):
    """Ask the vault (spec 10) through the API: threads, questions with
    sources checked by Python, the estimate, close with and without a
    note, delete, and a thread that survives a restart."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.vault = Path(cls.tmp.name) / "vault"
        cls.vault.mkdir()
        (cls.vault / "Tooling.md").write_text("---\ntitle: Tooling\ntags: [tooling]\n---\nTooling is late.\n", encoding="utf-8")
        make_roles(cls.vault / "Roles&Responsibilities")
        cls.threads = Path(cls.tmp.name) / "threads"
        cls.config_path = Path(cls.tmp.name) / "config.local.json"
        cls.config = {"provider": {"models": {"board": "fake/m"}},
                      "knowledge": {"vault_path": str(cls.vault), "token_budget": 6000, "selection": "python"},
                      "server": {"threads_folder": str(cls.threads)}, "ask": {"token_budget": 3000}}
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

    call = TestServerFlow.call
    wait_for = TestServerFlow.wait_for

    def _wait(self, thread_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, state = self.call("GET", f"/api/ask/{thread_id}")
            if not state["busy"] and state["phase"] not in ("asking", "proposing"):
                return state
            time.sleep(0.05)
        raise AssertionError("thread still busy")

    def test_a_new_thread_is_listed_and_answers_with_checked_sources(self):
        first_prompt = len(self.provider.prompts)
        status, created = self.call("POST", "/api/ask", {"projects": []})
        self.assertEqual(status, 201)
        self.assertEqual(created["kind"], "ask")
        self.assertEqual(created["status"], "open")
        self.assertEqual(created["budget"], 3000)
        self.assertEqual(created["turns"], [])
        _, listed = self.call("GET", "/api/ask")
        row = next(r for r in listed["threads"] if r["id"] == created["id"])
        self.assertEqual((row["questions"], row["title"]), (0, "New thread"))
        status, state = self.call("POST", f"/api/ask/{created['id']}/question", {"question": "Is the tooling late?"})
        self.assertEqual(status, 200)
        self.assertIn(state["phase"], ("asking", "idle"))
        state = self._wait(created["id"])
        self.assertEqual(len(state["turns"]), 1)
        turn = state["turns"][0]
        self.assertEqual(turn["answer"], "- Tooling is late.")
        self.assertEqual([s["path"] for s in turn["sources"]], ["Tooling.md"])   # the invented one is dropped
        self.assertEqual(turn["dropped"], 1)
        self.assertEqual(turn["gaps"], ["the new date"])
        self.assertEqual(turn["paths"], ["Tooling.md"])
        self.assertEqual([c["step"] for c in state["stats"]["calls"]], ["ask the vault"])
        self.assertEqual(state["llm_calls"], 1)
        prompt = [p for p in self.provider.prompts[first_prompt:] if "## Question to the vault" in p][0]
        self.assertIn("Tooling is late.", prompt)
        self.assertNotIn("## Earlier in this thread", prompt)
        # The second question carries the first turn.
        self.call("POST", f"/api/ask/{created['id']}/question", {"question": "And who fixes it?"})
        state = self._wait(created["id"])
        self.assertEqual(len(state["turns"]), 2)
        prompt = [p for p in self.provider.prompts[first_prompt:] if "## Question to the vault" in p][-1]
        self.assertIn("## Earlier in this thread", prompt)
        self.assertIn("Q: Is the tooling late?\nA: - Tooling is late.", prompt)
        _, listed = self.call("GET", "/api/ask")
        row = next(r for r in listed["threads"] if r["id"] == created["id"])
        self.assertEqual((row["questions"], row["title"]), (2, "Is the tooling late?"))
        # The estimate: one call, the sections it would receive.
        _, est = self.call("POST", f"/api/ask/{created['id']}/estimate", {"question": "x", "budget": 3000})
        self.assertEqual(est["calls"], 1)
        self.assertEqual(est["per_call"][0]["label"], "ask the vault")
        self.assertIn("Tooling.md", [s["path"] for s in est["sections"]])
        self.assertGreater(est["tokens_in"], 0)

    def test_a_busy_thread_refuses_a_second_question(self):
        self.provider.delay = 0.6
        try:
            _, created = self.call("POST", "/api/ask", {})
            self.call("POST", f"/api/ask/{created['id']}/question", {"question": "One?"})
            status, body = self.call("POST", f"/api/ask/{created['id']}/question", {"question": "Two?"})
            self.assertEqual(status, 409)
            self.assertIn("Wait", body["error"])
            status, _ = self.call("POST", f"/api/ask/{created['id']}/close", {"remember": False})
            self.assertEqual(status, 409)
        finally:
            self.provider.delay = 0.0
        self._wait(created["id"])

    def test_close_without_a_note_and_a_closed_thread_takes_no_question(self):
        _, created = self.call("POST", "/api/ask", {})
        status, _ = self.call("POST", f"/api/ask/{created['id']}/close", {"remember": False})
        self.assertEqual(status, 409)                       # nothing asked yet
        self.call("POST", f"/api/ask/{created['id']}/question", {"question": "Late?"})
        self._wait(created["id"])
        status, state = self.call("POST", f"/api/ask/{created['id']}/close", {"remember": False})
        self.assertEqual((status, state["status"]), (200, "closed"))
        status, body = self.call("POST", f"/api/ask/{created['id']}/question", {"question": "More?"})
        self.assertEqual(status, 409)
        self.assertIn("closed", body["error"])
        _, listed = self.call("GET", "/api/ask")
        self.assertEqual(next(r for r in listed["threads"] if r["id"] == created["id"])["status"], "closed")

    def test_close_with_a_note_proposes_and_writes_through_the_memory_step(self):
        _, created = self.call("POST", "/api/ask", {})
        self.call("POST", f"/api/ask/{created['id']}/question", {"question": "Late?"})
        self._wait(created["id"])
        status, state = self.call("POST", f"/api/ask/{created['id']}/close", {"remember": True})
        self.assertEqual(status, 200)
        self.assertIn(state["phase"], ("proposing", "proposal"))
        state = self._wait(created["id"])
        self.assertEqual(state["phase"], "proposal")
        self.assertTrue(state["proposal"]["path"])
        self.assertEqual(state["status"], "closed")
        status, state = self.call("POST", f"/api/ask/{created['id']}/memory", {"body": "# Note\n\nkept"})
        self.assertEqual(status, 200)
        self.assertEqual(state["phase"], "written")
        self.assertTrue(Path(state["written_path"]).exists())
        self.assertTrue(Path(state["written_path"]).resolve().is_relative_to(self.vault.resolve()))
        on_disk = json.loads((self.threads / f"{created['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["written_path"], state["written_path"])

    def test_delete_removes_the_file_and_a_thread_survives_a_restart(self):
        _, created = self.call("POST", "/api/ask", {})
        self.call("POST", f"/api/ask/{created['id']}/question", {"question": "Late?"})
        self._wait(created["id"])
        # A second server on the same config reads the thread from disk.
        httpd, _server = create_http_server(self.config, self.config_path, port=0, provider=self.provider)
        try:
            other = threading.Thread(target=httpd.serve_forever, daemon=True)
            other.start()
            request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/ask/{created['id']}")
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(len(data["turns"]), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
        status, body = self.call("DELETE", f"/api/ask/{created['id']}")
        self.assertEqual((status, body["deleted"]), (200, True))
        self.assertFalse((self.threads / f"{created['id']}.json").exists())
        status, _ = self.call("GET", f"/api/ask/{created['id']}")
        self.assertEqual(status, 404)
        status, _ = self.call("DELETE", f"/api/ask/{created['id']}")
        self.assertEqual(status, 404)

    def test_config_carries_the_site_name_the_ask_budget_and_the_vault_name(self):
        _, cfg = self.call("GET", "/api/config")
        self.assertEqual(cfg["site_name"], "mind")
        self.assertEqual(cfg["ask_budget"], 3000)
        self.assertEqual(cfg["vault_name"], "vault")


class TestShellStatus(unittest.TestCase):
    """Spec 11.1, decision 7: the three status checks, cheap; the test call
    only on request. Decision 5: the recent open work of every agent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "Tooling.md").write_text("---\ntitle: Tooling\n---\nTooling is late.\n", encoding="utf-8")
        self.config_path = Path(self.tmp.name) / "config.local.json"
        self.config = {"provider": {"models": {"board": "fake/m"}},
                       "knowledge": {"vault_path": str(self.vault), "selection": "python"},
                       "server": {"threads_folder": str(Path(self.tmp.name) / "threads")}}
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.provider = RoutingFakeProvider()
        self.httpd, self.board_server = create_http_server(self.config, self.config_path, port=0, provider=self.provider)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_the_vault_is_amber_without_project_pages_and_green_with_them_and_a_roles_folder(self):
        status, s = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(s["vault"]["state"], "amber")
        self.assertEqual(s["vault"]["notes"], 1)
        self.assertIn("no project pages", s["vault"]["detail"])
        self.assertIn("no roles folder", s["vault"]["detail"])
        self.assertEqual(s["project"]["state"], "red")            # no project pages at all
        (self.vault / "Dual DCDC.md").write_text(
            "---\nkind: project\nsummary: The Gen6 project.\n---\n# Dual DCDC\n\n| Gate | Date |\n|---|---|\n| MG3 | 12 March 2027 |\n| MG7 | SOP Aug 2028 |\n",
            encoding="utf-8")
        make_roles(self.vault / "Roles&Responsibilities")
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["vault"]["state"], "green")
        self.assertEqual(s["vault"]["projects"], 1)
        self.assertTrue(s["vault"]["roles_folder"].endswith("Roles&Responsibilities"))
        self.assertIsNotNone(s["vault"]["last_read"])
        self.assertEqual(s["project"]["state"], "amber")          # nothing chosen: all projects
        _, s = self.call("GET", "/api/status?projects=Dual%20DCDC")
        self.assertEqual(s["project"]["state"], "green")
        page = s["project"]["pages"][0]
        self.assertEqual(page["title"], "Dual DCDC")
        self.assertEqual(page["gates"], ["MG3 · 12 March 2027", "MG7 · SOP Aug 2028"])   # the gate baseline, from the page
        _, s = self.call("GET", "/api/status?projects=Nowhere")
        self.assertEqual(s["project"]["state"], "amber")
        self.assertIn("No project page for: Nowhere", s["project"]["detail"])

    def test_the_vault_is_red_when_unset_or_unreachable(self):
        self.config["knowledge"]["vault_path"] = ""
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["vault"]["state"], "red")
        self.config["knowledge"]["vault_path"] = str(self.vault / "gone")
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["vault"]["state"], "red")
        self.assertIn("not found", s["vault"]["detail"])

    def test_the_ai_check_is_cheap_and_the_test_call_settles_it(self):
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "amber")               # a model, no gateway file: unverified
        self.assertIsNone(s["ai"]["last_call"])
        self.config["provider"]["models"]["board"] = ""
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "red")
        self.config["provider"]["models"]["board"] = "fake/m"
        gateway = Path(self.tmp.name) / "opencode.json"
        gateway.write_text(json.dumps({"provider": {"azure": {"options": {"apiKey": "secret"}}}, "model": "azure/x"}), encoding="utf-8")
        self.config["provider"]["opencode"] = {"config_file": str(gateway)}
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "green")
        self.assertNotIn("secret", json.dumps(s))                 # the key never reaches the page
        gateway.write_text(json.dumps({"provider": {"azure": {"options": {}}}, "model": "azure/x"}), encoding="utf-8")
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "amber")
        before = len(self.provider.prompts)
        status, ai = self.call("POST", "/api/status/ai")             # the confirmed test call
        self.assertEqual(status, 200)
        self.assertEqual(len(self.provider.prompts), before + 1)      # exactly one call, never on a timer
        self.assertEqual(ai["state"], "green")
        self.assertTrue(ai["last_call"]["ok"])
        self.assertIn("answered in", ai["detail"])
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "green")                   # the settings are unchanged: the call still counts
        self.assertEqual(len(self.provider.prompts), before + 1)
        self.config["provider"]["models"]["board"] = "fake/other"
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["ai"]["state"], "amber")                   # new settings: back to the cheap look

    def test_a_failed_test_call_turns_the_icon_red(self):
        class Broken(AiProvider):
            def complete(self, task, prompt):
                raise RuntimeError("gateway said no")
        self.board_server._provider_override = Broken()
        _, ai = self.call("POST", "/api/status/ai")
        self.assertEqual(ai["state"], "red")
        self.assertIn("gateway said no", ai["detail"])

    def test_the_recent_work_lists_open_threads_and_topics_newest_first(self):
        _, created = self.call("POST", "/api/ask", {"projects": ["Dual DCDC"]})
        self.call("POST", f"/api/ask/{created['id']}/question", {"question": "What is late?"})
        time.sleep(0.05)
        _, session = self.call("POST", "/api/sessions", {"question": "Rework or switch?"})
        _, listed = self.call("GET", "/api/history")
        items = listed["items"]
        self.assertEqual([i["kind"] for i in items], ["board", "ask"])
        self.assertEqual(items[0]["title"], "Rework or switch?")
        self.assertEqual(items[0]["unit"], "call")
        self.assertEqual(items[1]["title"], "What is late?")
        self.assertEqual(items[1]["projects"], ["Dual DCDC"])
        self.assertEqual(items[1]["unit"], "question")
        self.call("POST", f"/api/ask/{created['id']}/close", {"remember": False})
        _, listed = self.call("GET", "/api/history?state=open")
        self.assertEqual([i["kind"] for i in listed["items"]], ["board"])
        _, listed = self.call("GET", "/api/history?state=closed")
        self.assertEqual([i["kind"] for i in listed["items"]], ["ask"])
        _, listed = self.call("GET", "/api/history?state=all")
        self.assertEqual(len(listed["items"]), 2)
        self.call("POST", f"/api/sessions/{session['id']}/abandon")
        _, listed = self.call("GET", "/api/history")
        self.assertEqual(listed["items"], [])
