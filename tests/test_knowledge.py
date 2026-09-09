#!/usr/bin/env python3
"""Tests for the knowledge source (spec section 5): vault loading, front
matter, deterministic note selection and the token budget."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from decisionboard import knowledge  # noqa: E402


def _vault(tmp: Path) -> Path:
    (tmp / ".obsidian").mkdir()
    (tmp / ".obsidian" / "workspace.md").write_text("ignored", encoding="utf-8")
    (tmp / "Suppliers").mkdir()
    (tmp / "Suppliers" / "Housing tooling.md").write_text(
        "---\ntitle: Housing tooling\ntags: [tooling, supplier]\n---\nThe housing tooling at supplier X is late.\n",
        encoding="utf-8",
    )
    (tmp / "Budget 2026.md").write_text(
        "---\ntitle: Budget 2026\ntags:\n  - finance\n---\nBudget is tight this year.\n", encoding="utf-8"
    )
    (tmp / "Unrelated.md").write_text("Nothing about anything.\n", encoding="utf-8")
    return tmp


class TestLoadVault(unittest.TestCase):
    def test_reads_every_note_and_skips_obsidian_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
        self.assertEqual(sorted(n.relative for n in notes),
                         ["Budget 2026.md", "Suppliers/Housing tooling.md", "Unrelated.md"])

    def test_front_matter_title_and_tags_in_both_yaml_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = {n.relative: n for n in knowledge.load_vault(_vault(Path(tmp)))}
        self.assertEqual(notes["Suppliers/Housing tooling.md"].tags, ("tooling", "supplier"))
        self.assertEqual(notes["Budget 2026.md"].tags, ("finance",))
        self.assertEqual(notes["Unrelated.md"].title, "Unrelated")

    def test_missing_folder_raises_knowledge_unavailable(self):
        with self.assertRaises(knowledge.KnowledgeUnavailable):
            knowledge.load_vault("/definitely/not/here")

    def test_a_file_instead_of_a_folder_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".md") as handle:
            with self.assertRaises(knowledge.KnowledgeUnavailable):
                knowledge.load_vault(handle.name)


class TestSelectNotes(unittest.TestCase):
    def test_everything_fits_so_everything_is_sent_best_match_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
            selection = knowledge.select_notes(notes, "Rework the housing tooling or switch supplier?", 6000)
        self.assertEqual(len(selection.notes), 3)
        self.assertEqual(selection.notes[0].relative, "Suppliers/Housing tooling.md")
        self.assertIn("### Suppliers/Housing tooling.md", selection.text)
        self.assertTrue(selection.text.startswith("## Knowledge from the vault"))

    def test_budget_limits_to_best_ranked_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(_vault(Path(tmp)))
            selection = knowledge.select_notes(notes, "housing tooling supplier", 60)
        self.assertEqual(selection.notes[0].relative, "Suppliers/Housing tooling.md")
        self.assertNotIn("Budget 2026.md", selection.relative_paths)
        self.assertLessEqual(selection.tokens, 60)
        self.assertFalse(selection.truncated)

    def test_a_note_too_large_for_the_budget_is_cut_to_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Big.md").write_text("tooling " * 2000, encoding="utf-8")
            notes = knowledge.load_vault(vault)
            selection = knowledge.select_notes(notes, "tooling", 500)
        self.assertEqual([n.relative for n in selection.notes], ["Big.md"])
        self.assertTrue(selection.truncated)
        self.assertIn("[... cut to fit the token budget]", selection.text)
        self.assertLessEqual(selection.tokens, 520)

    def test_empty_vault_gives_empty_text(self):
        selection = knowledge.select_notes([], "anything", 6000)
        self.assertEqual(selection.text, "")
        self.assertEqual(selection.total_notes, 0)

    def test_query_terms_drop_stopwords_and_short_words(self):
        self.assertEqual(knowledge.query_terms("Should we rework the housing or not?"), ["rework", "housing"])


class TestGather(unittest.TestCase):
    def test_no_source_configured_is_an_empty_selection_not_an_error(self):
        selection = knowledge.gather({}, "anything")
        self.assertIsNone(selection.vault_path)
        self.assertEqual(selection.text, "")

    def test_configured_source_is_read_with_the_configured_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            _vault(Path(tmp))
            selection = knowledge.gather({"knowledge": {"vault_path": tmp, "token_budget": 6000}}, "budget")
        self.assertEqual(selection.total_notes, 3)
        self.assertEqual(selection.notes[0].relative, "Budget 2026.md")

    def test_vault_outline_lists_folders_and_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            outline = knowledge.vault_outline(_vault(Path(tmp)))
        self.assertIn("- Suppliers/", outline)
        self.assertIn("- Budget 2026.md: Budget 2026", outline)


if __name__ == "__main__":
    unittest.main()


class TestSections(unittest.TestCase):
    """Decided 9 September 2026: the budget buys sections, not whole pages."""

    def _long_note(self, tmp: Path) -> Path:
        (tmp / "VPDS_Tasks.md").write_text(
            "---\nkind: process\n---\n# VPDS tasks\n\nIntro line about the process.\n\n"
            "## Change Management\n\n" + ("A change request goes to the CCB. " * 40) + "\n\n"
            "## Project Timing Plan\n\n" + ("The timing plan lives in Jira. " * 40) + "\n\n"
            "## Manufacturing readiness\n\n" + ("Fixtures and testers for the housing. " * 40) + "\n",
            encoding="utf-8")
        return tmp / "VPDS_Tasks.md"

    def test_a_long_note_is_split_at_its_headings_and_a_short_one_stays_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._long_note(Path(tmp))
            (Path(tmp) / "Short.md").write_text("# Short\n\nOne line.\n", encoding="utf-8")
            notes = {n.title: n for n in knowledge.load_vault(Path(tmp))}
            parts = knowledge.split_sections(notes["VPDS_Tasks"])
            self.assertEqual([p.heading for p in parts], ["VPDS tasks", "Change Management", "Project Timing Plan", "Manufacturing readiness"])
            self.assertTrue(parts[0].body.startswith("---"))       # the front matter stays with the opening part
            self.assertEqual(len(knowledge.split_sections(notes["Short"])), 1)

    def test_the_budget_buys_the_matching_section_not_the_whole_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._long_note(Path(tmp))
            notes = knowledge.load_vault(Path(tmp))
            selection = knowledge.select_sections(notes, "Can the timing plan absorb the change?", 400)
        self.assertEqual(len(selection.notes), 1)
        headings = [s.heading for s in selection.sections]
        self.assertIn("Project Timing Plan", headings)
        self.assertNotIn("Manufacturing readiness", headings)
        self.assertIn("### VPDS_Tasks.md - Project Timing Plan", selection.text)
        self.assertLessEqual(selection.tokens, 400)
        self.assertIn("timing plan lives in Jira", selection.sent["VPDS_Tasks.md"])
        self.assertNotIn("Fixtures and testers", selection.sent["VPDS_Tasks.md"])

    def test_a_member_block_is_ranked_by_the_members_own_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._long_note(Path(tmp))
            notes = knowledge.load_vault(Path(tmp))
            question = "Should we source now?"          # matches nothing in particular
            manufacturing = knowledge.select_sections(notes, question, 400, extra_terms=("fixtures", "testers", "housing"))
            timing = knowledge.select_sections(notes, question, 400, extra_terms=("timing", "jira"))
        self.assertIn("Manufacturing readiness", [s.heading for s in manufacturing.sections])
        self.assertNotIn("Project Timing Plan", [s.heading for s in manufacturing.sections])
        self.assertIn("Project Timing Plan", [s.heading for s in timing.sections])
        self.assertNotIn("Manufacturing readiness", [s.heading for s in timing.sections])

    def test_gather_for_members_reads_the_vault_once_per_call_and_pins_the_project_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._long_note(Path(tmp))
            (Path(tmp) / "Dual DCDC.md").write_text("---\nkind: project\nprojects: [Dual DCDC]\n---\n# Dual DCDC\n\nSOP Aug 2028.\n", encoding="utf-8")
            config = {"knowledge": {"vault_path": tmp, "token_budget": 400, "project": "Dual DCDC"}}
            blocks = knowledge.gather_for_members(config, "Should we source now?",
                                                  {"Manufacturing": ["fixtures", "testers"], "Finance": ["budget", "jira"]})
        self.assertEqual(sorted(blocks), ["Finance", "Manufacturing"])
        for block in blocks.values():
            self.assertEqual(block.notes[0].relative, "Dual DCDC.md")      # the project page comes first for everyone
        self.assertIn("Fixtures", blocks["Manufacturing"].text)
        self.assertNotIn("Fixtures", blocks["Finance"].text)
        self.assertEqual(knowledge.gather_for_members({}, "q", {"A": []})["A"].text, "")
