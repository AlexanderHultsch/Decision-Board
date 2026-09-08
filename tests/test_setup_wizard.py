#!/usr/bin/env python3
"""Tests for the setup wizard and for how a failed ``opencode run`` is
described: OpenCode reports errors on stdout as JSON events, so the
message must come from there, not from an empty stderr."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard import setup_wizard  # noqa: E402
from decisionboard.agent.opencode_client import describe_failure  # noqa: E402


class TestDescribeFailure(unittest.TestCase):
    def test_error_event_on_stdout_is_the_message(self):
        stdout = json.dumps({"type": "error", "error": {"name": "ProviderAuthError", "data": {"message": "not logged in"}}})
        self.assertIn("not logged in", describe_failure(stdout, ""))

    def test_stderr_is_still_used_when_present(self):
        self.assertIn("boom", describe_failure("", "boom"))

    def test_no_output_at_all_says_what_to_check(self):
        message = describe_failure("", "")
        self.assertIn("opencode auth list", message)
        self.assertIn("opencode models", message)

    def test_plain_text_on_stdout_is_shown(self):
        self.assertIn("Model not found", describe_failure("Model not found: x/y", ""))


class TestDiagnose(unittest.TestCase):
    def test_sqlite_migration_error_names_opencode_upgrade(self):
        lines = setup_wizard.diagnose("SQLiteError: no such column: replacement_seq", "")
        self.assertTrue(any("opencode upgrade" in line for line in lines))

    def test_auth_error_names_auth_login(self):
        lines = setup_wizard.diagnose(json.dumps({"type": "error", "error": {"name": "ProviderAuthError"}}), "")
        self.assertTrue(any("opencode auth login" in line for line in lines))

    def test_unknown_error_gives_the_generic_list(self):
        self.assertTrue(any("Likely causes" in line for line in setup_wizard.diagnose("", "")))


class TestWizard(unittest.TestCase):
    def _completed(self, stdout="", returncode=0, stderr=""):
        return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)

    def test_non_interactive_run_writes_config_and_reports_the_test_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "a.md").write_text("# a\n", encoding="utf-8")
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()

            def fake_run(command, **kwargs):
                if command[1:] == ["--version"]:
                    return self._completed("1.2.3")
                if command[1:] == ["run", "--help"]:
                    return self._completed("--format --model --file")
                if command[1:] == ["auth", "list"]:
                    return self._completed("opencode  logged in")
                if command[1:] == ["models"]:
                    return self._completed("opencode/big-pickle\nopencode/other")
                if command[1] == "run":
                    self.assertNotIn("--auto", command)
                    return self._completed(
                        json.dumps({"type": "text", "part": {"text": "OK"}}) + "\n"
                        + json.dumps({"type": "step_finish", "part": {"tokens": {"input": 8025, "output": 2}}}))
                raise AssertionError(command)

            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
                 mock.patch.object(setup_wizard.subprocess, "run", fake_run):
                wizard = setup_wizard.Wizard(interactive=False, vault=str(vault), model="opencode/big-pickle",
                                             run_test=True, out=out)
                code = wizard.run()
            text = out.getvalue()
            self.assertEqual(code, 0, text)
            config = json.loads(local.read_text(encoding="utf-8"))
            self.assertEqual(config["provider"]["models"]["board"], "opencode/big-pickle")
            self.assertEqual(config["knowledge"]["vault_path"], str(vault))
            self.assertEqual(config["knowledge"]["token_budget"], 6000)
            self.assertEqual(config["server"]["port"], 8765)
            self.assertIn("model answered", text)
            self.assertIn("8025 in / 2 out", text)
            self.assertIn("(1 notes)", text)

    def test_failed_test_call_dumps_stdout_and_stderr_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()

            def fake_run(command, **kwargs):
                if command[1] == "run" and command[2] != "--help":
                    return self._completed(json.dumps({"type": "error", "error": {"message": "invalid api key"}}), returncode=1)
                return self._completed("")

            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
                 mock.patch.object(setup_wizard.subprocess, "run", fake_run):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=True, out=out)
                code = wizard.run()
            text = out.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("invalid api key", text)
            self.assertIn("--- stdout", text)
            self.assertIn("opencode auth login", text)

    def test_missing_opencode_is_reported_and_test_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()
            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: None):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=True, out=out)
                code = wizard.run()
            self.assertEqual(code, 1)
            self.assertIn("opencode is not on PATH", out.getvalue())
            self.assertTrue(local.exists())


if __name__ == "__main__":
    unittest.main()


class TestCompanyOpencodeConfig(unittest.TestCase):
    def test_wizard_reads_the_company_file_and_uses_its_model_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            company = Path(tmp) / "opencode.json"
            company.write_text(json.dumps({
                "model": "Opencode-Kimi-K2.7",
                "provider": {"azure": {"npm": "@ai-sdk/openai-compatible",
                                       "options": {"baseURL": "http://litellm-ai.example.com/v1", "apiKey": "{env:LITELLM_KEY}"},
                                       "models": {"Opencode-Kimi-K2.7": {"name": "Opencode-Kimi-K2.7"}}}},
            }), encoding="utf-8")
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()
            seen_env = {}

            def fake_run(command, **kwargs):
                if command[1] == "run" and command[2] != "--help":
                    seen_env.update(kwargs.get("env") or {})
                    return mock.Mock(stdout='{"type":"text","part":{"text":"OK"}}', stderr="", returncode=0)
                if command[1:] == ["models"]:
                    return mock.Mock(stdout="azure/Opencode-Kimi-K2.7", stderr="", returncode=0)
                return mock.Mock(stdout="", stderr="", returncode=0)

            class FakeResponse:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return json.dumps({"data": [{"id": "Opencode-Kimi-K2.7"}, {"id": "claude-sonnet-5"}]}).encode()

            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
                 mock.patch.object(setup_wizard.subprocess, "run", fake_run), \
                 mock.patch.dict(setup_wizard.os.environ, {"LITELLM_KEY": "secret"}), \
                 mock.patch.object(setup_wizard.urllib.request, "urlopen", lambda req, timeout: FakeResponse()):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=True, out=out,
                                             opencode_config=str(company))
                code = wizard.run()
            text = out.getvalue()
            self.assertEqual(code, 0, text)
            config = json.loads(local.read_text(encoding="utf-8"))
            self.assertEqual(config["provider"]["models"]["board"], "azure/Opencode-Kimi-K2.7")
            self.assertEqual(config["provider"]["opencode"]["config_file"], str(company))
            self.assertEqual(seen_env.get("OPENCODE_CONFIG"), str(company))
            self.assertIn("model string: azure/Opencode-Kimi-K2.7", text)
            self.assertIn("plain http", text)
            self.assertIn("claude-sonnet-5", text)

    def test_placeholder_key_is_flagged_and_gateway_not_queried(self):
        with tempfile.TemporaryDirectory() as tmp:
            company = Path(tmp) / "opencode.json"
            company.write_text(json.dumps({"provider": {"azure": {"options": {"baseURL": "http://gw/v1", "apiKey": "xxx"},
                                                                   "models": {"m": {}}}}}), encoding="utf-8")
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False, out=out)
            with mock.patch.object(setup_wizard.urllib.request, "urlopen", side_effect=AssertionError("must not be called")):
                defined = wizard.describe_opencode_config(str(company))
            self.assertEqual(defined, ["azure/m"])
            self.assertIn("placeholder", out.getvalue())


class TestDatabaseRepair(unittest.TestCase):
    def test_database_files_are_renamed_and_the_call_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "opencode"
            data.mkdir()
            (data / "opencode.db").write_bytes(b"x")
            (data / "opencode.db-wal").write_bytes(b"x")
            (data / "auth.json").write_text("{}", encoding="utf-8")
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()
            calls = {"run": 0}

            def fake_run(command, **kwargs):
                if command[1] == "run" and command[2] != "--help":
                    calls["run"] += 1
                    if calls["run"] == 1:
                        return mock.Mock(stdout="SQLiteError: no such column: replacement_seq", stderr="", returncode=1)
                    return mock.Mock(stdout='{"type":"text","part":{"text":"OK"}}', stderr="", returncode=0)
                return mock.Mock(stdout="", stderr="", returncode=0)

            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
                 mock.patch.object(setup_wizard.subprocess, "run", fake_run), \
                 mock.patch.dict(setup_wizard.os.environ, {"XDG_DATA_HOME": tmp}):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=True, out=out)
                code = wizard.run()
            text = out.getvalue()
            self.assertEqual(code, 0, text)
            self.assertEqual(calls["run"], 2)
            self.assertFalse((data / "opencode.db").exists())
            self.assertTrue(any(p.name.startswith("opencode.db.") and p.name.endswith(".bak") for p in data.iterdir()))
            self.assertTrue((data / "auth.json").exists())
            self.assertIn("model answered", text)


class TestChooseModel(unittest.TestCase):
    def _wizard(self, answers):
        out = io.StringIO()
        wizard = setup_wizard.Wizard(interactive=True, vault="", model=None, run_test=False, out=out)
        wizard.ask = lambda prompt, default="": answers.pop(0) if answers else default
        return wizard, out

    def test_enter_keeps_the_current_model(self):
        wizard, _ = self._wizard([""])
        self.assertEqual(wizard.choose_model("azure/Kimi", ["azure/Kimi"], ""), "azure/Kimi")

    def test_a_number_picks_from_the_list(self):
        wizard, _ = self._wizard(["2"])
        self.assertEqual(wizard.choose_model("azure/Kimi", ["azure/Kimi", "azure/Other"], ""), "azure/Other")

    def test_y_is_rejected_and_asked_again(self):
        wizard, out = self._wizard(["y", ""])
        self.assertEqual(wizard.choose_model("azure/Kimi", ["azure/Kimi"], ""), "azure/Kimi")
        self.assertIn("not a model string", out.getvalue())

    def test_a_saved_invalid_model_is_replaced_by_the_defined_one(self):
        self.assertFalse(setup_wizard._looks_like_model_string("y"))
        self.assertTrue(setup_wizard._looks_like_model_string("azure/Opencode-Kimi-K2.7"))


class TestAllOnPath(unittest.TestCase):
    def test_windows_scan_uses_pathext_only_and_deduplicates_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"; b = Path(tmp) / "b"
            a.mkdir(); b.mkdir()
            (a / "opencode.EXE").write_bytes(b"x")   # PATHEXT casing; Windows matches either
            (b / "opencode").write_text("#!/bin/sh\n", encoding="utf-8")     # npm launcher, not runnable on Windows
            (b / "opencode.CMD").write_text("@echo off\n", encoding="utf-8")
            with mock.patch.dict(setup_wizard.os.environ, {"PATH": os.pathsep.join([str(a), str(b)]), "PATHEXT": ".EXE;.CMD"}), \
                 mock.patch.object(setup_wizard.sys, "platform", "win32"):
                found = setup_wizard._all_on_path("opencode")
        self.assertEqual(len(found), 2)
        self.assertTrue(all(Path(f).suffix.lower() in (".exe", ".cmd") for f in found))

    def test_a_probe_that_cannot_start_does_not_crash(self):
        out = io.StringIO()
        wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=False, out=out)
        with mock.patch.object(setup_wizard.subprocess, "run", side_effect=OSError(193, "not a valid Win32 application")):
            result = wizard._run(["opencode", "--version"], timeout=5)
        self.assertEqual(result.returncode, 126)
        self.assertIn("cannot be started", result.stderr)
