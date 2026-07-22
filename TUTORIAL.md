# Enterprise Scientific Knowledge Graph Platform (v3.1) - User Guide

Welcome to the **Enterprise Automated Scientific Knowledge Graph Integration Platform v3.1**.

---

## 🚀 Quickstart Usage Guide

### 1. Interactive Query Assistant & Search Library Build
Run the interactive Query Assistant to answer domain-specific questions (organism, bioactivity cutoff, chemical class) and build a reusable Search Library:
```bash
py -3.13 scigraph.py "Ammonia Monooxygenase" --assistant
```

### 2. Generate Search Library File Only
To construct a search library (`search_library.json`, `library_queries.txt`) without executing the full search:
```bash
py -3.13 scigraph.py "Ammonia Monooxygenase" --build-library
```

### 3. Run Batch Processing from Search Library
To execute multi-hop discovery on all queries in your generated Search Library:
```bash
py -3.13 scigraph.py --batch ./exports/library_queries.txt --export-dir ./batch_exports
```

### 4. Direct Multi-Hop Search
```bash
py -3.13 scigraph.py "Ammonia Monooxygenase" --hops 2
```

---

## 📦 Supported Active Data Sources (50+ Repositories)

- **Chemistry & Bioactivity**: ChEMBL, PubChem, BindingDB, DrugBank, Guide to Pharmacology (GtoPdb), CompTox.
- **Proteins & Structures**: UniProt, PDB, PDBe-KB, AlphaFold DB, STRING PPI.
- **Genomics & Taxonomy**: NCBI Gene, NCBI Taxonomy, Ensembl, dbSNP, ClinVar.
- **Literature & Patents**: PubMed, Europe PMC, CrossRef, OpenAlex, PatentsView.
- **Enzymes & Pathways**: BRENDA, Rhea, KEGG, Reactome, Gene Ontology.
- **Clinical**: ClinicalTrials.gov.
