# Single-service deployment for chemical-data-extractor.onrender.com
#
# Architecture (back to the layout that fits the 512MB free tier):
#   1. Stage 1 (node): builds the Next.js UI as a STATIC EXPORT (out/)
#   2. Stage 2 (python): FastAPI engine serves BOTH the API and the static UI
#
# The container runs ONLY Python at runtime — no Node process, no second
# server. uvicorn binds Render's $PORT directly and serves:
#   /api/search, /api/search/{id}, /api/exports/*, /api/health  (engine)
#   /api/analyze                                               (AI analyst, ported to Python)
#   /, /_next/*                                                (static UI)
#
# Build: docker build -t chemical-data-extractor .
# Run:   docker run -p 10000:10000 chemical-data-extractor

# ── Stage 1: build the Next.js UI as a static export ─────────────────────────
FROM node:20-slim AS ui-build

WORKDIR /ui

# Install UI dependencies (package-lock.json present → reproducible install)
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy UI sources and build (NEXT_STATIC_EXPORT=1 → static export in out/).
# The app/api route handlers are removed for the export — the Python engine
# implements the same endpoints (search, status, log, exports, health, analyze).
COPY app ./app
COPY components ./components
COPY lib ./lib
COPY next.config.js tailwind.config.js postcss.config.js tsconfig.json ./
RUN rm -rf app/api
ENV NEXT_STATIC_EXPORT=1
RUN npm run build

# ── Stage 2: Python runtime — engine + static UI, nothing else ────────────────
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Python dependencies for the engine + FastAPI layer
COPY python-api/requirements.txt /app/python-api/requirements.txt
RUN pip install --no-cache-dir -r /app/python-api/requirements.txt

# Copy engine code (api/) — imported at runtime via sys.path
COPY api/ /app/api/

# Copy Python API (FastAPI app)
COPY python-api/ /app/python-api/

# Copy the statically exported UI from stage 1
COPY --from=ui-build /ui/out /app/ui-out

WORKDIR /app/python-api

# Render injects PORT; uvicorn binds it directly (the ONLY server process)
EXPOSE 10000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
