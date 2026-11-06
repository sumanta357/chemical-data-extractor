"""The interactive graph endpoint and the engine telemetry it feeds.

The network canvas is only worth having if it draws what the search actually
found, so these tests run against a small graph with known accessions and
assert the response carries those exact rows — plus that a large graph is cut
at the cap with a truthful `truncated` flag rather than quietly losing entities.

The log filter and step parser are here too: both decide what a customer sees
while a search runs, and both are easy to get subtly wrong.
"""

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_graph_test_")
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


NODES_CSV = """uid:ID,name,:LABEL,canonical_id,synonyms,cross_references,smiles,formula,molecular_weight,bioactivity_summary
COMPOUND:CHEMBL25,Aspirin,Entity;Compound,CHEMBL25,,ChEMBL:CHEMBL25;PUBCHEM:2244,CC(=O)Oc1ccccc1C(=O)O,C9H8O4,180.16,Potent COX inhibitor
TARGET:P23219,COX-1,Entity;Target,P23219,,UniProt:P23219,,,,
STRUCTURE:1EQG,COX-1 with aspirin,Entity;Structure,1EQG,,,,,,
PATHWAY:R-HSA-2162123,Prostaglandin synthesis,Entity;Pathway,R-HSA-2162123,,,,,,
"""

EDGES_CSV = """:START_ID,:END_ID,:TYPE,confidence,activity_type,activity_value,units,pchembl_value,mechanism_of_action,assay_id
COMPOUND:CHEMBL25,TARGET:P23219,INHIBITS,0.95,IC50,30.0,nM,7.52,acetylation,CHEMBL1
TARGET:P23219,STRUCTURE:1EQG,HAS_STRUCTURE,0.9,,,,,,
TARGET:P23219,PATHWAY:R-HSA-2162123,PARTICIPATES_IN,0.8,,,,,,
"""


class GraphEndpoint(unittest.TestCase):
    """The wiring: owner scoping, the real rows, and the honest totals."""

    def setUp(self):
        search_dir = Path(engine_app.EXPORTS_DIR) / "gr4ph001"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "nodes.csv").write_text(NODES_CSV, encoding="utf-8")
        (search_dir / "edges.csv").write_text(EDGES_CSV, encoding="utf-8")
        self.search_dir = str(search_dir)

        self.tmp = TemporaryDirectory()
        self._orig_db = engine_app.SEARCH_DB
        engine_app.SEARCH_DB = Path(self.tmp.name) / "searches.db"
        engine_app._init_db()

        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)

        engine_app.searches.clear()
        engine_app.searches["gr4ph001"] = {
            "search_id": "gr4ph001",
            "query": "Aspirin",
            "hops": 1,
            "status": "completed",
            "export_dir": self.search_dir,
            "export_files": [],
            "owner_user_id": self.account["id"],
        }

    def tearDown(self):
        engine_app.searches.clear()
        engine_app.SEARCH_DB = self._orig_db
        self.tmp.cleanup()

    def test_draws_the_graph_the_search_actually_found(self):
        res = self.client.get("/api/search/gr4ph001/graph")
        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertEqual(body["node_count"], 4)
        self.assertEqual(body["edge_count"], 3)
        self.assertFalse(body["truncated"])

        aspirin = next(n["data"] for n in body["nodes"] if n["data"]["id"] == "COMPOUND:CHEMBL25")
        # Real identifiers, so a chemist can check the node against the source.
        self.assertEqual(aspirin["canonical_id"], "CHEMBL25")
        self.assertIn("ChEMBL:CHEMBL25", aspirin["cross_references"])
        self.assertEqual(aspirin["formula"], "C9H8O4")
        self.assertEqual(aspirin["type"], "Compound")  # "Entity;Compound" -> Compound

        inhibited = next(
            e["data"] for e in body["edges"] if e["data"]["relation"] == "INHIBITS"
        )
        self.assertEqual(inhibited["source"], "COMPOUND:CHEMBL25")
        self.assertIn("30", inhibited["label"])
        self.assertEqual(inhibited["pchembl"], "7.52")

    def test_running_search_has_no_graph_yet(self):
        engine_app.searches["gr4ph001"]["status"] = "running"
        (Path(self.search_dir) / "nodes.csv").unlink()
        res = self.client.get("/api/search/gr4ph001/graph")
        self.assertEqual(res.status_code, 404)

    def test_a_foreign_caller_cannot_read_the_graph(self):
        # Graphs are customer data: a caller who does not own the search gets
        # the same 404 as one asking for a search that does not exist.
        anonymous = TestClient(engine_app.app)
        self.assertEqual(anonymous.get("/api/search/gr4ph001/graph").status_code, 404)


class GraphCap(unittest.TestCase):
    """A huge graph is cut at the canvas budget, and says so."""

    def setUp(self):
        search_dir = Path(engine_app.EXPORTS_DIR) / "b1ggraph"
        search_dir.mkdir(parents=True, exist_ok=True)
        lines = [NODES_CSV.splitlines()[0]]
        for i in range(500):
            lines.append(f"COMPOUND:CHEMBL{i},Compound {i},Entity;Compound,CHEMBL{i},,,CCO,,,")
        (search_dir / "nodes.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

        edges = [EDGES_CSV.splitlines()[0]]
        # Chains across the whole set: edges into the cut tail must not be drawn.
        for i in range(1500):
            edges.append(
                f"COMPOUND:CHEMBL{i % 500},COMPOUND:CHEMBL{(i + 1) % 500},"
                "RELATED,0.5,,,,,"
            )
        (search_dir / "edges.csv").write_text("\n".join(edges) + "\n", encoding="utf-8")
        self.search_dir = str(search_dir)

        self.tmp = TemporaryDirectory()
        self._orig_db = engine_app.SEARCH_DB
        engine_app.SEARCH_DB = Path(self.tmp.name) / "searches.db"
        engine_app._init_db()
        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)

        engine_app.searches.clear()
        engine_app.searches["b1ggraph"] = {
            "search_id": "b1ggraph",
            "query": "big",
            "hops": 4,
            "status": "completed",
            "export_dir": self.search_dir,
            "export_files": [],
            "owner_user_id": self.account["id"],
        }

    def tearDown(self):
        engine_app.searches.clear()
        engine_app.SEARCH_DB = self._orig_db
        self.tmp.cleanup()

    def test_cap_is_reported_not_hidden(self):
        body = self.client.get("/api/search/b1ggraph/graph").json()
        self.assertEqual(body["node_count"], 450)
        self.assertEqual(body["node_total"], 500)
        self.assertTrue(body["truncated"])

    def test_edges_only_join_drawn_nodes(self):
        body = self.client.get("/api/search/b1ggraph/graph").json()
        drawn = {n["data"]["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            self.assertIn(edge["data"]["source"], drawn)
            self.assertIn(edge["data"]["target"], drawn)


class UserLogFilter(unittest.TestCase):
    """Interpreter internals stay out of the customer's log."""

    def setUp(self):
        self.keep = engine_app._UserLogFilter().keep

    def test_a_traceback_is_dropped_whole(self):
        lines = [
            "Traceback (most recent call last):",
            '  File "api/scigraph.py", line 12, in <module>',
            "    import aiohttp",
            "ModuleNotFoundError: No module named 'aiohttp'",
        ]
        self.assertEqual([line for line in lines if self.keep(line)], [])

    def test_package_advice_is_dropped(self):
        self.assertFalse(self.keep("Missing critical dependencies: No module named 'aiohttp'"))
        self.assertFalse(self.keep("Please run: pip install pydantic networkx duckdb"))

    def test_real_engine_output_is_kept(self):
        kept = [
            "  ── Step 1/6: Intelligent Routing & Multi-Hop Expansion (Hops: 2)",
            "    Batch progress: 12 follow-up candidates so far (hop 2)",
            "  * Unique Entities Found: 411",
            "  ⚠️ ChEMBL returned no records for this term",
            "  ✔ Done in 4.2s  (elapsed: 12s | ETA remaining: ~30s)",
        ]
        for line in kept:
            self.assertTrue(self.keep(line), f"dropped a line the user needs: {line!r}")

    def test_a_plain_sentence_mentioning_an_error_survives(self):
        self.assertTrue(self.keep("  Done — 2 sources returned an error and were skipped"))


class StepTracking(unittest.TestCase):
    """The step rail is driven by the engine's own headers."""

    def test_step_header_sets_step_and_label(self):
        state: dict = {}
        engine_app._update_progress(
            state, "  ── Step 2/6: Entity Resolution & Deduplication"
        )
        self.assertEqual(state["step"], 2)
        self.assertEqual(state["step_total"], 6)
        self.assertEqual(state["step_label"], "Entity Resolution & Deduplication")

    def test_section_header_sets_step(self):
        state: dict = {}
        engine_app._update_progress(state, "[3/6] ENTITIES & CONNECTIONS SUMMARY:")
        self.assertEqual(state["step"], 3)
        self.assertEqual(state["step_label"], "ENTITIES & CONNECTIONS SUMMARY")

    def test_hop_banner_still_drives_progress_text(self):
        state: dict = {}
        engine_app._update_progress(state, "  ╔══ Hop 2/3 ═══════════╗")
        self.assertIn("Hop 2/3", state["progress"])
        self.assertNotIn("step", state)


if __name__ == "__main__":
    unittest.main()
