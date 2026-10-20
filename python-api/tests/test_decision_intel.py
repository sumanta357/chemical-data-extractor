"""Decision intelligence endpoints and computation, against real export CSVs.

decision.py is deliberately offline: every verdict is computed from the
nodes.csv / edges.csv a search already produced. These tests therefore run it
against a small synthetic graph with known answers — a potent compound tied to
an old patent, a weak one tied to a recent one — rather than mocking a
network, because the failure mode being guarded against is a wrong verdict,
not a failed request.
"""

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_decision_test_")
os.environ["BILLING_DB"] = str(os.path.join(_TEST_DIR, "billing.db"))
os.environ["SEARCH_DB"] = str(os.path.join(_TEST_DIR, "search.db"))
os.environ["EXPORTS_DIR"] = str(os.path.join(_TEST_DIR, "exports"))
os.environ["WORKSPACE_DIR"] = str(os.path.join(_TEST_DIR, "workspace"))
os.environ["REQUIRE_AUTH"] = "1"

import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402
import decision  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402


NODES_CSV = """uid:ID,name,:LABEL,canonical_id,synonyms,cross_references,smiles,formula,molecular_weight,bioactivity_summary
COMPOUND:CHEMBL111,Dartacitinib,Entity;Compound,CHEMBL111,,ChEMBL:CHEMBL111;INCHIKEY:ABCDEFGHABCDEFGH-OOOOOO,CCOc1cc2nccc(O)c2cc1,CCOc1,244.29,
COMPOUND:CHEMBL222,Failtolinib,Entity;Compound,CHEMBL222,,ChEMBL:CHEMBL222,CCN1CCN(CC1)c2ccccc2,CCN1,178.24,
PATENT:US5444444-1995,Old compound patent,Entity;Patent,US5444444,,ChEMBL:,,,,US5444444 (1995)
PATENT:WO202411111,Fresh PCT filing,Entity;Patent,WO202411111,,ChEMBL:,,,,WO2024/111111
STRUCTURE:1ABX,Dartacitinib complex,Entity;Structure,1ABX,,ChEMBL:,,,,
"""

EDGES_CSV = """:START_ID,:END_ID,:TYPE,activity_type,activity_value,units,pchembl_value
COMPOUND:CHEMBL111,TARGET:EGFR,BINDS,IC50,2.1,nM,8.68
COMPOUND:CHEMBL111,TARGET:EGFR,INHIBITS,IC50,3.3,nM,8.48
COMPOUND:CHEMBL111,STRUCTURE:1ABX,HAS_STRUCTURE,,9.1,,
COMPOUND:CHEMBL222,TARGET:EGFR,BINDS,IC50,3100,nM,5.51
COMPOUND:CHEMBL111,PATENT:US5444444-1995,PATENTED_IN,,,
COMPOUND:CHEMBL222,PATENT:US5444444-1995,PATENTED_IN,,,
COMPOUND:CHEMBL222,PATENT:WO202411111,PATENTED_IN,,,
"""


class DecisionMath(unittest.TestCase):
    """The verdicts themselves, on a graph with known answers."""

    def setUp(self):
        self.nodes = list(__import__("csv").DictReader(NODES_CSV.splitlines()))
        self.edges = list(__import__("csv").DictReader(EDGES_CSV.splitlines()))

    def test_overall_grade_is_median_of_all_datapoints(self):
        # Affinity set: {8.68, 8.48} (IC50 pChEMBLs) + 5.51. The 9.1 riding on
        # a HAS_STRUCTURE edge is not an affinity and must be excluded.
        result = decision.feasibility_verdict(self.nodes, self.edges)
        self.assertEqual(result["grade"], "B")
        self.assertEqual(result["affinity_datapoints"], 3)
        self.assertEqual(result["scored_compounds"], 2)

    def test_per_compound_grades_split_sharply(self):
        result = decision.feasibility_verdict(self.nodes, self.edges)
        by_name = {c["name"]: c for c in result["compounds"]}
        self.assertEqual(by_name["Dartacitinib"]["grade"], "B")  # median 8.58
        self.assertEqual(by_name["Failtolinib"]["grade"], "F")   # 5.51
        self.assertEqual(by_name["Dartacitinib"]["datapoints"], 2)

    def test_no_affinity_data_degrades_honestly(self):
        edges = [e for e in self.edges if not e["pchembl_value"]]
        result = decision.feasibility_verdict(self.nodes, edges)
        self.assertEqual(result["grade"], "N/A")
        self.assertIn("No quantitative affinity", result["note"])

    def test_ip_green_when_newest_patent_is_old(self):
        result = decision.ip_status(self.nodes, self.edges, now_year=2098)
        self.assertEqual(result["status"], "green")
        self.assertIn("lapsed", result["reason"])

    def test_ip_yellow_inside_two_year_window(self):
        # WO202411111 -> 2024; 2024 + 20 = 2044. remaining(2042) = 2 -> yellow.
        result = decision.ip_status(self.nodes, self.edges, now_year=2042)
        self.assertEqual(result["status"], "yellow")

    def test_ip_red_when_active_protection(self):
        # remaining(2039) = 5, outside the 2-year window -> red.
        result = decision.ip_status(self.nodes, self.edges, now_year=2039)
        self.assertEqual(result["status"], "red")

    def test_future_publication_years_are_not_parsed(self):
        # A year after the current calendar date cannot be a publication year;
        # the parser must ignore it rather than back a verdict on it.
        self.assertIsNone(decision._patent_year("PATENT:WO2999111"))

    def test_ip_green_when_no_patents_at_all(self):
        nodes = [n for n in self.nodes if "Entity;Patent" not in n[":LABEL"]]
        edges = [e for e in self.edges if e[":TYPE"] != "PATENTED_IN"]
        result = decision.ip_status(nodes, edges, now_year=2095)
        self.assertEqual(result["status"], "green")

    def test_dark_literature_counts_overlap(self):
        result = decision.dark_literature(self.nodes, self.edges)
        # US5444444 links two compounds; WO2099111 links one.
        self.assertEqual(result["patent_count"], 2)
        self.assertEqual(result["compounds_with_patents"], 2)
        self.assertEqual(result["overlap_signals"], 1)  # US5444444 has 2 compounds
        self.assertEqual(result["compounds"][0]["name"], "Failtolinib")  # 2 patents
        self.assertIn("prior art", result["reading"])

    def test_dark_literature_quiet_when_no_overlap(self):
        edges = [e for e in self.edges if e[":START_ID"] != "COMPOUND:CHEMBL222"]
        result = decision.dark_literature(self.nodes, edges)
        self.assertEqual(result["overlap_signals"], 0)
        self.assertIn("not detected", result["reading"])

    def test_moa_collects_structures_and_targets(self):
        result = decision.moa_storyboard(self.nodes, self.edges, "kinase y")
        self.assertEqual(result["structures"][0]["pdb_id"], "1ABX")
        self.assertEqual(result["targets"][0]["name"], "TARGET:EGFR")
        self.assertEqual(result["publications"], 0)
        self.assertEqual(result["patents"], 3)  # all three PATENTED_IN edges

    def test_vendors_resolve_identifiers(self):
        result = decision.vendor_sourcing(self.nodes)
        by_name = {c["name"]: c for c in result["compounds"]}
        dart = by_name["Dartacitinib"]
        self.assertTrue(dart["vendors_url"].startswith("https://pubchem.ncbi.nlm.nih.gov/"))
        self.assertEqual(dart["identifier"], "ABCDEFGHABCDEFGH-OOOOOO")
        self.assertEqual(dart["identifier_kind"], "inchikey/pubchem")

    def test_decision_intel_rejects_outside_dirs(self):
        self.assertIsNone(decision.decision_intel("/etc", "q"))
        self.assertIsNone(decision.decision_intel("", "q"))


class DecisionEndpoint(unittest.TestCase):
    """The endpoint wiring: auth, 404s, and the real JSON envelope."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        # Export directories must live under the app's EXPORTS_DIR: the
        # endpoint refuses any export_dir outside it, exactly as it should.
        # The app's value is authoritative — another suite may have imported
        # it first with its own environment.
        search_dir = Path(engine_app.EXPORTS_DIR) / "ab12cd34"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "nodes.csv").write_text(NODES_CSV, encoding="utf-8")
        (search_dir / "edges.csv").write_text(EDGES_CSV, encoding="utf-8")
        self.search_dir = str(search_dir)

        db = Path(self.tmp.name) / "searches.db"
        self._orig_db = engine_app.SEARCH_DB
        engine_app.SEARCH_DB = db
        engine_app._init_db()

        # Identity is stubbed exactly as the other suites do: a bearer token
        # the stub recognises, resolving to a real billing account row, which
        # then owns the search under test.
        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)

        engine_app.searches.clear()
        engine_app.searches["ab12cd34"] = {
            "search_id": "ab12cd34",
            "query": "kinase y scaffold z",
            "status": "completed",
            "export_dir": self.search_dir,
            "export_files": [],
            "owner_user_id": self.account["id"],
        }

    def tearDown(self):
        engine_app.searches.clear()
        engine_app.SEARCH_DB = self._orig_db
        self.tmp.cleanup()

    def test_returns_full_bundle(self):
        res = self.client.get("/api/search/ab12cd34/decision-intel")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["search_id"], "ab12cd34")
        self.assertEqual(body["feasibility"]["grade"], "B")
        self.assertEqual(body["ip"]["status"], "red")  # WO2099111 vs real now-year
        self.assertIn("dark_literature", body)
        self.assertIn("vendors", body)
        self.assertIn("CompTox", "".join(body["not_assessed"]))

    def test_owner_scoped_like_every_other_search_endpoint(self):
        anon = TestClient(engine_app.app)
        res = anon.get("/api/search/ab12cd34/decision-intel")
        self.assertEqual(res.status_code, 404)

    def test_unknown_search_404(self):
        res = self.client.get("/api/search/00000000/decision-intel")
        self.assertEqual(res.status_code, 404)

    def test_missing_export_dir_404(self):
        engine_app.searches["ab12cd34"]["export_dir"] = str(
            Path(engine_app.EXPORTS_DIR) / "gone"
        )
        res = self.client.get("/api/search/ab12cd34/decision-intel")
        self.assertEqual(res.status_code, 404)

    def test_export_dir_outside_exports_is_refused(self):
        engine_app.searches["ab12cd34"]["export_dir"] = self.tmp.name
        res = self.client.get("/api/search/ab12cd34/decision-intel")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
