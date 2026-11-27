# SciGraph Enterprise Version Changelog

## [Unreleased]

### Security
- **Removed the AI provider keys that were committed to the repository.**
  `python-api/app.py` shipped literal fallbacks for AgentRouter, Google AI
  Studio and OpenCode Zen, and `app/api/analyze/route.ts` repeated two of them,
  so anyone who could read the repo could spend those credentials and a fresh
  clone worked with no configuration. All three are gone; the analyst now reads
  `OPENCODE_API_KEY`, `AGENTROUTER_API_KEY` and `GEMINI_API_KEY`
  (`GOOGLE_AI_STUDIO_API_KEY` also accepted) from the environment only, and with
  none set `/api/analyze` answers 503 "AI service is not configured" instead of
  calling out with a key nobody can see. **The old values remain in the Git
  history and must be rotated in each provider console** — deleting them from
  HEAD does not revoke them. `tests/test_secrets.py` now fails the build if a
  key-shaped literal is committed again.

### Fixed
- **Downloads failed with "… can't be downloaded securely".** Signed export
  links were built from `request.base_url`, which is `http://` behind Render's
  TLS-terminating proxy (uvicorn only trusts forwarded headers from localhost,
  and the proxy is not localhost). Chrome blocks a download from an `http` URL
  on an `https` page, so every Excel/graph download was refused. The engine now
  derives the browser-facing origin from `PUBLIC_BASE_URL`, then
  `X-Forwarded-Proto`/`X-Forwarded-Host`, and only then the request URL. The
  download response also carries `Content-Disposition: attachment`,
  `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-store`, and
  the Next.js route emits RFC 5987 `filename*` for non-ASCII names. The browser
  download path additionally refuses to navigate to a plain-`http` link from an
  `https` page, falling back to the same-origin route instead of a blocked
  download.
- **Patent links did not open.** Links were built by pasting the recorded number
  into `patents.google.com/patent/`, but sources record the punctuated form
  (`US-20190021252-A1`) while Google Patents addresses the document without it
  (`US20190021252A1`). A recorded URL is now kept only when it is already a
  document URL; otherwise the canonical number is used, and a number that
  cannot be parsed falls back to a Google Patents search rather than a dead
  link. Applied to the verdict register, the Next.js reader, the workbook's
  patent sheet and the engine's patent connector.
- **The Cytoscape link did not open anything.** It copied a signed URL minted
  against an `http://` origin, so the import failed — and nothing in the app
  rendered a network at all. The Network tab now draws the search's own graph
  with Cytoscape.js (force-directed, concentric, hierarchy, circle and grid
  layouts; type filters; hover highlighting; zoom/pan; node inspector; PNG
  export; expanded view), and offers the signed `.cyjs` URL for Cytoscape
  desktop.

### Changed
- **Results workspace instead of a file list.** The finished search opens on a
  KPI strip (entities, relationships, patents, exports) and five tabs —
  Overview, Network, Patents, Evidence, Files — with the potency grade,
  per-compound pChEMBL bars, mechanism evidence, prior art, toxicity records,
  sourcing and caveats laid out as tables and cards rather than paragraphs.
- **Patents moved out of the top of the page.** Patent rows are a dedicated tab
  with a **View patent** action per row, so the main view stays on the data;
  the overview shows only the aggregate signal.
- **File centre.** Exports are grouped (Tables, Graph, Database & RDF,
  Structure & reports) with what each file contains, its size, a download
  button and, for graph files, the Cytoscape hand-off.
- **Dependency:** `cytoscape` (MIT) for the in-app network renderer.

### Fixed
- **Real reissue, design and plant patents were dropped as "not patents".**
  The publication-number filter that keeps the "Patent Search: …" link row out
  of the register accepted only an office code followed by digits, so
  `USRE45123E1` (reissue), `USD123456S1` (design) and `USPP12345P2` (plant)
  failed it and disappeared from the register and the IP signal. The filter now
  accepts the US series letters as well (reissue `RE`, design `D`, plant `PP`,
  statutory invention registration `H`, defensive publication `T`), in both the
  Python verdict and the Next.js reader, and the engine's ingestion check no
  longer rejects those numbers before they reach a graph. The link rows stay
  excluded: only those series letters count, so query text such as `EGFR2024`
  is still not a publication number.
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
- **A patent search link was listed and counted as a patent.** The connector's
  fallback entity ("Patent Search: EGFR", canonical id `SEARCH:EGFR`, typed
  `PATENT` like a real document) reached `patent_register()` and the IP verdict.
  It appeared at the top of the register with a 404 Google Patents link, and a
  protein query with no patents could report that "patents exist". Only rows
  carrying a real publication number are patents now, in both the Python
  verdict and the Next.js local reader.

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

### Changed
- **`/api/exports` is no longer open to anyone holding a search id.** Search ids
  are eight characters, so a bare id was enough to fetch any search's files. A
  download now needs either a signed link or the search's owner — the account
  on the engine, and, for a search this Next app ran itself, the browser that
  created it (an httpOnly `scigraph_owner` cookie set when the search starts).
  Minting an export link is owner-only for the same reason, and a request with
  neither gets the same 404 as a missing file. The results screen mints a signed
  link on download, so the ordinary browser path keeps working.
- **The IP verdict no longer calls an empty result "green".** A search that
  surfaced no patent document now reports `insufficient_data` — zero records is
  the most common outcome of a name lookup and the most dangerous answer to
  colour green. Every verdict carries its `scope` (sources searched, and that
  legal status, claims, families and jurisdictions were not checked) and an
  `assessed_at` date, and the results screen labels the block "Patent signal"
  rather than "IP freedom".
- **GraphML carries attribute types, and every search writes `graph.cyjs`.**
  Attributes are no longer coerced to strings, so `pchembl_value`,
  `molecular_weight` and the other numeric fields stay numeric and can drive
  Cytoscape visual mappings; `graph.cyjs` is Cytoscape element JSON for
  Cytoscape Desktop, Web and Cytoscape.js.
- **Signed export links for Cytoscape.** `GET /api/search/{id}/export-link?file=…`
  mints an owner-scoped, expiring HMAC URL that works without a session cookie,
  which is what Cytoscape's "Network from URL" needs. Each GraphML/.cyjs row in
  the results screen has a **Cytoscape link** button that copies it. Set
  `EXPORT_SIGNING_SECRET` to keep links valid across restarts (otherwise a
  warning is logged and links expire with the process).

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
