# Python application with pre-built web dashboard
# Dashboard is built locally (npm run build in web/) before docker build.
# The build output at src/mycellm/web/ is copied directly into the image.

FROM python:3.13-slim

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
