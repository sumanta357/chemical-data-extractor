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
from fastapi.responses import FileResponse
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
    return {
        "message": "Scientific Knowledge Graph API",
        "docs": "/docs",
        "health": "/api/health",
    }


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
