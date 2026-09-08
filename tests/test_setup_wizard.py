#!/usr/bin/env python3
"""Tests for the setup wizard and for how a failed ``opencode run`` is
described: OpenCode reports errors on stdout as JSON events, so the
message must come from there, not from an empty stderr."""

from __future__ import annotations

import io
import json
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
