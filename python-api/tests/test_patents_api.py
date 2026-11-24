"""The patent register the results screen reads.

Patents were in the graph and in the workbook, but nowhere a reader could
actually see a patent number, a year or a link — which is what "I am not
getting patent information" meant. This endpoint is that list, so it is tested
against real export rows: newest first, the year parsed from the identifier the
way the IP verdict parses it, owner-scoped like every other search endpoint,
and honest when the search surfaced none.
"""

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_patents_api_")
os.environ["BILLING_DB"] = str(os.path.join(_TEST_DIR, "billing.db"))
os.environ["SEARCH_DB"] = str(os.path.join(_TEST_DIR, "search.db"))
os.environ["EXPORTS_DIR"] = str(os.path.join(_TEST_DIR, "exports"))
os.environ["WORKSPACE_DIR"] = str(os.path.join(_TEST_DIR, "workspace"))
os.environ["REQUIRE_AUTH"] = "1"

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402


NODES_CSV = (
    "uid:ID,name,:LABEL,canonical_id,synonyms,cross_references,smiles,formula,"
    "molecular_weight,bioactivity_summary,patent_number,publication_year,"
    "queried_compound,title,assignee,google_patents_url\n"
    "COMPOUND:MXWJVTOOROXGIU-UHFFFAOYSA-N,Atrazine,Entity;Compound,"
    "MXWJVTOOROXGIU-UHFFFAOYSA-N,,PUBCHEM:2256,C1=NC(=NC(=N1)Cl)NC(C)C,C8H14ClN5,"
    "215.68,,,Atrazine,,,\n"
    "PATENT:US-20190021252-A1,US Patent US-20190021252-A1 (Atrazine),Entity;Patent,"
    "US-20190021252-A1,,,,,,,,,Atrazine,Herbicide composition,Example Assignee,"
    "https://patents.google.com/patent/US-20190021252-A1\n"
    "PATENT:EP0248548B1,EP Patent EP0248548B1 (Atrazine),Entity;Patent,"
    "EP0248548B1,,,,,,,,,Atrazine,,,\n"
    "PATENT:AR-018833-A1,Patent AR-018833-A1 (Atrazine),Entity;Patent,"
    "AR-018833-A1,,,,,,,,,Atrazine,,,\n"
)

EDGES_CSV = (
    ":START_ID,:END_ID,:TYPE,confidence,activity_type,activity_value,units,"
    "pchembl_value,mechanism_of_action,assay_id\n"
    "COMPOUND:MXWJVTOOROXGIU-UHFFFAOYSA-N,PATENT:US-20190021252-A1,PATENTED_IN,"
    "1.0,,,,,,\n"
    "COMPOUND:MXWJVTOOROXGIU-UHFFFAOYSA-N,PATENT:EP0248548B1,PATENTED_IN,1.0,,,,,,\n"
)

EMPTY_NODES = NODES_CSV.split("\n")[0] + "\n"
EMPTY_EDGES = EDGES_CSV.split("\n")[0] + "\n"


class PatentEndpoint(unittest.TestCase):
    def setUp(self):
        root = Path(engine_app.EXPORTS_DIR)
        self.dir = root / "pat3nts1"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "nodes.csv").write_text(NODES_CSV, encoding="utf-8")
        (self.dir / "edges.csv").write_text(EDGES_CSV, encoding="utf-8")

        self.empty = root / "pat3nts2"
        self.empty.mkdir(parents=True, exist_ok=True)
        (self.empty / "nodes.csv").write_text(EMPTY_NODES, encoding="utf-8")
        (self.empty / "edges.csv").write_text(EMPTY_EDGES, encoding="utf-8")

        self.tmp = TemporaryDirectory()
        self._orig_db = engine_app.SEARCH_DB
        engine_app.SEARCH_DB = Path(self.tmp.name) / "searches.db"
        engine_app._init_db()

        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)

        engine_app.searches.clear()
        for search_id, directory in (("pat3nts1", self.dir), ("pat3nts2", self.empty)):
            engine_app.searches[search_id] = {
                "search_id": search_id,
                "query": "Atrazine",
                "hops": 1,
                "status": "completed",
                "export_dir": str(directory),
                "export_files": [],
                "owner_user_id": self.account["id"],
            }

    def tearDown(self):
        engine_app.searches.clear()
        engine_app.SEARCH_DB = self._orig_db
        self.tmp.cleanup()

    def test_every_patent_row_is_listed_with_its_link(self):
        body = self.client.get("/api/search/pat3nts1/patents").json()
        self.assertEqual(body["patent_count"], 3)
        self.assertEqual(body["compound_count"], 1)
        self.assertEqual(body["compounds"], ["Atrazine"])
        self.assertFalse(body["truncated"])

        numbers = {p["number"] for p in body["patents"]}
        self.assertEqual(numbers, {"US-20190021252-A1", "EP0248548B1", "AR-018833-A1"})
        for patent in body["patents"]:
            self.assertTrue(patent["url"].startswith("https://patents.google.com/patent/"))

    def test_newest_first_and_the_year_comes_from_the_identifier(self):
        body = self.client.get("/api/search/pat3nts1/patents").json()
        self.assertEqual(body["patents"][0]["number"], "US-20190021252-A1")
        self.assertEqual(body["patents"][0]["year"], 2019)
        self.assertEqual(body["patents"][0]["assignee"], "Example Assignee")
        # A number with no year in it reports none rather than guessing.
        self.assertIsNone(
            next(p for p in body["patents"] if p["number"] == "AR-018833-A1")["year"]
        )

    def test_a_search_with_no_patents_says_zero_not_something_else(self):
        body = self.client.get("/api/search/pat3nts2/patents").json()
        self.assertEqual(body["patent_count"], 0)
        self.assertEqual(body["patents"], [])

    def test_owned_like_every_other_search_endpoint(self):
        anon = TestClient(engine_app.app)
        self.assertEqual(anon.get("/api/search/pat3nts1/patents").status_code, 404)

    def test_unknown_search_is_404(self):
        self.assertEqual(self.client.get("/api/search/nosuchid/patents").status_code, 404)


if __name__ == "__main__":
    unittest.main()
