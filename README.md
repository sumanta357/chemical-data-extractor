# Chemical Data Extractor & Enterprise Scientific Knowledge Graph Platform (v3.1)

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Excel Export](https://img.shields.io/badge/Export-Styled%20Excel%20%28.xlsx%29-green.svg)](#styled-multi-tab-excel-workbook)
[![Databases Covered](https://img.shields.io/badge/Databases-23%20Connectors-orange.svg)](#-23-integrated-database-connectors)

An enterprise-grade, multi-hop automated scientific discovery engine designed to extract, resolve, align, and construct comprehensive knowledge graphs for chemical compounds, bioactivities, enzyme kinetics, patent claims, 3D PDB crystal structures, and biological pathways.

---

## 🌟 Key Features

* **Intelligent Multi-Hop Graph Expansion**: Performs recursive $N$-hop discovery (default `--hops 4`) across global chemical, biological, and patent networks.
* **23 Active Live Database APIs**: Queries PubChem, ChEMBL, BindingDB, BRENDA, UniProt, RCSB PDB, AlphaFold DB, DrugBank, KEGG, Reactome, EPA CompTox, ChEBI, ZINC20, Lens Patent Engine, Google Patents, and scientific literature repositories in parallel.
* **Styled Multi-Tab Excel Export (.xlsx)**: Generates production-ready spreadsheets complete with formatted headers, auto-adjusted column widths, SMILES structures, $IC_{50}/K_i$ bioactivities, and clickable web links.
* **Universal & Query-Agnostic**: Works dynamically for any query (e.g. `Ammonia Monooxygenase`, `Aspirin`, `EGFR`, `COX-2`, `P23219`, `COVID-19 Main Protease`, `Dopamine Receptor`).
* **Multi-Format Export Suite**: Exports to Excel (`.xlsx`), CSV (`nodes.csv`, `edges.csv`), GraphML (`graph.graphml`), Neo4j Cypher (`neo4j_import.cypher`), RDF/Turtle (`graph.ttl`), Parquet, and AI Vector RAG Metadata (`vector_index_metadata.json`).
* **Fault-Tolerant Infrastructure**: Built-in DuckDB in-memory fallback and Excel file-lock protection.

---

## 📊 23 Integrated Database Connectors

| Category | Database / API Resource | Extracted Data Fields |
| :--- | :--- | :--- |
| **Chemical & Bioactivity** | **PubChem PUG REST** | Canonical SMILES, CIDs, IUPAC Names, Formulas, Molecular Weights ($MW$) |
| | **ChEMBL DB (EMBL-EBI)** | Bioactivity values ($IC_{50}, K_i, K_d$), pChEMBL scores, Assay IDs, Mechanisms |
| | **BindingDB** | Quantitative target binding affinities ($K_i, K_d, IC_{50}, EC_{50}$) |
| | **Guide to Pharmacology** | Ligand-target interactions, affinity parameters, selectivity |
| | **ChEBI** | Chemical Entities of Biological Interest ontology terms |
| | **ZINC20** | 3D virtual screening candidates & molecular docking structures |
| **Enzyme & Pathways** | **BRENDA Enzyme DB** | EC Numbers (**EC 1.14.99.39**), substrate profiles, turnover kinetics |
| | **KEGG** | KEGG Compound IDs (`C00001`), metabolic pathways, reaction maps |
| | **Reactome** | Human biological pathways, reaction steps, event diagrams |
| | **Gene Ontology (QuickGO)** | Biological Process (`GO:0009061`) & Molecular Function terms |
| **3D Structures** | **RCSB Protein Data Bank (PDB)** | Experimental 3D X-ray & Cryo-EM PDB IDs (`9PXF`, `6N4N`, `6C0B`, `7Z36`) |
| | **PDBe-KB (EMBL-EBI)** | Co-crystallized 3D ligand binding pockets & UniProt mappings |
| | **AlphaFold DB** | AI-predicted 3D protein structure models & pLDDT confidence scores |
| **Patents & IP** | **Google Patents Register** | International Patent IDs (`US`, `WO`, `EP`, `AU`, `AR`), Assignees, Titles |
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

## 📁 Multi-Tab Excel Output Structure

The master workbook (`exports/Nitrification_Inhibitors_Knowledge_Graph.xlsx`) includes 4 dedicated sheets:

1. **Tab 1: `Entities & Compounds`**
   * Compound Names, Entity Categories, Canonical IDs
   * **SMILES Strings**
   * **Molecular Formulas** & **Molecular Weights ($MW$)**
   * Bioactivity Summaries ($IC_{50}, K_i$)
2. **Tab 2: `Relationships & Patents`**
   * Source Entity $\rightarrow$ Target Entity
   * Relationship Types (`INHIBITS`, `PATENTED_IN`, `BINDS`, `SUBSTRATE_OF`)
   * Bioactivity Values & Units ($\mu\text{M}, \text{nM}$)
   * Confidence Scores & Source Web URLs
3. **Tab 3: `Patents & Patented Molecules`**
   * Global Patent Numbers (`US3135594A`, `US7883568B2`, `EP0386767B1`, `US5354726A`, `WO2020123456A1`, `WO2015098123A1`)
   * Patent Titles, Corporate Assignees (Dow Chemical, BASF, SKW Trostberg, JIRCAS/FAO)
   * Publication Years & **Clickable Google Patent Links**
4. **Tab 4: `PDB 3D Structures`**
   * PDB Structure Entry IDs (`9PXF`, `6N4N`, `6C0B`, `7Z36`)
   * Structure Titles, Release Dates, and **Direct RCSB PDB Links**

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/sumanta357/chemical-data-extractor.git
cd chemical-data-extractor
pip install aiohttp pydantic networkx openpyxl duckdb pyarrow
```

### 2. Run Single Target Extraction (Default 4-Hop Search)
```bash
python scigraph.py "Ammonia Monooxygenase"
```

### 3. Run Custom Multi-Hop Expansion Depth
```bash
python scigraph.py "EGFR" --hops 4
```

### 4. Run Interactive Query Assistant
```bash
python scigraph.py "Ammonia Monooxygenase" --assistant
```

### 5. Run Batch Search from Query File
```bash
python scigraph.py --batch exports/library_queries.txt --hops 4
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
