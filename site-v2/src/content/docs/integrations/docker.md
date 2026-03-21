---
title: "Docker"
---


## Quick start

```bash
docker run -d --name mycellm \
  -p 8420:8420 \
  -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  ghcr.io/mycellm/mycellm
```

The container auto-initializes on first run (creates identity, joins public network).

## Configuration

```bash
docker run -d --name mycellm \
  -p 8420:8420 \
  -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  -e MYCELLM_API_KEY=your-secret-key \
  -e MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421 \
  -e MYCELLM_HF_TOKEN=hf_... \
  -e MYCELLM_TELEMETRY=true \
  ghcr.io/mycellm/mycellm
```

## Create a private network

```bash
docker run -d --name mycellm \
  -p 8420:8420 \
  -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  -e MYCELLM_NETWORK_NAME="my-org" \
  -e MYCELLM_PUBLIC=true \
  -e MYCELLM_API_KEY=admin-key \
  ghcr.io/mycellm/mycellm
```

## Docker Compose

```yaml
services:
  mycellm:
    image: ghcr.io/mycellm/mycellm
    ports:
      - "8420:8420"
      - "8421:8421/udp"
    volumes:
      - mycellm-data:/data/mycellm
    environment:
      - MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421
      - MYCELLM_TELEMETRY=true
    restart: unless-stopped

volumes:
  mycellm-data:
```

## With PostgreSQL

```yaml
services:
  mycellm:
    image: ghcr.io/mycellm/mycellm
    ports:
      - "8420:8420"
      - "8421:8421/udp"
    volumes:
      - mycellm-data:/data/mycellm
    environment:
      - MYCELLM_DB_URL=postgresql+asyncpg://mycellm:password@db/mycellm
    depends_on:
      - db
    restart: unless-stopped

  db:
    image: postgres:17
    volumes:
      - pg-data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=mycellm
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=mycellm

volumes:
  mycellm-data:
  pg-data:
```

## GPU support (NVIDIA)

```bash
docker run -d --name mycellm \
  --gpus all \
  -p 8420:8420 \
  -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  ghcr.io/mycellm/mycellm
```

## Building the image

```bash
cd /path/to/mycellm
docker build -f docker/Dockerfile -t mycellm .
```
