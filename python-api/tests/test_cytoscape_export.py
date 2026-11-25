#!/usr/bin/env python3
"""The typed GraphML and Cytoscape exports the graph is opened with.

A GraphML opened in Cytoscape only supports numeric visual mappings and filters
if the attributes carry types. The engine used to stringify every attribute, so
pchembl_value arrived as text. These tests export a small graph with known
values and assert the types survive, and that graph.cyjs is valid Cytoscape
element JSON with the same identifiers.

Run: cd python-api && python3 -m pytest tests/test_cytoscape_export.py -q
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent.parent / "api"
sys.path.insert(0, str(_ENGINE))

import networkx as nx  # noqa: E402

import scigraph  # noqa: E402


def _graph():
    compound = scigraph.Entity(
        uid="COMPOUND:CHEMBL25",
        entity_type=scigraph.EntityType.COMPOUND,
        preferred_name="Aspirin",
        canonical_id="CHEMBL25",
        attributes={
            "smiles": "CC(=O)Oc1ccccc1C(=O)O",
            "molecular_weight": "180.16",
            "max_phase": "4",
            "formula": "C9H8O4",
        },
    )
    target = scigraph.Entity(
        uid="TARGET:P23219",
        entity_type=scigraph.EntityType.TARGET,
        preferred_name="COX-1",
        canonical_id="P23219",
    )
    relation = scigraph.Relation(
        source_uid="COMPOUND:CHEMBL25",
        target_uid="TARGET:P23219",
        relation_type=scigraph.RelationType.INHIBITS,
        attributes={
            "activity_type": "IC50",
            "activity_value": "30.0",
            "units": "nM",
            "pchembl_value": "7.52",
            "assay_id": "CHEMBL1",
        },
    )
    return [compound, target], [relation]


class TypedGraphML(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.entities, self.relations = _graph()

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbers_stay_numbers(self):
        path = self.dir / "graph.graphml"
        scigraph.export_to_graphml(self.entities, self.relations, str(path))

        graph = nx.read_graphml(path)
        self.assertAlmostEqual(
            float(graph.nodes["COMPOUND:CHEMBL25"]["molecular_weight"]), 180.16
        )
        self.assertIsInstance(graph.nodes["COMPOUND:CHEMBL25"]["max_phase"], int)
        edge = graph.edges[("COMPOUND:CHEMBL25", "TARGET:P23219")]
        self.assertAlmostEqual(float(edge["pchembl_value"]), 7.52)
        self.assertAlmostEqual(float(edge["activity_value"]), 30.0)

    def test_identifiers_and_labels_survive(self):
        path = self.dir / "graph.graphml"
        scigraph.export_to_graphml(self.entities, self.relations, str(path))

        graph = nx.read_graphml(path)
        node = graph.nodes["COMPOUND:CHEMBL25"]
        self.assertEqual(node["canonical_id"], "CHEMBL25")
        self.assertEqual(node["label"], "Aspirin")
        self.assertEqual(node["type"], "Compound")
        self.assertEqual(node["smiles"], "CC(=O)Oc1ccccc1C(=O)O")
        edge = graph.edges[("COMPOUND:CHEMBL25", "TARGET:P23219")]
        self.assertEqual(edge["interaction"], "INHIBITS")
        self.assertEqual(edge["units"], "nM")

    def test_cyjs_is_cytoscape_element_json(self):
        path = self.dir / "graph.cyjs"
        scigraph.export_to_cyjs(self.entities, self.relations, str(path))

        payload = json.loads(path.read_text(encoding="utf-8"))
        elements = payload["elements"]
        self.assertEqual(len(elements["nodes"]), 2)
        self.assertEqual(len(elements["edges"]), 1)

        by_id = {n["data"]["id"]: n["data"] for n in elements["nodes"]}
        self.assertEqual(by_id["COMPOUND:CHEMBL25"]["label"], "Aspirin")
        self.assertAlmostEqual(by_id["COMPOUND:CHEMBL25"]["molecular_weight"], 180.16)

        edge = elements["edges"][0]["data"]
        self.assertEqual(edge["source"], "COMPOUND:CHEMBL25")
        self.assertEqual(edge["target"], "TARGET:P23219")
        self.assertAlmostEqual(edge["pchembl_value"], 7.52)
        self.assertEqual(edge["relation"], "inhibits")
        # Every element needs its own id, or Cytoscape rejects the file.
        self.assertTrue(edge["id"])

    def test_empty_graph_still_writes_valid_files(self):
        graphml = self.dir / "graph.graphml"
        cyjs = self.dir / "graph.cyjs"
        scigraph.export_to_graphml([], [], str(graphml))
        scigraph.export_to_cyjs([], [], str(cyjs))
        self.assertTrue(graphml.stat().st_size > 0)
        self.assertEqual(
            json.loads(cyjs.read_text(encoding="utf-8"))["elements"],
            {"nodes": [], "edges": []},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
