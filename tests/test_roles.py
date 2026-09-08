#!/usr/bin/env python3
"""Tests for the role profiles (spec section 3.4): read from the vault on
every run, each member sees only its own, the synthesis sees one line per
member, the shipped examples fill any gap visibly."""

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

from decisionboard import board, knowledge, roles, setup_wizard  # noqa: E402
from decisionboard.agent.provider import AiProvider, AiResult  # noqa: E402


def _vault_with_roles(tmp: Path, members=("Finance",)) -> Path:
    vault = tmp / "vault"
    (vault / "Roles").mkdir(parents=True)
    for member in members:
        (vault / "Roles" / f"{member}.md").write_text(
            f"---\nmember: {member}\ntitle: {member} (ours)\nperspective: our own {member} line\n---\n"
            f"# {member}\n\n## Character\nEdited in Obsidian for {member}.\n", encoding="utf-8")
    (vault / "Some note.md").write_text("A note about tooling.\n", encoding="utf-8")
    return vault


class TestShippedExamples(unittest.TestCase):
    def test_the_repository_ships_one_example_per_member(self):
        files = roles._profile_files(roles.BUILTIN_DIR)
        self.assertEqual(sorted(files), sorted(roles.MEMBERS))
        for member, path in files.items():
            profile = roles._read_profile(path, member, "built-in")
            self.assertTrue(profile.perspective, member)
            for heading in ("## Character", "## Skills", "## KPIs", "## Vocabulary", "## How I assess", "## What I push back on"):
                self.assertIn(heading, profile.body, f"{member}: {heading}")

    def test_members_match_the_board(self):
        self.assertEqual(roles.MEMBERS, board.BOARD_MEMBERS)


class TestLoadRoles(unittest.TestCase):
    def test_no_vault_gives_six_built_in_profiles(self):
        profiles = roles.load_roles({})
        self.assertEqual(len(profiles), 6)
        self.assertTrue(all(p.source == "built-in" and p.body for p in profiles.values()))

    def test_vault_profiles_win_and_gaps_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp), ("Finance", "KPI Check"))
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault)}})
            info = roles.summary(profiles)
        self.assertEqual(profiles["Finance"].source, "vault")
        self.assertEqual(profiles["Finance"].title, "Finance (ours)")
        self.assertIn("Edited in Obsidian", profiles["Finance"].body)
        self.assertNotIn("---", profiles["Finance"].body.split("\n")[0])
        self.assertEqual(profiles["Manufacturing"].source, "built-in")
        self.assertEqual(sorted(info["from_vault"]), ["Finance", "KPI Check"])
        self.assertEqual(len(info["built_in"]), 4)
        self.assertTrue(info["folder"].endswith("Roles"))

    def test_front_matter_member_beats_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "Roles").mkdir(parents=True)
            (vault / "Roles" / "Money person.md").write_text("---\nmember: Finance\n---\nBody\n", encoding="utf-8")
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault)}})
        self.assertEqual(profiles["Finance"].source, "vault")
        self.assertEqual(profiles["Finance"].path.name, "Money person.md")

    def test_an_edit_is_read_on_the_next_load_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp))
            config = {"knowledge": {"vault_path": str(vault)}}
            first = roles.load_roles(config)["Finance"].body
            (vault / "Roles" / "Finance.md").write_text("---\nmember: Finance\n---\nChanged.\n", encoding="utf-8")
            second = roles.load_roles(config)["Finance"].body
        self.assertNotEqual(first, second)
        self.assertEqual(second, "Changed.")

    def test_install_examples_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp))
            written = roles.install_examples(vault / "Roles")
            kept = (vault / "Roles" / "Finance.md").read_text(encoding="utf-8")
        self.assertEqual(len(written), 5)
        self.assertNotIn("Finance.md", [p.name for p in written])
        self.assertIn("Edited in Obsidian", kept)


class TestRolesInPrompts(unittest.TestCase):
    def test_each_member_gets_only_its_own_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp), ("Finance", "Manufacturing"))
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault)}})
        finance = board._member_prompt("t", "c", (), (), "Finance", profiles["Finance"])
        manu = board._member_prompt("t", "c", (), (), "Manufacturing", profiles["Manufacturing"])
        self.assertIn("## Role profile", finance)
        self.assertIn("Edited in Obsidian for Finance", finance)
        self.assertNotIn("Manufacturing.", finance.split("## Role profile")[1].split("## Input")[0])
        self.assertIn("Edited in Obsidian for Manufacturing", manu)
        self.assertNotIn("Edited in Obsidian for Finance", manu)

    def test_synthesis_gets_one_line_per_member_not_the_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp))
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault)}})
        prompt = board._synthesis_prompt([board.MemberAssessment("Finance", "v", "r", "rec")], profiles)
        self.assertIn("## Board members", prompt)
        self.assertIn("- Finance (ours): our own Finance line", prompt)
        self.assertNotIn("Edited in Obsidian", prompt)

    def test_run_board_passes_profiles_into_every_member_call(self):
        class Recorder(AiProvider):
            def __init__(self): self.prompts = []
            def complete(self, task, prompt):
                self.prompts.append(prompt)
                text = json.dumps({"overall_recommendation": "x", "decisive_criterion": "y",
                                   "counter_arguments": [], "what_would_change_it": "", "disagreements": []}) \
                    if "## Assessments" in prompt else json.dumps({"view": "v", "risks": "r", "recommendation": "rec"})
                return AiResult(text=text, provider="f", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)
        profiles = roles.load_roles({})
        provider = Recorder()
        board.run_board({"provider": {"models": {"board": "f/m"}}}, provider, topic="t", roles=profiles)
        member_prompts = [p for p in provider.prompts if "Member: " in p]
        self.assertEqual(len(member_prompts), 6)
        self.assertTrue(all("## Role profile" in p for p in member_prompts))
        synthesis = [p for p in provider.prompts if "## Assessments" in p][0]
        self.assertIn("## Board members", synthesis)


class TestRolesAndKnowledge(unittest.TestCase):
    def test_the_roles_folder_is_not_part_of_the_knowledge_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp))
            selection = knowledge.gather({"knowledge": {"vault_path": str(vault)}}, "Finance character tooling")
        self.assertEqual(selection.relative_paths, ["Some note.md"])


class TestWizardRoles(unittest.TestCase):
    def test_wizard_creates_the_roles_folder_with_the_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault=str(vault), model="x/y", run_test=False, out=out)
            wizard.check_vault(str(vault))
            count = len(list((vault / "Roles").glob("*.md")))
        self.assertEqual(count, 6)
        self.assertIn("6 file(s) written", out.getvalue())

    def test_wizard_reports_a_complete_folder_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault_with_roles(Path(tmp), roles.MEMBERS)
            before = {p.name: p.read_text(encoding="utf-8") for p in (vault / "Roles").glob("*.md")}
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault=str(vault), model="x/y", run_test=False, out=out)
            wizard.check_vault(str(vault))
            after = {p.name: p.read_text(encoding="utf-8") for p in (vault / "Roles").glob("*.md")}
        self.assertEqual(before, after)
        self.assertIn("all six", out.getvalue())


if __name__ == "__main__":
    unittest.main()
