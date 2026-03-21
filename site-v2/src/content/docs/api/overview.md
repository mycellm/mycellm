---
title: "API Overview"
---


mycellm exposes an **OpenAI-compatible REST API** on port 8420. Any tool that works with the OpenAI API works with mycellm.

## Base URL

```
http://localhost:8420/v1
```

## Authentication

When `MYCELLM_API_KEY` is set, all `/v1/*` endpoints require authentication:

```
Authorization: Bearer <your-api-key>
```

Public endpoints (`/health`, `/metrics`, `/v1/public/*`) never require auth.

## Endpoints

### Inference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/chat/completions` | Yes | Chat completions (streaming supported) |
| GET | `/v1/models` | Yes | List available models |
| POST | `/v1/embeddings` | Yes | Text embeddings |

### Public (no auth)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/public/chat/completions` | Rate-limited public chat (5K tokens/day) |
| GET | `/v1/node/public/stats` | Network stats |

### Node Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/node/status` | Node status |
| GET | `/v1/node/system` | Hardware info |
| GET | `/v1/node/credits` | Credit balance |
| POST | `/v1/node/models/load` | Load a model |
| POST | `/v1/node/models/unload` | Unload a model |
| GET | `/v1/node/fleet/hardware` | Fleet aggregate stats |

### Monitoring

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Health check + version |
| GET | `/metrics` | No | Prometheus metrics |
| GET | `/v1/node/version` | Yes | Version + update check |

### Admin (Bootstrap nodes)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/admin/nodes/announce` | Node announcement |
| GET | `/v1/admin/nodes` | List fleet nodes |
| POST | `/v1/admin/nodes/{id}/approve` | Approve pending node |

## Interactive docs

Every running node serves OpenAPI docs at:

```
http://localhost:8420/docs
```
