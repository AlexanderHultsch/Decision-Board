#!/usr/bin/env python3
"""Tests for the ``projects`` property (spec section 5, decided 9 September
2026): a vault that serves several projects, one active project per
configuration, common pages shared by all."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard import board, knowledge  # noqa: E402
from decisionboard.agent.provider import AiProvider, AiResult  # noqa: E402
from _roles_fixture import make_roles  # noqa: E402


def _vault(tmp: Path) -> Path:
    vault = tmp / "vault"
    (vault / "Process").mkdir(parents=True)
    (vault / "Projects").mkdir()
    (vault / "KPIs").mkdir()
    (vault / "Process" / "VPDS.md").write_text(
        "---\nkind: process\nupdated: 2026-09-09\n---\nThe gates of the process, common to every project.\n",
        encoding="utf-8")
    (vault / "Projects" / "Dual DCDC.md").write_text(
        "---\nkind: project\nprojects: [Dual DCDC]\n---\nThe Dual DCDC project and its gates.\n", encoding="utf-8")
    (vault / "Projects" / "Sister.md").write_text(
        "---\nkind: project\nprojects:\n  - Sister\n---\nThe Sister project and its gates.\n", encoding="utf-8")
    (vault / "Projects" / "Shared supplier.md").write_text(
        "---\nkind: note\nproject: Dual DCDC, Sister\n---\nA supplier both projects use for the gates.\n",
        encoding="utf-8")
    (vault / "KPIs" / "Dual DCDC - Maturity Gates.md").write_text(
        "---\nkind: kpi\nprojects: [Dual DCDC]\naffected_swimlanes: [Hardware]\nupdated: 2026-09-01\n---\n"
        "| KPI | MG0 |\n|---|---|\n| cBOM | 100 |\n", encoding="utf-8")
    (vault / "KPIs" / "Sister - Maturity Gates.md").write_text(
        "---\nkind: kpi\nprojects: [Sister]\naffected_swimlanes: [Hardware]\nupdated: 2026-09-01\n---\n"
        "| KPI | MG0 |\n|---|---|\n| cBOM | 999 |\n", encoding="utf-8")
    return vault


class TestProjectsProperty(unittest.TestCase):
    def test_projects_are_read_in_every_yaml_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = {n.title: n for n in knowledge.load_vault(_vault(Path(tmp)))}
        self.assertEqual(notes["Dual DCDC"].projects, ("Dual DCDC",))
        self.assertEqual(notes["Sister"].projects, ("Sister",))
        self.assertEqual(notes["Shared supplier"].projects, ("Dual DCDC", "Sister"))
        self.assertEqual(notes["VPDS"].projects, ())

    def test_for_project_keeps_common_pages_and_drops_the_other_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
            kept = sorted(n.title for n in knowledge.for_project(notes, "dual dcdc"))
        self.assertEqual(kept, ["Dual DCDC", "Dual DCDC - Maturity Gates", "Shared supplier", "VPDS"])

    def test_several_projects_keep_the_pages_of_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
            kept = sorted(n.title for n in knowledge.for_project(notes, ["Dual DCDC", "Sister"]))
            self.assertEqual(kept, ["Dual DCDC", "Dual DCDC - Maturity Gates", "Shared supplier", "Sister", "Sister - Maturity Gates", "VPDS"])
            self.assertEqual(sorted(n.title for n in knowledge.for_project(notes, "Dual DCDC, Sister")), kept)
        self.assertEqual(knowledge.active_projects({"knowledge": {"project": "Dual DCDC; Sister"}}), ["Dual DCDC", "Sister"])
        self.assertEqual(knowledge.active_projects({"knowledge": {"project": ["A", " B "]}}), ["A", "B"])
        self.assertEqual(knowledge.active_project({"knowledge": {"project": ["A", "B"]}}), "A, B")

    def test_no_active_project_keeps_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
            self.assertEqual(len(knowledge.for_project(notes, None)), len(notes))
            self.assertEqual(len(knowledge.for_project(notes, "  ")), len(notes))

    def test_project_names_come_from_project_pages_and_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _vault(Path(tmp))
            notes = knowledge.load_vault(vault)
            self.assertEqual(knowledge.project_names(notes), ["Dual DCDC", "Sister"])
            config = {"knowledge": {"vault_path": str(vault)}}
            self.assertEqual(knowledge.list_projects(config), ["Dual DCDC", "Sister"])
        self.assertEqual(knowledge.list_projects({"knowledge": {"vault_path": ""}}), [])
        self.assertEqual(knowledge.list_projects({"knowledge": {"vault_path": "/nowhere/at/all"}}), [])


class TestProjectFilterInGatherAndKpi(unittest.TestCase):
    def test_gather_leaves_the_other_projects_pages_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"knowledge": {"vault_path": str(_vault(Path(tmp))), "project": "Dual DCDC"}}
            selection = knowledge.gather(config, "what about the gates of the project")
        self.assertEqual(selection.project, "Dual DCDC")
        self.assertIn("Projects/Dual DCDC.md", selection.relative_paths)
        self.assertIn("Process/VPDS.md", selection.relative_paths)
        self.assertNotIn("Projects/Sister.md", selection.relative_paths)

    def test_kpi_notes_of_the_other_project_never_reach_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"knowledge": {"vault_path": str(_vault(Path(tmp))), "project": "Dual DCDC"}}
            data = knowledge.kpi_notes(config, ["Hardware"])
            self.assertIn("Dual DCDC - Maturity Gates", data["Hardware"])
            self.assertNotIn("Sister - Maturity Gates", data["Hardware"])
            config["knowledge"]["project"] = ""
            both = knowledge.kpi_notes(config, ["Hardware"])
        self.assertIn("Sister - Maturity Gates", both["Hardware"])


class _Provider(AiProvider):
    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, task, prompt, **kwargs) -> AiResult:
        self.prompts.append(prompt)
        text = ('{"view": "v", "risks": ["r"], "recommendation": "rec"}' if "## Member" in prompt
                else '{"consensus": "c", "disagreements": [], "recommendation": "x", "next_steps": []}')
        return AiResult(text=text, provider="fake", model="m", input_tokens=1, output_tokens=1)


class TestProjectInThePrompt(unittest.TestCase):
    def test_member_prompts_name_the_active_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            roles_dir = make_roles(Path(tmp) / "roles")
            config = {"provider": {"models": {"board": "fake/m"}},
                      "knowledge": {"vault_path": str(_vault(Path(tmp))), "project": "Dual DCDC",
                                    "roles_folder": str(roles_dir)}}
            provider = _Provider()
            board.run_board(config, provider, topic="Gates", context="", options=(), constraints=())
        member_prompts = [p for p in provider.prompts if "## Member" in p]
        self.assertTrue(member_prompts)
        for prompt in member_prompts:
            self.assertIn("Project: Dual DCDC.", prompt)

    def test_no_project_means_no_project_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            roles_dir = make_roles(Path(tmp) / "roles")
            config = {"provider": {"models": {"board": "fake/m"}},
                      "knowledge": {"vault_path": str(_vault(Path(tmp))), "roles_folder": str(roles_dir)}}
            provider = _Provider()
            board.run_board(config, provider, topic="Gates", context="", options=(), constraints=())
        for prompt in provider.prompts:
            self.assertNotIn("Project: ", prompt)


if __name__ == "__main__":
    unittest.main()
