# Public Gateway

When a node runs with `MYCELLM_PUBLIC=true` it becomes a **public bootstrap
gateway** — anonymous callers can use the standard OpenAI-compatible
endpoints with no API key required. The canonical instance is
[`api.mycellm.dev`](https://api.mycellm.dev), but the same image with
`MYCELLM_PUBLIC=true` produces a fully-equivalent alternative gateway.
See [Public Bootstrap](../architecture/public-bootstrap.md) for the full
architecture.

## Anonymous endpoints

When `MYCELLM_PUBLIC=true`, these are open to the public:

| Path | Method | Notes |
|------|--------|-------|
| `/v1/models` | GET | Union of local + QUIC peer + fleet models |
| `/v1/models/{id}` | GET | Per-model details |
| `/v1/chat/completions` | POST | Streaming and non-streaming |
| `/v1/completions` | POST | Legacy OpenAI completions |
| `/v1/embeddings` | POST | If a backing model supports embeddings |
| `/api/tags` `/api/show` `/api/chat` | GET/POST | Ollama-compatible routes |
| `/health` `/metrics` | GET | Always public |

Admin and node-management endpoints (`/v1/node/*`, `/v1/admin/*`) **still
require** the bootstrap's `MYCELLM_API_KEY`.

## Quick start

```bash
# List models on api.mycellm.dev
curl https://api.mycellm.dev/v1/models

# Chat
curl https://api.mycellm.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Streaming
curl -N https://api.mycellm.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Tell me a haiku"}],
    "stream": true
  }'
```

The `auto` model lets the gateway pick the best available — typically the
highest-tier model with the lowest queue depth across all online seeders.

## Rate limits

Anonymous callers are subject to a per-IP token bucket. The default is
**30 requests per minute** per source IP, configurable via
`MYCELLM_PUBLIC_ANON_RATE_PER_MIN`.

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
Content-Type: application/json

{
  "error": "rate_limited",
  "message": "Anonymous inference limit (30/min) reached. Retry in 42s, or send an api_key for higher limits."
}
```

Authenticated callers (clients that send `Authorization: Bearer <api_key>`
matching the bootstrap's key) **bypass** the anon rate limit entirely.

## Routing details

A request to a public bootstrap is routed in priority order:

1. **Local model** on the bootstrap, if loaded (×1.5 score)
2. **Direct QUIC peer** that advertises the requested model (×1.2 score)
3. **HTTP fleet route** to a seeder's `api_addr` (×1.0 score)

The bootstrap reports the chosen path back in a header:

```
X-Mycellm-Routed-To: quic:hokulea
```

If a peer fails mid-request the failure is recorded against that peer's
score and the next best candidate is tried. P2P is always preferred —
running a public bootstrap adds reach, not centralisation.

## Public stats

```
GET /v1/public/stats
```

Network stats — no auth required, no rate limit.

```json
{
  "network_name": "mycellm-public",
  "nodes": {"total": 3, "online": 3, "seeding": 3},
  "compute": {"total_tps": 12.5, "total_vram_gb": 80.0, "total_ram_gb": 87.6},
  "models": {
    "unique": 11,
    "names": ["Qwen2.5-32B-Instruct-Q4_K_M", "Qwen2.5-7B-Instruct-Q4_K_M", "..."]
  },
  "activity": {"total_requests": 1542, "total_tokens": 89420}
}
```

## Error responses

| Status | Meaning |
|--------|---------|
| 200 | OK |
| 400 | Invalid request body |
| 429 | Per-IP anon rate limit reached |
| 503 | No models available across the network |

## Streaming notes

The gateway returns standard OpenAI SSE chunks. If you put your own reverse
proxy in front of a public bootstrap, two things matter:

- Disable response buffering on the proxy. With Caddy: `flush_interval -1`.
- Use HTTP/1.1 to the upstream — HTTP/2 to a uvicorn upstream produces
  intermittent 502s.
