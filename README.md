# Chemical Data Extractor & Enterprise Scientific Knowledge Graph Platform

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Databases](https://img.shields.io/badge/Databases-19%20Connectors-orange.svg)](#-integrated-database-connectors)

An enterprise-grade, multi-hop automated scientific discovery engine that extracts, resolves, aligns, and constructs comprehensive knowledge graphs for chemical compounds, bioactivities, enzyme kinetics, 3D PDB structures, and biological pathways.

**New: Web UI with deployment support!** Run queries via a modern web interface or the CLI — deployed on **Freebuff Cloud** as a Next.js app with Python backend.

---

## 🚀 Quick Start (CLI)

```bash
pip install -r requirements.txt

# Find inhibitors for a protein target
python3 scigraph.py "tubulin" --query-type protein --hops 1

# Find targets for a compound
python3 scigraph.py "Aspirin" --query-type ligand --hops 1
```

## 🌐 Run the Web UI

```bash
npm install        # Install Next.js dependencies
bun run build      # or: npm run build
bun run start      # or: npm run start
# Open http://localhost:3001
```

## 🚢 Deploy on Freebuff Cloud

This project is structured as a **Next.js app** with Python API routes. To deploy on Freebuff:

1. Push to GitHub
2. In Freebuff Cloud dashboard, configure:
   - **Install Command:** `pip install -r requirements.txt && npm install`
   - **Build Command:** `npx next build`
   - **Preview Command:** `npx next start --port 3001`
   - **Preview Port:** `3001`
3. Click **Deploy**

### Architecture

```
User Browser → Next.js (React UI) → API Routes (Node.js) → Python Subprocess (scigraph.py)
                                                              ↓
                                                         exports/ (CSV, Excel, GraphML, etc.)
```

## 📊 Integrated Database Connectors (19)

| Category | Database | Extracted Data |
| :--- | :--- | :--- |
| **Chemical & Bioactivity** | PubChem, ChEMBL, BindingDB, GtoPdb, ChEBI | SMILES, IC₅₀, Kᵢ, Kd, pChEMBL |
| **Enzyme & Pathways** | BRENDA, KEGG, Reactome, GO (QuickGO) | EC numbers, metabolic pathways |
| **3D Structures** | RCSB PDB, PDBe-KB, AlphaFold DB | PDB IDs, binding pockets, pLDDT |
| **Patents** | Google Patents, PubChem Patents, Lens.org | Patent IDs, assignees |
| **Proteins & Genes** | UniProt, STRING, NCBI Gene/Taxonomy | Accessions, PPI networks |
| **Literature** | Europe PMC, PubMed, OpenAlex | DOIs, PMCIDs, citations |
| **Toxicity** | EPA CompTox | Environmental hazard profiles |

[Full CLI reference and export format details below...]

## 📁 Project Structure

```
├── app/                    # Next.js App Router (frontend + API)
│   ├── api/                # Route handlers (search, health, exports)
│   ├── page.tsx            # Main search page
│   └── layout.tsx          # Root layout
├── components/             # React components (SearchForm, ProgressView, etc.)
├── lib/                    # TypeScript library
│   ├── types.ts            # Type definitions
│   └── search-engine.ts    # Python subprocess manager
├── scigraph.py             # Python CLI engine (unchanged)
├── enrich_exports.py       # Enrichment pipeline (PubChem SMILES + CrossRef)
├── app.py                  # Legacy FastAPI (reference only)
├── requirements.txt        # Python dependencies
├── package.json            # Next.js + React dependencies
├── next.config.js          # Next.js configuration
├── tailwind.config.js      # Tailwind CSS theme
└── server.sh               # Freebuff entry point
```
