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

sys.path.insert(0, str(REPO_ROOT / "tests"))
from decisionboard import setup_wizard  # noqa: E402
from _roles_fixture import make_roles  # noqa: E402
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
                                             run_test=True, out=out, roles=str(make_roles(Path(tmp) / "roles")))
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
            self.assertIn("shaped like a real board call", text)
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
                                             opencode_config=str(company), roles=str(make_roles(Path(tmp) / "roles")))
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
                wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=True, out=out,
                                             roles=str(make_roles(Path(tmp) / "roles")))
                code = wizard.run()
            text = out.getvalue()
            self.assertEqual(code, 0, text)
            self.assertEqual(calls["run"], 3)   # failed call, retry after the repair, board-shaped call
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
        self.assertIn("Nothing matches 'y'", out.getvalue())

    def test_a_long_list_is_filtered_by_typing_part_of_a_name(self):
        listed = "\n".join(f"openai/gpt-{i}" for i in range(40)) + "\nazure/Opencode-Kimi-K2.7\n"
        wizard, out = self._wizard(["kimi", "1"])
        self.assertEqual(wizard.choose_model("openai/gpt-0", [], listed), "azure/Opencode-Kimi-K2.7")
        text = out.getvalue()
        self.assertIn("26 more", text)                      # 41 listed, 15 shown
        self.assertIn("Models matching 'kimi'", text)

    def test_all_lists_every_model_and_numbers_then_reach_the_end(self):
        listed = "\n".join(f"openai/gpt-{i}" for i in range(40)) + "\n"
        wizard, out = self._wizard(["40", "all", "40"])
        self.assertEqual(wizard.choose_model("openai/gpt-0", [], listed), "openai/gpt-39")
        self.assertIn("40 is not in the list above (1 to 15)", out.getvalue())
        self.assertIn("All 40 models", out.getvalue())

    def test_the_company_file_is_tagged_in_the_list(self):
        wizard, out = self._wizard([""])
        wizard.choose_model("azure/Kimi", ["azure/Kimi"], "openai/gpt-5\n")
        self.assertIn("azure/Kimi   <- defined in opencode.json, current", out.getvalue())

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


class TestProfiles(unittest.TestCase):
    """The wizard has to serve a company machine, a private machine and any
    operating system - not only the one it was first written on."""

    def _run_wizard(self, tmp, profile, models="anthropic/claude-opus-5\nanthropic/claude-sonnet-5"):
        local = Path(tmp) / "config.local.json"
        out = io.StringIO()

        def fake_run(command, **kwargs):
            if command[1:] == ["models"]:
                return mock.Mock(stdout=models, stderr="", returncode=0)
            if command[1:] == ["auth", "list"]:
                return mock.Mock(stdout="anthropic  api key", stderr="", returncode=0)
            return mock.Mock(stdout="", stderr="", returncode=0)

        with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
             mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
             mock.patch.object(setup_wizard.subprocess, "run", fake_run):
            wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False,
                                         out=out, profile=profile)
            wizard.run()
        return json.loads(local.read_text(encoding="utf-8")), out.getvalue()

    def test_private_profile_needs_no_company_file_and_picks_a_listed_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, text = self._run_wizard(tmp, "private")
        self.assertEqual(config["setup"]["profile"], "private")
        self.assertEqual(config["provider"]["opencode"]["config_file"], "")
        self.assertEqual(config["provider"]["models"]["board"], "anthropic/claude-opus-5")
        self.assertIn("private account via OpenCode", config["provider"]["endpoint"])
        self.assertIn("Provider login", text)

    def test_company_profile_is_recorded_and_login_is_not_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            company = Path(tmp) / "opencode.json"
            company.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": "https://gw/v1", "apiKey": "k"},
                                                               "models": {"m1": {}}}}}), encoding="utf-8")
            local = Path(tmp) / "config.local.json"
            out = io.StringIO()
            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: "/bin/" + name), \
                 mock.patch.object(setup_wizard.subprocess, "run",
                                   lambda command, **kw: mock.Mock(stdout="", stderr="", returncode=0)), \
                 mock.patch.object(setup_wizard.urllib.request, "urlopen", side_effect=OSError("offline")):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False,
                                             out=out, opencode_config=str(company))
                wizard.run()
            config = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(config["setup"]["profile"], "company")
        self.assertEqual(config["provider"]["opencode"]["config_file"], str(company))
        self.assertEqual(config["provider"]["models"]["board"], "gw/m1")
        self.assertIn("carries the gateway key", out.getvalue())

    def test_a_stored_profile_is_the_default_next_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "config.local.json"
            local.write_text(json.dumps({"setup": {"profile": "company"}}), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False, out=out)
                wizard.opencode = None
                self.assertEqual(wizard.step_profile(setup_wizard._read_json(local)), "company")

    def test_install_hint_matches_the_platform(self):
        out = io.StringIO()
        wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False, out=out)
        for platform_name, expected in (("win32", "winget"), ("darwin", "brew"), ("linux", "curl")):
            with mock.patch.object(setup_wizard.sys, "platform", platform_name):
                self.assertIn(expected, " ".join(wizard.install_hint()))

    def test_the_shipped_example_config_names_no_company_and_no_person(self):
        text = (REPO_ROOT / "config" / "config.example.json").read_text(encoding="utf-8")
        config = json.loads(text)
        for word in ("visteon", "ahultsch", "onedrive", "big-pickle", "kimi", "c:/users"):
            self.assertNotIn(word, text.lower())
        self.assertEqual(config["provider"]["models"]["board"], "")
        self.assertEqual(config["knowledge"]["vault_path"], "")
        self.assertEqual(config["runtime"]["audit_folder"], "")


class TestFirstListedModel(unittest.TestCase):
    def test_a_strong_family_is_preferred_over_the_first_line(self):
        listed = "openai/gpt-3.5\nanthropic/claude-opus-5\nanthropic/claude-haiku-4-5"
        self.assertEqual(setup_wizard._first_listed_model(listed), "anthropic/claude-opus-5")

    def test_otherwise_the_first_model_shaped_line_wins(self):
        self.assertEqual(setup_wizard._first_listed_model("not a model\nvendor/thing"), "vendor/thing")

    def test_nothing_listed_gives_nothing(self):
        self.assertEqual(setup_wizard._first_listed_model(""), "")


class TestMovedFolders(unittest.TestCase):
    """A moved folder tree leaves every saved absolute path pointing at
    nothing; the wizard must forget those, not propose them."""

    def test_stale_paths_are_dropped_and_live_ones_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_vault = Path(tmp) / "AI" / "Obsidian"
            live_vault.mkdir(parents=True)
            live_file = Path(tmp) / "AI" / "OpenCode" / "opencode.json"
            live_file.parent.mkdir(parents=True)
            live_file.write_text("{}", encoding="utf-8")
            config = {
                "knowledge": {"vault_path": str(live_vault), "roles_folder": str(Path(tmp) / "old" / "Roles")},
                "provider": {"opencode": {"config_file": str(live_file)}},
            }
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault=None, model=None, run_test=False, out=out)
            wizard.drop_stale_paths(config)
        self.assertEqual(config["knowledge"]["vault_path"], str(live_vault))
        self.assertEqual(config["provider"]["opencode"]["config_file"], str(live_file))
        self.assertEqual(config["knowledge"]["roles_folder"], "")
        self.assertIn("roles folder no longer at", out.getvalue())

    def test_a_stale_audit_folder_moves_back_into_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "config.local.json"
            local.write_text(json.dumps({"runtime": {"audit_folder": "/gone/old/path/audit"},
                                         "provider": {"models": {"board": "x/y"}}}), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: None):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=False,
                                             out=out, profile="private")
                wizard.run()
            config = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(config["runtime"]["audit_folder"], str(setup_wizard.REPO_ROOT / "audit"))

    def test_a_stale_company_file_is_not_offered_as_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False,
                                         out=out, profile="company")
            with mock.patch.object(wizard, "find_opencode_config", lambda: ""):
                result = wizard.step_opencode_config(str(Path(tmp) / "gone" / "opencode.json"))
        self.assertEqual(result, "")
        self.assertTrue(any("without an opencode.json" in f for f in wizard.failures))


class TestConfigurationIsExplained(unittest.TestCase):
    """A fresh clone has no configuration; every entry point must name the
    one command that writes it, and re-running must not cost settings."""

    def test_a_missing_configuration_names_the_setup_command(self):
        import io as _io
        import contextlib
        from decisionboard import cli
        with tempfile.TemporaryDirectory() as tmp:
            stderr = _io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                cli.load_config(Path(tmp) / "config.local.json")
        self.assertEqual(raised.exception.code, 2)
        text = stderr.getvalue()
        self.assertIn("scripts", text)
        self.assertIn("setup.py", text)
        self.assertIn("No configuration yet", text)

    def test_broken_json_is_reported_rather_than_crashing(self):
        import io as _io
        import contextlib
        from decisionboard import cli
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.local.json"
            path.write_text("{not json", encoding="utf-8")
            stderr = _io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                cli.load_config(path)
        self.assertIn("not valid JSON", stderr.getvalue())

    def test_running_the_wizard_again_keeps_settings_and_backs_them_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "config.local.json"
            local.write_text(json.dumps({
                "provider": {"models": {"board": "azure/kept"}, "opencode": {"config_file": ""}},
                "knowledge": {"vault_path": "", "token_budget": 4242},
                "ui": {"theme": "dark"},
            }), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(setup_wizard, "LOCAL_CONFIG", local), \
                 mock.patch.object(setup_wizard.shutil, "which", lambda name: None):
                wizard = setup_wizard.Wizard(interactive=False, vault="", model=None, run_test=False,
                                             out=out, profile="private")
                wizard.run()
            config = json.loads(local.read_text(encoding="utf-8"))
            backup = json.loads((Path(tmp) / "config.local.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(config["provider"]["models"]["board"], "azure/kept")
        self.assertEqual(config["knowledge"]["token_budget"], 4242)
        self.assertEqual(config["ui"]["theme"], "dark")
        self.assertEqual(backup["knowledge"]["token_budget"], 4242)
        self.assertIn("only what you answer now changes", out.getvalue())


class TestOpenCodeConfigCheck(unittest.TestCase):
    """9 September 2026: Decision Board's own config.local.json was handed to
    OpenCode as OPENCODE_CONFIG. OpenCode refused it ("unrecognized keys:
    _comment, setup, storage, runtime, knowledge, ui") and the wizard blamed
    the model string."""

    def test_the_boards_own_config_is_refused_with_the_reason(self):
        from decisionboard.agent.opencode_client import opencode_config_problem
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "config.local.json"
            wrong.write_text(json.dumps({"setup": {"profile": "company"}, "knowledge": {}, "provider": {"models": {"board": "azure/x"}}}),
                             encoding="utf-8")
            problem = opencode_config_problem(str(wrong))
            right = Path(tmp) / "opencode.json"
            right.write_text(json.dumps({"$schema": "https://opencode.ai/config.json",
                                         "provider": {"azure": {"options": {"baseURL": "http://gw"}, "models": {"Kimi": {}}}}}),
                             encoding="utf-8")
            self.assertIsNone(opencode_config_problem(str(right)))
            empty = Path(tmp) / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            self.assertIn("defines no provider", opencode_config_problem(str(empty)))
        self.assertIn("Decision Board's own configuration", problem)
        self.assertIn("knowledge, setup", problem)
        self.assertIsNone(opencode_config_problem(""))
        self.assertIn("not found", opencode_config_problem("/nowhere/opencode.json"))

    def test_the_failure_text_and_the_diagnosis_name_the_configuration_file(self):
        from decisionboard.agent.opencode_client import describe_failure
        stdout = json.dumps({"type": "error", "error": {"message": "Config file is invalid: unrecognized keys: _comment, setup, storage"}})
        text = describe_failure(stdout, "")
        self.assertIn("unrecognized keys", text)
        self.assertIn("not at Decision Board's config.local.json", text)
        self.assertIn("rejected its configuration file", setup_wizard.diagnose(stdout, "")[0])

    def test_a_wrong_file_in_opencode_config_is_not_offered_as_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "config.local.json"
            wrong.write_text(json.dumps({"knowledge": {"vault_path": ""}, "provider": {}}), encoding="utf-8")
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault="", model="x/y", run_test=False, out=out)
            with mock.patch.dict(os.environ, {"OPENCODE_CONFIG": str(wrong)}):
                found = wizard.find_opencode_config()
        self.assertNotEqual(found, str(wrong))
        self.assertIn("not usable", out.getvalue())

    def test_the_wizard_refuses_the_wrong_file_and_offers_to_choose_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "config.local.json"
            wrong.write_text(json.dumps({"setup": {}, "provider": {}}), encoding="utf-8")
            right = Path(tmp) / "opencode.json"
            right.write_text(json.dumps({"provider": {"azure": {"models": {"Kimi": {}}}}}), encoding="utf-8")
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=True, vault="", model=None, run_test=False, out=out)
            answers = ["3", str(wrong), "3", str(right)]
            wizard.ask = lambda prompt, default="": answers.pop(0) if answers else default
            chosen = wizard._choose_company_file(str(right))
        self.assertEqual(chosen, str(right))
        self.assertIn("Decision Board's own configuration", out.getvalue())
        self.assertIn("Choose again", out.getvalue())
