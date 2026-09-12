# Combined single-service deployment for chemical-data-extractor.onrender.com
#
# Architecture on Render:
#   1. Stage 1 (node): builds the Next.js UI with `npm ci` + `npm run build`
#   2. Stage 2 (python): runtime with the Python engine + the built Next.js app.
#      A supervisor script starts:
#        - uvicorn (FastAPI engine)  on 127.0.0.1:8000  (internal only)
#        - next start                on $PORT           (Render's public port)
#   The Next.js /api/* routes talk to the engine over localhost, so the same
#   container serves the full app (search form, AI Analyst panel, exports) at
#   the public Render URL.
#
# Build: docker build -t chemical-data-extractor .
# Run:   docker run -p 10000:10000 chemical-data-extractor

# ── Stage 1: build the Next.js UI ────────────────────────────────────────────
FROM node:20-slim AS ui-build

WORKDIR /ui

# Install UI dependencies (package-lock.json present → reproducible install)
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy UI sources and build
COPY app ./app
COPY components ./components
COPY lib ./lib
COPY next.config.js tailwind.config.js postcss.config.js tsconfig.json ./

# SEARCH_ENGINE_URL is intentionally NOT baked in — it is runtime config.
RUN npm run build

# ── Stage 2: Python runtime with the built UI ────────────────────────────────
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production

WORKDIR /app

# Python dependencies for the engine + FastAPI layer
COPY python-api/requirements.txt /app/python-api/requirements.txt
RUN pip install --no-cache-dir -r /app/python-api/requirements.txt

# Node.js runtime to serve the Next.js app
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

# Copy engine code (api/) — imported at runtime via sys.path
COPY api/ /app/api/

# Copy Python API (FastAPI app)
COPY python-api/ /app/python-api/

# Copy the built Next.js UI + node_modules from stage 1
COPY --from=ui-build /ui/.next /ui/.next
COPY --from=ui-build /ui/node_modules /ui/node_modules
COPY --from=ui-build /ui/package.json /ui/package.json

# Supervisor script that starts both processes
COPY start-render.sh /app/start-render.sh
RUN chmod +x /app/start-render.sh

WORKDIR /ui

# Render injects PORT; the supervisor binds next start to it
EXPOSE 10000

CMD ["sh", "/app/start-render.sh"]
