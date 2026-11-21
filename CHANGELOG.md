# SciGraph Enterprise Version Changelog

## [Unreleased]

### Fixed
- **Patent rows could be empty for a whole search.** Two independent causes:
  - `QueryRouter` sent identifier-shaped queries (`CHEMBL25`, `2244`, `CHEBI:2244`,
    `C00003`, `DB#####`) to connector lists that did not include `Patents`, so an
    accession search never ran the patent connector at all.
  - A compound with tens of thousands of patents (Atrazine, 71,220) answers
    PubChem with a multi-megabyte payload. The 8s request timeout expired, the
    slower fallbacks ran past the expander's 25s per-connector limit, and the
    cancelled connector returned nothing — everything it had found was thrown
    away. Patents now load large payloads with a 14s timeout, run to an internal
    18s budget with a longer 40s connector limit, use a single bounded Google
    Patents attempt, and always emit at least the Google Patents search link so a
    search can never come back with zero patent rows.

### Added
- **PubChem Toxicity connector** (`PubChem Toxicity`). The catalogue entries
  `EPA CompTox`, `CompTox Toxicity`, `DSSTox` and `TOXNET` were auto-generated
  no-op connectors, so no toxicity data could ever reach a user. The new
  connector reads the Toxicity section of a compound's PubChem PUG-View record
  and emits one `Toxicity` node per statement — carcinogen classification,
  human and non-human toxicity values, lethal doses, target organs, acute
  effects, ecotoxicity and the HSDB summary — joined to the compound by
  `has_toxicity`, each carrying its section, citation and PubChem link.
  `decision-intel` now reports those statements (`toxicity` block, shown in the
  verdict panel) instead of a "not computed" note, and an empty result is
  reported as "PubChem lists none", never as "non-toxic".

### Note
- The data-source audit and `data_sources_status.json` now include a Toxicology
  group with 19 implemented connectors.

## [3.2.0] - 2026-09-22 (CLI/Docker Version Parity)

### Changed
- **CLI version alignment**: The command-line engine (`api/scigraph.py`) now self-identifies as **v3.2**, matching the Docker deployment (FastAPI service v3.2.2 runs the same engine code). Updated the engine banner, CLI argparse description, per-search header, User-Agent headers (3 call sites), workflow metadata (`workflow_version` → `3.2.0`), and the generated Cypher script header.
- **Dependency alignment**: The CLI dependency lists (`requirements.txt`, `api/requirements.txt`) now match the Docker v3.2 runtime set — added **pydantic** (a hard engine import that was missing) and **orjson** (optional JSON accelerator with stdlib fallback); removed **matplotlib** (listed but never imported by the engine). Parquet support stays optional via lazy `pyarrow` import, matching the Docker runtime.
- Docs updated (`TUTORIAL.md`, `README.md`, `app/api/health` route) to report v3.2 and the new CLI behavior.
- **CLI enrichment parity**: The CLI now runs the **enrichment pipeline** (`api/enrich_exports.py`) after every search by default, producing `enriched_data.xlsx` (multi-sheet Excel with compounds/SMILES/properties, publications, inhibitors) and the `structure_images/` folder — the same post-processing output as the Docker/web deployment. Use `--no-enrich` to skip it (faster, base exports only). In batch mode (`--batch`), enrichment runs **once after the whole batch** since queries share one export directory. The web layer (`python-api/app.py`) passes `--no-enrich` when spawning the engine so enrichment is not run twice.

### Note
- The CLI and the Docker v3.2 deployment run the **same engine source** (`api/scigraph.py` is copied into the Docker image), so features are inherently identical; this release aligns the version identity and installed dependency set.

---

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
