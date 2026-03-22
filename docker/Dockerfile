# Multi-stage build: build web dashboard → package Python app

# Stage 1: Build web dashboard
FROM node:22-slim AS web-builder
WORKDIR /build
COPY web/package*.json web/
RUN cd web && npm ci --no-audit 2>/dev/null || true
COPY web/ web/
COPY src/mycellm/web/ src/mycellm/web/
RUN cd web && if [ -f package.json ]; then npm run build; fi
# Output lands at /build/src/mycellm/web/ (vite outDir: ../src/mycellm/web)

# Stage 2: Python application
FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps — wheels only, no compilation
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir --only-binary :all: \
    cryptography aioquic cbor2 fastapi uvicorn sse-starlette \
    asyncpg sqlalchemy aiosqlite typer rich pydantic pydantic-settings \
    pyyaml httpx prometheus-client && \
    pip install --no-cache-dir -e .

# Copy built web dashboard (overwrites placeholder in src/mycellm/web/)
COPY --from=web-builder /build/src/mycellm/web/ src/mycellm/web/

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8420 8421/udp
VOLUME /data/mycellm

ENV MYCELLM_DATA_DIR=/data/mycellm
ENV MYCELLM_CONFIG_DIR=/data/mycellm/config

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8420/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--no-dht"]
