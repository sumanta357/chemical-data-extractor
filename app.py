#!/usr/bin/env python3
"""
Freebuff Scientific Knowledge Graph Platform - Web API
FastAPI backend that wraps scigraph.py CLI for deployment.

Run: uvicorn app:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── App Setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Scientific Knowledge Graph Platform",
    description="Multi-hop automated scientific discovery engine. Search proteins, compounds, and pathways across 19 databases.",
    version="3.1.0",
)

BASE_DIR = Path(__file__).parent
EXPORTS_DIR = BASE_DIR / "exports"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
EXPORTS_DIR.mkdir(exist_ok=True)

# ── In-memory search state ──────────────────────────────────────────────────────
# Stores running/completed searches
searches: dict[str, dict] = {}


# ── Models ──────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    query_type: str = "auto"
    hops: int = 1
    export_dir: Optional[str] = None


class SearchStatus(BaseModel):
    search_id: str
    query: str
    status: str  # running | completed | failed
    progress: Optional[str] = None
    log: list[str] = []
    export_files: list[dict] = []
    created_at: str = ""
    elapsed_seconds: Optional[float] = None
    error: Optional[str] = None


# ── Background Search Runner ────────────────────────────────────────────────────

async def run_search_in_background(search_id: str, query: str, query_type: str, hops: int, export_dir: str):
    """Run scigraph.py as a subprocess and capture output."""
    state = searches[search_id]
    state["status"] = "running"
    state["log"] = []
    start_time = time.time()

    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)

    # Build CLI command
    cmd = [
        sys.executable,
        str(BASE_DIR / "scigraph.py"),
        query,
        "--query-type", query_type,
        "--hops", str(hops),
        "--export-dir", export_dir,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        # Read output line by line
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
            state["log"].append(line)

            # Extract progress from the output
            if "[1/6]" in line or "[2/6]" in line or "[3/6]" in line or "[4/6]" in line or "[5/6]" in line or "[6/6]" in line:
                state["progress"] = line.strip()
            elif "╔══ Hop" in line:
                state["progress"] = line.strip()
            elif "Pipeline finished" in line or "finished successfully" in line:
                state["progress"] = "✅ Complete!"
            elif "Error" in line or "error" in line.lower():
                state["progress"] = f"⚠️ {line.strip()[:100]}"

        await process.wait()

        if process.returncode == 0:
            state["status"] = "completed"
            state["elapsed_seconds"] = time.time() - start_time
            # List export files
            state["export_files"] = _list_export_files(export_dir)
            state["progress"] = f"✅ Completed in {state['elapsed_seconds']:.1f}s"
        else:
            state["status"] = "failed"
            state["error"] = f"Process exited with code {process.returncode}"
            state["elapsed_seconds"] = time.time() - start_time

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["elapsed_seconds"] = time.time() - start_time


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


# ── API Endpoints ───────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "scigraph-api", "version": "3.1.0"}


@app.post("/api/search", response_model=SearchStatus)
async def start_search(request: SearchRequest, background_tasks: BackgroundTasks):
    """Start a new knowledge graph search."""
    # Validate
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

    # Start background task
    background_tasks.add_task(
        run_search_in_background,
        search_id, request.query, request.query_type, request.hops, export_dir
    )

    # Wait a brief moment for startup
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

    # Refresh export files if completed
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
        # Try to find the file in any export directory
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


# ── Static Files (Frontend) ─────────────────────────────────────────────────────

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {
            "message": "Scientific Knowledge Graph API",
            "docs": "/docs",
            "frontend_build": "Not found — run 'cd frontend && bun install && bun run build'",
        }


# ── MIME helpers ────────────────────────────────────────────────────────────────

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


# ── Startup ─────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print(f"🔬 SciGraph API v3.1 starting...")
    print(f"   Python: {sys.version}")
    print(f"   Exports dir: {EXPORTS_DIR}")
    print(f"   Frontend: {'✅ ' + str(FRONTEND_DIST) if FRONTEND_DIST.exists() else '⚠️  Not built'}")
