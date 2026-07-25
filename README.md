# Chemical Data Extractor & Enterprise Scientific Knowledge Graph Platform

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Excel Export](https://img.shields.io/badge/Export-Styled%20Excel%20%28.xlsx%29-green.svg)](#-enrichment-pipeline)
[![Databases Covered](https://img.shields.io/badge/Databases-23%20Connectors-orange.svg)](#-integrated-database-connectors)

An enterprise-grade, multi-hop automated scientific discovery engine that extracts, resolves, aligns, and constructs comprehensive knowledge graphs for chemical compounds, bioactivities, enzyme kinetics, 3D PDB structures, and biological pathways. Queries **protein targets** (e.g., "EGFR", "Ammonia Monooxygenase") or **ligands/compounds** (e.g., "Aspirin", "Nitrapyrin") across 23+ live database APIs.

---

## 🌟 Key Features

- **Query-Type Selection**: Specify `--query-type protein` (find inhibitors/binders for a target) or `--query-type ligand` (find proteins that bind a compound) — or let `auto` detect it.
- **Multi-Hop Graph Expansion**: Recursive N-hop discovery (default `--hops 4`) across global chemical, biological, and patent networks.
- **23 Active Live Database APIs**: Queries PubChem, ChEMBL, BindingDB, BRENDA, UniProt, RCSB PDB, AlphaFold DB, DrugBank, KEGG, Reactome, EPA CompTox, ChEBI, ZINC20, Lens Patent Engine, Google Patents, and scientific literature in parallel.
- **Rich Excel Export**: 6-sheet styled workbook with SMILES, embedded 2D structure images, Ki/IC₅₀ bioactivities, publication abstracts, and PDB structure mappings.
- **Multi-Format Export Suite**: Excel (`.xlsx`), CSV (`nodes.csv`, `edges.csv`), GraphML (`graph.graphml`), Neo4j Cypher (`neo4j_import.cypher`), RDF/Turtle (`graph.ttl`), Parquet, and AI Vector RAG Metadata.
- **Data Enrichment Pipeline**: Post-query script fetches SMILES, molecular properties, and publication abstracts via PubChem and CrossRef APIs.
- **Knowledge Graph Visualization**: Generate publication-quality network graphs with NetworkX + Matplotlib.
- **Fault-Tolerant**: DuckDB in-memory fallback, retry logic, and caching.

---

## 📊 Integrated Database Connectors

| Category | Database / API Resource | Extracted Data Fields |
| :--- | :--- | :--- |
| **Chemical & Bioactivity** | **PubChem PUG REST** | Canonical SMILES, CIDs, IUPAC Names, Formulas, Molecular Weight |
| | **ChEMBL (EMBL-EBI)** | Bioactivity (IC₅₀, Kᵢ, Kd), pChEMBL scores, Assay IDs, Mechanisms |
| | **BindingDB** | Quantitative target binding affinities (Kᵢ, Kd, IC₅₀, EC₅₀) |
| | **Guide to Pharmacology (IUPHAR)** | Ligand-target interactions, affinity parameters, selectivity |
| | **ChEBI** | Chemical Entities of Biological Interest ontology terms |
| | **ZINC20** | 3D virtual screening candidates & molecular docking structures |
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

### 2. Run a Protein Query (Find Inhibitors for a Target)

```bash
# Search for compounds that inhibit Ammonia Monooxygenase (1 hop = focused)
python3 scigraph.py "Ammonia Monooxygenase" --query-type protein --hops 1

# Deeper 2-hop expansion (also searches via ChEMBL target linkages)
python3 scigraph.py "Ammonia Monooxygenase" --query-type protein --hops 2
```

### 3. Run a Ligand Query (Find Targets for a Compound)

```bash
python3 scigraph.py "Aspirin" --query-type ligand --hops 1
```

### 4. Data Enrichment (Add SMILES, 2D Structures, Publication Metadata)

After a query completes, enrich the exports into a beautiful multi-sheet Excel workbook:

```bash
python3 enrich_exports.py
```

**Output:** `exports/enriched_data.xlsx` with 6 sheets.

### 5. Knowledge Graph Visualization

Generate a network graph PNG from the export data:

```bash
python3 visualize_graph.py
```

**Output:** `exports/ammonia_monooxygenase_graph.png`

### 6. Run Tests

```bash
python3 test_connectors_new.py
```

---

## 📁 Excel Output Structure (6 Sheets)

The enriched workbook (`exports/enriched_data.xlsx`) contains:

| Sheet | Contents |
| :--- | :--- |
| **Summary** | Pipeline statistics and sheet overview |
| **Compounds** | Name, SMILES, Molecular Formula, MW, InChI Key, IUPAC Name, PubChem CID, **embedded 2D structure images** |
| **Inhibitor Activities** | Compound → Target mappings with Ki/IC₅₀ values, pChEMBL scores, mechanism of action, assay IDs |
| **Publications** | DOIs, titles, authors, journals, years, **abstracts**, citation counts, URLs |
| **PDB Structures** | PDB ID → Protein target mappings with method, resolution |
| **All Relations** | Complete edge table with all relationship types |

---

## 🧪 Examples by Use Case

### Find Known Inhibitors for an Enzyme

```bash
# BRENDA curated inhibitors + ChEMBL bioactivity data
python3 scigraph.py "Ammonia Monooxygenase" --query-type protein --hops 2
```

### Find All Proteins That Bind a Drug

```bash
python3 scigraph.py "Aspirin" --query-type ligand --hops 2
```

### Batch Search from File

```bash
python3 scigraph.py --batch exports/library_queries.txt --hops 2
```

---

## 📁 Export Formats

| Format | File | Description |
| :--- | :--- | :--- |
| **Excel** | `exports/enriched_data.xlsx` | Formatted 6-sheet workbook with SMILES & images |
| **CSV** | `exports/nodes.csv` | Node/entity table |
| **CSV** | `exports/edges.csv` | Edge/relationship table |
| **GraphML** | `exports/graph.graphml` | Standard graph exchange format |
| **Neo4j Cypher** | `exports/neo4j_import.cypher` | Import script for Neo4j graph database |
| **RDF/Turtle** | `exports/graph.ttl` | Semantic web format |
| **Parquet** | `exports/nodes.parquet` / `edges.parquet` | Columnar storage for large graphs |
| **PNG** | `exports/ammonia_monooxygenase_graph.png` | Knowledge graph visualization |

---

## 🔧 CLI Reference

### `scigraph.py` — Knowledge Graph Extraction

```
usage: scigraph.py [-h] [--batch BATCH] [--workspace WORKSPACE]
                   [--export-dir EXPORT_DIR] [--hops HOPS] [--assistant]
                   [--build-library]
                   [--query-type {ligand,protein,auto}]
                   [query]

positional arguments:
  query                 Query: compound name, protein name, gene symbol, or
                        UniProt ID (e.g. "Ammonia Monooxygenase", "Aspirin")

options:
  -h, --help            Show help and exit
  --batch BATCH         Path to batch input queries file
  --workspace WORKSPACE Working directory for cache/data
  --export-dir EXPORT_DIR Output directory
  --hops HOPS           Multi-hop expansion depth (default: 4)
  --assistant           Launch interactive query assistant
  --build-library       Build search library without full graph search
  --query-type {ligand,protein,auto}
                        Query type: ligand/compound, protein/target, or
                        auto-detect (default: auto)
```

### `enrich_exports.py` — Data Enrichment Pipeline

Takes no arguments — reads from `exports/nodes.csv` and `exports/edges.csv`, outputs `exports/enriched_data.xlsx`.

```bash
python3 enrich_exports.py
```

### `visualize_graph.py` — Knowledge Graph Visualization

Takes no arguments — reads from `exports/nodes.csv` and `exports/edges.csv`, outputs `exports/ammonia_monooxygenase_graph.png`.

```bash
python3 visualize_graph.py
```

---

## 🧪 Tests

```bash
# Connector unit tests (KEGG, ChEBI, Reactome, PDBe-KB)
python3 test_connectors_new.py
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
