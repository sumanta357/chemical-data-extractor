#!/usr/bin/env python3
"""
Two expansion faults that made the graph look emptier the deeper it went.

1. ChEMBL threw away every inhibitor except one per target. The activity loop
   ended after reading the fields, and the Entity/Relation construction sat
   one dedent outside it, so 500 fetched activities produced a single compound.
   BindingDB was then the only connector visibly returning inhibitors.

2. The follow-up frontier was an unordered set sliced with [:10], so which
   queries a hop ran was arbitrary. Two identical runs searched completely
   different queries, and a hop that drew bare PubChem CIDs returned almost
   nothing -- which is how 4-hop came back thinner than 3-hop. The
   "no results" placeholder entities connectors emit were fed back in as
   queries too, one of them literally "SEARCH:BindingDB Search: EGFR".

Run:  cd python-api && python3 -m pytest tests/test_expansion_quality.py -q
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_expansion_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")

_PY_API = Path(__file__).resolve().parent.parent
_ENGINE = _PY_API.parent / "api"
sys.path.insert(0, str(_PY_API))
sys.path.insert(0, str(_ENGINE))

import scigraph  # noqa: E402


class _ChEMBLStub(scigraph.ChEMBLConnector):
    """ChEMBL with canned HTTP, so the test never touches the network."""

    def __init__(self, pages):
        super().__init__(scigraph.KnowledgeCache(
            str(Path(_TEST_DIR) / "cache.sqlite")),
            scigraph.UniversalIDTranslator())
        self._pages = pages
        self._served = 0

    async def _safe_get(self, session, url, timeout=8.0):
        if "target/search" in url:
            return {"targets": [{
                "target_chembl_id": "CHEMBL203", "pref_name": "EGFR",
                "organism": "Homo sapiens", "synonyms": ["EGFR"]}]}
        if "/molecule" in url:
            return None
        if "/activity" in url:
            if self._served >= len(self._pages):
                return {"activities": []}
            page = self._pages[self._served]
            self._served += 1
            return {"activities": page}
        return None


def _activity(mol_id, value="10", pchembl="8.0", assay="A1"):
    return {"molecule_chembl_id": mol_id, "standard_type": "IC50",
            "standard_value": value, "standard_units": "nM",
            "pchembl_value": pchembl, "assay_chembl_id": assay,
            "canonical_smiles": "CCO"}


class ChEMBLActivities(unittest.IsolatedAsyncioTestCase):
    """Every fetched activity must become an inhibitor, not just the last one."""

    async def _search(self, pages):
        async with scigraph.aiohttp.ClientSession() as sess:
            connector = _ChEMBLStub(pages)
            return await connector.search(sess, "EGFR", "protein")

    async def test_every_distinct_molecule_becomes_an_inhibitor(self):
        """The regression: one compound per target regardless of page size."""
        page = [_activity(f"CHEMBL{100 + i}") for i in range(50)]
        ents, rels = await self._search([page])
        compounds = [e for e in ents
                     if e.entity_type == scigraph.EntityType.COMPOUND]
        self.assertEqual(len(compounds), 50,
                         "activities were discarded instead of becoming entities")
        self.assertEqual(len(rels), 50,
                         "each inhibitor needs its activity relation")

    async def test_a_repeated_molecule_is_not_added_twice(self):
        """One drug measured in 400 assays is still one inhibitor."""
        page = [_activity("CHEMBL999", value=str(i)) for i in range(20)]
        ents, _ = await self._search([page])
        compounds = [e for e in ents
                     if e.entity_type == scigraph.EntityType.COMPOUND]
        self.assertEqual(len(compounds), 1)

    async def test_dedup_spans_pages(self):
        """A drug measured in two assays often straddles two 500-row pages.

        The dedup set has to live outside the pagination loop, or the second
        page re-adds everything it shares with the first.
        """
        pages = [
            [_activity("CHEMBL999"), _activity("CHEMBL777")],
            [_activity("CHEMBL999"), _activity("CHEMBL888")],
        ]
        ents, _ = await self._search(pages)
        uids = [e.uid for e in ents
                if e.entity_type == scigraph.EntityType.COMPOUND]
        self.assertEqual(sorted(uids), [
            "COMPOUND:CHEMBL777", "COMPOUND:CHEMBL888", "COMPOUND:CHEMBL999"])
        self.assertEqual(len(uids), len(set(uids)),
                         "the same inhibitor was emitted once per page")

    async def test_relations_carry_the_activity_measurement(self):
        """A relation without its IC50/pChEMBL is not evidence of anything."""
        _, rels = await self._search([[_activity("CHEMBL999", "42", "7.5")]])
        rel = next(r for r in rels if r.relation_type ==
                   scigraph.RelationType.INHIBITS)
        self.assertEqual(rel.attributes["activity_value"], "42")
        self.assertEqual(rel.attributes["pchembl_value"], "7.5")
        self.assertEqual(rel.attributes["units"], "nM")


class FrontierSelection(unittest.TestCase):
    """The rule that picks which follow-ups a hop actually runs."""

    def test_selection_is_deterministic(self):
        """The old set slice gave different queries on every run."""
        frontier = {q: scigraph._frontier_rank(q) for q in
                    ("EGFR", "P00533", "1WT5", "CHEBI:100753", "50375662")}
        picks = [tuple(scigraph._select_frontier(dict(frontier), 5))
                 for _ in range(20)]
        self.assertEqual(len(set(picks)), 1,
                         "identical input produced different hops")

    def test_bare_cids_are_never_preferred_over_real_identifiers(self):
        """A PubChem CID from an activity table drags in an unrelated molecule."""
        ranked = sorted({"EGFR": 0.0, "P00533": 0.0, "50375662": 3.0,
                         "411455": 3.0}.items(), key=lambda kv: kv[1])
        self.assertGreater(scigraph._frontier_rank("50375662"),
                           scigraph._frontier_rank("EGFR"))

    def test_placeholder_entities_are_never_expanded(self):
        """These are "no results" links, not database keys."""
        for junk in ("BindingDB Search: EGFR", "Patent Search: EGFR",
                     "PDBbind Search: 1WT5", "SEARCH:EGFR",
                     "SEARCH:BindingDB Search: EGFR"):
            with self.subTest(junk=junk):
                self.assertEqual(scigraph._frontier_rank(junk), 4.0)

    def test_placeholders_do_not_consume_a_hops_budget(self):
        """They match no database but cost a full 18-connector fan-out."""
        frontier = {"BindingDB Search: EGFR": 4.0,
                    "Patent Search: EGFR": 4.0,
                    "EGFR": 0.0, "P00533": 0.0}
        self.assertEqual(scigraph._select_frontier(frontier, 4),
                         ["EGFR", "P00533"])

    def test_placeholders_still_run_when_nothing_else_is_left(self):
        """Otherwise a hop silently dies on the last one."""
        self.assertEqual(
            scigraph._select_frontier({"Patent Search: X": 4.0}, 4),
            ["Patent Search: X"])

    def test_higher_hops_keep_more_useful_candidates(self):
        """The reported symptom: 4-hop thinner than 3-hop."""
        frontier = {f"CID{i}": 3.0 for i in range(200)}
        frontier.update({"EGFR": 0.0, "P00533": 0.0, "1WT5": 1.0})
        picks = scigraph._select_frontier(frontier, 10)
        self.assertEqual(len(picks), 10)
        self.assertLessEqual(
            sum(1 for p in picks if p.startswith("CID")), 7,
            "a hop's whole budget went to bare CIDs")


if __name__ == "__main__":
    unittest.main(verbosity=2)