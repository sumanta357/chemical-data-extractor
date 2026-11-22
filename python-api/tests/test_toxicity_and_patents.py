"""Toxicity collection, and the two routes that made patent rows disappear.

Toxicity had no implementation at all: every toxicity source in the registry
was an auto-generated no-op, so the answer to "what does PubChem say about this
compound's hazards" was always silence. The connector is tested against a
canned PUG-View document, because what matters is that statements are carried
through with their section and citation — not that the HTTP call succeeds.

Patents failed in two ways that both end in *zero* patent rows for a user:
a query shaped like an identifier (CHEMBL25, 2244, CHEBI:2244) was routed to a
connector list that did not include Patents at all, and a compound with tens of
thousands of patents could be cancelled by the expander's per-connector
timeout, throwing away everything it had collected.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_toxicity_test_")
os.environ.setdefault("EXPORTS_DIR", str(Path(_TEST_DIR) / "exports"))

_ENGINE = Path(__file__).resolve().parent.parent.parent / "api"
sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scigraph  # noqa: E402
import decision  # noqa: E402


TOX_DOCUMENT = {
    "Record": {
        "Section": [
            {
                "TOCHeading": "Toxicity",
                "Section": [
                    {
                        "TOCHeading": "Carcinogen Classification",
                        "Information": [
                            {
                                "Value": {
                                    "StringWithMarkup": [
                                        {"String": "IARC Group 2A: Probably carcinogenic to humans"}
                                    ]
                                },
                                "Reference": ["IARC Monographs Vol 53 (1991)"],
                            }
                        ],
                    },
                    {
                        "TOCHeading": "Non-Human Toxicity Values",
                        "Information": [
                            {
                                "Value": {"StringWithMarkup": [{"String": "LD50 RAT ORAL 1000 MG/KG"}]},
                                "Reference": ["HSDB, peer reviewed"],
                            },
                            {
                                "Value": {"StringWithMarkup": [{"String": "LD50 RABBIT ORAL 1800 MG/KG"}]},
                            },
                            # Same statement, different case: one node, not two.
                            {
                                "Value": {"StringWithMarkup": [{"String": "ld50 rat oral 1000 mg/kg"}]},
                            },
                        ],
                    },
                ],
            }
        ]
    }
}


class _StubToxicity(scigraph.PubChemToxicityConnector):
    """PubChem Toxicity with canned HTTP, so the test never touches the network."""

    def __init__(self, document=None, props=None, cids=None):
        super().__init__(
            scigraph.KnowledgeCache(str(Path(_TEST_DIR) / "cache.sqlite")),
            scigraph.UniversalIDTranslator(),
        )
        self._document = document
        self._props = props if props is not None else {
            "PropertyTable": {
                "Properties": [
                    {
                        "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                        "Title": "Aspirin",
                        "MolecularFormula": "C9H8O4",
                    }
                ]
            }
        }
        self._cids = cids if cids is not None else {"IdentifierList": {"CID": [2244]}}

    async def _safe_get(self, session, url, timeout=8.0):
        if "pug_view" in url:
            return self._document
        if "property" in url:
            return self._props
        return self._cids


def _kind(entity) -> str:
    """Entity type as its declared value ("Toxicity"), not "EntityType.TOXICITY"."""
    return getattr(entity.entity_type, "value", str(entity.entity_type))


class ToxicityStatements(unittest.TestCase):
    def collect(self, query, query_type="auto", document=TOX_DOCUMENT):
        connector = _StubToxicity(document=document)
        return asyncio.run(connector.search(session=None, query=query, query_type=query_type))

    def test_statements_become_nodes_joined_to_the_compound(self):
        entities, relations = self.collect("Aspirin")

        compound_uid = "COMPOUND:BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
        tox = [e for e in entities if _kind(e) == "Toxicity"]
        self.assertEqual(len(tox), 3)
        self.assertEqual(len(relations), 3)
        for rel in relations:
            self.assertEqual(rel.source_uid, compound_uid)
            self.assertIn(rel.target_uid, {e.uid for e in tox})
            self.assertEqual(rel.relationship_type(), "HAS_TOXICITY")

        carc = next(e for e in tox if e.attributes["toxicity_section"] == "Carcinogen Classification")
        self.assertEqual(carc.attributes["toxicity_statement"],
                         "IARC Group 2A: Probably carcinogenic to humans")
        # The citation travels with the statement: a hazard claim without its
        # source is not usable in a report.
        self.assertEqual(carc.evidence[0].citation, "IARC Monographs Vol 53 (1991)")
        self.assertIn("pubchem.ncbi.nlm.nih.gov", carc.evidence[0].source_url)
        self.assertEqual(carc.attributes["pubchem_cid"], "2244")

    def test_duplicate_statements_are_one_node(self):
        _entities, relations = self.collect("Aspirin")
        rat = [r for r in relations
               if "1000 MG/KG" in r.attributes["mechanism_of_action"].upper()]
        self.assertEqual(len(rat), 1)

    def test_a_compound_with_no_toxicity_section_yields_nothing(self):
        entities, relations = self.collect("Aspirin", document={"Record": {"Section": []}})
        self.assertEqual(entities, [])
        self.assertEqual(relations, [])

    def test_a_protein_query_does_not_ask_pubchem_for_toxicity(self):
        entities, relations = self.collect("EGFR", query_type="protein")
        self.assertEqual((entities, relations), ([], []))

    def test_registry_entry_is_a_real_connector(self):
        classes = scigraph.ConnectorRegistry.get_all()
        self.assertIn("PubChem Toxicity", classes)
        # The catalogue fallbacks are built inside a factory; this one is not.
        self.assertNotIn("<locals>", classes["PubChem Toxicity"].__qualname__)


class PatentRouting(unittest.TestCase):
    """A patent-capable query must reach the Patents connector."""

    def test_identifier_shaped_queries_include_patents(self):
        for query in ("CHEMBL25", "2244", "CID:2244", "CHEBI:2244", "C00003", "DB00001"):
            route = scigraph.QueryRouter.route(query)
            self.assertIn("Patents", route, f"{query} routed away from patents")
            self.assertIn("PubChem Toxicity", route, f"{query} routed away from toxicity")

    def test_plain_names_include_patents_and_toxicity(self):
        for query in ("Aspirin", "Nitrapyrin", "Ammonia Monooxygenase", "P23219"):
            route = scigraph.QueryRouter.route(query)
            self.assertIn("Patents", route)
            self.assertIn("PubChem Toxicity", route)

    def test_literature_identifiers_do_not_route_to_patents(self):
        # A DOI is not a patentable subject: fanning out to Patents would only
        # spend the hop's time budget on a search link nobody asked for.
        self.assertNotIn("Patents", scigraph.QueryRouter.route("10.1038/nature12373"))


class _StubPatents(scigraph.PatentConnector):
    def __init__(self, patents=None, records=None):
        super().__init__(
            scigraph.KnowledgeCache(str(Path(_TEST_DIR) / "cache.sqlite")),
            scigraph.UniversalIDTranslator(),
        )
        self._patents = patents
        self._records = records or []

    async def _safe_get(self, session, url, timeout=8.0):
        if self._patents is None:
            return None
        return {"InformationList": {"Information": [{"PatentID": self._patents}]}}

    async def _google_patents_records(self, session, query, budget=None):
        return self._records


class PatentNeverEmpty(unittest.TestCase):
    def test_a_search_link_is_still_a_row(self):
        """When every lookup fails the connector reports a search link.

        Returning nothing is the failure mode that produced \"no patent rows at
        all\": the user cannot tell an empty connector from a compound with no
        patents, and there is no link to follow up with.
        """
        connector = _StubPatents(patents=None)
        entities, relations = asyncio.run(connector.search(session=None, query="Fictionalium"))
        patents = [e for e in entities if _kind(e) == "Patent"]
        self.assertEqual(len(patents), 1)
        self.assertTrue(patents[0].uid.startswith("PATENTSEARCH:"))
        self.assertIn("google_patents", " ".join(patents[0].attributes))
        self.assertEqual(relations, [])

    def test_a_huge_patent_payload_is_capped_not_dropped(self):
        connector = _StubPatents(patents=[f"US{1000000 + i}" for i in range(5000)])
        entities, _relations = asyncio.run(connector.search(session=None, query="Aspirin"))
        patents = [e for e in entities if _kind(e) == "Patent"]
        self.assertEqual(len(patents), 200)
        self.assertTrue(all(e.uid.startswith("PATENT:US") for e in patents))

    def test_the_connector_budget_stays_inside_the_expander_limit(self):
        # A connector cancelled at the limit returns nothing, so its own budget
        # must be the smaller number.
        self.assertLess(scigraph.PatentConnector.BUDGET_SECONDS,
                        scigraph.PatentConnector.TIMEOUT)
        self.assertGreater(scigraph.PatentConnector.TIMEOUT,
                           scigraph.BaseConnector.TIMEOUT)


class RelationEndpoints(unittest.TestCase):
    """Edges are exported only when both of their nodes are exported."""

    def setUp(self):
        self.compound = scigraph.Entity(
            uid="COMPOUND:MXWJVTOOROXGIU-UHFFFAOYSA-N",
            entity_type=scigraph.EntityType.COMPOUND,
            preferred_name="6-chloro-4-N-ethyl-2-N-propan-2-yl-1,3,5-triazine-2,4-diamine",
            canonical_id="MXWJVTOOROXGIU-UHFFFAOYSA-N",
            synonyms={"Atrazine"},
        )
        self.compound.add_cross_ref(scigraph.DatabaseSource.PUBCHEM, "2256")
        self.compound.add_cross_ref("KEGG", "C06552")
        self.patent = scigraph.Entity(
            uid="PATENT:US-20190021252-A1",
            entity_type=scigraph.EntityType.PATENT,
            preferred_name="US Patent US-20190021252-A1 (Atrazine)",
            canonical_id="US-20190021252-A1",
        )

    def _relation(self, source, target, **attributes):
        return scigraph.Relation(
            source_uid=source,
            target_uid=target,
            relation_type=scigraph.RelationType.PATENTED_IN,
            attributes=attributes,
        )

    def test_invented_compound_ids_are_pointed_at_the_real_node(self):
        entities = [self.compound, self.patent]
        relations = [
            self._relation("COMPOUND:C06552", self.patent.uid),
            self._relation("COMPOUND:2256", self.patent.uid),
            self._relation("COMPOUND:ATRAZINE", self.patent.uid),
        ]
        kept, dropped = scigraph.repoint_relation_endpoints(entities, relations)
        self.assertEqual(dropped, 0)
        self.assertEqual({rel.source_uid for rel in kept}, {self.compound.uid})
        self.assertEqual(len(kept), 3)

    def test_the_connectors_own_query_string_is_the_last_resort(self):
        entities = [self.compound, self.patent]
        relations = [self._relation("PROTEIN:PDBQUERY:c4002849", self.patent.uid,
                                    query="Atrazine")]
        kept, dropped = scigraph.repoint_relation_endpoints(entities, relations)
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0].source_uid, self.compound.uid)

    def test_a_truly_unknown_endpoint_is_dropped_not_exported(self):
        kept, dropped = scigraph.repoint_relation_endpoints(
            [self.compound, self.patent],
            [self._relation("COMPOUND:NOSUCHTHING", self.patent.uid)],
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)

    def test_a_self_loop_is_dropped(self):
        kept, dropped = scigraph.repoint_relation_endpoints(
            [self.compound, self.patent],
            [self._relation("COMPOUND:ATRAZINE", self.compound.uid)],
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)


class ToxicityVerdict(unittest.TestCase):
    """The verdict reads the statements the engine actually wrote."""

    NODES = [
        {"uid:ID": "COMPOUND:BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "name": "Aspirin",
         ":LABEL": "Entity;Compound"},
        {"uid:ID": "TOXICITY:2244:abc123", "name": "Carcinogen Classification: Group 3",
         ":LABEL": "Entity;Toxicity", "toxicity_section": "Carcinogen Classification",
         "bioactivity_summary": "IARC Group 3: Not classifiable as to its carcinogenicity",
         "source_citation": "IARC Monographs Vol 73"},
    ]
    EDGES = [
        {
            ":START_ID": "COMPOUND:BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            ":END_ID": "TOXICITY:2244:abc123",
            ":TYPE": "HAS_TOXICITY",
        },
    ]

    def test_statements_are_passed_through_with_their_citation(self):
        profile = decision.toxicity_profile(self.NODES, self.EDGES)
        self.assertEqual(profile["statement_count"], 1)
        self.assertEqual(profile["compound_count"], 1)
        statement = profile["compounds"][0]["statements"][0]
        self.assertEqual(profile["compounds"][0]["name"], "Aspirin")
        self.assertEqual(statement["section"], "Carcinogen Classification")
        self.assertIn("IARC Group 3", statement["statement"])
        self.assertEqual(statement["citation"], "IARC Monographs Vol 73")

    def test_absence_is_not_reported_as_safety(self):
        profile = decision.toxicity_profile([self.NODES[0]], [])
        self.assertEqual(profile["statement_count"], 0)
        self.assertIn("not that they are safe", profile["reading"])
        self.assertIn("not evidence of safety", profile["caveat"])


if __name__ == "__main__":
    unittest.main()
