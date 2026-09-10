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
import logging
import os
import re
import shutil
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

# Persistent runtime logger (stderr/stdout outside poll logs)
LOG = logging.getLogger("scigraph.runtime")
LOG.setLevel(logging.DEBUG)
if not LOG.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(h)

# ── Persistent search state (SQLite) ─────────────────────────────────────────
import sqlite3
import json as _json

SEARCH_DB = Path(os.environ.get("SEARCH_DB", "/tmp/scigraph_searches.db"))

def _init_db():
    with sqlite3.connect(str(SEARCH_DB)) as conn:
        # Enable WAL mode for concurrent read/write without blocking
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS searches (
                search_id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                query_type TEXT DEFAULT 'auto',
                hops INTEGER DEFAULT 1,
                status TEXT DEFAULT 'queued',
                progress TEXT DEFAULT '',
                log TEXT DEFAULT '[]',
                export_files TEXT DEFAULT '[]',
                export_dir TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                elapsed_seconds REAL,
                error TEXT
            )
        """)
    # In-memory cache for active searches (fast access during polling)
searches: dict[str, dict] = {}

# Load any completed searches from DB on startup
try:
    with sqlite3.connect(str(SEARCH_DB)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM searches WHERE status IN ('completed','failed') ORDER BY created_at DESC LIMIT 50"):
            sid = row["search_id"]
            searches[sid] = {
                "search_id": sid, "query": row["query"], "query_type": row["query_type"],
                "hops": row["hops"], "status": row["status"], "progress": row["progress"],
                "log": _json.loads(row["log"]), "export_files": _json.loads(row["export_files"]),
                "export_dir": row["export_dir"], "created_at": row["created_at"],
                "elapsed_seconds": row["elapsed_seconds"], "error": row["error"],
            }
except Exception:
    pass

def _save_search(state: dict):
    """Persist search state to SQLite."""
    try:
        with sqlite3.connect(str(SEARCH_DB), timeout=10) as conn:
            conn.execute("""
                INSERT INTO searches (search_id, query, query_type, hops, status, progress,
                    log, export_files, export_dir, created_at, elapsed_seconds, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(search_id) DO UPDATE SET
                    status=excluded.status, progress=excluded.progress, log=excluded.log,
                    export_files=excluded.export_files, elapsed_seconds=excluded.elapsed_seconds,
                    error=excluded.error
            """, (
                state["search_id"], state["query"], state.get("query_type","auto"),
                state.get("hops",1), state["status"], state.get("progress",""),
                _json.dumps(state.get("log",[])), _json.dumps(state.get("export_files",[])),
                state.get("export_dir",""), state.get("created_at",""),
                state.get("elapsed_seconds"), state.get("error"),
            ))
    except Exception as e:
        LOG.warning("Failed to save search %s: %s", state.get("search_id","?"), e)

_init_db()

# Flag to guard first-request cleanup (defined at module level so root() never gets NameError)
_cleanup_done: bool = False

# Shutdown event for the periodic cleanup coroutine
_shutdown = asyncio.Event()


# ── Models ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    query_type: str = "auto"
    hops: int = 1
    export_dir: Optional[str] = None


def _clean_status(d: dict) -> dict:
    """Strip empty/None fields and convert truncated log strings to empty lists."""
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if k == "log" and isinstance(v, str):
            out[k] = []
        elif k == "export_files" and (not v or (isinstance(v, list) and not v)):
            continue
        else:
            out[k] = v
    return out


class SearchStatus(BaseModel):
    """Public search status. Empty fields are omitted on the wire."""
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
    state = searches.get(search_id)
    if state is None:
        LOG.error("Background runner: no in-memory state for search_id=%s", search_id)
        return
    state["status"] = "running"
    state["log"] = []
    start_time = time.time()
    _save_search(state)

    # Build CLI command
    cmd = [
        sys.executable,
        str(ENGINE_DIR / "scigraph.py"),
        query,
        "--query-type", query_type,
        "--hops", str(hops),
        "--workspace", str(WORKSPACE_DIR),
        "--export-dir", export_dir,
    ]
    LOG.info("Launching scigraph search search_id=%s query=%r hops=%s", search_id, query, hops)

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
        rc = process.returncode

        if rc == 0:
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
                LOG.warning("Enrichment step failed for search_id=%s: %s", search_id, enrich_err, exc_info=True)
                state["log"].append(f"  Enrichment step error: {enrich_err}")

            # --- Finalize ---
            state["status"] = "completed"
            state["elapsed_seconds"] = time.time() - start_time
            state["export_files"] = _list_export_files(export_dir)
            enriched_exists = any(f["name"] == "enriched_data.xlsx" for f in state["export_files"])
            if enriched_exists:
                state["progress"] = f"✅ Completed in {state['elapsed_seconds']:.1f}s + enriched multi-sheet Excel"
            else:
                state["progress"] = f"✅ Completed in {state['elapsed_seconds']:.1f}s"
            _save_search(state)
        else:
            state["status"] = "failed"
            state["error"] = f"Process exited with code {rc}"
            state["elapsed_seconds"] = time.time() - start_time
            _save_search(state)

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["elapsed_seconds"] = time.time() - start_time
        _save_search(state)


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
    """List files in the export directory. Directory missing means nothing to list."""
    files: list[dict] = []
    path = Path(export_dir)
    if not path.exists():
        return files
    for f in sorted(path.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            try:
                st = f.stat()
            except OSError:
                continue
            files.append({
                "name": f.name,
                "size_bytes": st.st_size,
                "size_display": _format_size(st.st_size),
                "url": f"/api/exports/{f.name}",
            })
    return files


def _format_size(size: int) -> str:
    if size < 0:
        return "0 B"
    for unit in ["B", "KB", "MB"]:
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "scigraph-api",
        "version": "3.2.2",
        "exports_dir": str(EXPORTS_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "search_db": str(SEARCH_DB),
    }


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
        "progress": "Queued...",
        "log": [],
        "export_files": [],
        "export_dir": export_dir,
        "created_at": datetime.utcnow().isoformat(),
        "elapsed_seconds": None,
        "error": None,
    }
    searches[search_id] = state
    _save_search(state)

    background_tasks.add_task(
        run_search_in_background,
        search_id, request.query, request.query_type, request.hops, export_dir
    )

    # Brief pause so the reader sees "queued" rather than an immediate start.
    try:
        await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass

    return SearchStatus(**_clean_status({
        k: v for k, v in state.items() if k != "export_dir"
    }))


@app.get("/api/search/{search_id}", response_model=SearchStatus)
async def get_search_status(search_id: str):
    """Get the status of a search."""
    state = searches.get(search_id)
    if not state:
        raise HTTPException(status_code=404, detail="Search not found")

    if state.get("status") == "completed" and not state.get("export_files"):
        state["export_files"] = _list_export_files(state.get("export_dir", ""))
        _save_search(state)

    # Purge cached state that is no longer useful.
    if state.get("status") in ("completed", "failed") and not state.get("export_files"):
        searches.pop(search_id, None)

    return SearchStatus(**_clean_status({
        k: v for k, v in state.items() if k != "export_dir"
    }))


@app.get("/api/search/{search_id}/log")
async def get_search_log(search_id: str, offset: int = Query(0, ge=0)):
    """Get incremental log output from a search."""
    state = searches.get(search_id)
    if not state:
        raise HTTPException(status_code=404, detail="Search not found")
    log = state.get("log")
    if isinstance(log, str):
        log = []
    elif not isinstance(log, list):
        log = []
    return {
        "search_id": search_id,
        "status": state["status"],
        "offset": offset,
        "total_lines": len(log),
        "new_lines": log[offset:],
    }


@app.get("/api/search/{search_id}/graph")
async def get_search_graph(search_id: str):
    """Return nodes and edges as Cytoscape.js-compatible JSON for interactive graph visualization."""
    import csv as _csv
    state = searches.get(search_id)
    if not state:
        raise HTTPException(status_code=404, detail="Search not found")

    export_dir = Path(state.get("export_dir", ""))
    nodes_file = export_dir / "nodes.csv"
    edges_file = export_dir / "edges.csv"

    if not nodes_file.exists() or not edges_file.exists():
        raise HTTPException(status_code=404, detail="Graph data not available yet")

    COLOR_MAP = {
        "COMPOUND": "#3b82f6", "LIGAND": "#3b82f6",  # Blue
        "PROTEIN": "#ef4444", "TARGET": "#ef4444",    # Red
        "STRUCTURE": "#22c55e", "PDB": "#22c55e",      # Green
        "ENZYME": "#f97316",                            # Orange
        "GENE": "#a855f7", "PATHWAY": "#eab308",       # Purple, Yellow
    }

    nodes: list[dict] = []
    try:
        with open(nodes_file, "r", encoding="utf-8", newline="") as f:
            for row in _csv.DictReader(f):
                uid = row.get("uid:ID", "").strip()
                if not uid:
                    continue
                label = row.get(":LABEL", "Other")
                name = row.get("name", uid)
                color = COLOR_MAP.get(label.upper(), "#6b7280")
                nodes.append({
                    "data": {
                        "id": uid,
                        "label": name[:30],
                        "type": label,
                        "color": color,
                        "smiles": row.get("smiles", ""),
                        "formula": row.get("formula", ""),
                    }
                })
    except OSError as e:
        LOG.warning("Failed to read graph nodes for search_id=%s: %s", search_id, e)
        raise HTTPException(status_code=500, detail="Graph node read error")

    edges: list[dict] = []
    try:
        with open(edges_file, "r", encoding="utf-8", newline="") as f:
            for row in _csv.DictReader(f):
                src = row.get(":START_ID", "").strip()
                tgt = row.get(":END_ID", "").strip()
                if not src or not tgt:
                    continue
                rel = row.get(":TYPE", "interacts")
                activity = row.get("activity_type", "")
                value = row.get("activity_value", "")
                edge_label = f"{activity}={value}" if value else rel
                edges.append({
                    "data": {
                        "source": src,
                        "target": tgt,
                        "label": edge_label[:40],
                        "relation": rel,
                    }
                })
    except OSError as e:
        LOG.warning("Failed to read graph edges for search_id=%s: %s", search_id, e)
        raise HTTPException(status_code=500, detail="Graph edge read error")

    return {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


@app.get("/api/exports/{filename:path}")
async def download_export(filename: str, search_id: Optional[str] = Query(None)):
    """Download an export file. Optionally specify a search_id to find the right directory."""
    filename = filename.strip()
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe = Path(filename)
    if safe.parts != (filename,):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path: Optional[Path] = None
    if search_id:
        state = searches.get(search_id)
        if state:
            file_path = Path(state.get("export_dir", "")) / filename
    else:
        for s in searches.values():
            candidate = Path(s.get("export_dir", "")) / filename
            if candidate.exists() and candidate.is_file():
                file_path = candidate
                break
    if file_path is None:
        candidate = EXPORTS_DIR / filename
        if candidate.exists() and candidate.is_file():
            file_path = candidate
    if file_path is None or not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=_guess_mime(filename),
    )


@app.get("/api/searches")
async def list_searches(limit: int = Query(20, ge=1, le=100)):
    """List recent searches."""
    rows = sorted(
        searches.values(),
        key=lambda s: s.get("created_at", "") or "",
        reverse=True,
    )
    return [
        {
            "search_id": s["search_id"],
            "query": s["query"],
            "status": s["status"],
            "progress": s["progress"],
            "created_at": s["created_at"],
            "elapsed_seconds": s["elapsed_seconds"],
            "file_count": len(s.get("export_files", [])) if isinstance(s.get("export_files"), list) else 0,
        }
        for s in rows[:limit]
    ]


@app.get("/")
async def root():
    # If the server can serve this page, it IS healthy.
    # No self-check needed — avoids Render port issues.
    global _cleanup_done
    if not _cleanup_done:
        _run_storage_cleanup()
        _cleanup_done = True
        LOG.info("First-request cleanup complete")

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
  .hero h2 {
    font-size: clamp(1.75rem, 4vw, 2.5rem);
    font-weight: 600;
    color: var(--text);
    margin-bottom: 0.875rem;
    position: relative;
    letter-spacing: -0.02em;
    line-height: 1.2;
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

  .hero-stat .num {
    font-size: 1.6rem;
    font-weight: 600;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
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


  /* ── Example chips ── */
  .examples {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 1.25rem;
  }
  .examples-label {
    font-size: 0.72rem;
    color: var(--text3);
    font-weight: 500;
  }
  .example-chip {
    padding: 0.4rem 0.8rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text2);
    font-size: 0.78rem;
    font-family: 'JetBrains Mono', monospace;
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease;
  }
  .example-chip:hover {
    border-color: var(--accent);
    color: var(--accent);
  }
  .exports-empty {
    padding: 2rem 1rem;
    text-align: center;
    color: var(--text3);
    font-size: 0.85rem;
  }

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
    background: var(--accent2);
    color: #fff;
    transition: all 0.3s ease;
    margin-top: 0.75rem;
    position: relative;
    overflow: hidden;
    letter-spacing: 0.01em;
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
      <h1>Chemical Data Extractor</h1>
    </div>
    <span class="badge ok" id="health-badge">● Online</span>
  </div>
</div>

<!-- Hero -->
<div class="hero">
  <h2>Chemical Data Extractor</h2>
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

    <div class="examples">
      <span class="examples-label">Try:</span>
      <button class="example-chip" data-q="Aspirin">Aspirin</button>
      <button class="example-chip" data-q="EGFR">EGFR</button>
      <button class="example-chip" data-q="Tubulin">Tubulin</button>
      <button class="example-chip" data-q="P23219">P23219</button>
    </div>

    <div class="field">
      <label>Query</label>
      <input id="query" type="text" placeholder='e.g. "Aspirin", "Tubulin", "EGFR", "P23219"' autofocus>
    </div>
    <div class="field">
      <label>Query Type</label>
      <div class="type-btns">
        <button class="active" data-type="auto">Auto</button>
        <button data-type="protein">Protein</button>
        <button data-type="ligand">Ligand</button>
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
    <button class="search-btn" id="search-btn" onclick="startSearch()">Run Search</button>
    <span id="elapsed" style="display:block;text-align:center;font-size:0.8rem;color:var(--text3);margin-top:0.5rem;font-family:'JetBrains Mono',monospace;"></span>
    <div class="error-msg" id="error-msg"></div>
  </div>

  <!-- Right: Progress + Results + Idle -->
  <div>
    <div class="card progress-section fade-in" id="progress-section">
      <div class="progress-header">
        <h3>Progress</h3>
        <span class="status-pill status-queued" id="status-pill">Queued</span>
      </div>
      <div class="progress-bar"><div class="fill"></div></div>
      <div class="progress-text" id="progress-text">Queued...</div>
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
            
            <h3>Enter a query to start searching</h3>
            <p>Extract chemical compounds, bioactivities, protein targets, 3D structures, and biological pathways from 19+ scientific databases.</p>
          </div>
        </div>
      </div>
      <div class="db-cards">
        <div class="db-card fade-in">
          
          <h4>Proteins</h4>
          <p>UniProt, PDB, AlphaFold, STRING</p>
        </div>
        <div class="db-card fade-in">
          
          <h4>Compounds</h4>
          <p>PubChem, ChEMBL, ChEBI, BindingDB</p>
        </div>
        <div class="db-card fade-in">
          
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
  <div class="credit">Built by Sumanta</div>
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

document.querySelectorAll('.example-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const q = document.getElementById('query');
    q.value = chip.dataset.q;
    q.focus();
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
    badge.textContent = 'Online';
  }).catch(() => {
    clearTimeout(tid);
    healthRetries++;
    if (healthRetries < 12) {
      badge.className = 'badge waking';
      badge.textContent = 'Waking...';
      setTimeout(checkHealth, 5000);
    } else {
      badge.className = 'badge';
      badge.textContent = 'Offline';
    }
  });
}
checkHealth();

function showError(msg) { const e = document.getElementById('error-msg'); e.textContent = msg; e.style.display = 'block'; }
function hideError() { document.getElementById('error-msg').style.display = 'none'; }

document.getElementById('query').addEventListener('keydown', e => { if (e.key === 'Enter') startSearch(); });

async function startSearch(retries) {
  retries = retries || 0;
  if (pollTimer) return;  // guard: a search is already running
  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('query').focus(); return; }
  hideError();
  const btn = document.getElementById('search-btn');
  btn.disabled = true; btn.textContent = 'Starting...';
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
      showError('Server cold start — retrying in ' + (waitTime/1000) + 's... (attempt ' + (retries+1) + '/3)');
      setTimeout(() => startSearch(retries + 1), waitTime);
      return;
    }
    showError(err.name === 'AbortError'
      ? 'Service is warming up (cold start). Please wait 60s and try again.'
      : 'Search failed: ' + err.message);
    btn.disabled = false; btn.textContent = 'Run Search';
  }
}

async function pollSearch(id) {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 15000);
    const res = await fetch('/api/search/' + id, {signal: ctrl.signal});
    clearTimeout(tid);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    updateUI(data);
    if (data.status === 'completed' || data.status === 'failed') {
      clearInterval(pollTimer); clearInterval(elapsedTimer);
      pollTimer = null; elapsedTimer = null;
      document.getElementById('search-btn').disabled = false;
      document.getElementById('search-btn').textContent = 'Run Search';
      if (data.status === 'completed') {
        showResults(data);  // handles empty file lists gracefully
      }
      if (data.status === 'failed') showError(data.error || 'Search failed.');
    }
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('Poll error:', e);
  }
}

function updateUI(data) {
  const pill = document.getElementById('status-pill');
  const m = { queued: ['Queued', 'status-queued'], running: ['Running', 'status-running'], completed: ['Done', 'status-completed'], failed: ['Failed', 'status-failed'] };
  const [l, c] = m[data.status] || ['—', ''];
  pill.textContent = l; pill.className = 'status-pill ' + c;
  document.getElementById('progress-text').textContent = data.progress || '';
  if (Array.isArray(data.log) && data.log.length > 0) {
    const box = document.getElementById('log-box');
    const nl = String.fromCharCode(10);
    box.textContent = data.log.join(nl);
    box.scrollTop = box.scrollHeight;
  }
}

function showResults(data) {
  document.getElementById('idle-section').style.display = 'none';
  document.getElementById('progress-section').style.display = 'none';
  document.getElementById('results-section').style.display = 'block';
  const g = document.getElementById('exports-grid');
  g.innerHTML = '';
  if (!data.export_files || data.export_files.length === 0) {
    g.innerHTML = '<div class="exports-empty">No export files were generated this run. They may still be processing or were cleaned up to free space. Start a new search to try again.</div>';
    return;
  }
  for (const f of data.export_files) {
    const d = document.createElement('div');
    d.className = 'export-card';
    const u = '/api/exports/' + encodeURIComponent(f.name) + '?search_id=' + data.search_id;
    d.innerHTML = '<div class="name">' + f.name + '</div><div class="size">' + f.size_display + '</div><a href="' + u + '" target="_blank">Download →</a>';
    g.appendChild(d);
  }
}

// --- Session restore: resume active search or show latest results after refresh ---
async function restoreSession() {
  try {
    const list = await (await fetch('/api/searches?limit=10')).json();
    if (!Array.isArray(list) || list.length === 0) return;
    const active = list.find(s => s.status === 'queued' || s.status === 'running');
    const done = !active && list.find(s => s.status === 'completed');
    if (!active && !done) return;
    const data = await (await fetch('/api/search/' + (active ? active.search_id : done.search_id))).json();
    if (data.query) document.getElementById('query').value = data.query;
    if (active) {
      startTime = new Date(data.created_at).getTime() || Date.now();
      document.getElementById('progress-section').style.display = 'block';
      document.getElementById('idle-section').style.display = 'none';
      document.getElementById('results-section').style.display = 'none';
      updateUI(data);
      document.getElementById('search-btn').disabled = true;
      document.getElementById('search-btn').textContent = 'Running...';
      elapsedTimer = setInterval(() => {
        document.getElementById('elapsed').textContent = ((Date.now() - startTime) / 1000).toFixed(1) + 's';
      }, 200);
      pollTimer = setInterval(() => pollSearch(active.search_id), 1500);
    } else if (data.export_files && data.export_files.length > 0) {
      showResults(data);
    }
  } catch (e) { console.warn('Session restore skipped:', e); }
}
restoreSession();
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


# ── Storage Management ───────────────────────────────────────────────────────

MAX_STORAGE_MB = int(os.environ.get("MAX_STORAGE_MB", "500"))
MAX_LOG_LINES_COMPLETED = 100  # Keep only last 100 log lines for completed searches
EXPORT_MAX_AGE_MINUTES = 30  # Auto-delete exports older than 30 minutes
CLEANUP_INTERVAL_SECONDS = 1800  # Run cleanup every 30 minutes

def _cleanup_old_exports():
    """Delete export directories older than EXPORT_MAX_AGE_MINUTES."""
    import shutil
    cutoff = time.time() - (EXPORT_MAX_AGE_MINUTES * 60)
    cleaned = 0
    for export_dir in EXPORTS_DIR.iterdir():
        if export_dir.is_dir() and export_dir.stat().st_mtime < cutoff:
            try:
                shutil.rmtree(export_dir)
                cleaned += 1
            except OSError as e:
                LOG.warning("Could not remove export dir %s: %s", export_dir.name, e)
    for ws_dir in WORKSPACE_DIR.iterdir():
        if ws_dir.is_dir() and ws_dir.stat().st_mtime < cutoff:
            try:
                shutil.rmtree(ws_dir)
                cleaned += 1
            except OSError as e:
                LOG.warning("Could not remove workspace dir %s: %s", ws_dir.name, e)
    if cleaned:
        LOG.info("Cleaned %d old export/workspace directories", cleaned)

def _enforce_storage_limit():
    """Delete oldest exports if total storage exceeds MAX_STORAGE_MB."""
    import shutil
    max_bytes = MAX_STORAGE_MB * 1024 * 1024
    total = sum(f.stat().st_size for f in EXPORTS_DIR.rglob("*") if f.is_file())
    if total <= max_bytes:
        return
    dirs = sorted(EXPORTS_DIR.iterdir(), key=lambda d: d.stat().st_mtime)
    for d in dirs:
        if total <= max_bytes * 0.8:
            break
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            try:
                shutil.rmtree(d)
                total -= size
            except OSError as e:
                LOG.warning("Could not remove dir %s during storage enforcement: %s", d.name, e)
    LOG.info("Storage enforced: %.1fMB used (limit: %sMB)", total / 1024 / 1024, MAX_STORAGE_MB)

def _truncate_completed_logs():
    """Truncate logs for completed/failed searches to save SQLite space."""
    try:
        with sqlite3.connect(str(SEARCH_DB), timeout=10) as conn:
            conn.execute("""
                UPDATE searches
                SET log = ?
                WHERE status IN ('completed', 'failed')
                  AND length(log) > ?
            """, ("[Log truncated for storage optimization]", 10000))
            conn.execute("""
                DELETE FROM searches
                WHERE created_at < datetime('now', '-30 days')
            """)
    except Exception as e:
        LOG.warning("Log truncation skipped: %s", e)

def _run_storage_cleanup():
    """Run all storage cleanup tasks."""
    try:
        _cleanup_old_exports()
        _enforce_storage_limit()
        _truncate_completed_logs()
    except Exception as e:
        print(f"  ⚠️ Storage cleanup error: {e}")

@app.on_event("startup")
async def startup():
    LOG.info("SciGraph API v3.2.2 starting")
    LOG.info("   Engine dir: %s", ENGINE_DIR)
    LOG.info("   Exports dir: %s", EXPORTS_DIR)
    LOG.info("   Workspace dir: %s", WORKSPACE_DIR)
    LOG.info("   Storage limit: %sMB | Export retention: %s min", MAX_STORAGE_MB, EXPORT_MAX_AGE_MINUTES)
    _run_storage_cleanup()
    # Schedule recurring cleanup every 30 minutes (cancelled on shutdown)
    async def _periodic_cleanup():
        while not _shutdown.is_set():
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                _run_storage_cleanup()
    asyncio.ensure_future(_periodic_cleanup())

@app.on_event("shutdown")
async def shutdown():
    _shutdown.set()
    LOG.info("SciGraph API shutting down")
