# Architecture

## Overview

mycellm is a peer-to-peer distributed LLM inference network. Nodes contribute GPU compute, earn credits, and serve models via standard protocols.

## Components

### Identity Layer
- **Account key**: Ed25519 master keypair (one per user)
- **Device cert**: Per-node certificate signed by account key
- **Peer ID**: SHA256 hash of device public key

### Transport
- **QUIC** (port 8421/UDP): Authenticated P2P connections with TLS 1.3
- **HTTP API** (port 8420): OpenAI-compatible REST API
- **DHT** (port 8422/UDP, optional): Kademlia-based peer discovery

### Inference
- **llama.cpp**: Local GGUF model inference (CPU, CUDA, Metal)
- **OpenAI-compatible**: Proxy to remote APIs (OpenRouter, Ollama, vLLM)
- **Model resolver**: Quality-aware routing by tier, tags, and latency

### Networking
- **PeerManager**: Manages QUIC connections to bootstrap peers
- **PeerRegistry**: Tracks connected peers and capabilities
- **Fleet registry**: HTTP-announced nodes managed by bootstrap
- **ChainBuilder**: Multi-hop routing across peers

### Accounting
- **Credits**: Zero-sum earn/spend system with signed receipts
- **Reputation**: Peer scoring by success rate, latency, volume
- **Storage**: SQLAlchemy ORM (SQLite default, PostgreSQL optional)

### Federation
- **Network identity**: SHA256 of bootstrap account key
- **Invite tokens**: Signed, portable, with max-uses and expiry
- **Public vs private**: Public networks auto-approve, private require invite

## Data flow

```
User prompt
    │
    ▼
API (/v1/chat/completions)
    │
    ├─── Model loaded locally? ──▶ llama.cpp / API backend
    │
    ├─── QUIC peer has model? ──▶ Forward via QUIC
    │
    └─── Fleet node has model? ──▶ HTTP proxy to fleet node
              │
              ▼
         Response streamed back to user
         Credits transferred (seeder earns, consumer spends)
```

## Monitoring

- **Dashboard**: React SPA at `:8420` with real-time stats
- **Prometheus**: `/metrics` endpoint for scraping
- **Telemetry**: Opt-in anonymous counters to bootstrap
- **Logs**: SSE stream at `/v1/node/logs/stream`
