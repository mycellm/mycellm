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

### Local GGUF model (llama.cpp backend)

```json
{
  "model_path": "/path/to/model.gguf",
  "name": "my-model",
  "ctx_len": 32768
}
```

`ctx_len` overrides the default for this load only. The default is
controlled by `MYCELLM_DEFAULT_CTX_LEN` (32768).

### Local MLX model (Apple Silicon)

```json
{
  "model_path": "mlx-community/Qwen3-Coder-30B-A3B-Instruct-MLX-4bit",
  "name": "Qwen3-Coder-30B-A3B-Instruct-MLX-4bit",
  "backend": "mlx",
  "ctx_len": 32768
}
```

`model_path` accepts either a local directory containing
`config.json` + safetensors, or a Hugging Face repo id — the MLX
backend resolves the latter on first load.

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

A public bootstrap exposes every model online across the network through `/v1/models`. Anonymous callers can request any of them; the [per-IP rate limit](../architecture/public-bootstrap.md) protects against abuse.
