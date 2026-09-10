#!/usr/bin/env python3
"""Tests for the knowledge source (spec section 5): vault loading, front
matter, deterministic note selection and the token budget."""

from __future__ import annotations

import json
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


class TestVaultEdgeCases(unittest.TestCase):
    """Review of 9 September 2026: BOM, CRLF, code fences, symlinks, size."""

    def test_a_bom_does_not_void_the_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "K.md").write_text("\ufeff---\nkind: kpi\naffected_swimlanes: [Hardware]\n---\n# K\n", encoding="utf-8")
            note = knowledge.load_vault(Path(tmp))[0]
        self.assertEqual(note.kind, "kpi")
        self.assertEqual(note.member, ("Hardware",))

    def test_crlf_notes_keep_document_order_and_fences_do_not_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "# Top\r\n\r\n" + ("intro " * 250) + "\r\n\r\n## Second\r\n\r\n```python\r\n# not a heading\r\n```\r\n" + ("more " * 250) + "\r\n"
            (Path(tmp) / "N.md").write_bytes(body.encode("utf-8"))
            notes = knowledge.load_vault(Path(tmp))
            parts = knowledge.split_sections(notes[0])
            self.assertEqual([p.heading for p in parts], ["Top", "Second"])
            selection = knowledge.select_sections(notes, "more", 6000)
        self.assertEqual([s.heading for s in selection.sections], ["Top", "Second"])   # document order, not rank

    def test_symlinked_folders_and_oversized_notes_stay_out(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "secret.md").write_text("# secret\n", encoding="utf-8")
            try:
                (Path(tmp) / "Link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("no symlinks here")
            (Path(tmp) / "Big.md").write_text("x" * (knowledge.MAX_NOTE_BYTES + 1), encoding="utf-8")
            (Path(tmp) / "Ok.md").write_text("# ok\n", encoding="utf-8")
            names = [n.relative for n in knowledge.load_vault(Path(tmp))]
        self.assertEqual(names, ["Ok.md"])

    def test_the_cache_rereads_only_what_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "A.md"; a.write_text("# A one\n", encoding="utf-8")
            first = knowledge.load_vault(Path(tmp))[0]
            again = knowledge.load_vault(Path(tmp))[0]
            self.assertIs(first, again)                       # unchanged: the cached note
            import os, time
            a.write_text("# A two\n", encoding="utf-8")
            os.utime(a, (time.time() + 5, time.time() + 5))
            changed = knowledge.load_vault(Path(tmp))[0]
        self.assertIn("A two", changed.body)

    def test_manual_picks_are_capped_and_a_project_page_pins_by_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Huge.md").write_text("# Huge\n\n" + ("word " * 60000), encoding="utf-8")
            (Path(tmp) / "P.md").write_text("---\nkind: project\ntitle: Dual DCDC\n---\n# Dual DCDC\n\nSOP 2028.\n", encoding="utf-8")
            notes = knowledge.load_vault(Path(tmp))
            pinned = knowledge._pinned(notes, ["Dual DCDC"])
            self.assertEqual([n.relative for n in pinned], ["P.md"])
            selection = knowledge.select_sections(notes, "anything", 500, pinned=pinned, extra=["Huge.md"])
        self.assertTrue(selection.truncated)
        self.assertLessEqual(selection.forced_tokens, knowledge.FORCED_CAP_TOKENS)
        self.assertEqual(selection.notes[0].relative, "P.md")


class TestSecondGeneration(unittest.TestCase):
    """Spec 5.1 (decided 10 September 2026): expansion, boosts, core, briefs, picks."""

    def _vault(self, tmp: Path) -> Path:
        (tmp / "Abbreviations.md").write_text(
            "---\nkind: reference\n---\n# Abbreviations\n\n| Abbreviation | Full form | Description |\n| --- | --- | --- |\n"
            "| PPAP | Production Part Approval Process | part approval |\n| DV | Design Verification | testing |\n", encoding="utf-8")
        long = "\n".join(f"Line {i} of coaching text about supplier parts and readiness." for i in range(40))
        (tmp / "VPDS_Customer Part Approval.md").write_text(
            "---\nkind: process\naliases: [PPAP]\nphases: [MP4, MP5, MP6]\nlead_swimlane: Manufacturing\n"
            "affected_swimlanes: [Manufacturing, Quality]\nsummary: AI summary, not official. The plant proves the part to the customer.\n---\n"
            f"# VPDS task - Customer Part Approval\n\n## Definition\n\nThe plant proves the part to the customer.\n\n## Coaching\n\n{long}\n", encoding="utf-8")
        (tmp / "VPDS_Design Verification Testing.md").write_text(
            "---\nkind: process\nphases: [MP1, MP2, MP3, MP4]\nlead_swimlane: Systems\naffected_swimlanes: [Systems]\n"
            "summary: AI summary, not official. Tests the design against the environment.\n---\n"
            f"# VPDS task - Design Verification Testing\n\n## Definition\n\nTests the design.\n\n## Coaching\n\n{long}\n", encoding="utf-8")
        (tmp / "VPDS_Overview.md").write_text(
            "---\nkind: process\n---\n# VPDS\n\n## Maturity phases and gates\n\nMP0 to MP10, each closed by a gate.\n\n## Tasks\n\n"
            + "\n".join(f"| task {i} | x |" for i in range(80)) + "\n", encoding="utf-8")
        (tmp / "Dual DCDC.md").write_text("---\nkind: project\nprojects: [Dual DCDC]\n---\n# Dual DCDC\n\nSOP Aug 2028.\n", encoding="utf-8")
        (tmp / "Other.md").write_text("---\nsummary: AI summary, not official. A page about something else.\n---\n# Other\n\nNothing here.\n", encoding="utf-8")
        return tmp

    def test_question_phases_read_numbers_gates_and_phase_words(self):
        self.assertEqual(knowledge.question_phases("Before MG4 and during DV testing"), ("MP4", "MP3"))
        self.assertEqual(knowledge.question_phases("MP 7 SOP readiness"), ("MP7",))
        self.assertEqual(knowledge.question_phases("nothing"), ())

    def test_aliases_and_abbreviations_expand_the_question_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(self._vault(Path(tmp)))
        terms = knowledge.expand_terms(knowledge.query_terms("Is the PPAP late?"), "Is the PPAP late?", notes)
        self.assertIn("production", terms)          # the abbreviation's full form
        self.assertIn("customer", terms)            # the alias reaches the page title
        terms = knowledge.expand_terms(knowledge.query_terms("part approval"), "part approval", notes)
        self.assertIn("ppap", terms)                # and the title words reach the alias
        terms = knowledge.expand_terms(knowledge.query_terms("DV plan"), "DV plan", notes)
        self.assertIn("verification", terms)        # a two-letter abbreviation is read from the question

    def test_property_boosts_prefer_the_members_own_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(self._vault(Path(tmp)))
            neutral = "Should we source now?"
            for_mfg = knowledge.select_sections(notes, neutral, 300, member="Manufacturing", core=[])
            for_sys = knowledge.select_sections(notes, neutral, 300, member="Systems", core=[])
        self.assertEqual(for_mfg.sections[0].relative, "VPDS_Customer Part Approval.md")   # lead_swimlane: Manufacturing
        self.assertEqual(for_sys.sections[0].relative, "VPDS_Design Verification Testing.md")

    def test_the_core_is_shared_and_capped_and_the_rest_is_one_line_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"knowledge": {"vault_path": tmp, "token_budget": 400, "project": "Dual DCDC"}}
            self._vault(Path(tmp))
            blocks = knowledge.gather_for_members(config, "What must happen in MP4?", {"Manufacturing": [], "Systems": []})
        for block in blocks.values():
            self.assertIn("## Knowledge from the vault, shared by every member", block.core_text)
            self.assertIn("Dual DCDC.md", block.core_text)                       # the project page
            self.assertIn("Maturity phases and gates", block.core_text)           # the gate definitions
            self.assertIn("Customer Part Approval.md - Definition", block.core_text)   # a task active in MP4
            self.assertLessEqual(block.core_tokens, int(400 * knowledge.CORE_SHARE) + 40)
            self.assertIn("## Further pages in the vault", block.brief_text)
            self.assertIn("Other.md: AI summary", block.brief_text)
            self.assertLessEqual(block.brief_tokens, knowledge.BRIEF_CAP_TOKENS)
            self.assertLessEqual(block.tokens, 400)                               # briefs ride on top of the budget
            self.assertIn("Other.md", block.brief_sent)                            # a cited brief page verifies against its summary

    def test_the_models_picks_lead_the_queue_and_carry_their_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"knowledge": {"vault_path": tmp, "token_budget": 250}}
            self._vault(Path(tmp))
            cands = knowledge.candidates(config, "Should we source now?", {"Systems": []})
            ids = [c["id"] for c in cands["Systems"]]
            self.assertIn("VPDS_Customer Part Approval.md#Definition", ids)
            self.assertTrue(all("summary" in c for c in cands["Systems"]))
            picks = {"Systems": {"full": ["VPDS_Customer Part Approval.md#Definition"], "brief": ["Other.md"],
                                 "reasons": {"VPDS_Customer Part Approval.md#Definition": "the plant proves the part"}}}
            block = knowledge.gather_for_members(config, "Should we source now?", {"Systems": []}, picks=picks)["Systems"]
        self.assertEqual(block.picked_by, "model")
        own_first = [l for l in block.own_text.splitlines() if l.startswith("### ")][0]
        self.assertEqual(own_first, "### VPDS_Customer Part Approval.md - Definition")   # the pick leads the member's own tier
        self.assertEqual(block.briefs[0].relative, "Other.md")
        self.assertEqual(block.reasons["VPDS_Customer Part Approval.md#Definition"], "the plant proves the part")


class TestMetaPages(unittest.TestCase):
    """Decided 10 September 2026: the guide and the abbreviations table are
    about the vault, not the decision. They never take a member's budget;
    the rows the question uses reach the core instead."""

    def _vault(self, tmp: Path) -> Path:
        (tmp / "_How this vault works.md").write_text(
            "---\nkind: guide\n---\n# How this vault works\n\n## Properties\n\nThe DV tests and the MG4 gate are words this page uses.\n"
            "Design verification, gates, freeze, housing - " * 20, encoding="utf-8")
        (tmp / "Abbreviations.md").write_text(
            "---\nkind: reference\n---\n# Abbreviations\n\nEvery abbreviation.\n\n| Abbreviation | Full form | Description |\n| --- | --- | --- |\n"
            "| DV | Design Verification | Closed by MG4 |\n| DVP&R | Design Verification Plan and Report | Results |\n"
            "| MG | Maturity Gate | The review |\n| PPAP | Production Part Approval Process | Part approval |\n"
            "| SQ/SQE | Supplier Quality Engineer | Supplier quality |\n" + "| X%d | Filler %d | Filler |\n" * 0, encoding="utf-8")
        (tmp / "VPDS_Design Verification Testing.md").write_text(
            "---\nkind: process\n---\n# VPDS task - Design Verification Testing\n\n## Definition\n\nDV tests verify the design before MG4.\n", encoding="utf-8")
        return tmp

    def test_guide_and_table_are_never_ranked_and_the_used_rows_reach_the_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = knowledge.load_vault(self._vault(Path(tmp)))
            selection = knowledge.select_sections(notes, "Can we pass MG4 with the DV tests and the DVP&R still open?", 3000)
            paths = [n.relative for n in selection.notes]
            self.assertNotIn("_How this vault works.md", paths)
            self.assertNotIn("_How this vault works.md", [n.relative for n in selection.briefs])
            core = [s for s in selection.sections if s.heading == knowledge._ABBREV_HEADING]
            self.assertEqual(len(core), 1)
            self.assertIn("| DV |", core[0].body)
            self.assertIn("| MG |", core[0].body)
            self.assertIn("| DVP&R |", core[0].body)
            self.assertNotIn("| PPAP |", core[0].body)
            self.assertIn("| Abbreviation | Full form |", core[0].body)
            self.assertIn(knowledge._ABBREV_HEADING, selection.core_text)
            self.assertNotIn("| PPAP |", selection.text)              # the whole table never goes out
            self.assertIn("VPDS_Design Verification Testing.md", paths)
            # A section over half the budget is not sent whole: it would be the whole block.
            (Path(tmp) / "Big table.md").write_text("---\nkind: process\nsummary: The DV table.\n---\n# DV table\n\n## DV rows\n\n" +
                                                  "| DV | MG4 | design verification |\n" * 300, encoding="utf-8")
            notes = knowledge.load_vault(Path(tmp))
            big = knowledge.select_sections(notes, "Can we pass MG4 with the DV tests still open?", 3000)
            self.assertNotIn("DV rows", [s.heading for s in big.sections])
            self.assertIn("DV rows", [s.heading for s in knowledge.select_sections(notes, "DV?", 3000, extra=["Big table.md"]).sections])
            # A manual pick still sends the guide, whole.
            forced = knowledge.select_sections(notes, "Anything?", 3000, extra=["_How this vault works.md"])
            self.assertIn("_How this vault works.md", [n.relative for n in forced.notes])
            self.assertIsNone(knowledge.abbreviation_rows(notes, "Nothing abbreviated here"))
            self.assertIsNone(knowledge.abbreviation_rows([n for n in notes if n.kind != "reference"], "MG4"))
            sq = knowledge.abbreviation_rows(notes, "Who is the SQE?")
            self.assertIn("| SQ/SQE |", sq.body)
            self.assertEqual(knowledge.section_id(sq), f"Abbreviations.md#{knowledge._ABBREV_HEADING}")


class TestPicker(unittest.TestCase):
    def test_only_candidate_ids_survive_and_a_bad_answer_keeps_python_in_force(self):
        from decisionboard import picker
        cands = {"Hardware": [{"id": "A.md#One", "path": "A.md", "heading": "One", "summary": "", "tokens": 10},
                              {"id": "B.md", "path": "B.md", "heading": "", "summary": "s", "tokens": 5}],
                 "Finance": []}
        text = json.dumps({"members": [{"member": "hardware", "full": ["A.md#One", "Z.md#Nope", "A.md#One"], "brief": ["B.md", "A.md#One"],
                                        "reasons": {"A.md#One": "needs it", "Z.md#Nope": "invented"}}]})
        result = picker.parse_picks(text, cands)
        self.assertTrue(result.ok)
        self.assertEqual(result.picks["Hardware"]["full"], ["A.md#One"])
        self.assertEqual(result.picks["Hardware"]["brief"], ["B.md"])
        self.assertEqual(result.picks["Hardware"]["reasons"], {"A.md#One": "needs it"})
        self.assertEqual(result.picks["Finance"], {"full": [], "brief": [], "reasons": {}})
        self.assertEqual(result.dropped, 1)
        self.assertFalse(picker.parse_picks("not json", cands).ok)
        self.assertFalse(picker.parse_picks(json.dumps({"members": []}), cands).ok)
        prompt = picker.pick_prompt("Q?", {"Hardware": "the chips"}, cands)
        self.assertIn(picker.MARKER, prompt)
        self.assertIn("- id: A.md#One | A.md - One | 10 tokens", prompt)
