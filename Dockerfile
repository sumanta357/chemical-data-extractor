FROM python:3.10-slim AS base

# Prevent Python from buffering stdout/stderr (critical for Render logs)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set working directory
WORKDIR /app

# Install system dependencies (none needed for current requirements)
# If future deps need libxml2 etc, add here:
# RUN apt-get update && apt-get install -y --no-install-recommends libxml2-dev && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY python-api/requirements.txt /app/python-api/requirements.txt
RUN pip install --no-cache-dir -r /app/python-api/requirements.txt

# Copy engine code (api/) — imported at runtime via sys.path
COPY api/ /app/api/

# Copy Python API
COPY python-api/ /app/python-api/

# Set working directory to the API root
WORKDIR /app/python-api

# Render injects $PORT — uvicorn binds to it
EXPOSE ${PORT:-8000}

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
