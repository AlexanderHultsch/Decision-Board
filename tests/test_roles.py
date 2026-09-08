#!/usr/bin/env python3
"""Tests for the role profiles (spec section 3.4): the board is whoever has
a profile in one folder, one file per role or one file with several, read
fresh on every run; each member sees only its own; the synthesis sees one
line per member; the shipped examples are the only fallback."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard import board, knowledge, roles, setup_wizard  # noqa: E402
from decisionboard.agent.provider import AiProvider, AiResult  # noqa: E402

CLASSIC = ("Finance", "HW Engineering", "Mechanical Engineering", "Manufacturing", "SW Engineering", "KPI Check")


def _single(folder: Path, member: str, body: str = "", **meta) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{key}: {value}" for key, value in {"member": member, **meta}.items())
    path = folder / f"{member}.md"
    path.write_text(f"---\n{front}\n---\n# {member}\n\n## Character\n{body or 'Profile of ' + member}.\n", encoding="utf-8")
    return path


class TestShippedExamples(unittest.TestCase):
    def test_no_folder_configured_gives_the_examples_in_their_declared_order(self):
        profiles = roles.load_roles({})
        self.assertEqual(tuple(profiles), CLASSIC)
        self.assertTrue(all(p.source == "built-in" for p in profiles.values()))
        for member, profile in profiles.items():
            self.assertTrue(profile.perspective, member)
            self.assertNotEqual(profile.icon, "person", member)
            for heading in ("## Character", "## Skills", "## KPIs", "## Vocabulary", "## How I assess", "## What I push back on"):
                self.assertIn(heading, profile.body, f"{member}: {heading}")

    def test_no_member_list_survives_in_the_code(self):
        self.assertFalse(hasattr(board, "BOARD_MEMBERS"))
        self.assertFalse(hasattr(roles, "MEMBERS"))
        prompt = (REPO_ROOT / "src/decisionboard/agent/prompts/board_members.md").read_text(encoding="utf-8")
        for name in CLASSIC:
            self.assertNotIn(name, prompt)


class TestParsing(unittest.TestCase):
    def test_one_file_per_role_with_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _single(Path(tmp), "Legal", "Contracts.", title="Legal counsel", perspective="Liability", icon="scale",
                           color="#123456", short="Law", order=2)
            profile = roles.parse_file(path, "configured")[0]
        self.assertEqual((profile.member, profile.title, profile.perspective, profile.icon, profile.color, profile.short, profile.order),
                         ("Legal", "Legal counsel", "Liability", "scale", "#123456", "Law", 2))
        self.assertTrue(profile.body.startswith("# Legal"))

    def test_a_file_without_front_matter_is_one_role_named_after_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Purchasing.md"
            path.write_text("# Purchasing\n\nBuys things.\n", encoding="utf-8")
            profile = roles.parse_file(path, "configured")[0]
        self.assertEqual(profile.member, "Purchasing")
        self.assertEqual(profile.perspective, "Buys things.")
        self.assertEqual(profile.icon, "truck")

    def test_one_file_with_several_roles_split_on_level_one_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Board.md"
            path.write_text(
                "# Finance\nperspective: Money\nicon: dollar\n\n## Character\nSober.\n\n"
                "# Legal\nperspective: Liability\n\n## Character\nCareful.\n\n"
                "# Customer\n\nSpeaks for the customer.\n", encoding="utf-8")
            profiles = roles.parse_file(path, "configured")
        self.assertEqual([p.member for p in profiles], ["Finance", "Legal", "Customer"])
        self.assertEqual(profiles[0].perspective, "Money")
        self.assertNotIn("perspective:", profiles[0].body)
        self.assertIn("Sober.", profiles[0].body)
        self.assertNotIn("Careful.", profiles[0].body)
        self.assertEqual(profiles[2].perspective, "Speaks for the customer.")
        self.assertEqual([p.order for p in profiles], [0, 1, 2])

    def test_front_matter_member_wins_over_headings_and_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Money person.md"
            path.write_text("---\nmember: Finance\n---\n# One\n\n# Two\n", encoding="utf-8")
            profiles = roles.parse_file(path, "configured")
        self.assertEqual([p.member for p in profiles], ["Finance"])


class TestLoadRoles(unittest.TestCase):
    def test_configured_folder_defines_the_board_whatever_the_vault_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            _single(vault / "Roles", "Finance")
            _single(vault / "Roles", "Legal")
            own = Path(tmp) / "MyBoard"
            _single(own, "Customer", order=2)
            _single(own, "Engineering", order=1)
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault), "roles_folder": str(own)}})
        self.assertEqual(list(profiles), ["Engineering", "Customer"])
        self.assertEqual(profiles["Customer"].source, "configured")
        self.assertEqual(roles.summary(profiles)["folder"], str(own))

    def test_roles_inside_the_vault_are_the_default_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            _single(vault / "Roles", "Finance")
            _single(vault / "Roles", "Legal")
            profiles = roles.load_roles({"knowledge": {"vault_path": str(vault)}})
        self.assertEqual(sorted(profiles), ["Finance", "Legal"])
        self.assertTrue(all(p.source == "vault" for p in profiles.values()))

    def test_fewer_than_two_members_is_an_error_not_a_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "one"
            _single(own, "Finance")
            with self.assertRaises(roles.RolesUnavailable) as raised:
                roles.load_roles({"knowledge": {"roles_folder": str(own)}})
            self.assertIn("at least 2", str(raised.exception))
            with self.assertRaises(roles.RolesUnavailable):
                roles.load_roles({"knowledge": {"roles_folder": str(Path(tmp) / "missing")}})

    def test_an_edit_is_read_on_the_next_load_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "r"
            path = _single(own, "Finance")
            _single(own, "Legal")
            config = {"knowledge": {"roles_folder": str(own)}}
            first = roles.load_roles(config)["Finance"].body
            path.write_text("---\nmember: Finance\n---\nChanged.\n", encoding="utf-8")
            second = roles.load_roles(config)["Finance"].body
        self.assertNotEqual(first, second)
        self.assertEqual(second, "Changed.")

    def test_colors_and_short_names_are_filled_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "r"
            _single(own, "Quality Assurance")
            _single(own, "Sales")
            profiles = roles.load_roles({"knowledge": {"roles_folder": str(own)}})
        self.assertTrue(all(p.color.startswith("#") for p in profiles.values()))
        self.assertEqual(profiles["Quality Assurance"].short, "Quality")
        self.assertEqual(profiles["Quality Assurance"].icon, "target")

    def test_install_examples_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "r"
            _single(own, "Finance", "Edited in Obsidian")
            written = roles.install_examples(own)
            kept = (own / "Finance.md").read_text(encoding="utf-8")
            members = roles.members_in(own)
        self.assertEqual(len(written), 5)
        self.assertIn("Edited in Obsidian", kept)
        self.assertEqual(len(members), 6)


class TestRolesInPrompts(unittest.TestCase):
    def test_each_member_gets_only_its_own_profile(self):
        profiles = roles.load_roles({})
        finance = board._member_prompt("t", "c", (), (), "Finance", profiles["Finance"])
        manu = board._member_prompt("t", "c", (), (), "Manufacturing", profiles["Manufacturing"])
        self.assertIn("## Role profile", finance)
        self.assertIn("cBOM", finance)
        self.assertNotIn("run-at-rate", finance.lower())
        self.assertIn("run-at-rate", manu.lower())

    def test_synthesis_gets_one_line_per_member_not_the_profiles(self):
        profiles = roles.load_roles({})
        prompt = board._synthesis_prompt([board.MemberAssessment("Finance", "v", "r", "rec")], profiles)
        self.assertIn("## Board members", prompt)
        self.assertIn("- Finance: Cost, budget vs forecast vs actuals, cBOM impact", prompt)
        self.assertNotIn("## Character", prompt)

    def test_run_board_takes_its_members_from_the_roles_folder(self):
        class Recorder(AiProvider):
            def __init__(self): self.prompts = []
            def complete(self, task, prompt):
                self.prompts.append(prompt)
                text = json.dumps({"overall_recommendation": "x", "decisive_criterion": "y",
                                   "counter_arguments": [], "what_would_change_it": "", "disagreements": []}) \
                    if "## Assessments" in prompt else json.dumps({"view": "v", "risks": "r", "recommendation": "rec"})
                return AiResult(text=text, provider="f", model="m", input_tokens=1, output_tokens=1, duration_seconds=0)
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / "r"
            _single(own, "Legal", order=1)
            _single(own, "Customer", order=2)
            _single(own, "Finance", order=3)
            config = {"provider": {"models": {"board": "f/m"}}, "knowledge": {"roles_folder": str(own)}}
            provider = Recorder()
            result = board.run_board(config, provider, topic="t")
        self.assertEqual([a.member for a in result.assessments], ["Legal", "Customer", "Finance"])
        self.assertEqual(result.llm_calls, 4)
        self.assertTrue(all("## Role profile" in p for p in provider.prompts if "Member: " in p))


class TestRolesAndKnowledge(unittest.TestCase):
    def test_a_roles_folder_inside_the_vault_is_not_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            _single(vault / "Roles", "Finance", "character tooling")
            _single(vault / "Roles", "Legal")
            (vault / "Some note.md").write_text("A note about tooling.\n", encoding="utf-8")
            selection = knowledge.gather({"knowledge": {"vault_path": str(vault)}}, "Finance character tooling")
        self.assertEqual(selection.relative_paths, ["Some note.md"])

    def test_a_configured_roles_folder_nested_deeper_in_the_vault_is_skipped_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            own = vault / "Meta" / "Board"
            _single(own, "Finance", "tooling")
            _single(own, "Legal")
            (vault / "Note.md").write_text("tooling\n", encoding="utf-8")
            selection = knowledge.gather({"knowledge": {"vault_path": str(vault), "roles_folder": str(own)}}, "tooling")
        self.assertEqual(selection.relative_paths, ["Note.md"])


class TestWizardRoles(unittest.TestCase):
    def test_wizard_creates_the_default_folder_with_the_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault=str(vault), model="x/y", run_test=False, out=out)
            wizard.check_vault(str(vault))
            count = len(list((vault / "Roles").glob("*.md")))
        self.assertEqual(count, 6)
        self.assertEqual(wizard.roles_folder, str(vault / "Roles"))
        self.assertIn("board of 6", out.getvalue())

    def test_wizard_accepts_another_folder_and_writes_nothing_when_it_has_a_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            own = Path(tmp) / "MyBoard"
            (own).mkdir()
            (own / "Board.md").write_text("# A\n\nAlpha.\n\n# B\n\nBeta.\n", encoding="utf-8")
            before = (own / "Board.md").read_text(encoding="utf-8")
            out = io.StringIO()
            wizard = setup_wizard.Wizard(interactive=False, vault=str(vault), model="x/y", run_test=False, out=out, roles=str(own))
            wizard.check_vault(str(vault))
            after = (own / "Board.md").read_text(encoding="utf-8")
            files = sorted(p.name for p in own.glob("*.md"))
        self.assertEqual(before, after)
        self.assertEqual(files, ["Board.md"])
        self.assertEqual(wizard.roles_folder, str(own))
        self.assertIn("board of 2", out.getvalue())


if __name__ == "__main__":
    unittest.main()
