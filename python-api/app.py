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
    # Server-side health check — embed status directly in HTML
    # so the browser never needs a separate fetch (avoids Cloudflare/browser issues)
    health_ok = False
    try:
        import urllib.request as _req
        port = os.environ.get('PORT', '8000')
        with _req.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as r:
            health_ok = r.status == 200
    except Exception:
        try:
            with _req.urlopen('http://127.0.0.1:8000/api/health', timeout=2) as r:
                health_ok = r.status == 200
        except Exception:
            pass

    badge_cls = 'badge ok' if health_ok else 'badge'
    badge_txt = '● Healthy' if health_ok else '● Starting…'
    html = (
        LANDING_PAGE_HTML
        .replace('class="badge waking" id="health-badge"',
                  f'class="{badge_cls}" id="health-badge"')
        .replace('>Connecting…</span>', f'>{badge_txt}</span>')
        .replace('>⏳ Connecting…</span>', f'>{badge_txt}</span>')
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
<style>
  :root {
    --bg: #0a0e1a;
    --surface: #111827;
    --surface2: #1a2234;
    --border: #1e2d4a;
    --accent: #22d3ee;
    --accent2: #06b6d4;
    --accent3: #8b5cf6;
    --text: #e2e8f0;
    --text2: #94a3b8;
    --success: #34d399;
    --warn: #fbbf24;
    --error: #f87171;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.6;
  }
  .hero {
    text-align: center;
    padding: 3rem 1.5rem 2rem;
    background: linear-gradient(135deg, #0a0e1a 0%, #111827 50%, #0f172a 100%);
    border-bottom: 1px solid var(--border);
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: radial-gradient(circle at 30% 50%, rgba(34,211,238,0.05) 0%, transparent 50%),
                radial-gradient(circle at 70% 50%, rgba(139,92,246,0.05) 0%, transparent 50%);
    pointer-events: none;
  }
  .hero h1 {
    font-size: 2.2rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--accent3));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 0.5rem;
    position: relative;
  }
  .hero p {
    color: var(--text2);
    font-size: 1rem;
    max-width: 600px;
    margin: 0 auto;
    position: relative;
  }
  .hero .badges {
    display: flex;
    gap: 0.5rem;
    justify-content: center;
    margin-top: 1rem;
    flex-wrap: wrap;
    position: relative;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.25rem 0.65rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 500;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text2);
  }
  .badge.ok { border-color: rgba(34,211,238,0.3); color: var(--accent); }
  .badge.waking { border-color: rgba(251,191,36,0.3); color: var(--warn); }
  .container {
    max-width: 820px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.5rem;
  }
  .card h2 {
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text);
  }
  .form-row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 0.75rem;
    align-items: end;
  }
  @media (max-width: 600px) {
    .form-row { grid-template-columns: 1fr; }
  }
  label {
    display: block;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--text2);
    margin-bottom: 0.35rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  input, select {
    width: 100%;
    padding: 0.65rem 0.85rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus, select:focus { border-color: var(--accent); }
  select { cursor: pointer; min-width: 120px; }
  button {
    padding: 0.65rem 1.5rem;
    border-radius: 8px;
    border: none;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent2), var(--accent));
    color: #0a0e1a;
  }
  .btn-primary:hover { filter: brightness(1.1); transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary {
    background: var(--surface2);
    color: var(--text2);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover { border-color: var(--accent); color: var(--text); }
  .error-msg {
    margin-top: 0.75rem;
    padding: 0.65rem 0.85rem;
    border-radius: 8px;
    background: rgba(248,113,113,0.1);
    border: 1px solid rgba(248,113,113,0.3);
    color: var(--error);
    font-size: 0.85rem;
    display: none;
  }

  /* Progress */
  #progress-section { display: none; }
  .progress-bar {
    height: 3px;
    background: var(--surface2);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 1rem;
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
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.3rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .status-running { background: rgba(34,211,238,0.15); color: var(--accent); }
  .status-completed { background: rgba(52,211,153,0.15); color: var(--success); }
  .status-failed { background: rgba(248,113,113,0.15); color: var(--error); }
  .status-queued { background: rgba(251,191,36,0.15); color: var(--warn); }

  .log-box {
    background: #0d1117;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 0.8rem;
    line-height: 1.5;
    color: var(--text2);
    max-height: 350px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    margin-top: 0.75rem;
  }
  .log-box::-webkit-scrollbar { width: 6px; }
  .log-box::-webkit-scrollbar-track { background: transparent; }
  .log-box::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* Results */
  #results-section { display: none; }
  .exports-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.5rem;
    margin-top: 0.75rem;
  }
  .export-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    transition: border-color 0.2s;
  }
  .export-card:hover { border-color: var(--accent); }
  .export-card .name {
    font-size: 0.85rem;
    font-weight: 500;
    color: var(--text);
    word-break: break-all;
  }
  .export-card .size { font-size: 0.75rem; color: var(--text2); }
  .export-card a {
    display: inline-block;
    margin-top: 0.35rem;
    font-size: 0.8rem;
    color: var(--accent);
    text-decoration: none;
  }
  .export-card a:hover { text-decoration: underline; }

  .footer {
    text-align: center;
    padding: 2rem 1rem;
    color: var(--text2);
    font-size: 0.8rem;
    border-top: 1px solid var(--border);
    margin-top: 2rem;
  }
</style>
</head>
<body>

<div class="hero">
  <h1>🔬 SciGraph</h1>
  <p>Multi-hop automated scientific discovery engine. Search proteins, compounds, and pathways across 19 databases.</p>
  <div class="badges">
    <span class="badge waking" id="health-badge" title="First visit may take 30-60s (free tier cold start)">⏳ Connecting…</span>
    <span class="badge">19 databases</span>
    <span class="badge">v3.1.0</span>
  </div>
</div>

<div class="container">
  <div class="card">
    <h2>Search Knowledge Graph</h2>
    <div class="form-row">
      <div>
        <label for="query">Query</label>
        <input id="query" type="text" placeholder="e.g. Aspirin, Tubulin, P23219…" autofocus>
      </div>
      <div>
        <label for="qtype">Type</label>
        <select id="qtype">
          <option value="auto">Auto</option>
          <option value="protein">Protein</option>
          <option value="ligand">Ligand</option>
        </select>
      </div>
      <div>
        <label for="hops">Hops</label>
        <select id="hops">
          <option value="1" selected>1</option>
          <option value="2">2</option>
          <option value="3">3</option>
        </select>
      </div>
    </div>
    <div style="margin-top:1rem;display:flex;gap:0.5rem;align-items:center;">
      <button class="btn-primary" id="search-btn" onclick="startSearch()">Search</button>
      <span id="elapsed" style="font-size:0.85rem;color:var(--text2);"></span>
    </div>
    <div class="error-msg" id="error-msg"></div>
  </div>

  <div class="card" id="progress-section">
    <h2 style="display:flex;align-items:center;gap:0.5rem;">
      Progress
      <span class="status-pill status-running" id="status-pill">⏳ Queued</span>
    </h2>
    <div class="progress-bar"><div class="fill"></div></div>
    <div style="font-size:0.9rem;color:var(--text);margin-bottom:0.5rem;" id="progress-text">⏳ Queued…</div>
    <details open>
      <summary style="font-size:0.85rem;color:var(--text2);cursor:pointer;margin-bottom:0.25rem;">Live log</summary>
      <div class="log-box" id="log-box"></div>
    </details>
  </div>

  <div class="card" id="results-section">
    <h2 style="display:flex;align-items:center;gap:0.5rem;">📦 Export Files</h2>
    <div class="exports-grid" id="exports-grid"></div>
  </div>
</div>

<div class="footer">
  Powered by <strong>SciGraph v3.1</strong> — 19 database connectors · Multi-hop graph traversal · Enrichment pipeline
</div>

<script>
let pollTimer = null;
let elapsedTimer = null;
let startTime = 0;
let healthRetries = 0;

function checkHealth() {
  const badge = document.getElementById('health-badge');
  const ctrl = new AbortController();
  const timeoutId = setTimeout(() => ctrl.abort(), 30000);
  fetch('/api/health', {signal: ctrl.signal}).then(r=>{
    clearTimeout(timeoutId);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(d=>{
    badge.className = 'badge ok';
    badge.textContent = '● Healthy';
    badge.title = 'Engine is online';
  }).catch(()=>{
    clearTimeout(timeoutId);
    healthRetries++;
    if (healthRetries < 10) {
      badge.className = 'badge waking';
      const elapsed = Math.round(healthRetries * 5);
      badge.textContent = '⏳ Warming up… (' + elapsed + 's)';
      badge.title = 'Free-tier cold start — typically takes 30-60s';
      setTimeout(checkHealth, 5000);
    } else {
      badge.className = 'badge';
      badge.textContent = '● Unreachable';
    }
  });
}
checkHealth();

function showError(msg) {
  const el = document.getElementById('error-msg');
  el.textContent = msg;
  el.style.display = 'block';
}
function hideError() {
  document.getElementById('error-msg').style.display = 'none';
}

// Enter key triggers search
document.getElementById('query').addEventListener('keydown', e=>{ if(e.key==='Enter') startSearch(); });

async function startSearch() {
  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('query').focus(); return; }
  const qtype = document.getElementById('qtype').value;
  const hops = parseInt(document.getElementById('hops').value);

  hideError();
  const btn = document.getElementById('search-btn');
  btn.disabled = true;
  btn.textContent = 'Starting…';

  try {
    const ctrl = new AbortController();
    const timeoutId = setTimeout(() => ctrl.abort(), 90000);
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, query_type: qtype, hops}),
      signal: ctrl.signal
    });
    clearTimeout(timeoutId);
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(txt || 'Server returned ' + res.status);
    }
    const data = await res.json();

    startTime = Date.now();
    document.getElementById('progress-section').style.display = '';
    document.getElementById('results-section').style.display = 'none';
    document.getElementById('exports-grid').innerHTML = '';
    document.getElementById('log-box').textContent = '';
    updateUI(data);

    elapsedTimer = setInterval(updateElapsed, 200);
    pollTimer = setInterval(()=>pollSearch(data.search_id), 1500);
  } catch(err) {
    let msg = err.name === 'AbortError'
      ? 'Service is warming up from idle. Please wait ~30s and try again.'
      : 'Search failed: ' + err.message;
    showError(msg);
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}

async function pollSearch(id) {
  try {
    const ctrl = new AbortController();
    const timeoutId = setTimeout(() => ctrl.abort(), 15000);
    const res = await fetch('/api/search/' + id, {signal: ctrl.signal});
    clearTimeout(timeoutId);
    const data = await res.json();
    updateUI(data);
    if (data.status === 'completed' || data.status === 'failed') {
      clearInterval(pollTimer);
      clearInterval(elapsedTimer);
      document.getElementById('search-btn').disabled = false;
      document.getElementById('search-btn').textContent = 'Search';
      if (data.status === 'completed' && data.export_files && data.export_files.length > 0) {
        showResults(data);
      }
      if (data.status === 'failed') {
        showError(data.error || 'Search failed. Check the log above for details.');
      }
    }
  } catch(e) { /* retry on next tick */ }
}

function updateUI(data) {
  const pill = document.getElementById('status-pill');
  const statusMap = {
    queued: ['⏳ Queued', 'status-queued'],
    running: ['⚡ Running', 'status-running'],
    completed: ['✅ Completed', 'status-completed'],
    failed: ['❌ Failed', 'status-failed']
  };
  const [label, cls] = statusMap[data.status] || ['?',''];
  pill.textContent = label;
  pill.className = 'status-pill ' + cls;

  document.getElementById('progress-text').textContent = data.progress || '';

  if (data.log && data.log.length > 0) {
    const box = document.getElementById('log-box');
    box.textContent = data.log.join('\n');
    box.scrollTop = box.scrollHeight;
  }
}

function showResults(data) {
  document.getElementById('results-section').style.display = '';
  const grid = document.getElementById('exports-grid');
  grid.innerHTML = '';
  for (const f of data.export_files) {
    const card = document.createElement('div');
    card.className = 'export-card';
    const dlUrl = '/api/exports/' + encodeURIComponent(f.name) + '?search_id=' + data.search_id;
    card.innerHTML = '<div class="name">📄 ' + f.name + '</div>'
      + '<div class="size">' + f.size_display + '</div>'
      + '<a href="' + dlUrl + '" target="_blank">Download →</a>';
    grid.appendChild(card);
  }
}

function updateElapsed() {
  const sec = ((Date.now() - startTime) / 1000).toFixed(1);
  document.getElementById('elapsed').textContent = sec + 's';
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
