# Models API

## `GET /v1/models`

List all available models across the network (local + QUIC peers + fleet).

```json
{
  "object": "list",
  "data": [
    {"id": "Qwen2.5-3B-Instruct-Q8_0", "object": "model", "owned_by": "local"},
    {"id": "Mistral-Small-24B-Q4_K_M", "object": "model", "owned_by": "fleet:aurora"}
  ]
}
```

## `POST /v1/node/models/load`

### Local GGUF model

```json
{
  "model_path": "/path/to/model.gguf",
  "name": "my-model",
  "ctx_len": 4096
}
```

### Remote API model

```json
{
  "name": "claude-sonnet",
  "backend": "openai",
  "api_base": "https://openrouter.ai/api/v1",
  "api_key": "secret:openrouter",
  "api_model": "anthropic/claude-sonnet-4"
}
```

Use `secret:name` to reference encrypted secrets instead of raw API keys.

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

The public gateway restricts anonymous users to the best available model. Contributors and power seeders get access to higher tiers.
