#!/usr/bin/env python3
"""Tests for the OpenCode client's command line (OC-7): optional flags are
passed only when the installed version lists them, and an over-long prompt
is refused on Windows before CreateProcess refuses it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard.agent import opencode_client  # noqa: E402
from decisionboard.agent.opencode_client import OpenCodeError, OpenCodeProvider  # noqa: E402

CONFIG = {"provider": {"models": {"board": "opencode/big-pickle"}, "opencode": {"auto_approve": True}}}
OLD_HELP = "opencode run [message..] Options: -h --help -m, --model --format --file --title --attach"
NEW_HELP = OLD_HELP + " --auto --dir"


def _probe(help_text: str):
    def fake_run(command, **kwargs):
        assert command[1:] == ["run", "--help"], command
        return mock.Mock(stdout=help_text, stderr="", returncode=0)
    return fake_run


class TestBuildCommand(unittest.TestCase):
    def test_auto_is_not_passed_when_the_installed_version_does_not_list_it(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with mock.patch.object(opencode_client.subprocess, "run", _probe(OLD_HELP)):
            command = provider._build_command("opencode/big-pickle", "hello")
        self.assertEqual(command, ["opencode", "run", "--format", "json", "--model", "opencode/big-pickle", "hello"])

    def test_auto_and_dir_are_passed_when_listed(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with mock.patch.object(opencode_client.subprocess, "run", _probe(NEW_HELP)):
            command = provider._build_command("opencode/big-pickle", "hello")
        self.assertEqual(command, ["opencode", "run", "--format", "json", "--model", "opencode/big-pickle",
                                   "--dir", "/repo", "--auto", "hello"])

    def test_auto_approve_false_never_passes_auto(self):
        config = {"provider": {"models": {"board": "x/y"}, "opencode": {"auto_approve": False}}}
        provider = OpenCodeProvider(config)
        with mock.patch.object(opencode_client.subprocess, "run", _probe(NEW_HELP)):
            command = provider._build_command("x/y", "hello")
        self.assertNotIn("--auto", command)

    def test_help_is_probed_once_per_provider(self):
        provider = OpenCodeProvider(CONFIG)
        probe = mock.Mock(side_effect=_probe(NEW_HELP))
        with mock.patch.object(opencode_client.subprocess, "run", probe):
            provider._build_command("x/y", "a")
            provider._build_command("x/y", "b")
        self.assertEqual(probe.call_count, 1)

    def test_a_failed_probe_means_no_optional_flags_not_a_failed_run(self):
        provider = OpenCodeProvider(CONFIG, cwd="/repo")
        with mock.patch.object(opencode_client.subprocess, "run", side_effect=OSError("no binary")):
            command = provider._build_command("x/y", "hello")
        self.assertNotIn("--auto", command)
        self.assertNotIn("--dir", command)

    def test_an_over_long_prompt_is_refused_on_windows_with_a_useful_message(self):
        provider = OpenCodeProvider(CONFIG)
        with mock.patch.object(opencode_client.subprocess, "run", _probe(OLD_HELP)), \
             mock.patch.object(opencode_client.sys, "platform", "win32"):
            with self.assertRaises(OpenCodeError) as raised:
                provider._build_command("x/y", "x" * 31000)
        self.assertIn("token budget", str(raised.exception))
        with mock.patch.object(opencode_client.subprocess, "run", _probe(OLD_HELP)), \
             mock.patch.object(opencode_client.sys, "platform", "linux"):
            provider._supported = None
            provider._build_command("x/y", "x" * 31000)   # no limit elsewhere


if __name__ == "__main__":
    unittest.main()
