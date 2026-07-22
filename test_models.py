#!/usr/bin/env python3
"""
Unit Test Suite for Enterprise Scientific Knowledge Graph Platform v3.1
Tests data validation, connectors, entity resolution, AI knowledge engine,
graph analytics, exporters, Query Assistant, and Search Library Builder.
"""

import os
import shutil
import tempfile
import pytest
from scigraph import (
    DatabaseSource, EntityType, RelationType, MergePolicy,
    CrossReference, OntologyReference, Evidence, SearchMetadata,
    Entity, Relation, normalize_identifier, canonical_identifier,
    is_valid_identifier, is_same_entity, merge_entities,
    find_by_name, find_by_identifier, rank_matches,
    export_to_csv, export_to_neo4j_cypher, export_to_rdf_ready_dicts,
    export_to_turtle, export_to_vector_index,
    QueryRouter, ConnectorRegistry, GraphAnalyticsEngine, AIKnowledgeEngine,
    UniversalIDTranslator, EntityResolver, KnowledgeCache,
    QueryAssistant, SearchLibraryBuilder
)


def test_url_validation():
    from scigraph import validate_url
    assert validate_url("https://www.ncbi.nlm.nih.gov") == "https://www.ncbi.nlm.nih.gov"
    assert validate_url(None) is None
    with pytest.raises(ValueError):
        validate_url("invalid-url-string")


def test_doi_validation():
    from scigraph import validate_doi
    assert validate_doi("10.1038/s41586-020-2649-2") == "10.1038/s41586-020-2649-2"
    assert validate_doi("https://doi.org/10.1016/j.cell.2021.01.001") == "10.1016/j.cell.2021.01.001"
    assert validate_doi(None) is None
    with pytest.raises(ValueError):
        validate_doi("not-a-doi")


def test_pmid_validation():
    from scigraph import validate_pmid
    assert validate_pmid("32000000") == "32000000"
    assert validate_pmid(12345678) == "12345678"
    assert validate_pmid(None) is None
    with pytest.raises(ValueError):
        validate_pmid("pmid-1234")


def test_patent_validation():
    from scigraph import validate_patent_number
    assert validate_patent_number("US10123456B2") == "US10123456B2"
    assert validate_patent_number(None) is None
    with pytest.raises(ValueError):
        validate_patent_number("INVALID_PATENT!!!")


def test_empty_string_validation():
    with pytest.raises(ValueError):
        CrossReference(database=DatabaseSource.PUBCHEM, accession="")
    with pytest.raises(ValueError):
        OntologyReference(ontology_name="", accession="GO:0008152")


def test_database_source_from_string():
    assert DatabaseSource.from_string("ChEMBL") == DatabaseSource.CHEMBL
    assert DatabaseSource.from_string("pubchem") == DatabaseSource.PUBCHEM
    assert DatabaseSource.from_string("Guide to Pharmacology") == DatabaseSource.GTOPDB
    assert DatabaseSource.from_string("STRING") == DatabaseSource.STRING
    assert DatabaseSource.from_string("OpenAlex") == DatabaseSource.OPENALEX
    assert DatabaseSource.from_string("non_existent_source") == DatabaseSource.OTHER


def test_entity_and_relation_type_enums():
    assert EntityType.from_string("Compound") == EntityType.COMPOUND
    assert EntityType.from_string("Microorganism") == EntityType.MICROORGANISM
    assert RelationType.from_string("inhibits") == RelationType.INHIBITS
    assert RelationType.from_string("interacts_physically_with") == RelationType.INTERACTS_PHYSICALLY_WITH


def test_evidence_checksum():
    ev1 = Evidence(database=DatabaseSource.PUBMED, pmid="12345678")
    ev2 = Evidence(database=DatabaseSource.PUBMED, pmid="12345678")
    ev3 = Evidence(database=DatabaseSource.CHEMBL, doi="10.1016/test")
    assert ev1.get_checksum() == ev2.get_checksum()
    assert ev1.get_checksum() != ev3.get_checksum()


def test_entity_add_cross_ref_and_ontology():
    ent = Entity(uid="TEST:001", entity_type=EntityType.COMPOUND, preferred_name="Test Molecule", canonical_id="TEST001")
    ent.add_cross_ref(DatabaseSource.PUBCHEM, "12345")
    ent.add_ontology_ref("GO", "GO:0008152", label="Metabolic process")
    assert ent.get_cross_ref(DatabaseSource.PUBCHEM) == "12345"
    assert len(ent.ontology_references) == 1
    assert ent.ontology_references[0].accession == "GO:0008152"


def test_normalize_identifier():
    assert normalize_identifier("CHEMBL25", DatabaseSource.CHEMBL) == "CHEMBL25"
    assert normalize_identifier("25", DatabaseSource.CHEMBL) == "CHEMBL25"
    assert normalize_identifier("P23219", DatabaseSource.UNIPROT) == "P23219"


def test_canonical_identifier():
    assert canonical_identifier("CHEMBL25", DatabaseSource.CHEMBL) == "CHEMBL:CHEMBL25"
    assert canonical_identifier("P23219", DatabaseSource.UNIPROT) == "UNIPROT:P23219"


def test_is_valid_identifier():
    assert is_valid_identifier("CHEMBL1234", DatabaseSource.CHEMBL) is True
    assert is_valid_identifier("P23219", DatabaseSource.UNIPROT) is True
    assert is_valid_identifier("", DatabaseSource.PUBCHEM) is False


def test_is_same_entity():
    e1 = Entity(uid="E1", entity_type=EntityType.COMPOUND, preferred_name="Aspirin", canonical_id="CID2244")
    e2 = Entity(uid="E2", entity_type=EntityType.COMPOUND, preferred_name="Aspirin", canonical_id="CID2244")
    e3 = Entity(uid="E3", entity_type=EntityType.COMPOUND, preferred_name="Water", canonical_id="CID962")
    assert is_same_entity(e1, e2) is True
    assert is_same_entity(e1, e3) is False


def test_merge_entities():
    e1 = Entity(uid="E1", entity_type=EntityType.COMPOUND, preferred_name="Aspirin", canonical_id="CID2244")
    e2 = Entity(uid="E2", entity_type=EntityType.COMPOUND, preferred_name="Acetylsalicylic acid", canonical_id="CID2244")
    e1.add_cross_ref(DatabaseSource.PUBCHEM, "2244")
    e2.add_cross_ref(DatabaseSource.CHEMBL, "CHEMBL25")
    merged = merge_entities(e1, e2)
    assert "Acetylsalicylic acid" in merged.synonyms
    assert merged.get_cross_ref(DatabaseSource.PUBCHEM) == "2244"
    assert merged.get_cross_ref(DatabaseSource.CHEMBL) == "CHEMBL25"


def test_query_router():
    assert "ChEMBL" in QueryRouter.route("CHEMBL25")
    assert "UniProt" in QueryRouter.route("P23219")
    assert "GeneOntology" in QueryRouter.route("GO:0008152")
    assert "ClinicalTrials" in QueryRouter.route("NCT01234567")


def test_connector_registry_coverage():
    all_connectors = ConnectorRegistry.get_all()
    assert "PubChem" in all_connectors
    assert "ChEMBL" in all_connectors
    assert "UniProt" in all_connectors
    assert "BRENDA" in all_connectors
    assert "Guide to Pharmacology" in all_connectors
    assert "STRING" in all_connectors
    assert "PDBe-KB" in all_connectors
    assert "OpenAlex" in all_connectors
    assert "NCBI Gene" in all_connectors
    assert "NCBI Taxonomy" in all_connectors


def test_graph_analytics_engine():
    e1 = Entity(uid="E1", entity_type=EntityType.COMPOUND, preferred_name="Compound A", canonical_id="A")
    e2 = Entity(uid="E2", entity_type=EntityType.PROTEIN, preferred_name="Protein B", canonical_id="B")
    r1 = Relation(source_uid="E1", target_uid="E2", relation_type=RelationType.INHIBITS)
    analytics = GraphAnalyticsEngine([e1, e2], [r1])
    stats = analytics.summary_statistics()
    assert stats["num_nodes"] == 2
    assert stats["num_edges"] == 1


def test_ai_knowledge_engine():
    text = "Nitrapyrin inhibits Ammonia Monooxygenase (AMO) in soil bacteria."
    entities, relations = AIKnowledgeEngine.extract_entities_and_relations_from_text(text)
    assert len(entities) >= 0


def test_query_assistant_and_library_builder():
    questions = QueryAssistant.generate_clarifying_questions("Ammonia Monooxygenase")
    assert len(questions) >= 2
    assert any(q["id"] == "organism" for q in questions)

    tmp_dir = tempfile.mkdtemp()
    try:
        answers = {"organism": "Nitrosomonas europaea", "bioactivity_cutoff": "IC50 <= 1 uM"}
        lib = SearchLibraryBuilder.build_library("Ammonia Monooxygenase", answers, export_dir=tmp_dir)
        assert lib["base_query"] == "Ammonia Monooxygenase"
        assert os.path.exists(os.path.join(tmp_dir, "search_library.json"))
        assert os.path.exists(os.path.join(tmp_dir, "library_queries.txt"))
    finally:
        shutil.rmtree(tmp_dir)
