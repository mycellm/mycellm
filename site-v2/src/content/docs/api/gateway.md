---
title: "Public Gateway"
---


The public gateway provides rate-limited, anonymous access to the mycellm network. No API key required.

## `POST /v1/public/chat/completions`

Same request format as `/v1/chat/completions` with restrictions:

- **Rate limit**: 5,000 tokens/day per IP, 10 requests/minute
- **Max tokens**: 1,024 per response
- **Max message length**: 2,000 characters
- **Model**: auto-selected (best available), user cannot choose
- **Metadata**: stripped — no peer IDs, routing details, or credit info

### Request

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

### Streaming metadata

The gateway includes `mycellm` metadata in streaming chunks:

```json
// First chunk — node attribution
{"mycellm": {"node": "99e58f4c", "served_by": "mycellm-public"}}

// Final chunk — latency
{"mycellm": {"node": "99e58f4c", "latency_ms": 485, "served_by": "mycellm-public"}}
```

### Error responses

| Status | Meaning |
|--------|---------|
| 429 | Rate limit exceeded |
| 503 | No models available |
| 400 | Invalid request |

## `GET /v1/node/public/stats`

Network stats — no auth required.

```json
{
  "network_name": "mycellm-public",
  "nodes": {"total": 3, "online": 3, "seeding": 3},
  "compute": {"total_tps": 12.5, "total_vram_gb": 80.0, "total_ram_gb": 87.6},
  "models": {
    "unique": 2,
    "names": ["Qwen2.5-3B-Instruct-Q8_0", "Mistral-Small-24B"],
    "by_tier": {"tier1": [...], "tier2": [...], "tier3": []}
  },
  "activity": {"total_requests": 1542, "total_tokens": 89420},
  "top_contributors": [{"name": "aurora", "tps": 12.3, "models": 2}]
}
```
