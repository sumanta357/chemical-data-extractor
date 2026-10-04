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

# Find inhibitors for a protein target (produces enriched_data.xlsx + structure images, like the web app)
python3 api/scigraph.py "tubulin" --query-type protein --hops 1

# Find targets for a compound
python3 api/scigraph.py "Aspirin" --query-type ligand --hops 1

# Skip the enrichment step (faster; no enriched_data.xlsx)
python3 api/scigraph.py "Aspirin" --query-type ligand --hops 1 --no-enrich
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
│   ├── api/                # Route handlers (search, health, exports) — local dev only
│   ├── page.tsx            # Search workspace
│   ├── resources/          # Documentation and downloads page
│   ├── reset-password/     # Password-reset landing page
│   ├── admin/academics/    # Academic customers directory
│   └── layout.tsx          # Root layout, theme bootstrap
├── components/             # React components (SearchForm, ProgressView, etc.)
│   ├── ui.tsx              # Button/field/pill class specifications
│   ├── icons.tsx           # Inline SVG icon set
│   ├── SiteChrome.tsx      # Shared header and footer
│   └── ThemeToggle.tsx     # Light/dark switcher
├── lib/                    # TypeScript library
│   ├── types.ts            # Type definitions
│   ├── theme.ts            # Theme state, persistence and events
│   └── search-engine.ts    # Python subprocess manager
├── api/scigraph.py         # Python CLI engine
├── api/enrich_exports.py   # Enrichment pipeline (PubChem SMILES + CrossRef)
├── python-api/             # Hosted Python service (Render blueprint)
│   ├── app.py              # FastAPI backend (search + enrichment + exports)
│   └── requirements.txt    # Python dependencies for the hosted service
├── docs/tutorial/          # Markdown source of the downloadable documentation
├── scripts/                # Documentation build + UI verification tools
├── public/downloads/       # Committed PDFs and templates served by /resources
├── render.yaml             # Render blueprint (deploys python-api/)
├── requirements.txt        # Python dependencies (CLI)
├── package.json            # Next.js + React dependencies
├── next.config.js          # Next.js configuration
├── tailwind.config.js      # Tailwind CSS theme tokens
└── server.sh               # Freebuff entry point
```

## 🎨 Interface

### Design system

Every colour, control and label comes from one place.

- **Colour tokens** live in `app/globals.css` as `R G B` triplets and are mapped
  into Tailwind in `tailwind.config.js` (`bg-surface`, `text-fg-muted`,
  `border-line`, `bg-accent-solid`, …). No component hardcodes a hex value, so
  both themes come from the same markup.
- **Light and Dark are designed separately.** Dark is a near-black canvas
  (`#080a0e`) with layered surfaces; Light is white with tinted surfaces. Both
  pass WCAG AA — verify with `python3 scripts/check_contrast.py`.
- **Controls are specified once** in `components/ui.tsx`: one button height, one
  radius, one focus ring, one segmented control, one status pill, one note style.
- **Icons** are inline SVG on one 24×24 grid in `components/icons.tsx`
  (`currentColor`, 1.6px stroke), so there is no icon-library dependency.
- **Theme choice** is written to `localStorage` under `cde-theme` and applied
  before first paint by the inline bootstrap in `app/layout.tsx`, so there is no
  flash of the wrong theme. With no stored choice the OS preference is used.

### Documentation pipeline

The PDFs on the `/resources` page are build-time assets, committed to
`public/downloads/` so the runtime container stays Python-only.

```bash
pip install reportlab
python3 scripts/build_resource_pdfs.py     # docs/tutorial/*.md -> public/downloads/*.pdf
python3 scripts/check_figures.py           # figures stay inside their frames
python3 scripts/check_contrast.py          # both themes pass WCAG AA
```

Edit `docs/tutorial/*.md` and re-run the build to keep the PDFs in step with the
interface. The illustrations are vector recreations of the live UI rather than
screenshots, so they stay sharp and never go stale in layout.

> `public/` is copied into the UI build stage of the `Dockerfile`; if you add a
> new directory of static assets, add it there too or it will not ship.
