#!/usr/bin/env python3
"""Tests for the OpenCode client's command line (OC-7): optional flags are
passed only when the installed version lists them, and an over-long prompt
is refused on Windows before CreateProcess refuses it."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
from _subprocess_fake import patch_subprocess  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "src"))

from programmind.ai import opencode_client  # noqa: E402
from programmind.ai.opencode_client import OpenCodeError, OpenCodeProvider  # noqa: E402

CONFIG = {"provider": {"models": {"board": "opencode/big-pickle"}, "opencode": {"auto_approve": True}}}
OLD_HELP = "opencode run [message..] Options: -h --help -m, --model --format --file --title --attach"
NEW_HELP = OLD_HELP + " --auto --dir"


def _probe(help_text: str):
    def fake_run(command, **kwargs):
        assert command[1:] == ["run", "--help"], command
        return mock.Mock(stdout=help_text, stderr="", returncode=0)
    return fake_run


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        opencode_client._FLAGS.clear()      # the flag probe is cached per process

    def test_auto_is_not_passed_when_the_installed_version_does_not_list_it(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with patch_subprocess(_probe(OLD_HELP)):
            command = provider._build_command("opencode/big-pickle")
        self.assertEqual(command, ["opencode", "run", "--format", "json", "--model", "opencode/big-pickle", opencode_client.PROMPT_HEADER])

    def test_auto_and_dir_are_passed_when_listed(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with patch_subprocess(_probe(NEW_HELP)):
            command = provider._build_command("opencode/big-pickle")
        self.assertEqual(command, ["opencode", "run", "--format", "json", "--model", "opencode/big-pickle",
                                   "--dir", "/repo", "--auto", opencode_client.PROMPT_HEADER])

    def test_auto_approve_false_never_passes_auto(self):
        config = {"provider": {"models": {"board": "x/y"}, "opencode": {"auto_approve": False}}}
        provider = OpenCodeProvider(config)
        with patch_subprocess(_probe(NEW_HELP)):
            command = provider._build_command("x/y")
        self.assertNotIn("--auto", command)

    def test_help_is_probed_once_per_provider(self):
        provider = OpenCodeProvider(CONFIG)
        probe = mock.Mock(side_effect=_probe(NEW_HELP))
        with patch_subprocess(probe):
            provider._build_command("x/y")
            provider._build_command("x/y")
        self.assertEqual(probe.call_count, 1)

    def test_a_failed_probe_means_no_optional_flags_not_a_failed_run(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with patch_subprocess(side_effect=OSError("no binary")):
            command = provider._build_command("x/y")
        self.assertNotIn("--auto", command)
        self.assertNotIn("--dir", command)

    def test_the_prompt_travels_on_stdin_so_windows_has_no_length_limit(self):
        """OC-10: a 33,033-character board call failed on Windows on 9
        September 2026 because the prompt was a command-line argument."""
        provider = OpenCodeProvider(CONFIG)
        prompt = "x" * 40000
        seen = {}

        def fake_run(command, **kwargs):
            if command[1:] == ["run", "--help"]:
                return mock.Mock(stdout=OLD_HELP, stderr="", returncode=0)
            seen["command"] = command
            seen["input"] = kwargs.get("input")
            return mock.Mock(stdout='{"type":"text","part":{"text":"OK"}}\n', stderr="", returncode=0)

        with patch_subprocess(fake_run):
            provider.complete("ai_board", prompt)
        self.assertEqual(seen["input"], prompt)
        self.assertNotIn(prompt, seen["command"])
        self.assertEqual(seen["command"][-1], opencode_client.PROMPT_HEADER)
        self.assertLess(sum(len(part) for part in seen["command"]), 1000)




class TestEnvironment(unittest.TestCase):
    def setUp(self):
        opencode_client._FLAGS.clear()      # the flag probe is cached per process

    def test_config_file_becomes_opencode_config_in_the_subprocess_environment(self):
        config = {"provider": {"models": {"board": "azure/Opencode-Kimi-K2.7"},
                               "opencode": {"config_file": "C:/Users/me/Opencode/opencode.json"}}}
        env = opencode_client.opencode_environment(config)
        self.assertEqual(env["OPENCODE_CONFIG"], str(Path("C:/Users/me/Opencode/opencode.json")))
        self.assertNotIn("OPENCODE_CONFIG", opencode_client.opencode_environment({}) if "OPENCODE_CONFIG" not in
                         __import__("os").environ else {})

    def test_the_environment_is_passed_to_every_opencode_call(self):
        config = {"provider": {"models": {"board": "azure/m"}, "opencode": {"config_file": "/x/opencode.json"}}}
        provider = OpenCodeProvider(config)
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs.get("env", {}).get("OPENCODE_CONFIG"))
            if command[1:] == ["run", "--help"]:
                return mock.Mock(stdout="", stderr="", returncode=0)
            return mock.Mock(stdout='{"type":"text","part":{"text":"OK"}}\n', stderr="", returncode=0)

        with patch_subprocess(fake_run):
            provider.complete("ai_board", "hello")
        self.assertEqual(seen, [str(Path("/x/opencode.json"))] * 2)


class TestNoAnswerDiagnostics(unittest.TestCase):
    """OC-9: a run that exits cleanly with no answer must say what it did
    instead, not just that there was nothing."""

    def _provider(self, audit=None):
        config = {"provider": {"models": {"board": "azure/m"}}}
        if audit:
            config["runtime"] = {"audit_folder": str(audit)}
        return OpenCodeProvider(config)

    def test_error_event_on_a_zero_exit_run_is_reported(self):
        stdout = "\n".join([
            json.dumps({"type": "step_start"}),
            json.dumps({"type": "error", "error": {"data": {"message": "context length exceeded"}}}),
        ])
        with self.assertRaises(OpenCodeError) as raised:
            self._provider()._parse_output(stdout)
        message = str(raised.exception)
        self.assertIn("context length exceeded", message)
        self.assertIn("step_start x1", message)

    def test_tool_calls_instead_of_an_answer_are_named_with_the_way_out(self):
        stdout = json.dumps({"type": "tool", "part": {"type": "tool", "tool": "read"}})
        with self.assertRaises(OpenCodeError) as raised:
            self._provider()._parse_output(stdout)
        self.assertIn("read", str(raised.exception))
        self.assertIn("extra_args", str(raised.exception))

    def test_raw_output_is_saved_next_to_the_audit_trail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OpenCodeError) as raised:
                self._provider(tmp)._parse_output(json.dumps({"type": "step_finish", "part": {}}))
            files = list((Path(tmp) / "opencode-debug").glob("run-*.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertIn(str(files[0]), str(raised.exception))

    def test_a_text_part_in_another_event_shape_still_counts_as_the_answer(self):
        stdout = json.dumps({"type": "message.part.updated", "part": {"type": "text", "text": "hello"}})
        text, _, _ = self._provider()._parse_output(stdout)
        self.assertEqual(text, "hello")

    def test_extra_args_are_appended_before_the_prompt(self):
        config = {"provider": {"models": {"board": "azure/m"},
                               "opencode": {"auto_approve": False, "extra_args": ["--agent", "plan"]}}}
        provider = OpenCodeProvider(config)
        with patch_subprocess(_probe(OLD_HELP)):
            command = provider._build_command("azure/m")
        self.assertEqual(command[-3:], ["--agent", "plan", opencode_client.PROMPT_HEADER])


class TestEmptyAnswerRetry(unittest.TestCase):
    """OC-11: a run that exits cleanly with reasoning but no text is retried
    once (9 September 2026: 920 reasoning tokens, 0 output tokens, reason
    stop, and the session died on it)."""

    EMPTY = (json.dumps({"type": "step_start", "part": {}}) + "\n"
             + json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 10481, "output": 0, "reasoning": 920}}}) + "\n")
    GOOD = json.dumps({"type": "text", "part": {"text": "{\"ok\": true}"}}) + "\n"

    def _provider(self, outputs):
        provider = OpenCodeProvider({"provider": {"models": {"board": "azure/m"}}})
        calls = []

        def fake_run(command, **kwargs):
            if command[1:] == ["run", "--help"]:
                return mock.Mock(stdout="", stderr="", returncode=0)
            calls.append(kwargs.get("input"))
            return mock.Mock(stdout=outputs.pop(0), stderr="", returncode=0)
        return provider, fake_run, calls

    def test_an_empty_run_is_retried_once_with_a_nudge(self):
        provider, fake_run, calls = self._provider([self.EMPTY, self.GOOD])
        with patch_subprocess(fake_run):
            result = provider.complete("ai_board", "the prompt")
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], "the prompt")
        self.assertTrue(calls[1].startswith(opencode_client.EMPTY_RETRY_PREFIX))
        self.assertTrue(calls[1].endswith("the prompt"))

    def test_two_empty_runs_are_reported_with_both_attempts_and_the_token_story(self):
        provider, fake_run, calls = self._provider([self.EMPTY, self.EMPTY])
        with patch_subprocess(fake_run):
            with self.assertRaises(OpenCodeError) as raised:
                provider.complete("ai_board", "the prompt")
        message = str(raised.exception)
        self.assertEqual(len(calls), 2)
        self.assertIn("this was the retry", message)
        self.assertIn("920 reasoning tokens, 0 output tokens", message)
        self.assertIn("thought and wrote nothing", message)

    def test_other_failures_are_not_retried(self):
        provider, fake_run, calls = self._provider(["not json at all\n", self.GOOD])
        with patch_subprocess(fake_run):
            with self.assertRaises(OpenCodeError):
                provider.complete("ai_board", "the prompt")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
