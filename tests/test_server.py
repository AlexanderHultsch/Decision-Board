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
from _roles_fixture import make_roles  # noqa: E402

CLARIFIER = json.dumps({"topic": "Rework or switch", "context": "SOP is fixed.", "options": ["Rework", "Switch"],
                        "constraints": ["SOP cannot move"], "questions": ["What is the budget?"]})
SYNTHESIS = json.dumps({"overall_recommendation": "Rework", "decisive_criterion": "Time", "counter_arguments": ["Cost"],
                        "what_would_change_it": "A quote", "disagreements": ["Finance vs Manufacturing"]})
PROPOSAL = json.dumps({"path": "Decisions/Rework.md", "title": "Rework decision", "tags": ["tooling"], "body": "Decided: rework."})


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
            text = CLARIFIER
        elif "## Vault outline" in prompt:
            text = PROPOSAL
        elif "## New question" in prompt:
            text = "Because time is the decisive criterion."
        elif "## Assessments" in prompt:
            text = SYNTHESIS
        elif "Member: " in prompt:
            member = prompt.split("Member: ", 1)[1].splitlines()[0]
            text = json.dumps({"view": f"{member} view", "risks": ["r1"], "recommendation": "Rework"})
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
        self.assertEqual(state["phase"], "confirm")
        self.assertIn("Q: What is the budget?\nA: 200k", state["inputs"]["context"])

        status, state = self.call("POST", f"/api/sessions/{sid}/run", {
            "topic": "Rework or switch (edited)", "context": state["inputs"]["context"],
            "options": ["Rework", "Switch"], "constraints": ["SOP cannot move"]})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: s["phase"] == "result")
        self.assertEqual(state["result"]["topic"], "Rework or switch (edited)")
        self.assertEqual(len(state["result"]["assessments"]), 6)
        self.assertEqual(state["result"]["synthesis_data"]["overall_recommendation"], "Rework")
        self.assertTrue(all(v == "done" for v in state["members"].values()))
        self.assertEqual(state["llm_calls"], 8)   # clarifier + six members + synthesis

        # Members received the knowledge and the clarification, never the clarifier's questions as a task.
        member_prompts = [p for p in self.provider.prompts[first_prompt:] if "Member: " in p]
        self.assertEqual(len(member_prompts), 6)
        self.assertTrue(all("Tooling is late." in p for p in member_prompts))
        self.assertTrue(all("A: 200k" in p for p in member_prompts))
        self.assertFalse(any("## Question from Alex" in p for p in member_prompts))

        status, state = self.call("POST", f"/api/sessions/{sid}/follow-up", {"question": "Why?"})
        self.assertEqual(status, 200)
        state = self.wait_for(sid, lambda s: not s["busy"])
        self.assertEqual(state["turns"][0]["answer"], "Because time is the decisive criterion.")
        self.assertEqual(state["llm_calls"], 9)

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
        self.call("POST", f"/api/sessions/{sid}/answers", {"answers": [""]})
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
