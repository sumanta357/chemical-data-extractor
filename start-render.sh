#!/bin/sh
# LEGACY — no longer used by the Docker deployment.
#
# The container now runs ONLY Python: the Next.js UI is statically exported
# at build time and served by FastAPI (see Dockerfile + python-api/app.py).
# This file is kept for reference/local runs that still want the old
# two-process layout (uvicorn on 8000 + `next start` on $PORT).

set -e

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
