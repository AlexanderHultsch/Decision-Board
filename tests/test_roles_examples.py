#!/usr/bin/env python3
"""The real example notes under roles/examples/ (a programme's swim-lane
roles and responsibilities) are the shape the parser has to read."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from programmind.agents.board import roles  # noqa: E402

EXAMPLES = REPO_ROOT / "roles" / "examples"


class TestRealExamples(unittest.TestCase):
    def test_every_example_file_is_one_member_named_after_the_file(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        self.assertEqual(sorted(board.profiles), sorted([
            "Configuration & Integration", "Finance", "Hardware", "Manufacturing",
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

    def test_a_role_file_carries_level_only_and_the_rest_is_derived(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        for path in sorted(EXAMPLES.glob("*.md")):
            head = path.read_text(encoding="utf-8").split("## ", 1)[0]
            for key in ("icon:", "perspective:", "color:", "short:", "order:"):
                self.assertNotIn(key, head, f"{path.name}: {key}")
            self.assertIn("level:", head, path.name)
        hw = board.profiles["Hardware"]
        self.assertEqual(hw.title, "Project Manager HW")
        self.assertEqual(hw.icon, "chip")            # derived from the name
        # the synthesis line is what the member protects, not shared boilerplate
        self.assertEqual(hw.perspective, "Technical maturity and the evidence behind it.")
        seen = set()
        for member, profile in board.profiles.items():
            self.assertTrue(profile.perspective, member)
            self.assertNotIn("Develop and maintain control", profile.perspective, member)
            self.assertLessEqual(len(profile.perspective), 200, member)
            seen.add(profile.perspective)
        self.assertEqual(len(seen), len(board.profiles))
        self.assertEqual(hw.icon, "chip")
        self.assertEqual(board.profiles["Software"].icon, "code")
        self.assertEqual(board.profiles["Systems"].icon, "flask")
        self.assertEqual(board.profiles["Configuration & Integration"].icon, "layers")
        self.assertEqual(board.profiles["Program Lead"].icon, "chart")
        self.assertEqual(board.profiles["Finance"].icon, "dollar")

    def test_every_example_keeps_its_official_text_and_adds_the_board_sections(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        for member, profile in board.profiles.items():
            for heading in ("## Official description",
                            "## Targets I am judged on",
                            "## What I protect when I cannot have everything",
                            "## Process"):
                self.assertIn(heading, profile.body, f"{member}: {heading}")
            for gone in ("## How this lane usually fails",
                         "## What I decide alone",
                         "## Vocabulary",
                         "## Keywords"):
                self.assertNotIn(gone, profile.body, f"{member}: {gone}")
        # the official wording is untouched inside its section
        self.assertIn("VPDS Process Compliance", board.profiles["Hardware"].body)
        self.assertIn("Champions as Project Manager VPRS preliminary analysis",
                      board.profiles["Manufacturing"].body)

    def test_targets_name_the_kpis_and_point_at_the_network_never_a_number(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        for member, profile in board.profiles.items():
            section = profile.body.split("## Targets I am judged on")[1].split("## What I protect")[0]
            self.assertIn("MG0", section, member)
            self.assertIn(f"`affected_swimlanes: [{member}]`", section, member)
            for kpi in ("Resources", "Expenses", "Milestones"):
                self.assertIn(kpi, section, f"{member}: {kpi}")
            has_cbom = "cBOM" in section
            self.assertEqual(has_cbom, member in ("Hardware", "Mechanical", "Finance"), member)
            self.assertIsNone(__import__("re").search(r"\d{2,}\s?(k|%|EUR|€)", section), f"{member}: a number in a role file")

    def test_what_each_member_protects_is_different(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        protects = set()
        for profile in board.profiles.values():
            section = profile.body.split("## What I protect when I cannot have everything")[1]
            protects.add(section.split("##")[0].strip()[:80])
        self.assertEqual(len(protects), len(board.profiles))

    def test_there_is_no_kpi_member_kpis_belong_to_each_role(self):
        board = roles.load_board({"knowledge": {"roles_folder": str(EXAMPLES)}})
        self.assertNotIn("KPI Check", board.profiles)
        self.assertEqual(len(board.profiles), 8)

    def test_member_from_stem_strips_only_the_folder_prefix(self):
        self.assertEqual(roles._member_from_stem("R&R Hardware"), "Hardware")
        self.assertEqual(roles._member_from_stem("R&R Configuration & Integration"), "Configuration & Integration")
        self.assertEqual(roles._member_from_stem("Roles_Finance"), "Finance")
        self.assertEqual(roles._member_from_stem("KPI Check"), "KPI Check")
        self.assertEqual(roles._member_from_stem("Roles"), "Roles")


if __name__ == "__main__":
    unittest.main()
