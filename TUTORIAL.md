# Enterprise Scientific Knowledge Graph Platform (v3.1) - User Guide

Welcome to the **Enterprise Automated Scientific Knowledge Graph Integration Platform v3.1**.

---

## 🚀 Quickstart Usage Guide

### 1. Interactive Query Assistant & Search Library Build
Run the interactive Query Assistant to answer domain-specific questions (organism, bioactivity cutoff, chemical class) and build a reusable Search Library:
```bash
py -3.13 api/scigraph.py "Ammonia Monooxygenase" --assistant
```

### 2. Generate Search Library File Only
To construct a search library (`search_library.json`, `library_queries.txt`) without executing the full search:
```bash
py -3.13 api/scigraph.py "Ammonia Monooxygenase" --build-library
```

### 3. Run Batch Processing from Search Library
To execute multi-hop discovery on all queries in your generated Search Library:
```bash
py -3.13 api/scigraph.py --batch ./exports/library_queries.txt --export-dir ./batch_exports
```

### 4. Direct Multi-Hop Search
```bash
py -3.13 api/scigraph.py "Ammonia Monooxygenase" --hops 2
```

---

## 🌐 Running the Web App (Production)

The web app runs search on a hosted Python engine:

1. **Deploy the Python API** to Render (blueprint `render.yaml` → `python-api/`), or run it locally:
   ```bash
   cd python-api && pip install -r requirements.txt
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```
2. **Point the Next.js app at it** by setting the env var:
   ```bash
   export SEARCH_ENGINE_URL=https://your-engine-host.onrender.com
   ```
3. Searches from the UI are proxied to the engine; progress + log stream back in real time, and export files download through `/api/exports/...`.

---

## 📦 Supported Active Data Sources (50+ Repositories)

- **Chemistry & Bioactivity**: ChEMBL, PubChem, BindingDB, DrugBank, Guide to Pharmacology (GtoPdb), CompTox.
- **Proteins & Structures**: UniProt, PDB, PDBe-KB, AlphaFold DB, STRING PPI.
- **Genomics & Taxonomy**: NCBI Gene, NCBI Taxonomy, Ensembl, dbSNP, ClinVar.
- **Literature & Patents**: PubMed, Europe PMC, CrossRef, OpenAlex, PatentsView.
- **Enzymes & Pathways**: BRENDA, Rhea, KEGG, Reactome, Gene Ontology.
- **Clinical**: ClinicalTrials.gov.
