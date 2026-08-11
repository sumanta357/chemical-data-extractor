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
python3 api/scigraph.py "tubulin" --query-type protein --hops 1

# Find targets for a compound
python3 api/scigraph.py "Aspirin" --query-type ligand --hops 1
```

## 🌐 Run the Web UI

```bash
npm install        # Install Next.js dependencies
bun run build      # or: npm run build
bun run start      # or: npm run start
# Open http://localhost:3001
```

## 🚢 Deploy on Freebuff Cloud

This project is a **Next.js app** (frontend + API) deployed on Freebuff/Vercel, with the Python search engine running as an **always-on hosted service** (Render or any Python host). Vercel's Node runtime can't run `python3`, so searches are proxied to the hosted engine via the `SEARCH_ENGINE_URL` environment variable.

### 1. Deploy the Python engine (once)

1. Push this repo to GitHub
2. On [render.com](https://render.com), choose **New → Blueprint** and select this repo
   - `render.yaml` at the repo root auto-creates the `scigraph-engine` web service (free plan, Python, root dir `python-api/`)
   - Health check: `/api/health`
3. Copy the service URL, e.g. `https://scigraph-engine.onrender.com`

> The free Render plan sleeps after ~15 min of idle — the first search after idle takes ~30–60s extra to wake the service.

### 2. Deploy the Next.js app

1. In Freebuff Cloud dashboard, the deploy is already configured (install `npm install`, build `npx next build`)
2. Set the production env var:
   ```
   SEARCH_ENGINE_URL=https://scigraph-engine.onrender.com
   ```
3. Click **Deploy**

### Architecture

```
User Browser → Next.js (React UI) → /api/search (Node.js) → SEARCH_ENGINE_URL → FastAPI (python-api/app.py)
                                                                                  ↓
                                                                        Python subprocess (api/scigraph.py + enrich)
                                                                                  ↓
                                                                        /tmp exports (CSV, Excel, GraphML, etc.)
```

In the sandbox/preview, if `SEARCH_ENGINE_URL` is unset, the Next.js app runs `python3 api/scigraph.py` directly (as before).

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
├── api/scigraph.py         # Python CLI engine
├── api/enrich_exports.py   # Enrichment pipeline (PubChem SMILES + CrossRef)
├── python-api/             # Hosted Python service (Render blueprint)
│   ├── app.py              # FastAPI backend (search + enrichment + exports)
│   └── requirements.txt    # Python dependencies for the hosted service
├── render.yaml             # Render blueprint (deploys python-api/)
├── requirements.txt        # Python dependencies (CLI)
├── lib/search-engine.ts    # Node runner: spawns python3 locally OR proxies to SEARCH_ENGINE_URL
├── package.json            # Next.js + React dependencies
├── next.config.js          # Next.js configuration
├── tailwind.config.js      # Tailwind CSS theme
└── server.sh               # Freebuff entry point
```
