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
    version="3.2.2",
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
    return {"status": "ok", "service": "scigraph-api", "version": "3.2.2"}


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
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
<title>Chemical Data Extractor — Scientific Knowledge Graph Platform</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #030712;
    --bg2: #0a0f1e;
    --surface: rgba(17,24,39,0.6);
    --surface2: #1f2937;
    --border: rgba(55,65,81,0.5);
    --accent: #06b6d4;
    --accent2: #0891b2;
    --accent3: #8b5cf6;
    --accent4: #a78bfa;
    --text: #f9fafb;
    --text2: #9ca3af;
    --text3: #6b7280;
    --success: #10b981;
    --warn: #f59e0b;
    --error: #ef4444;
    --glow: rgba(6,182,212,0.15);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.6;
    overflow-x: hidden;
    background-image: radial-gradient(ellipse at 20% 50%, rgba(6,182,212,0.03) 0%, transparent 50%),
                      radial-gradient(ellipse at 80% 20%, rgba(139,92,246,0.03) 0%, transparent 50%),
                      radial-gradient(ellipse at 50% 80%, rgba(16,185,129,0.02) 0%, transparent 50%);
    background-attachment: fixed;
  }

  /* ── Animated particle background ── */
  #particles {
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    overflow: hidden;
  }
  .particle {
    position: absolute;
    border-radius: 50%;
    background: var(--accent);
    opacity: 0;
    animation: float-up linear infinite;
  }
  @keyframes float-up {
    0% { opacity: 0; transform: translateY(100vh) scale(0); }
    10% { opacity: 0.8; }
    50% { opacity: 0.5; }
    90% { opacity: 0.3; }
    100% { opacity: 0; transform: translateY(-10vh) scale(1); }
  }

  /* ── Grid background ── */
  .grid-bg {
    position: fixed;
    inset: 0;
    z-index: 0;
    background-image:
      linear-gradient(rgba(6,182,212,0.06) 1px, transparent 1px),
      linear-gradient(90deg, rgba(6,182,212,0.06) 1px, transparent 1px);
    background-size: 50px 50px;
    pointer-events: none;
  }

  /* ── Header ── */
  .header {
    border-bottom: 1px solid var(--border);
    background: rgba(3,7,18,0.85);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header-inner {
    max-width: 1280px;
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
  .header-brand .logo {
    font-size: 1.6rem;
    filter: drop-shadow(0 0 8px rgba(6,182,212,0.5));
    animation: pulse-glow 3s ease-in-out infinite;
  }
  @keyframes pulse-glow {
    0%, 100% { filter: drop-shadow(0 0 8px rgba(6,182,212,0.3)); transform: scale(1); }
    50% { filter: drop-shadow(0 0 20px rgba(6,182,212,0.8)); transform: scale(1.05); }
  }
  .header-brand h1 {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.02em;
  }
  .header-brand .version {
    font-size: 0.65rem;
    color: var(--accent);
    font-weight: 600;
    background: rgba(6,182,212,0.1);
    padding: 0.15rem 0.5rem;
    border-radius: 9999px;
    border: 1px solid rgba(6,182,212,0.2);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.72rem;
    font-weight: 500;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text2);
    backdrop-filter: blur(10px);
  }
  .badge.ok { border-color: rgba(16,185,129,0.4); color: var(--success); background: rgba(16,185,129,0.08); }
  .badge.waking { border-color: rgba(245,158,11,0.4); color: var(--warn); background: rgba(245,158,11,0.08); }

  /* ── Hero ── */
  .hero {
    text-align: center;
    padding: 5rem 1.5rem 3.5rem;
    position: relative;
    z-index: 1;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    top: -200px; left: 50%;
    width: 1000px; height: 600px;
    transform: translateX(-50%);
    background: radial-gradient(ellipse, rgba(6,182,212,0.12) 0%, rgba(139,92,246,0.06) 40%, transparent 70%);
    pointer-events: none;
    animation: hero-glow 6s ease-in-out infinite alternate;
  }
  @keyframes hero-glow {
    0% { opacity: 0.6; transform: translateX(-50%) scale(1); }
    100% { opacity: 1; transform: translateX(-50%) scale(1.1); }
  }
  .hero .molecules {
    position: absolute;
    inset: 0;
    pointer-events: none;
    overflow: hidden;
  }
  .molecule {
    position: absolute;
    font-size: 1.5rem;
    opacity: 0.3;
    filter: drop-shadow(0 0 6px currentColor);
    animation: molecule-float 15s ease-in-out infinite;
  }
  .molecule:nth-child(1) { left: 5%; top: 15%; animation-delay: 0s; animation-duration: 18s; }
  .molecule:nth-child(2) { left: 90%; top: 25%; animation-delay: -5s; animation-duration: 16s; }
  .molecule:nth-child(3) { left: 15%; top: 75%; animation-delay: -10s; animation-duration: 20s; }
  .molecule:nth-child(4) { left: 80%; top: 70%; animation-delay: -7s; animation-duration: 15s; }
  .molecule:nth-child(5) { left: 50%; top: 10%; animation-delay: -3s; animation-duration: 17s; }
  .molecule:nth-child(6) { left: 30%; top: 85%; animation-delay: -12s; animation-duration: 19s; }
  @keyframes molecule-float {
    0%, 100% { transform: translate(0, 0) rotate(0deg) scale(1); }
    25% { transform: translate(40px, -30px) rotate(90deg) scale(1.1); }
    50% { transform: translate(-30px, 20px) rotate(180deg) scale(0.95); }
    75% { transform: translate(20px, 35px) rotate(270deg) scale(1.05); }
  }
  .hero h2 {
    font-size: clamp(2rem, 5vw, 3.2rem);
    font-weight: 800;
    color: var(--text);
    margin-bottom: 1rem;
    position: relative;
    letter-spacing: -0.03em;
    line-height: 1.1;
  }
  .hero h2 span {
    background: linear-gradient(135deg, var(--accent) 0%, var(--accent3) 50%, var(--accent4) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    background-size: 200% 200%;
    animation: gradient-shift 6s ease-in-out infinite;
  }
  @keyframes gradient-shift {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
  }
  .hero p {
    color: var(--text2);
    font-size: 1.1rem;
    max-width: 580px;
    margin: 0 auto 2rem;
    position: relative;
    line-height: 1.7;
  }
  .hero-stats {
    display: flex;
    gap: 2.5rem;
    justify-content: center;
    flex-wrap: wrap;
    position: relative;
  }
  .hero-stat {
    text-align: center;
    padding: 1rem;
    border-radius: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    backdrop-filter: blur(10px);
    min-width: 100px;
    transition: all 0.3s ease;
  }
  .hero-stat:hover {
    border-color: rgba(6,182,212,0.3);
    transform: translateY(-2px);
    box-shadow: 0 8px 25px rgba(6,182,212,0.1);
  }
  .hero-stat {
    animation: stat-glow 4s ease-in-out infinite;
  }
  .hero-stat:nth-child(2) { animation-delay: 0.5s; }
  .hero-stat:nth-child(3) { animation-delay: 1s; }
  .hero-stat:nth-child(4) { animation-delay: 1.5s; }
  @keyframes stat-glow {
    0%, 100% { box-shadow: 0 0 0 rgba(6,182,212,0); }
    50% { box-shadow: 0 0 20px rgba(6,182,212,0.08); }
  }
  .hero-stat .num {
    font-size: 1.8rem;
    font-weight: 800;
    background: linear-gradient(135deg, var(--accent), var(--accent3));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .hero-stat .label {
    font-size: 0.7rem;
    color: var(--text3);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    margin-top: 0.25rem;
  }

  /* ── Main layout ── */
  .main {
    max-width: 1280px;
    margin: 0 auto;
    padding: 2rem 1.5rem 3rem;
    display: grid;
    grid-template-columns: 1fr;
    gap: 1.5rem;
    position: relative;
    z-index: 1;
  }
  @media (min-width: 768px) {
    .main { grid-template-columns: 2fr 3fr; }
  }

  /* ── Glass card ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 1.5rem;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
  }
  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(6,182,212,0.3), transparent);
  }
  .card:hover {
    border-color: rgba(6,182,212,0.25);
    box-shadow: 0 8px 32px rgba(6,182,212,0.08), 0 0 60px rgba(6,182,212,0.03);
  }
  .card h3 {
    font-size: 0.8rem;
    font-weight: 600;
    margin-bottom: 1.25rem;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  /* ── Instruction boxes ── */
  .info-box {
    background: rgba(6,182,212,0.04);
    border: 1px solid rgba(6,182,212,0.12);
    border-radius: 10px;
    padding: 1rem;
    margin-bottom: 1rem;
    transition: all 0.3s ease;
  }
  .info-box:hover {
    border-color: rgba(6,182,212,0.25);
    background: rgba(6,182,212,0.06);
  }
  .info-box .label {
    font-size: 0.65rem;
    font-weight: 700;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.6rem;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .info-box .label::before {
    content: '';
    width: 3px;
    height: 12px;
    background: linear-gradient(180deg, var(--accent), var(--accent3));
    border-radius: 2px;
  }
  .step {
    display: flex;
    gap: 0.6rem;
    margin-bottom: 0.5rem;
    font-size: 0.8rem;
    color: var(--text2);
    line-height: 1.5;
  }
  .step:last-child { margin-bottom: 0; }
  .step-num {
    color: var(--accent);
    font-weight: 800;
    font-size: 0.75rem;
    min-width: 1.2rem;
    flex-shrink: 0;
  }
  .step strong { color: var(--text); font-weight: 600; }
  .hop-row {
    display: flex;
    gap: 0.6rem;
    margin-bottom: 0.4rem;
    font-size: 0.78rem;
    color: var(--text2);
  }
  .hop-row:last-child { margin-bottom: 0; }
  .hop-tag {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    font-size: 0.72rem;
    min-width: 3.5rem;
    flex-shrink: 0;
  }
  .hop-1 { color: var(--success); }
  .hop-2 { color: var(--accent); }
  .hop-3 { color: var(--accent3); }
  .hop-4 { color: var(--warn); }

  /* ── Form fields ── */
  .field { margin-bottom: 1rem; }
  .field label {
    display: block;
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--text3);
    margin-bottom: 0.4rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .field input, .field select {
    width: 100%;
    padding: 0.75rem 1rem;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: rgba(3,7,18,0.6);
    color: var(--text);
    font-size: 0.9rem;
    font-family: inherit;
    outline: none;
    transition: all 0.2s ease;
    backdrop-filter: blur(5px);
  }
  .field input::placeholder { color: var(--text3); }
  .field input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(6,182,212,0.1), 0 0 20px rgba(6,182,212,0.05);
  }
  .field select { cursor: pointer; }

  .type-btns, .hop-btns {
    display: flex;
    gap: 0.5rem;
  }
  .type-btns button, .hop-btns button {
    flex: 1;
    padding: 0.65rem 0.5rem;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: rgba(3,7,18,0.4);
    color: var(--text2);
    font-size: 0.8rem;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.2s ease;
    backdrop-filter: blur(5px);
  }
  .type-btns button.active, .hop-btns button.active {
    border-color: var(--accent);
    background: rgba(6,182,212,0.1);
    color: var(--accent);
    box-shadow: 0 0 15px rgba(6,182,212,0.1);
  }
  .type-btns button:hover, .hop-btns button:hover:not(.active) {
    border-color: #4b5563;
    color: var(--text);
    background: rgba(31,41,55,0.5);
  }

  .search-btn {
    width: 100%;
    padding: 0.85rem;
    border-radius: 10px;
    border: none;
    font-size: 0.95rem;
    font-weight: 700;
    font-family: inherit;
    cursor: pointer;
    background: linear-gradient(135deg, var(--accent2), var(--accent), var(--accent3));
    background-size: 200% 200%;
    color: #fff;
    transition: all 0.3s ease;
    margin-top: 0.75rem;
    position: relative;
    overflow: hidden;
    letter-spacing: 0.02em;
    animation: btn-glow 3s ease-in-out infinite;
  }
  @keyframes btn-glow {
    0%, 100% { box-shadow: 0 4px 15px rgba(6,182,212,0.2); }
    50% { box-shadow: 0 4px 25px rgba(6,182,212,0.4), 0 0 40px rgba(139,92,246,0.15); }
  }
  .search-btn::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, transparent 30%, rgba(255,255,255,0.15) 50%, transparent 70%);
    transform: translateX(-100%);
    transition: transform 0.6s ease;
  }
  .search-btn:hover::before { transform: translateX(100%); }
  .search-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 30px rgba(6,182,212,0.3);
    background-position: 100% 0;
  }
  .search-btn:active { transform: translateY(0); }
  .search-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; box-shadow: none; }
  .search-btn:disabled::before { display: none; }

  .error-msg {
    margin-top: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: 10px;
    background: rgba(239,68,68,0.08);
    border: 1px solid rgba(239,68,68,0.2);
    color: var(--error);
    font-size: 0.8rem;
    display: none;
    animation: shake 0.3s ease;
  }
  @keyframes shake {
    0%, 100% { transform: translateX(0); }
    25% { transform: translateX(-4px); }
    75% { transform: translateX(4px); }
  }

  /* ── Progress ── */
  .progress-section { display: none; }
  .progress-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1rem;
  }
  .progress-header h3 { margin-bottom: 0; }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.7rem;
    font-weight: 600;
    animation: pill-pulse 1.5s ease-in-out infinite;
  }
  @keyframes pill-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.8; }
  }
  .status-queued { background: rgba(245,158,11,0.12); color: var(--warn); border: 1px solid rgba(245,158,11,0.2); }
  .status-running { background: rgba(6,182,212,0.12); color: var(--accent); border: 1px solid rgba(6,182,212,0.2); }
  .status-completed { background: rgba(16,185,129,0.12); color: var(--success); border: 1px solid rgba(16,185,129,0.2); }
  .status-failed { background: rgba(239,68,68,0.12); color: var(--error); border: 1px solid rgba(239,68,68,0.2); }
  .progress-bar {
    height: 4px;
    background: rgba(31,41,55,0.8);
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 1rem;
  }
  .progress-bar .fill {
    height: 100%;
    width: 30%;
    background: linear-gradient(90deg, var(--accent2), var(--accent3), var(--accent));
    border-radius: 4px;
    animation: shimmer 2s ease-in-out infinite;
  }
  @keyframes shimmer {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }
  .progress-text {
    font-size: 0.85rem;
    color: var(--text);
    margin-bottom: 0.75rem;
    font-weight: 500;
  }
  .log-box {
    background: rgba(3,7,18,0.8);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.72rem;
    line-height: 1.7;
    color: var(--text2);
    max-height: 450px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .log-box::-webkit-scrollbar { width: 5px; }
  .log-box::-webkit-scrollbar-track { background: transparent; }
  .log-box::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  /* ── Results ── */
  .results-section { display: none; }
  .exports-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: 0.75rem;
  }
  .export-card {
    background: rgba(3,7,18,0.5);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.85rem;
    transition: all 0.2s ease;
  }
  .export-card:hover {
    border-color: var(--accent);
    transform: translateY(-2px);
    box-shadow: 0 4px 15px rgba(6,182,212,0.1);
  }
  .export-card .name {
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--text);
    word-break: break-all;
    margin-bottom: 0.2rem;
  }
  .export-card .size { font-size: 0.7rem; color: var(--text3); }
  .export-card a {
    display: inline-block;
    margin-top: 0.4rem;
    font-size: 0.72rem;
    color: var(--accent);
    text-decoration: none;
    font-weight: 600;
    transition: color 0.2s;
  }
  .export-card a:hover { color: var(--accent3); }

  /* ── Database cards ── */
  .db-cards {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
    margin-top: 2rem;
    position: relative;
  }
  @media (max-width: 640px) { .db-cards { grid-template-columns: 1fr; } }
  .db-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem;
    text-align: center;
    transition: all 0.3s ease;
    backdrop-filter: blur(10px);
  }
  .db-card:hover {
    border-color: rgba(6,182,212,0.3);
    transform: translateY(-3px);
    box-shadow: 0 8px 25px rgba(6,182,212,0.08);
  }
  .db-card .icon { font-size: 2rem; margin-bottom: 0.5rem; }
  .db-card h4 { font-size: 0.85rem; font-weight: 600; color: var(--text); margin-bottom: 0.25rem; }
  .db-card p { font-size: 0.7rem; color: var(--text3); line-height: 1.5; }

  /* ── Idle state ── */
  .idle-state {
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 2rem;
  }
  .idle-state .content { max-width: 500px; }
  .idle-state .icon { font-size: 4rem; margin-bottom: 1rem; animation: float 3s ease-in-out infinite; filter: drop-shadow(0 0 20px rgba(6,182,212,0.4)); }
  @keyframes float {
    0%, 100% { transform: translateY(0) rotate(0deg); }
    50% { transform: translateY(-15px) rotate(5deg); }
  }
  .idle-state h3 {
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 0.5rem;
  }
  .idle-state p {
    color: var(--text3);
    font-size: 0.9rem;
    line-height: 1.6;
  }

  /* ── Footer ── */
  .footer {
    text-align: center;
    padding: 3rem 1.5rem;
    color: var(--text3);
    font-size: 0.75rem;
    border-top: 1px solid var(--border);
    position: relative;
    z-index: 1;
    background: rgba(3,7,18,0.5);
    backdrop-filter: blur(10px);
  }
  .footer .brand {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text2);
    margin-bottom: 0.5rem;
  }
  .footer .credit {
    margin-top: 0.5rem;
    color: var(--text3);
  }
  .footer .credit strong {
    color: var(--accent);
    font-weight: 600;
  }

  /* ── Responsive ── */
  @media (max-width: 640px) {
    .hero { padding: 3rem 1rem 2rem; }
    .hero h2 { font-size: 1.8rem; }
    .hero p { font-size: 0.95rem; }
    .hero-stats { gap: 1rem; }
    .hero-stat { min-width: 80px; padding: 0.75rem; }
    .hero-stat .num { font-size: 1.4rem; }
    .main { padding: 1rem; }
    .card { padding: 1.25rem; border-radius: 12px; }
    .header-brand h1 { font-size: 0.95rem; }
  }

  /* ── Scroll fade-in ── */
  .fade-in {
    opacity: 0;
    transform: translateY(20px);
    animation: fadeInUp 0.6s ease forwards;
  }
  @keyframes fadeInUp {
    to { opacity: 1; transform: translateY(0); }
  }
  .fade-in:nth-child(2) { animation-delay: 0.1s; }
  .fade-in:nth-child(3) { animation-delay: 0.2s; }
  .fade-in:nth-child(4) { animation-delay: 0.3s; }
</style>
</head>
<body>

<!-- Animated particles -->
<div id="particles"></div>
<div class="grid-bg"></div>

<!-- Header -->
<div class="header">
  <div class="header-inner">
    <div class="header-brand">
      <span class="logo">🔬</span>
      <h1>Chemical Data Extractor</h1>
      <span class="version">v3.2.2</span>
    </div>
    <span class="badge ok" id="health-badge">● Online</span>
  </div>
</div>

<!-- Hero -->
<div class="hero">
  <div class="molecules">
    <span class="molecule">⚗️</span>
    <span class="molecule">🧬</span>
    <span class="molecule">💊</span>
    <span class="molecule">🔬</span>
    <span class="molecule">⚛️</span>
    <span class="molecule">🧪</span>
  </div>
  <h2><span>Chemical Data Extractor</span></h2>
  <p>Multi-hop automated discovery engine. Extract chemical compounds, bioactivities, protein targets, and biological pathways from 19+ scientific databases.</p>
  <div class="hero-stats">
    <div class="hero-stat fade-in"><div class="num">19+</div><div class="label">Databases</div></div>
    <div class="hero-stat fade-in"><div class="num">4</div><div class="label">Hop Depth</div></div>
    <div class="hero-stat fade-in"><div class="num">7+</div><div class="label">Export Formats</div></div>
    <div class="hero-stat fade-in"><div class="num">15</div><div class="label">Export Files</div></div>
  </div>
</div>

<!-- Main Content -->
<div class="main">
  <!-- Left: Search Form -->
  <div class="card fade-in">
    <h3>Search Knowledge Graph</h3>

    <!-- How it works -->
    <div class="info-box">
      <div class="label">How it works</div>
      <div class="step"><span class="step-num">1.</span><span>Enter a <strong>protein</strong> (e.g. "tubulin", "EGFR") or <strong>compound</strong> (e.g. "Aspirin", "Ibuprofen")</span></div>
      <div class="step"><span class="step-num">2.</span><span>Choose <strong>Auto</strong> to detect, or pick <strong>Protein/Ligand</strong> manually</span></div>
      <div class="step"><span class="step-num">3.</span><span>Select <strong>hops</strong> — how many connection steps to explore</span></div>
      <div class="step"><span class="step-num">4.</span><span>Click <strong>Run Search</strong> and watch the extraction in real-time</span></div>
    </div>

    <!-- What are Hops? -->
    <div class="info-box">
      <div class="label">What are Hops?</div>
      <div class="hop-row"><span class="hop-tag hop-1">1-hop</span><span>Direct connections (e.g., Aspirin → COX-1 enzyme)</span></div>
      <div class="hop-row"><span class="hop-tag hop-2">2-hop</span><span>Follow one more step (e.g., Aspirin → COX-1 → Prostaglandin pathway)</span></div>
      <div class="hop-row"><span class="hop-tag hop-3">3-hop</span><span>Deeper network (e.g., ... → Related diseases)</span></div>
      <div class="hop-row"><span class="hop-tag hop-4">4-hop</span><span>Maximum depth — comprehensive graph (slower)</span></div>
    </div>

    <div class="field">
      <label>Query</label>
      <input id="query" type="text" placeholder='e.g. "Aspirin", "Tubulin", "EGFR", "P23219"' autofocus>
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
    <span id="elapsed" style="display:block;text-align:center;font-size:0.8rem;color:var(--text3);margin-top:0.5rem;font-family:'JetBrains Mono',monospace;"></span>
    <div class="error-msg" id="error-msg"></div>
  </div>

  <!-- Right: Progress + Results + Idle -->
  <div>
    <div class="card progress-section fade-in" id="progress-section">
      <div class="progress-header">
        <h3>Progress</h3>
        <span class="status-pill status-running" id="status-pill">⏳ Queued</span>
      </div>
      <div class="progress-bar"><div class="fill"></div></div>
      <div class="progress-text" id="progress-text">⏳ Queued…</div>
      <details open>
        <summary style="font-size:0.78rem;color:var(--text3);cursor:pointer;margin-bottom:0.5rem;font-weight:500;">Live Log</summary>
        <div class="log-box" id="log-box"></div>
      </details>
    </div>

    <div class="card results-section fade-in" id="results-section">
      <h3>Export Files</h3>
      <div class="exports-grid" id="exports-grid"></div>
    </div>

    <!-- Idle state with DB cards -->
    <div id="idle-section">
      <div class="card fade-in">
        <div class="idle-state">
          <div class="content">
            <div class="icon">🔬</div>
            <h3>Enter a query to start searching</h3>
            <p>Extract chemical compounds, bioactivities, protein targets, 3D structures, and biological pathways from 19+ scientific databases.</p>
          </div>
        </div>
      </div>
      <div class="db-cards">
        <div class="db-card fade-in">
          <div class="icon">🧬</div>
          <h4>Proteins</h4>
          <p>UniProt, PDB, AlphaFold, STRING</p>
        </div>
        <div class="db-card fade-in">
          <div class="icon">💊</div>
          <h4>Compounds</h4>
          <p>PubChem, ChEMBL, ChEBI, BindingDB</p>
        </div>
        <div class="db-card fade-in">
          <div class="icon">🔗</div>
          <h4>Pathways</h4>
          <p>KEGG, Reactome, Gene Ontology</p>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Footer -->
<div class="footer">
  <div class="brand">Chemical Data Extractor</div>
  <div>19 database connectors · Multi-hop graph traversal · Enrichment pipeline</div>
  <div class="credit">Developed with ❤️ by <strong>Sumanta</strong></div>
</div>

<script>
// ── Particle system ──
(function() {
  const c = document.getElementById('particles');
  const colors = ['#06b6d4','#8b5cf6','#10b981','#f59e0b'];
  for (let i = 0; i < 30; i++) {
    const p = document.createElement('div');
    p.className = 'particle';
    const size = Math.random() * 8 + 4;
    p.style.cssText = `width:${size}px;height:${size}px;left:${Math.random()*100}%;animation-duration:${Math.random()*15+10}s;animation-delay:${Math.random()*10}s;background:${colors[Math.floor(Math.random()*colors.length)]}`;
    c.appendChild(p);
  }
})();

// ── JS Logic ──
let pollTimer = null, elapsedTimer = null, startTime = 0, healthRetries = 0, queryType = 'auto', hops = 1;

document.querySelectorAll('.type-btns button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.type-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    queryType = btn.dataset.type;
  });
});
document.querySelectorAll('.hop-btns button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.hop-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    hops = parseInt(btn.dataset.hops);
  });
});

function checkHealth() {
  const badge = document.getElementById('health-badge');
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 60000);
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
      if (healthRetries < 12) {
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
    const tid = setTimeout(() => ctrl.abort(), 180000);
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, query_type: queryType, hops: hops}),
      signal: ctrl.signal
    });
    clearTimeout(tid);
    if (!res.ok) throw new Error(await res.text() || 'HTTP ' + res.status);
    const data = await res.json();
    startTime = Date.now();
    document.getElementById('progress-section').style.display = 'block';
    document.getElementById('results-section').style.display = 'none';
    document.getElementById('idle-section').style.display = 'none';
    document.getElementById('exports-grid').innerHTML = '';
    document.getElementById('log-box').textContent = '';
    updateUI(data);
    elapsedTimer = setInterval(() => {
      document.getElementById('elapsed').textContent = ((Date.now() - startTime) / 1000).toFixed(1) + 's';
    }, 200);
    pollTimer = setInterval(() => pollSearch(data.search_id), 1500);
  } catch (err) {
    console.error('Search error:', err);
    if (err.name === 'AbortError' && retries < 3) {
      const waitTime = [8000, 10000, 12000][retries];
      showError('⏳ Server cold start — retrying in ' + (waitTime/1000) + 's... (attempt ' + (retries+1) + '/3)');
      setTimeout(() => startSearch(retries + 1), waitTime);
      return;
    }
    showError(err.name === 'AbortError'
      ? 'Service is warming up (cold start). Please wait 60s and try again.'
      : 'Search failed: ' + err.message);
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
      if (data.status === 'completed') {
        if (data.export_files?.length > 0) {
          showResults(data);
        } else {
          setTimeout(() => pollSearch(id), 2000);
        }
      }
      if (data.status === 'failed') showError(data.error || 'Search failed.');
    }
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('Poll error:', e);
  }
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
  document.getElementById('results-section').style.display = 'block';
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
    print(f"🔬 SciGraph API v3.2.0 starting...")
    print(f"   Python: {sys.version}")
    print(f"   Engine dir: {ENGINE_DIR}")
    print(f"   Exports dir: {EXPORTS_DIR}")
    print(f"   Workspace dir: {WORKSPACE_DIR}")
