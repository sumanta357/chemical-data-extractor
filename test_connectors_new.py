#!/usr/bin/env python3
"""
Unit Tests for New and Fixed Connectors (PDBe-KB, KEGG, ChEBI, Reactome)
Tests API response parsing, entity/relation creation, error handling, and edge cases.
"""

import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scigraph import (
    Entity,
    EntityType,
    Relation,
    RelationType,
    DatabaseSource,
    KnowledgeCache,
    UniversalIDTranslator,
    QueryRouter,
    PDBeKBConnector,
    KEGGConnector,
    ChEBIConnector,
    ReactomeConnector,
    ConnectorRegistry,
)


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.get = AsyncMock()
    session.post = AsyncMock()
    return session


@pytest.fixture
def temp_cache():
    tmp_path = tempfile.mktemp(suffix=".sqlite")
    cache = KnowledgeCache(tmp_path)
    yield cache
    try:
        os.remove(tmp_path)
    except OSError:
        pass


@pytest.fixture
def translator():
    return UniversalIDTranslator()


@pytest.fixture
def pdbe_connector(temp_cache, translator):
    conn = PDBeKBConnector(temp_cache, translator)
    conn._safe_get = AsyncMock()
    conn._safe_post = AsyncMock()
    return conn


@pytest.fixture
def kegg_connector(temp_cache, translator):
    conn = KEGGConnector(temp_cache, translator)
    # KEGG uses _safe_get for compound/reaction JSON lookups
    # and _raw_get for pathway/keyword text lookups
    conn._safe_get = AsyncMock()
    conn._raw_get = AsyncMock()
    return conn


@pytest.fixture
def chebi_connector(temp_cache, translator):
    conn = ChEBIConnector(temp_cache, translator)
    conn._safe_get = AsyncMock()
    conn._safe_post = AsyncMock()
    return conn


@pytest.fixture
def reactome_connector(temp_cache, translator):
    conn = ReactomeConnector(temp_cache, translator)
    conn._safe_get = AsyncMock()
    return conn


def async_test(coro):
    return asyncio.run(coro)


# ==============================================================================
# 1. PDBe-KB Connector Tests
# ==============================================================================

class TestPDBeKBConnector:
    """PDBe-KB: RCSB Search API + PDBe direct API integration."""

    def test_registered_in_registry(self):
        assert "PDBe-KB" in ConnectorRegistry.get_all()

    def test_pdb_id_pattern_direct_lookup(self, pdbe_connector, mock_session):
        """Direct PDB ID (e.g. 4PH9) -> PDBe summary + ligand monomers API."""
        query = "4PH9"
        pid_lower = "4ph9"

        # Mock PDBe summary response — PDBe API returns lowercase keys
        summary_response = {
            pid_lower: [{
                "title": "Crystal Structure of Prostaglandin G/H Synthase 1",
                "deposition_date": "2015-01-15",
                "release_date": "2015-03-01",
            }]
        }
        # Mock PDBe ligand response
        ligand_response = {
            pid_lower: [
                {
                    "chem_comp_id": "ASP",
                    "chem_comp_name": "ASPIRIN",
                    "formula": "C9H8O4",
                }
            ]
        }
        pdbe_connector._safe_get.side_effect = [summary_response, ligand_response]

        entities, relations = async_test(
            pdbe_connector.search(mock_session, query)
        )

        # Two _safe_get calls: summary + ligands
        assert pdbe_connector._safe_get.call_count == 2
        summary_url = pdbe_connector._safe_get.call_args_list[0][0][1]
        lig_url = pdbe_connector._safe_get.call_args_list[1][0][1]
        assert "pdbe/api/pdb/entry/summary" in summary_url
        assert "ligand_monomers" in lig_url

        # 1 structure + 1 compound
        structures = [e for e in entities if e.entity_type == EntityType.STRUCTURE]
        compounds = [e for e in entities if e.entity_type == EntityType.COMPOUND]
        assert len(structures) == 1
        assert len(compounds) == 1
        assert structures[0].canonical_id == query  # stored uppercased

        # BINDS relation between compound (source) -> structure (target)
        binds = [r for r in relations if r.relation_type == RelationType.BINDS]
        assert len(binds) == 1
        assert binds[0].source_uid.startswith("COMPOUND:")
        assert binds[0].target_uid == f"PDB:{query}"

    def test_text_search_via_rcsb(self, pdbe_connector, mock_session):
        """Text queries -> RCSB Search API + HAS_STRUCTURE relations."""
        query = "Aspirin"
        rcsb_response = {
            "result_set": [
                {"identifier": "4PH9", "score": 0.95},
                {"identifier": "3KK6", "score": 0.85},
            ]
        }
        pdbe_connector._safe_post.return_value = rcsb_response

        entities, relations = async_test(
            pdbe_connector.search(mock_session, query)
        )

        # RCSB POST called once
        pdbe_connector._safe_post.assert_called_once()
        post_url = pdbe_connector._safe_post.call_args[0][1]
        assert "search.rcsb.org/rcsbsearch/v2/query" in post_url

        assert len(entities) == 2
        for e in entities:
            assert e.entity_type == EntityType.STRUCTURE
        pdb_ids = {e.canonical_id for e in entities}
        assert "4PH9" in pdb_ids
        assert "3KK6" in pdb_ids

        # Each PDB gets a HAS_STRUCTURE relation
        has_struct = [r for r in relations if r.relation_type == RelationType.HAS_STRUCTURE]
        assert len(has_struct) == 2

    def test_text_search_empty_results(self, pdbe_connector, mock_session):
        """Empty result_set -> empty lists."""
        pdbe_connector._safe_post.return_value = {"result_set": []}
        entities, relations = async_test(
            pdbe_connector.search(mock_session, "XyzzyNonExistent")
        )
        assert entities == []
        assert relations == []

    def test_text_search_none_response(self, pdbe_connector, mock_session):
        """_safe_post returns None (API error) -> empty lists."""
        pdbe_connector._safe_post.return_value = None
        entities, relations = async_test(
            pdbe_connector.search(mock_session, "Aspirin")
        )
        assert entities == []
        assert relations == []

    def test_direct_pdb_no_ligands(self, pdbe_connector, mock_session):
        """PDB ID with no ligands still returns the structure entity."""
        pid_lower = "1crn"
        pdbe_connector._safe_get.side_effect = [
            {pid_lower: [{"title": "Crambin", "deposition_date": "1980-01-01", "release_date": "1981-01-01"}]},
            {},  # no ligands
        ]
        entities, relations = async_test(
            pdbe_connector.search(mock_session, "1CRN")
        )
        structures = [e for e in entities if e.entity_type == EntityType.STRUCTURE]
        assert len(structures) == 1
        assert structures[0].canonical_id == "1CRN"
        compounds = [e for e in entities if e.entity_type == EntityType.COMPOUND]
        assert len(compounds) == 0

    def test_rcsb_payload_structure(self, pdbe_connector, mock_session):
        """Verify RCSB payload uses terminal full_text service."""
        pdbe_connector._safe_post.return_value = {"result_set": []}
        async_test(pdbe_connector.search(mock_session, "COX-1"))
        args, _ = pdbe_connector._safe_post.call_args
        payload = args[2]
        assert payload["query"]["type"] == "terminal"
        assert payload["query"]["service"] == "full_text"
        assert payload["query"]["parameters"]["value"] == "COX-1"


# ==============================================================================
# 2. KEGG Connector Tests
# ==============================================================================

class TestKEGGConnector:
    """KEGG: REST API for compounds, reactions, pathways, and keyword search."""

    def test_registered_in_registry(self):
        assert "KEGG" in ConnectorRegistry.get_all()

    def test_compound_id_lookup(self, kegg_connector, mock_session):
        """C##### -> JSON compound details via _safe_get."""
        kegg_connector._safe_get.return_value = {
            "C00001": {
                "name": "Water; H2O; Dihydrogen oxide",
                "formula": "H2O",
                "exact_mass": 18.01056,
                "mol_weight": 18.01528,
            }
        }
        entities, relations = async_test(
            kegg_connector.search(mock_session, "C00001")
        )
        assert len(entities) == 1
        e = entities[0]
        assert e.entity_type == EntityType.COMPOUND
        assert "Water" in e.preferred_name
        assert e.attributes.get("formula") == "H2O"
        assert e.get_cross_ref(DatabaseSource.KEGG) == "C00001"

    def test_reaction_id_lookup(self, kegg_connector, mock_session):
        """R##### -> JSON reaction details via _safe_get."""
        kegg_connector._safe_get.return_value = {
            "R00001": {
                "name": "H2O + CO2 <=> H2CO3",
                "definition": "Water + CO2 -> Carbonic Acid",
            }
        }
        entities, relations = async_test(
            kegg_connector.search(mock_session, "R00001")
        )
        assert len(entities) == 1
        e = entities[0]
        assert e.entity_type == EntityType.REACTION
        assert "CO2" in e.preferred_name

    def test_pathway_id_lookup(self, kegg_connector, mock_session):
        """mapXXXXX -> tab-separated pathway list via _raw_get."""
        kegg_connector._raw_get.return_value = (
            "map00010\tGlycolysis / Gluconeogenesis\n"
            "map00020\tCitrate cycle (TCA cycle)\n"
        )
        entities, relations = async_test(
            kegg_connector.search(mock_session, "map00010")
        )
        assert len(entities) >= 1
        assert any(e.entity_type == EntityType.PATHWAY for e in entities)

    def test_keyword_search(self, kegg_connector, mock_session):
        """Plain text -> compound keyword search via _raw_get."""
        kegg_connector._raw_get.return_value = (
            "C00001\tWater\n"
            "C01322\tHeavy water\n"
        )
        entities, relations = async_test(
            kegg_connector.search(mock_session, "water")
        )
        assert len(entities) >= 1
        assert any(e.entity_type == EntityType.COMPOUND for e in entities)

    def test_compound_uses_safe_get(self, kegg_connector, mock_session):
        """Verify compound lookups call _safe_get, not _raw_get."""
        kegg_connector._safe_get.return_value = {"C00001": {"name": "Water", "entry_id": "C00001"}}
        async_test(kegg_connector.search(mock_session, "C00001"))
        assert kegg_connector._safe_get.call_count == 1
        assert kegg_connector._raw_get.call_count == 0

    def test_keyword_uses_raw_get(self, kegg_connector, mock_session):
        """Verify keyword searches call _raw_get, not _safe_get."""
        kegg_connector._raw_get.return_value = "C00001\tWater\n"
        async_test(kegg_connector.search(mock_session, "water"))
        assert kegg_connector._raw_get.call_count == 1
        assert kegg_connector._safe_get.call_count == 0

    def test_compound_no_data(self, kegg_connector, mock_session):
        """_safe_get returns None -> empty lists."""
        kegg_connector._safe_get.return_value = None
        e, r = async_test(kegg_connector.search(mock_session, "C99999"))
        assert e == []
        assert r == []


# ==============================================================================
# 3. ChEBI Connector Tests
# ==============================================================================

class TestChEBIConnector:
    """ChEBI 2.0 REST API for compound lookup and keyword search."""

    def test_registered_in_registry(self):
        assert "ChEBI" in ConnectorRegistry.get_all()

    def test_chebi_id_lookup(self, chebi_connector, mock_session):
        """CHEBI:NNNNN -> GET compound details."""
        chebi_connector._safe_get.return_value = {
            "chebiAsciiName": "water",
            "chemicalFormula": "H2O",
            "exactMass": 18.01056,
            "chebiId": "CHEBI:15377",
        }
        entities, relations = async_test(
            chebi_connector.search(mock_session, "CHEBI:15377")
        )
        assert len(entities) == 1
        e = entities[0]
        assert e.entity_type == EntityType.COMPOUND
        assert "water" in e.preferred_name
        assert e.attributes.get("formula") == "H2O"
        assert e.get_cross_ref(DatabaseSource.CHEBI) == "CHEBI:15377"

    def test_keyword_search(self, chebi_connector, mock_session):
        """Plain text -> POST advanced search."""
        chebi_connector._safe_post.return_value = {
            "listElement": [
                {
                    "chebiId": "CHEBI:15377",
                    "chebiAsciiName": "water",
                    "chemicalFormula": "H2O",
                    "exactMass": 18.01056,
                },
                {
                    "chebiId": "CHEBI:29191",
                    "chebiAsciiName": "hydroxide",
                    "chemicalFormula": "HO-",
                    "exactMass": 17.00735,
                },
            ]
        }
        entities, relations = async_test(
            chebi_connector.search(mock_session, "water")
        )
        assert len(entities) >= 1
        assert any(e.entity_type == EntityType.COMPOUND for e in entities)
        # Verify advanced search POST was called
        chebi_connector._safe_post.assert_called_once()
        post_url = chebi_connector._safe_post.call_args[0][1]
        assert "advanced_search" in post_url

    def test_none_response(self, chebi_connector, mock_session):
        """_safe_get returns None -> empty lists."""
        chebi_connector._safe_get.return_value = None
        e, r = async_test(chebi_connector.search(mock_session, "CHEBI:00000"))
        assert e == []
        assert r == []

    def test_empty_list_element(self, chebi_connector, mock_session):
        """Advanced search returns empty listElement -> empty lists."""
        chebi_connector._safe_post.return_value = {"listElement": []}
        e, r = async_test(chebi_connector.search(mock_session, "nonexistent"))
        assert e == []
        assert r == []

    def test_missing_fields_in_response(self, chebi_connector, mock_session):
        """Partial response still creates an entity with defaults."""
        chebi_connector._safe_get.return_value = {
            "chebiAsciiName": "unknown_compound",
            "chebiId": "CHEBI:99999",
        }
        e, r = async_test(chebi_connector.search(mock_session, "CHEBI:99999"))
        assert len(e) == 1
        assert e[0].preferred_name == "unknown_compound"
        assert e[0].attributes.get("formula", "") == ""


# ==============================================================================
# 4. Reactome Connector Tests
# ==============================================================================

class TestReactomeConnector:
    """Reactome Content Service for pathways, reactions, and participants."""

    def test_registered_in_registry(self):
        assert "Reactome" in ConnectorRegistry.get_all()

    def test_reactome_id_lookup(self, reactome_connector, mock_session):
        """R-XXX-NNNNN -> pathway/reaction detail + participants."""
        query_id = "R-HSA-109582"
        reactome_connector._safe_get.side_effect = [
            # Pathway/reaction details
            {
                "stId": "R-HSA-109582",
                "displayName": "Hemostasis",
                "schemaClass": "Pathway",
                "species": [{"displayName": "Homo sapiens"}],
            },
            # Participants (up to 5)
            [
                {
                    "stId": "R-HSA-140834",
                    "displayName": "Fibrinogen",
                    "schemaClass": "EntityWithAccessionedSequence",
                    "species": [{"displayName": "Homo sapiens"}],
                },
                {
                    "stId": "R-HSA-140877",
                    "displayName": "Thrombin",
                    "schemaClass": "EntityWithAccessionedSequence",
                    "species": [{"displayName": "Homo sapiens"}],
                },
            ],
        ]
        entities, relations = async_test(
            reactome_connector.search(mock_session, query_id)
        )

        # 2 _safe_get calls: details + participants
        assert reactome_connector._safe_get.call_count == 2
        detail_url = reactome_connector._safe_get.call_args_list[0][0][1]
        assert "ContentService/data/query" in detail_url

        # 1 pathway + 2 participant proteins = 3 total entities
        assert len(entities) == 3
        pathways = [e for e in entities if e.entity_type == EntityType.PATHWAY]
        proteins = [e for e in entities if e.entity_type == EntityType.PROTEIN]
        assert len(pathways) == 1
        assert len(proteins) == 2
        assert pathways[0].canonical_id == query_id

        # Relations should connect participants to the pathway
        assert len(relations) >= 1

    def test_keyword_search(self, reactome_connector, mock_session):
        """Plain text -> pathway search + fallback direct query."""
        reactome_connector._safe_get.side_effect = [
            # Pathway search returns 1 result
            [
                {
                    "stId": "R-HSA-109582",
                    "displayName": "Hemostasis",
                    "species": [{"displayName": "Homo sapiens"}],
                },
            ],
            # Direct query returns no new results (deduplication)
            None,
        ]
        entities, relations = async_test(
            reactome_connector.search(mock_session, "hemostasis")
        )
        assert reactome_connector._safe_get.call_count == 2
        pathway_url = reactome_connector._safe_get.call_args_list[0][0][1]
        assert "pathways/low/entity" in pathway_url

        pathways = [e for e in entities if e.entity_type == EntityType.PATHWAY]
        assert len(pathways) >= 1
        assert any("Hemostasis" in e.preferred_name for e in pathways)

    def test_reactome_id_no_participants(self, reactome_connector, mock_session):
        """ID with no participants returns just the main entity."""
        reactome_connector._safe_get.side_effect = [
            {"stId": "R-HSA-123456", "displayName": "Some Pathway", "schemaClass": "Pathway", "species": [{"displayName": "Homo sapiens"}]},
            None,  # no participants
        ]
        entities, relations = async_test(
            reactome_connector.search(mock_session, "R-HSA-123456")
        )
        assert len(entities) == 1
        assert entities[0].entity_type == EntityType.PATHWAY
        assert relations == []

    def test_keyword_none_results(self, reactome_connector, mock_session):
        """Keyword search with all-null results -> empty lists."""
        reactome_connector._safe_get.side_effect = [None, None]
        e, r = async_test(reactome_connector.search(mock_session, "nonexistent_pathway_xyz"))
        assert e == []
        assert r == []

    def test_keyword_empty_list_fallback(self, reactome_connector, mock_session):
        """Empty pathway list falls through to direct query."""
        reactome_connector._safe_get.side_effect = [
            [],  # no pathways
            {
                "stId": "R-HSA-109582",
                "displayName": "Hemostasis",
                "schemaClass": "Pathway",
            },
        ]
        entities, relations = async_test(
            reactome_connector.search(mock_session, "Hemostasis")
        )
        assert len(entities) >= 1
        assert any(e.entity_type == EntityType.PATHWAY for e in entities)


# ==============================================================================
# 5. Cross-Connector Integration Tests
# ==============================================================================

class TestConnectorIntegration:
    """Consistency checks across all four connectors."""

    def test_all_four_connectors_in_registry(self):
        names = ["PDBe-KB", "KEGG", "ChEBI", "Reactome"]
        all_c = ConnectorRegistry.get_all()
        for n in names:
            assert n in all_c, f"Missing: {n}"

    def test_each_returns_expected_types(self, pdbe_connector, kegg_connector,
                                          chebi_connector, reactome_connector,
                                          mock_session):
        """All return Tuple[List[Entity], List[Relation]]."""
        pdbe_connector._safe_post.return_value = {"result_set": []}
        kegg_connector._safe_get.return_value = None
        kegg_connector._raw_get.return_value = None
        chebi_connector._safe_post.return_value = {"listElement": []}
        reactome_connector._safe_get.side_effect = [None, None]

        for conn, q in [
            (pdbe_connector, "test_query"),
            (kegg_connector, "test_query"),
            (chebi_connector, "test_query"),
            (reactome_connector, "test_query"),
        ]:
            entities, relations = async_test(conn.search(mock_session, q))
            assert isinstance(entities, list)
            assert isinstance(relations, list)
            for e in entities:
                assert isinstance(e, Entity)
            for r in relations:
                assert isinstance(r, Relation)


# ==============================================================================
# 6. QueryRouter Routing Tests
# ==============================================================================

class TestQueryRouterNewRoutes:
    """QueryRouter correctly routes IDs to the new connectors."""

    def test_kegg_compound_route(self):
        route = str(QueryRouter.route("C00001"))
        assert "KEGG" in route

    def test_kegg_reaction_route(self):
        route = str(QueryRouter.route("R00001"))
        assert "KEGG" in route

    def test_chebi_route(self):
        route = str(QueryRouter.route("CHEBI:15377"))
        # Accept either "ChEBI" or "CHEBI" case-insensitively
        assert "CHEBI" in route.upper()

    def test_reactome_route(self):
        route = str(QueryRouter.route("R-HSA-109582"))
        assert "Reactome" in route

    def test_pdb_id_route(self):
        route = str(QueryRouter.route("4PH9"))
        assert "PDBe" in route or "PDB" in route.upper()

    def test_default_route_includes_new_connectors(self):
        route = str(QueryRouter.route("aspirin"))
        upper_route = route.upper()
        assert "KEGG" in upper_route or "kegg" in route
        assert "CHEBI" in upper_route or "chebi" in route.lower()
        assert "REACTOME" in upper_route or "reactome" in route.lower()
        assert "PDBE" in upper_route or "PDBe" in route
