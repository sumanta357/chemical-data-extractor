#!/bin/sh
# Render container entrypoint — runs BOTH services in one container:
#   1. uvicorn (FastAPI search engine)  on 127.0.0.1:8000   (internal only)
#   2. next start (Next.js UI)          on $PORT            (Render public port)
#
# The Next.js /api/* routes proxy to the engine via SEARCH_ENGINE_URL, which
# this script points at the in-container engine. If either process dies the
# container exits and Render restarts both together.

set -e

# Memory notes — the free tier gives the container 512MB shared between Node
# and Python plus each search subprocess. Render OOM-kills at the container
# level, so Node's internal heap flag does not prevent that; a tight
# --max-old-space-size actually starved the search subprocess of CPU (GC
# churn) and froze jobs with zero output, so it stays unset.
export MALLOC_ARENA_MAX=2

PORT="${PORT:-10000}"
export SEARCH_ENGINE_URL="http://127.0.0.1:8000"
export PYTHONUNBUFFERED=1

echo "[start-render] starting FastAPI engine on 127.0.0.1:8000 ..."
cd /app/python-api
uvicorn app:app --host 127.0.0.1 --port 8000 &
ENGINE_PID=$!

echo "[start-render] starting Next.js UI on 0.0.0.0:${PORT} ..."
cd /ui
node ./node_modules/next/dist/bin/next start -H 0.0.0.0 -p "${PORT}" &
UI_PID=$!

# Wait for either process to exit; if one dies, kill the other so the
# container exits and Render restarts the pair cleanly.
wait -n "$ENGINE_PID" "$UI_PID" 2>/dev/null || wait "$ENGINE_PID" "$UI_PID"

echo "[start-render] a process exited — shutting down container"
kill "$ENGINE_PID" "$UI_PID" 2>/dev/null || true
exit 1
