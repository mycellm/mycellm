---
title: "mycellm_ Documentation"
---


**Distributed LLM inference across heterogeneous hardware.**

A peer-to-peer network where anyone can contribute GPU power and run LLM inference. Models run across distributed nodes with an OpenAI-compatible API. No single server, no single owner.

---

## 60-Second Quick Start

### pip

```bash
pip install mycellm
mycellm init
mycellm serve
```

### Docker

```bash
docker run -d --name mycellm \
  -p 8420:8420 -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  ghcr.io/mycellm/mycellm
```

### One-liner

```bash
curl -fsSL https://mycellm.dev/install.sh | sh
```

Then chat:

```bash
mycellm chat
```

Or use as an OpenAI-compatible backend:

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-key  # optional
```

---

## What mycellm_ does

| Feature | Description |
|---------|-------------|
| **OpenAI-compatible API** | Drop-in replacement at `/v1/chat/completions` |
| **P2P inference** | Routes requests across QUIC-connected peers |
| **Fleet management** | Bootstrap nodes coordinate GPU clusters |
| **Credit system** | Earn credits by serving, spend by consuming |
| **Model routing** | Quality-aware routing by tier, tags, latency |
| **Zero-config chat** | `mycellm chat` auto-discovers the network |

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Client (OpenAI SDK / curl / mycellm chat)      │
├─────────────────────────────────────────────────┤
│  API Layer (:8420)                              │
│  /v1/chat/completions  (OpenAI-compatible)      │
│  /v1/public/chat/completions  (rate-limited)    │
│  /metrics  (Prometheus)                         │
├─────────────────────────────────────────────────┤
│  Router (model resolver, quality constraints)   │
├──────────┬──────────┬───────────────────────────┤
│  Local   │  QUIC    │  Fleet (HTTP proxy)       │
│  llama   │  peers   │  to registered nodes      │
│  .cpp    │  :8421   │                           │
├──────────┴──────────┴───────────────────────────┤
│  Identity (Ed25519 keys + device certs)         │
│  Credits (SQLAlchemy — SQLite or PostgreSQL)    │
│  Secrets (Fernet-encrypted at rest)             │
└─────────────────────────────────────────────────┘
```

## Links

- [Quick Start](/quickstart/install/) — Install and join the network
- [API Reference](/api/overview/) — OpenAI-compatible endpoints
- [CLI Reference](/cli/reference/) — All commands and options
- [Configuration](/config/settings/) — Environment variables
- [Integrations](/integrations/openai/) — Use with existing tools
