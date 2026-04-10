# API Overview

mycellm exposes an **OpenAI-compatible REST API** on port 8420. Any tool that works with the OpenAI API works with mycellm. An **Ollama-compatible API** is also available for tools that use the Ollama SDK.

## Base URLs

```
http://localhost:8420/v1    # OpenAI-compatible
http://localhost:8420/api   # Ollama-compatible
```

## The `auto` Model

Use `model: "auto"` to let mycellm route to the best available model
across all local, peer, and fleet nodes. This works in both the OpenAI
and Ollama APIs.

## Authentication

mycellm has two auth modes, controlled by `MYCELLM_PUBLIC`:

**Private mode (default).** When `MYCELLM_API_KEY` is set, all `/v1/*` and
`/api/*` endpoints require authentication:

```
Authorization: Bearer <your-api-key>
```

`/health`, `/metrics`, `/v1/public/*`, and `/v1/admin/nodes/announce` are
always public.

**Public bootstrap mode (`MYCELLM_PUBLIC=true`).** Inference endpoints
(`/v1/models`, `/v1/chat/*`, `/v1/completions`, `/v1/embeddings`, `/api/*`)
are unauthenticated regardless of `MYCELLM_API_KEY`. Admin and node
management endpoints still require the key. Anonymous inference is rate
limited per source IP — see [Public Bootstrap](../architecture/public-bootstrap.md).

## Endpoints

### Inference

| Method | Path | Private auth | Public-mode auth | Description |
|--------|------|------|------|-------------|
| POST | `/v1/chat/completions` | Yes | **No** | Chat completions (streaming supported) |
| POST | `/v1/completions` | Yes | **No** | Legacy completions |
| GET | `/v1/models` | Yes | **No** | List available models |
| GET | `/v1/models/{id}` | Yes | **No** | Retrieve a single model |
| POST | `/v1/embeddings` | Yes | **No** | Text embeddings |

### Public stats (always no auth)

| Method | Path | Description |
|--------|------|-------------|
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

### Ollama-Compatible

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tags` | List models (Ollama format) |
| POST | `/api/show` | Model info (Ollama format) |
| POST | `/api/chat` | Chat completion (Ollama format) |

These endpoints allow Ollama SDK clients (OpenClaw, etc.) to use mycellm
as a drop-in Ollama replacement without configuration changes.

## Interactive docs

Every running node serves OpenAPI docs at:

```
http://localhost:8420/docs
```
