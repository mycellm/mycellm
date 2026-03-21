# Multi-stage build: build web dashboard → package Python app
# Base: nvidia/cuda for GPU support, falls back to CPU

# Stage 1: Build web dashboard
FROM node:22-slim AS web-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci --no-audit 2>/dev/null || echo "No web package.json yet"
COPY web/ ./
RUN if [ -f package.json ]; then npm run build; else mkdir -p dist && echo '<!DOCTYPE html><html><body><h1>mycellm</h1></body></html>' > dist/index.html; fi

# Stage 2: Python application
FROM python:3.12-slim AS runtime

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir \
    cryptography aioquic cbor2 fastapi uvicorn sse-starlette \
    aiosqlite typer rich pydantic pydantic-settings pyyaml httpx && \
    pip install --no-cache-dir -e .

# Copy built web dashboard
COPY --from=web-builder /app/web/dist/ src/mycellm/web/

# Copy remaining files
COPY tests/ tests/
COPY scripts/ scripts/
COPY docs/ docs/

# Default ports
EXPOSE 8420 8421/udp 8422/udp

# Data volume
VOLUME /data/mycellm

ENV MYCELLM_DATA_DIR=/data/mycellm
ENV MYCELLM_CONFIG_DIR=/data/mycellm/config

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8420/health || exit 1

# Entrypoint: auto-init if no identity, then exec mycellm
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0"]
