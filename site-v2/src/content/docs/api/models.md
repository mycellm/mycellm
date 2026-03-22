---
title: "Models API"
---


## `GET /v1/models`

List all available models across the network (local + QUIC peers + fleet).

```json
{
  "object": "list",
  "data": [
    {"id": "Qwen2.5-3B-Instruct-Q8_0", "object": "model", "owned_by": "local"},
    {"id": "Mistral-Small-24B-Q4_K_M", "object": "model", "owned_by": "fleet:aurora"},
    {"id": "relay:llama3.2:3b", "object": "model", "owned_by": "relay:ipad"}
  ]
}
```

Models prefixed with `relay:` are served by external devices connected via [relay backends](/integrations/relay/).

## `POST /v1/node/models/load`

### Local GGUF model

```json
{
  "model_path": "/path/to/model.gguf",
  "name": "my-model",
  "ctx_len": 4096
}
```

### API Provider model

```json
{
  "name": "claude-sonnet",
  "backend": "openai",
  "api_base": "https://openrouter.ai/api/v1",
  "api_key": "secret:openrouter",
  "api_model": "anthropic/claude-sonnet-4",
  "max_concurrent": 32
}
```

Use `secret:name` to reference encrypted secrets instead of raw API keys.

`max_concurrent` controls how many simultaneous requests this model can handle (default: 32 for API/relay, 1 for local GGUF). See [relay docs](/integrations/relay/#concurrency) for details.

## `POST /v1/node/models/unload`

```json
{"model": "my-model"}
```

## Model Tiers

Models are classified by parameter count:

| Tier | Parameters | Examples |
|------|-----------|----------|
| Tier 1 | up to 8B | Qwen 7B, Llama 8B, Phi-4 |
| Tier 2 | up to 70B | Mistral Small 24B, Llama 70B |
| Tier 3 | 70B+ | Qwen 72B, Llama 405B |

Access is governed by credit balance — earned by contributing GPU time to the network:

| Tier | Credit balance | Access |
|------|---------------|--------|
| Free | Anonymous / < 10 | Tier 1 models, 5K tokens/day |
| Contributor | 10+ credits | Tier 1 + 2 models, 50K tokens/day |
| Power Seeder | 50+ credits | All tiers, highest daily limits |

Credits are earned by serving inference and spent by consuming it. The system is designed to be fair — contribute compute, get compute back.
