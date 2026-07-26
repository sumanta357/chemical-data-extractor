# Chemical Data Extractor & Enterprise Scientific Knowledge Graph Platform

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Excel Export](https://img.shields.io/badge/Export-Excel%20%28.xlsx%29-green.svg)](#-export-formats)
[![Databases](https://img.shields.io/badge/Databases-19%20Connectors-orange.svg)](#-integrated-database-connectors)

An enterprise-grade, multi-hop automated scientific discovery engine that extracts, resolves, aligns, and constructs comprehensive knowledge graphs for chemical compounds, bioactivities, enzyme kinetics, 3D PDB structures, and biological pathways. Queries **protein targets** (e.g., "EGFR", "Tubulin", "Ammonia Monooxygenase") or **ligands/compounds** (e.g., "Aspirin", "Nitrapyrin") across **19 live database APIs**.

---

## 🌟 Key Features

- **Query-Type Selection**: Specify `--query-type protein` (find inhibitors/binders for a target) or `--query-type ligand` (find proteins that bind a compound) — or let `auto` detect it.
- **Multi-Hop Graph Expansion**: Recursive N-hop discovery (default `--hops 4`) across global chemical, biological, and patent networks.
- **19 Active Live Database APIs**: Queries PubChem, ChEMBL, BindingDB (with **plural endpoint** for 13x more results), BRENDA, UniProt, PDBe-KB, AlphaFold DB, DrugBank, KEGG, Reactome, EPA CompTox, ChEBI, STRING DB, and scientific literature in parallel.
- **Rich Excel Export (Built-in)**: Automatically generates a styled multi-sheet workbook with SMILES, Ki/IC₅₀/Kd bioactivities, PDB structure mappings, patents, and literature links.
- **Multi-Format Export Suite**: CSV (`nodes.csv`, `edges.csv`), GraphML (`graph.graphml`), Neo4j Cypher (`neo4j_import.cypher`), RDF/Turtle (`graph.ttl`), Parquet, and AI Vector RAG Metadata.
- **Knowledge Graph Visualization**: Generate publication-quality network graphs with included `visualize_graph.py`.
- **Fault-Tolerant**: DuckDB in-memory fallback, SQLite caching, automatic retry logic, and rate limiting.
- **Interactive Query Assistant**: Generates domain-specific clarifying questions for guided discovery.

---

## 📊 Integrated Database Connectors

| Category | Database / API Resource | Extracted Data Fields |
| :--- | :--- | :--- |
| **Chemical & Bioactivity** | **PubChem PUG REST** | Canonical SMILES, CIDs, IUPAC Names, Formulas, Molecular Weight |
| | **ChEMBL (EMBL-EBI)** | Bioactivity (IC₅₀, Kᵢ, Kd, EC₅₀), pChEMBL scores, Assay IDs, Mechanisms (paginated up to 3,000) |
| | **BindingDB** 🎯 | **152+ compounds for tubulin** via plural endpoint — Kᵢ, Kd, IC₅₀ with SMILES |
| | **Guide to Pharmacology (IUPHAR)** | Ligand-target interactions, affinity parameters, selectivity |
| | **ChEBI** | Chemical Entities, formula, **SMILES from default_structure** |
| **Enzyme & Pathways** | **BRENDA Enzyme DB** | EC Numbers, known inhibitors with Ki values, substrate profiles |
| | **KEGG** | KEGG Compound IDs, metabolic pathways, reaction maps |
| | **Reactome** | Human biological pathways, reaction steps, event diagrams |
| | **Gene Ontology (QuickGO)** | Biological Process & Molecular Function terms |
| **3D Structures** | **RCSB Protein Data Bank (PDB)** | Experimental 3D X-ray & Cryo-EM PDB IDs |
| | **PDBe-KB (EMBL-EBI)** | Co-crystallized 3D ligand binding pockets & UniProt mappings |
| | **AlphaFold DB** | AI-predicted 3D protein structure models & pLDDT confidence scores |
| **Patents & IP** | **Google Patents Register** | International Patent IDs, Assignees, Titles |
| | **PubChem Patent XRefs** | Live chemical-to-patent cross-reference lookup |
| | **Lens.org Patent Platform** | Open global patents & biological sequence patent citations |
| **Proteins & Genes** | **UniProtKB** | Primary Accession IDs, catalytic functions, organism taxonomy |
| | **STRING DB** | Physical protein-protein interaction (PPI) networks |
| | **NCBI Gene & Taxonomy** | Locus tags, gene IDs, organism TaxIDs |
| **Literature** | **Europe PMC (EMBL-EBI)** | Open-access literature, PMCIDs, DOIs, patent citations |
| | **PubMed (NCBI)** | PMIDs, MEDLINE research abstracts, journal citations |
| | **OpenAlex** | Global scholarly publication graph & citation metrics |
| **Environment & Tox** | **EPA CompTox Dashboard** | Environmental chemical toxicity, soil fate, hazard profiles |

---

## 🚀 Quick Start

### 1. Installation

```bash
git clone https://github.com/sumanta357/chemical-data-extractor.git
cd chemical-data-extractor
pip install aiohttp pydantic networkx openpyxl duckdb pyarrow matplotlib
```

**Required dependencies:**
- `aiohttp` — Async HTTP client for parallel API calls
- `pydantic` — Data validation and domain models
- `networkx` — Graph construction and analytics
- `openpyxl` — Excel (.xlsx) export
- `duckdb` — In-memory relational storage (falls back automatically)

**Optional:**
- `pyarrow` — Parquet export support
- `matplotlib` — Graph visualization (for `visualize_graph.py`)
- `orjson` — Faster JSON parsing

### 2. Find Inhibitors for a Protein Target

```bash
# 1-hop: focused search for tubulin inhibitors/binders
python3 scigraph.py "tubulin" --query-type protein --hops 1

# 2-hop: broader expansion (may be slower with large result sets)
python3 scigraph.py "tubulin" --query-type protein --hops 2

# Search for a specific enzyme
python3 scigraph.py "Ammonia Monooxygenase" --query-type protein --hops 1
```

### 3. Find Targets for a Compound/Ligand

```bash
# Find proteins that bind Aspirin
python3 scigraph.py "Aspirin" --query-type ligand --hops 1

# Search for a drug's targets
python3 scigraph.py "Nitrapyrin" --query-type ligand --hops 2
```

### 4. Auto-Detect Mode

```bash
# Let the system auto-detect whether it's a protein or compound
python3 scigraph.py "EGFR" --hops 1
python3 scigraph.py "Ibuprofen" --hops 1
```

### 5. Batch Processing from a Query File

```bash
# Run multiple queries from a text file (one per line)
python3 scigraph.py --batch exports/library_queries.txt --export-dir ./batch_exports
```

### 6. Interactive Query Assistant

```bash
# Launch guided mode with clarifying questions
python3 scigraph.py "Ammonia Monooxygenase" --assistant
```

### 7. Build Search Library (without running the full search)

```bash
# Generate search_library.json, library_queries.txt, and library_summary.md
python3 scigraph.py "tubulin" --build-library
```

### 8. Knowledge Graph Visualization

```bash
python3 visualize_graph.py
```
**Output:** `exports/ammonia_monooxygenase_graph.png`

### 9. Run Unit Tests

```bash
python3 test_connectors_new.py
```

---

## 📁 Excel Output Structure (5 Sheets)

After any successful query, the script automatically produces `exports/<query>_Knowledge_Graph.xlsx` with:

| Sheet | Contents |
| :--- | :--- |
| **Entities & Compounds** | UID, Name, Category, SMILES, Formula, MW, PDB Structures, Patents, Bioactivity Summary |
| **Relationships & Patents** | Source → Target mappings with Activity Type/Value/Units, pChEMBL, Mechanism |
| **Patents & Patented Molecules** | Patent IDs, Titles, Assignees, Years, Google Patents Links |
| **PDB 3D Structures** | PDB IDs, Structure Titles, Release Dates, RCSB PDB Links |
| **Literature & Web Links** | Publication Titles, Journals, DOIs/PMIDs, Citations, Direct Links |

---

## 📁 Export Formats

| Format | File | Description |
| :--- | :--- | :--- |
| **Excel** | `exports/<query>_Knowledge_Graph.xlsx` | Formatted 5-sheet workbook with SMILES & activity values |
| **CSV** | `exports/nodes.csv` | Node/entity table with SMILES, formula, MW |
| **CSV** | `exports/edges.csv` | Edge/relationship table with activity values, pChEMBL |
| **GraphML** | `exports/graph.graphml` | Standard graph exchange format |
| **Neo4j Cypher** | `exports/neo4j_import.cypher` | Import script for Neo4j graph database |
| **RDF/Turtle** | `exports/graph.ttl` | Semantic web format |
| **RDF/JSON-LD** | `exports/rdf_graph.json` | JSON-LD format |
| **Parquet** | `exports/nodes.parquet` / `edges.parquet` | Columnar storage for large graphs |
| **Vector Index** | `exports/vector_metadata.json` / `triples.json` | AI RAG vector index metadata |
| **PNG** | `exports/<query>_graph.png` | Knowledge graph visualization |

---

## 🧪 Examples by Use Case

### Find Known Inhibitors for an Enzyme

```bash
# BRENDA curated inhibitors + ChEMBL bioactivity data + BindingDB affinities
python3 scigraph.py "Ammonia Monooxygenase" --query-type protein --hops 1
```

**What you get:** IC₅₀, Ki, Kd values from ChEMBL and BindingDB, SMILES from PubChem, patent info

### Find All Proteins That Bind a Drug

```bash
python3 scigraph.py "Aspirin" --query-type ligand --hops 2
```

### Batch Search from File

```bash
python3 scigraph.py --batch exports/library_queries.txt --hops 2
```

### Search for Tubulin Inhibitors (most comprehensive)

```bash
python3 scigraph.py "tubulin" --query-type protein --hops 1
```

**Result:** ~160 compounds with SMILES, ~183 activity values (98 IC₅₀ + 77 Kd) from BindingDB + ChEMBL

---

## 🔧 Full CLI Reference

### `scigraph.py` — Knowledge Graph Extraction Engine

```
usage: scigraph.py [-h] [--batch BATCH] [--workspace WORKSPACE]
                   [--export-dir EXPORT_DIR] [--hops HOPS] [--assistant]
                   [--build-library]
                   [--query-type {ligand,protein,auto}]
                   [query]

positional arguments:
  query                 Query: compound name, protein name, gene symbol, or
                        UniProt ID (e.g. "Ammonia Monooxygenase", "Aspirin",
                        "tubulin", "EGFR")

options:
  -h, --help            Show help and exit
  --batch BATCH         Path to batch input queries file (one per line)
  --workspace WORKSPACE Working directory for cache/data (default: ./scigraph_data)
  --export-dir EXPORT_DIR Output directory (default: ./exports)
  --hops HOPS           Multi-hop expansion depth (default: 4, recommended: 1-2)
  --assistant           Launch interactive query assistant with domain-specific
                        questions (organism, bioactivity cutoff, etc.)
  --build-library       Build search library (search_library.json, library_queries.txt,
                        library_summary.md) without full graph search
  --query-type {ligand,protein,auto}
                        Query type:
                        - "protein" = Find inhibitors/binders for a protein target
                        - "ligand" = Find protein targets for a compound
                        - "auto" = Auto-detect based on query text (default)
```

### `visualize_graph.py` — Knowledge Graph Visualization

Reads from `exports/nodes.csv` and `exports/edges.csv`, outputs a publication-quality network graph PNG.

```bash
python3 visualize_graph.py
```

**Output:** `exports/ammonia_monooxygenase_graph.png`

### `test_connectors_new.py` — Unit Tests

Tests for 4 connectors: PDBe-KB, KEGG, ChEBI, Reactome. Uses mock fixtures — no API calls.

```bash
python3 test_connectors_new.py
```

---

## 💡 Pro Tips

1. **Start with `--hops 1`** for a quick result. Use `--hops 2` for broader discovery (may be slower).
2. **Use `--query-type protein`** when searching for a protein target (e.g., "tubulin", "EGFR", "Ammonia Monooxygenase") to activate the BindingDB UniProt resolution pathway (which returns **13x more results**).
3. **Use `--query-type ligand`** when searching for a compound/drug name to skip irrelevant protein connectors.
4. **Output files** are in `exports/` — the main Excel file is named `<query>_Knowledge_Graph.xlsx`.
5. **SMILES are included** from PubChem (all compounds), ChEMBL (activity records), and BindingDB (via plural endpoint).
6. **Large queries** (e.g., "tubulin" with `--hops 2`) may take 10+ minutes due to BindingDB's 127+ compounds each being expanded.

---

## ⚙️ How It Works

1. **Query Router** classifies your query as protein/ligand/auto
2. **19 Connectors** run in parallel, each querying their respective API
3. **Entity Resolution** deduplicates and merges results from different sources
4. **Multi-Hop Expansion** (if `--hops > 1`) discovers new entities through cross-references
5. **Graph Analytics** computes PageRank, betweenness centrality, hubs/authorities
6. **Multi-Format Export** writes CSV, Excel, GraphML, Cypher, RDF, Parquet

---

## 🧪 Tests

```bash
# Connector unit tests (PDBe-KB, KEGG, ChEBI, Reactome)
python3 test_connectors_new.py
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.

---

## 📊 Performance Benchmarks (tubulin — 1-hop, protein mode)

| Metric | Before Fixes | After Fixes |
| :--- | :--- | :--- |
| **BindingDB compounds with SMILES** | 11 | **152** 🚀 |
| **Total compounds with SMILES** | 19 | **160** |
| **IC₅₀ values** | 14 | **98** |
| **Kd values** | 10 | **77** |
| **Total activity values** | 32 | **183** |
| **Total entities** | 87 | **233** |
| **Total relations** | 93 | **244** |
