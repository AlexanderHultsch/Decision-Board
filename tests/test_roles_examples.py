#!/usr/bin/env python3
"""The real example notes under roles/examples/ (a programme's swim-lane
roles and responsibilities) are the shape the parser has to read."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard import roles  # noqa: E402

EXAMPLES = REPO_ROOT / "roles" / "examples"


class TestRealExamples(unittest.TestCase):
    def test_every_example_file_is_one_member_named_after_the_file(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        self.assertEqual(sorted(board.profiles), sorted([
            "Configuration & Integration", "Finance", "Hardware", "KPI Check", "Manufacturing",
            "Mechanical", "Program Lead", "Software", "Systems",
        ]))
        self.assertEqual(board.skipped, [])

    def test_configuration_and_integration_has_its_own_heading_and_body(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        ci = board.profiles["Configuration & Integration"]
        manu = board.profiles["Manufacturing"]
        self.assertEqual(ci.title, "Project Manager Configuration & Integration")
        self.assertIn("GBC releases", ci.body)
        self.assertNotIn("GBC releases", manu.body)

    def test_every_role_carries_a_level_and_the_program_lead_is_level_one(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        for member, profile in board.profiles.items():
            self.assertTrue(profile.roles, member)
            self.assertNotIn("level:", profile.body, member)
        self.assertEqual(board.profiles["Program Lead"].level, 1)
        self.assertTrue(all(p.level == 2 for m, p in board.profiles.items() if m != "Program Lead"))
        self.assertEqual(board.profiles["Hardware"].roles[0].name, "Project Manager HW")

    def test_titles_perspectives_and_icons_come_from_the_files(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        hw = board.profiles["Hardware"]
        self.assertEqual(hw.title, "Project Manager HW")
        self.assertTrue(hw.perspective.startswith("Develop and maintain control over all the Program Management elements of HW Swim Lane"))
        self.assertLessEqual(len(hw.perspective), 200)
        self.assertEqual(hw.icon, "chip")
        self.assertEqual(board.profiles["Software"].icon, "code")
        self.assertEqual(board.profiles["Systems"].icon, "flask")
        self.assertEqual(board.profiles["Configuration & Integration"].icon, "layers")
        self.assertEqual(board.profiles["Program Lead"].icon, "chart")
        self.assertEqual(board.profiles["KPI Check"].icon, "target")
        self.assertEqual(board.profiles["Finance"].icon, "dollar")

    def test_old_style_and_new_style_notes_mix(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        self.assertIn("## Character", board.profiles["KPI Check"].body)
        self.assertIn("## Responsibilities", board.profiles["Systems"].body)

    def test_member_from_stem_strips_only_the_folder_prefix(self):
        self.assertEqual(roles._member_from_stem("R&R Hardware"), "Hardware")
        self.assertEqual(roles._member_from_stem("R&R Configuration & Integration"), "Configuration & Integration")
        self.assertEqual(roles._member_from_stem("Roles_Finance"), "Finance")
        self.assertEqual(roles._member_from_stem("KPI Check"), "KPI Check")
        self.assertEqual(roles._member_from_stem("Roles"), "Roles")


if __name__ == "__main__":
    unittest.main()
