# SciGraph Enterprise Version Changelog

## [3.1.0] - 2.026-07-22 (Ultra Release with Active REST Connectors & Search Library Builder)

### Added
- **8 New Active REST Connectors**:
  - **Guide to Pharmacology (`GtoPdb`)**: Fetches selective ligands, $pIC_{50}$, $pK_i$ values, and mechanism classifications.
  - **STRING DB (`STRING`)**: Fetches physical PPI networks, combined confidence scores (`combined_score`), and interactors.
  - **PDBe-KB (`PDBe-KB`)**: Fetches co-crystallized ligand IDs, chemical names, and PDB structural binding pockets.
  - **OpenAlex (`OpenAlex`)**: Fetches scholarly publications, citation counts (`cited_by_count`), DOIs, concepts.
  - **NCBI Gene (`NCBI Gene`)**: Fetches gene symbols, locus tags, orthologs, chromosome locations.
  - **NCBI Taxonomy (`NCBI Taxonomy`)**: Fetches TaxIDs, organism scientific names, lineage.
  - **CompTox (`EPA CompTox`)**: Fetches environmental toxicity and hazard classifications.
  - **Rhea (`Rhea`)**: Fetches EC enzyme catalytic reaction equations and RHEA IDs.
- **Interactive Query Assistant (`QueryAssistant`)**:
  - Generates domain-aware clarifying questions (organism, bioactivity cutoff, chemical class, depth) for any query.
- **Search Library Builder (`SearchLibraryBuilder`)**:
  - Compiles structured, reusable **Search Libraries** (`search_library.json`, `library_queries.txt`, `library_summary.md`) for batch processing.

---

## [3.0.0] - 2026-07-22 (Target Bioactivity & Inhibitor Data Integration)

### Added
- **ChEMBL Target Bioactivity Retrieval**: Standard types (IC50, Ki, EC50, Kd), values, units, pChEMBL, assay IDs.
- **BRENDA Enzyme Inhibitors**: Nitrapyrin, Allylthiourea, Dicyandiamide, Acetylene, DMPP, substrates.
- **Enhanced Exporters**: Added SMILES, formula, MW, bioactivity summary to nodes and activity metrics to edge CSVs.
