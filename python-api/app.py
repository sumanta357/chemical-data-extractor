#!/usr/bin/env python3
"""
SciGraph Knowledge Graph Platform - Hosted Python API (Render / any Python host)

FastAPI backend that runs the scigraph search engine (api/scigraph.py) followed
by the enrichment pipeline (api/enrich_exports.py) as background jobs, and
exposes the polling API that the Next.js app proxies to:

    POST /api/search                 -> start a search (returns immediately)
    GET  /api/search/{id}            -> status + log + export file list
    GET  /api/search/{id}/log        -> incremental log
    GET  /api/exports/{filename}     -> download an export file
    GET  /api/searches               -> recent searches
    GET  /api/health                 -> health check

Run: uvicorn app:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# Engine files (scigraph.py, enrich_exports.py) live in ../api/
ENGINE_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(ENGINE_DIR))

# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Scientific Knowledge Graph Platform",
    description="Multi-hop automated scientific discovery engine. Search proteins, compounds, and pathways across 19 databases.",
    version="3.1.0",
)

# Writable state dirs (Render /tmp is writable; repo dirs may be read-only)
EXPORTS_DIR = Path(os.environ.get("EXPORTS_DIR", "/tmp/scigraph_exports"))
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/tmp/scigraph_workspace"))
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory search state ───────────────────────────────────────────────────
searches: dict[str, dict] = {}


# ── Models ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    query_type: str = "auto"
    hops: int = 1
    export_dir: Optional[str] = None


class SearchStatus(BaseModel):
    search_id: str
    query: str
    status: str  # queued | running | completed | failed
    progress: Optional[str] = None
    log: list[str] = []
    export_files: list[dict] = []
    created_at: str = ""
    elapsed_seconds: Optional[float] = None
    error: Optional[str] = None


# ── Background Search Runner ─────────────────────────────────────────────────

async def run_search_in_background(search_id: str, query: str, query_type: str, hops: int, export_dir: str):
    """Run api/scigraph.py as a subprocess, then enrich, capturing output."""
    state = searches[search_id]
    state["status"] = "running"
    state["log"] = []
    start_time = time.time()

    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    workspace = WORKSPACE_DIR / search_id
    workspace.mkdir(parents=True, exist_ok=True)

    # Build CLI command
    cmd = [
        sys.executable,
        str(ENGINE_DIR / "scigraph.py"),
        query,
        "--query-type", query_type,
        "--hops", str(hops),
        "--workspace", str(workspace),
        "--export-dir", export_dir,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ENGINE_DIR),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
            state["log"].append(line)
            _update_progress(state, line)

        await process.wait()

        if process.returncode == 0:
            # --- Run enrichment pipeline ---
            state["progress"] = "🧪 Enriching compounds with PubChem & CrossRef..."
            state["log"].append("")
            state["log"].append("═" * 60)
            state["log"].append("  Starting Enrichment Pipeline (PubChem SMILES / CrossRef metadata)")
            state["log"].append("═" * 60)
            enrichment_start = time.time()
            try:
                enrich_cmd = [
                    sys.executable,
                    str(ENGINE_DIR / "enrich_exports.py"),
                    "--export-dir", export_dir,
                ]
                enrich_proc = await asyncio.create_subprocess_exec(
                    *enrich_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(ENGINE_DIR),
                )
                assert enrich_proc.stdout is not None
                async for raw_line in enrich_proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                    state["log"].append(line)
                    if "Enriching compounds" in line:
                        state["progress"] = "🧪 " + line.strip()[:80]
                    elif "Downloading 2D" in line:
                        state["progress"] = "🖼️ " + line.strip()[:80]
                    elif "Enriching publications" in line:
                        state["progress"] = "📄 " + line.strip()[:80]
                    elif "Writing Excel" in line:
                        state["progress"] = "📊 " + line.strip()[:80]
                    elif "Enriched Excel saved" in line:
                        state["progress"] = "✅ Enrichment complete!"
                await enrich_proc.wait()
                enrich_elapsed = time.time() - enrichment_start
                state["log"].append(f"  ✦ Enrichment pipeline completed in {enrich_elapsed:.1f}s")
            except Exception as enrich_err:
                state["log"].append(f"  ⚠️  Enrichment step error: {enrich_err}")

            # --- Finalize ---
            state["status"] = "completed"
            state["elapsed_seconds"] = time.time() - start_time
            state["export_files"] = _list_export_files(export_dir)
            enriched_exists = any(f["name"] == "enriched_data.xlsx" for f in state["export_files"])
            if enriched_exists:
                state["progress"] = f"✅ Completed in {state['elapsed_seconds']:.1f}s + enriched multi-sheet Excel"
            else:
                state["progress"] = f"✅ Completed in {state['elapsed_seconds']:.1f}s"
        else:
            state["status"] = "failed"
            state["error"] = f"Process exited with code {process.returncode}"
            state["elapsed_seconds"] = time.time() - start_time

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["elapsed_seconds"] = time.time() - start_time


def _update_progress(state: dict, line: str):
    if ("[1/6]" in line or "[2/6]" in line or "[3/6]" in line
            or "[4/6]" in line or "[5/6]" in line or "[6/6]" in line):
        state["progress"] = line.strip()
    elif "╔══ Hop" in line:
        state["progress"] = line.strip()
    elif "Pipeline finished" in line or "finished successfully" in line:
        state["progress"] = "✅ Complete!"
    elif "Error" in line or "error" in line.lower():
        state["progress"] = f"⚠️ {line.strip()[:100]}"


def _list_export_files(export_dir: str) -> list[dict]:
    """List all files in the export directory with metadata."""
    files = []
    path = Path(export_dir)
    if not path.exists():
        return files
    for f in sorted(path.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append({
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "size_display": _format_size(f.stat().st_size),
                "url": f"/api/exports/{f.name}",
            })
    return files


def _format_size(size: int) -> str:
    for unit in ["B", "KB", "MB"]:
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "scigraph-api", "version": "3.1.0"}


@app.post("/api/search", response_model=SearchStatus)
async def start_search(request: SearchRequest, background_tasks: BackgroundTasks):
    """Start a new knowledge graph search (runs in the background)."""
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query is required")
    if request.hops < 1 or request.hops > 4:
        raise HTTPException(status_code=400, detail="Hops must be 1-4")
    if request.query_type not in ("protein", "ligand", "auto"):
        raise HTTPException(status_code=400, detail="query_type must be protein, ligand, or auto")

    search_id = str(uuid.uuid4())[:8]
    clean_q = re.sub(r"[^a-zA-Z0-9_\-]", "_", request.query.strip())[:30]
    export_dir = request.export_dir or str(EXPORTS_DIR / f"{search_id}_{clean_q}")

    state: dict = {
        "search_id": search_id,
        "query": request.query,
        "query_type": request.query_type,
        "hops": request.hops,
        "status": "queued",
        "progress": "⏳ Queued...",
        "log": [],
        "export_files": [],
        "export_dir": export_dir,
        "created_at": datetime.utcnow().isoformat(),
        "elapsed_seconds": None,
        "error": None,
    }
    searches[search_id] = state

    background_tasks.add_task(
        run_search_in_background,
        search_id, request.query, request.query_type, request.hops, export_dir
    )

    await asyncio.sleep(0.5)

    return SearchStatus(**{
        k: v for k, v in state.items() if k != "export_dir"
    })


@app.get("/api/search/{search_id}", response_model=SearchStatus)
async def get_search_status(search_id: str):
    """Get the status of a search."""
    state = searches.get(search_id)
    if not state:
        raise HTTPException(status_code=404, detail="Search not found")

    if state["status"] == "completed" and not state["export_files"]:
        state["export_files"] = _list_export_files(state.get("export_dir", ""))

    return SearchStatus(**{
        k: v for k, v in state.items() if k != "export_dir"
    })


@app.get("/api/search/{search_id}/log")
async def get_search_log(search_id: str, offset: int = Query(0, ge=0)):
    """Get incremental log output from a search."""
    state = searches.get(search_id)
    if not state:
        raise HTTPException(status_code=404, detail="Search not found")
    return {
        "search_id": search_id,
        "status": state["status"],
        "offset": offset,
        "total_lines": len(state["log"]),
        "new_lines": state["log"][offset:],
    }


@app.get("/api/exports/{filename:path}")
async def download_export(filename: str, search_id: Optional[str] = Query(None)):
    """Download an export file. Optionally specify a search_id to find the right directory."""
    if search_id:
        state = searches.get(search_id)
        if not state:
            raise HTTPException(status_code=404, detail="Search not found")
        file_path = Path(state["export_dir"]) / filename
    else:
        for export_dir in [EXPORTS_DIR] + [Path(s["export_dir"]) for s in searches.values()]:
            candidate = export_dir / filename
            if candidate.exists():
                file_path = candidate
                break
        else:
            file_path = EXPORTS_DIR / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=_guess_mime(filename),
    )


@app.get("/api/searches")
async def list_searches(limit: int = Query(20, ge=1, le=100)):
    """List recent searches."""
    recent = sorted(
        searches.values(),
        key=lambda s: s["created_at"],
        reverse=True,
    )[:limit]
    return [
        {
            "search_id": s["search_id"],
            "query": s["query"],
            "status": s["status"],
            "progress": s["progress"],
            "created_at": s["created_at"],
            "elapsed_seconds": s["elapsed_seconds"],
            "file_count": len(s.get("export_files", [])),
        }
        for s in recent
    ]


@app.get("/")
async def root():
    # If the server can serve this page, it IS healthy.
    # No self-check needed — avoids Render port issues.
    html = (
        LANDING_PAGE_HTML
        .replace('class="badge waking" id="health-badge"',
                  'class="badge ok" id="health-badge"')
        .replace('>Connecting…</span>', '>● Healthy</span>')
        .replace('>⏳ Connecting…</span>', '>● Healthy</span>')
    )
    resp = HTMLResponse(html)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


# ── Landing Page HTML ────────────────────────────────────────────────────────

LANDING_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SciGraph — Scientific Knowledge Graph Platform</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #030712;
    --surface: #111827;
    --surface2: #1f2937;
    --border: #1f2937;
    --accent: #06b6d4;
    --accent2: #0891b2;
    --accent3: #8b5cf6;
    --text: #f9fafb;
    --text2: #9ca3af;
    --success: #10b981;
    --warn: #f59e0b;
    --error: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.6;
  }

  /* Header */
  .header {
    border-bottom: 1px solid var(--border);
    background: rgba(17,24,39,0.8);
    backdrop-filter: blur(12px);
    position: sticky;
    top: 0;
    z-index: 50;
  }
  .header-inner {
    max-width: 1200px;
    margin: 0 auto;
    padding: 0.75rem 1.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header-brand {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .header-brand .logo { font-size: 1.5rem; }
  .header-brand h1 {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.02em;
  }
  .header-brand .version {
    font-size: 0.7rem;
    color: var(--text2);
    font-weight: 500;
    background: var(--surface2);
    padding: 0.15rem 0.5rem;
    border-radius: 9999px;
    border: 1px solid var(--border);
  }
  .header-status {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 500;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text2);
  }
  .badge.ok { border-color: rgba(16,185,129,0.4); color: var(--success); background: rgba(16,185,129,0.1); }
  .badge.waking { border-color: rgba(245,158,11,0.4); color: var(--warn); background: rgba(245,158,11,0.1); }

  /* Hero */
  .hero {
    text-align: center;
    padding: 4rem 1.5rem 3rem;
    background: linear-gradient(180deg, #030712 0%, #0c1222 50%, #030712 100%);
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    top: 0; left: 50%;
    width: 800px; height: 400px;
    transform: translateX(-50%);
    background: radial-gradient(ellipse, rgba(6,182,212,0.08) 0%, transparent 70%);
    pointer-events: none;
  }
  .hero h2 {
    font-size: 2.5rem;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 0.75rem;
    position: relative;
    letter-spacing: -0.03em;
  }
  .hero h2 span {
    background: linear-gradient(135deg, var(--accent), var(--accent3));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .hero p {
    color: var(--text2);
    font-size: 1.1rem;
    max-width: 550px;
    margin: 0 auto 1.5rem;
    position: relative;
  }
  .hero-stats {
    display: flex;
    gap: 2rem;
    justify-content: center;
    flex-wrap: wrap;
    position: relative;
  }
  .hero-stat {
    text-align: center;
  }
  .hero-stat .num {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--accent);
  }
  .hero-stat .label {
    font-size: 0.75rem;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  /* Main layout */
  .main {
    max-width: 1200px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
    display: grid;
    grid-template-columns: 1fr;
    gap: 1.5rem;
  }
  @media (min-width: 768px) {
    .main { grid-template-columns: 2fr 3fr; }
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem;
  }
  .card h3 {
    font-size: 0.8rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  /* Form */
  .field { margin-bottom: 0.85rem; }
  .field label {
    display: block;
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--text2);
    margin-bottom: 0.35rem;
  }
  .field input, .field select {
    width: 100%;
    padding: 0.6rem 0.75rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    font-size: 0.9rem;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .field input:focus, .field select:focus { border-color: var(--accent); }
  .field select { cursor: pointer; }

  .type-btns, .hop-btns {
    display: flex;
    gap: 0.4rem;
  }
  .type-btns button, .hop-btns button {
    flex: 1;
    padding: 0.5rem 0.25rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text2);
    font-size: 0.8rem;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.15s;
  }
  .type-btns button.active, .hop-btns button.active {
    border-color: var(--accent);
    background: rgba(6,182,212,0.1);
    color: var(--accent);
  }
  .type-btns button:hover, .hop-btns button:hover:not(.active) {
    border-color: #374151;
    color: var(--text);
  }

  .search-btn {
    width: 100%;
    padding: 0.7rem;
    border-radius: 8px;
    border: none;
    font-size: 0.95rem;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    background: linear-gradient(135deg, var(--accent2), var(--accent));
    color: #030712;
    transition: all 0.15s;
    margin-top: 0.5rem;
  }
  .search-btn:hover { filter: brightness(1.1); transform: translateY(-1px); }
  .search-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

  .error-msg {
    margin-top: 0.65rem;
    padding: 0.6rem 0.75rem;
    border-radius: 8px;
    background: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    color: var(--error);
    font-size: 0.8rem;
    display: none;
  }

  /* Progress */
  .progress-section { display: none; }
  .progress-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
  }
  .progress-header h3 { margin-bottom: 0; }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    font-size: 0.7rem;
    font-weight: 600;
  }
  .status-queued { background: rgba(245,158,11,0.15); color: var(--warn); }
  .status-running { background: rgba(6,182,212,0.15); color: var(--accent); }
  .status-completed { background: rgba(16,185,129,0.15); color: var(--success); }
  .status-failed { background: rgba(239,68,68,0.15); color: var(--error); }
  .progress-bar {
    height: 3px;
    background: var(--surface2);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 0.75rem;
  }
  .progress-bar .fill {
    height: 100%;
    width: 30%;
    background: linear-gradient(90deg, var(--accent2), var(--accent3));
    border-radius: 2px;
    animation: shimmer 1.5s infinite;
  }
  @keyframes shimmer {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
  .progress-text {
    font-size: 0.85rem;
    color: var(--text);
    margin-bottom: 0.5rem;
  }
  .log-box {
    background: #0d1117;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.85rem;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.72rem;
    line-height: 1.6;
    color: var(--text2);
    max-height: 400px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .log-box::-webkit-scrollbar { width: 5px; }
  .log-box::-webkit-scrollbar-track { background: transparent; }
  .log-box::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  /* Results */
  .results-section { display: none; }
  .exports-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.5rem;
  }
  .export-card {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.65rem;
    transition: border-color 0.15s;
  }
  .export-card:hover { border-color: var(--accent); }
  .export-card .name {
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--text);
    word-break: break-all;
    margin-bottom: 0.15rem;
  }
  .export-card .size { font-size: 0.7rem; color: var(--text2); }
  .export-card a {
    display: inline-block;
    margin-top: 0.3rem;
    font-size: 0.72rem;
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
  }
  .export-card a:hover { text-decoration: underline; }

  /* Features */
  .features {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
    margin-top: 2rem;
    max-width: 1200px;
    margin-left: auto;
    margin-right: auto;
    padding: 0 1.5rem 2rem;
  }
  .feature-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
  }
  .feature-card .icon { font-size: 1.5rem; margin-bottom: 0.5rem; }
  .feature-card h4 { font-size: 0.9rem; font-weight: 600; color: var(--text); margin-bottom: 0.25rem; }
  .feature-card p { font-size: 0.8rem; color: var(--text2); line-height: 1.5; }

  /* Footer */
  .footer {
    text-align: center;
    padding: 2rem 1.5rem;
    color: var(--text2);
    font-size: 0.75rem;
    border-top: 1px solid var(--border);
    margin-top: 1rem;
  }
  .footer a { color: var(--accent); text-decoration: none; }
  .footer a:hover { text-decoration: underline; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="header-inner">
    <div class="header-brand">
      <span class="logo">🔬</span>
      <h1>SciGraph</h1>
      <span class="version">v3.1</span>
    </div>
    <div class="header-status">
      <span class="badge ok" id="health-badge">● Online</span>
    </div>
  </div>
</div>

<!-- Hero -->
<div class="hero">
  <h2><span>Scientific Knowledge Graph</span></h2>
  <p>Multi-hop automated discovery engine. Search proteins, compounds, and pathways across 19+ databases.</p>
  <div class="hero-stats">
    <div class="hero-stat"><div class="num">19+</div><div class="label">Databases</div></div>
    <div class="hero-stat"><div class="num">4</div><div class="label">Hop Depth</div></div>
    <div class="hero-stat"><div class="num">7+</div><div class="label">Export Formats</div></div>
    <div class="hero-stat"><div class="num">15</div><div class="label">Export Files</div></div>
  </div>
</div>

<!-- Main Content -->
<div class="main">
  <!-- Left: Search Form -->
  <div class="card">
    <h3>Search Knowledge Graph</h3>
    <div class="field">
      <label>Query</label>
      <input id="query" type="text" placeholder='e.g. &quot;Aspirin&quot;, &quot;Tubulin&quot;, &quot;EGFR&quot;, &quot;P23219&quot;' autofocus>
    </div>
    <div class="field">
      <label>Query Type</label>
      <div class="type-btns">
        <button class="active" data-type="auto">🔄 Auto</button>
        <button data-type="protein">🧬 Protein</button>
        <button data-type="ligand">💊 Ligand</button>
      </div>
    </div>
    <div class="field">
      <label>Expansion Depth</label>
      <div class="hop-btns">
        <button class="active" data-hops="1">1</button>
        <button data-hops="2">2</button>
        <button data-hops="3">3</button>
        <button data-hops="4">4</button>
      </div>
    </div>
    <button class="search-btn" id="search-btn" onclick="startSearch()">🚀 Run Search</button>
    <span id="elapsed" style="display:block;text-align:center;font-size:0.8rem;color:var(--text2);margin-top:0.4rem;"></span>
    <div class="error-msg" id="error-msg"></div>
  </div>

  <!-- Right: Progress + Results -->
  <div>
    <div class="card progress-section" id="progress-section">
      <div class="progress-header">
        <h3>Progress</h3>
        <span class="status-pill status-running" id="status-pill">⏳ Queued</span>
      </div>
      <div class="progress-bar"><div class="fill"></div></div>
      <div class="progress-text" id="progress-text">⏳ Queued…</div>
      <details open>
        <summary style="font-size:0.8rem;color:var(--text2);cursor:pointer;margin-bottom:0.5rem;">Live Log</summary>
        <div class="log-box" id="log-box"></div>
      </details>
    </div>

    <div class="card results-section" id="results-section">
      <h3>Export Files</h3>
      <div class="exports-grid" id="exports-grid"></div>
    </div>
  </div>
</div>

<!-- Features -->
<div class="features">
  <div class="feature-card">
    <div class="icon">🧬</div>
    <h4>Multi-Hop Expansion</h4>
    <p>  Traverse knowledge graphs up to 4 hops deep to discover hidden connections.
  Note: Higher hop counts may take longer on first visit (server cold start).</p>
  </div>
  <div class="feature-card">
    <div class="icon">📊</div>
    <h4>Rich Exports</h4>
    <p>Excel, CSV, GraphML, Cypher, RDF, Turtle, Parquet — ready for analysis.</p>
  </div>
  <div class="feature-card">
    <div class="icon">🔬</div>
    <h4>19+ Databases</h4>
    <p>PubChem, ChEMBL, UniProt, PDB, KEGG, Reactome, and many more.</p>
  </div>
  <div class="feature-card">
    <div class="icon">✨</div>
    <h4>Auto-Enrichment</h4>
    <p>SMILES, molecular formulas, CrossRef metadata added automatically.</p>
  </div>
</div>

<!-- Footer -->
<div class="footer">
  Powered by <strong>SciGraph v3.1</strong> — Enterprise Scientific Knowledge Graph Platform<br>
  19 database connectors · Multi-hop graph traversal · Enrichment pipeline
</div>

<script>
let pollTimer = null;
let elapsedTimer = null;
let startTime = 0;
let healthRetries = 0;
let queryType = 'auto';
let hops = 1;

// Type buttons
document.querySelectorAll('.type-btns button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.type-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    queryType = btn.dataset.type;
  });
});
// Hops buttons
document.querySelectorAll('.hop-btns button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.hop-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    hops = parseInt(btn.dataset.hops);
  });
});

// Health check (only downgrade, never override server-side Healthy)
function checkHealth() {
  const badge = document.getElementById('health-badge');
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 30000);
  fetch('/api/health', {signal: ctrl.signal}).then(r => {
    clearTimeout(tid);
    if (!r.ok) throw new Error();
    return r.json();
  }).then(() => {
    badge.className = 'badge ok';
    badge.textContent = '● Online';
  }).catch(() => {
    clearTimeout(tid);
    if (badge.textContent.includes('Online')) {
      healthRetries++;
      if (healthRetries < 10) {
        badge.className = 'badge waking';
        badge.textContent = '⏳ Waking…';
        setTimeout(checkHealth, 5000);
      } else {
        badge.className = 'badge';
        badge.textContent = '● Offline';
      }
    }
  });
}
checkHealth();

function showError(msg) { const e = document.getElementById('error-msg'); e.textContent = msg; e.style.display = 'block'; }
function hideError() { document.getElementById('error-msg').style.display = 'none'; }

document.getElementById('query').addEventListener('keydown', e => { if (e.key === 'Enter') startSearch(); });

async function startSearch(retries) {
  retries = retries || 0;
  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('query').focus(); return; }
  hideError();
  const btn = document.getElementById('search-btn');
  btn.disabled = true; btn.textContent = '⏳ Starting…';
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 150000);
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, query_type: queryType, hops}),
      signal: ctrl.signal
    });
    clearTimeout(tid);
    if (!res.ok) throw new Error(await res.text() || 'HTTP ' + res.status);
    const data = await res.json();
    startTime = Date.now();
    document.getElementById('progress-section').style.display = '';
    document.getElementById('results-section').style.display = 'none';
    document.getElementById('exports-grid').innerHTML = '';
    document.getElementById('log-box').textContent = '';
    updateUI(data);
    elapsedTimer = setInterval(() => {
      document.getElementById('elapsed').textContent = ((Date.now() - startTime) / 1000).toFixed(1) + 's';
    }, 200);
    pollTimer = setInterval(() => pollSearch(data.search_id), 1500);
  } catch (err) {
    if (err.name === 'AbortError' && retries < 2) {
      document.getElementById('error-msg').textContent = '⏳ Server is waking up, retrying in 5s...';
      document.getElementById('error-msg').style.display = 'block';
      setTimeout(() => startSearch(retries + 1), 5000);
      return;
    }
    showError(err.name === 'AbortError' ? 'Service is still warming up. Wait 30s and try again.' : 'Search failed: ' + err.message);
    btn.disabled = false; btn.textContent = '🚀 Run Search';
  }
}

async function pollSearch(id) {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 15000);
    const res = await fetch('/api/search/' + id, {signal: ctrl.signal});
    clearTimeout(tid);
    const data = await res.json();
    updateUI(data);
    if (data.status === 'completed' || data.status === 'failed') {
      clearInterval(pollTimer); clearInterval(elapsedTimer);
      document.getElementById('search-btn').disabled = false;
      document.getElementById('search-btn').textContent = '🚀 Run Search';
      if (data.status === 'completed' && data.export_files?.length > 0) showResults(data);
      if (data.status === 'failed') showError(data.error || 'Search failed.');
    }
  } catch (e) { /* retry */ }
}

function updateUI(data) {
  const pill = document.getElementById('status-pill');
  const m = { queued: ['⏳ Queued', 'status-queued'], running: ['⚡ Running', 'status-running'], completed: ['✅ Done', 'status-completed'], failed: ['❌ Failed', 'status-failed'] };
  const [l, c] = m[data.status] || ['?', ''];
  pill.textContent = l; pill.className = 'status-pill ' + c;
  document.getElementById('progress-text').textContent = data.progress || '';
  if (data.log?.length > 0) {
    const box = document.getElementById('log-box');
    box.textContent = data.log.join('\n');
    box.scrollTop = box.scrollHeight;
  }
}

function showResults(data) {
  document.getElementById('results-section').style.display = '';
  const g = document.getElementById('exports-grid');
  g.innerHTML = '';
  for (const f of data.export_files) {
    const d = document.createElement('div');
    d.className = 'export-card';
    const u = '/api/exports/' + encodeURIComponent(f.name) + '?search_id=' + data.search_id;
    d.innerHTML = '<div class="name">📄 ' + f.name + '</div><div class="size">' + f.size_display + '</div><a href="' + u + '" target="_blank">Download →</a>';
    g.appendChild(d);
  }
}
</script>
</body>
</html>
"""


# ── MIME helpers ─────────────────────────────────────────────────────────────

def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".graphml": "application/xml",
        ".ttl": "text/turtle",
        ".cypher": "text/plain",
        ".parquet": "application/octet-stream",
        ".png": "image/png",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(ext, "application/octet-stream")


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print(f"🔬 SciGraph API v3.1 starting...")
    print(f"   Python: {sys.version}")
    print(f"   Engine dir: {ENGINE_DIR}")
    print(f"   Exports dir: {EXPORTS_DIR}")
    print(f"   Workspace dir: {WORKSPACE_DIR}")
