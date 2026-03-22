---
title: "Relay Backends"
---

Use any device running an OpenAI-compatible API as an inference backend for your mycellm node. The relay device provides the compute — mycellm provides the routing, credit accounting, and network presence.

## How it works

```
iPad / Phone / GPU box              Your mycellm node
┌──────────────────────┐           ┌────────────────────┐
│ Ollama / LM Studio / │  ← HTTP → │ mycellm serve      │
│ PocketPal / vLLM     │           │   --relay device:80 │
│ :8080/v1/models      │           │ announces models   │
└──────────────────────┘           └────────────────────┘
                                          │
                                     QUIC to network
```

1. The relay device runs any app that exposes `/v1/models` and `/v1/chat/completions`
2. mycellm discovers models from the relay's `/v1/models` endpoint
3. Models are announced to the network as `relay:<model-name>`
4. Inference requests are proxied transparently to the relay device
5. Credits accrue to your node (you contributed the compute)

## Setup

### Via CLI flag

```bash
mycellm serve --relay http://ipad.lan:8080
```

Multiple relays:

```bash
mycellm serve --relay http://ipad.lan:8080 --relay http://ollama.lan:11434
```

### Via environment variable

```bash
MYCELLM_RELAY_BACKENDS=http://ipad.lan:8080,http://ollama.lan:11434
```

### Via dashboard

Open the dashboard → **Models** tab → **Relay Device** tab → paste the device URL and click **Add Relay**.

Connected relays show online/offline status and their discovered models.

### Via API

```bash
curl -X POST http://localhost:8420/v1/node/relay/add \
  -H "Content-Type: application/json" \
  -d '{"url": "http://ipad.lan:8080", "name": "iPad Pro"}'
```

### Via chat REPL

```
/relay add http://ipad.lan:8080
/relay              # list all relays
/relay refresh      # re-discover models
/relay remove http://ipad.lan:8080
```

## Compatible apps

Any app that exposes an OpenAI-compatible API works as a relay:

| App | Platform | Notes |
|-----|----------|-------|
| [Ollama](https://ollama.com) | macOS, Linux, Windows | Default port 11434. Batches requests. |
| [LM Studio](https://lmstudio.ai) | macOS, Linux, Windows | Enable "Local Server" in sidebar |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md) | Any | `llama-server --port 8080` |
| [vLLM](https://docs.vllm.ai) | Linux (CUDA) | High-throughput, continuous batching |
| [LocalAI](https://localai.io) | Any | Drop-in OpenAI replacement |

## iPad / iPhone as a relay

Apple Silicon devices (M1–M4) are excellent inference backends. You need an app that runs a local LLM **and** exposes an OpenAI-compatible API server.

Currently the best option for iOS/iPadOS is running Ollama via a Mac on the same network, then pointing the relay at that Mac. Native iOS apps with API server support are still emerging — check the [App Store](https://apps.apple.com/us/charts/iphone/productivity-apps/6007) for new options.

For **Mac** devices (MacBook, Mac Mini, Mac Studio):

1. Install [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai)
2. Pull a model: `ollama pull llama3.2:3b`
3. Ollama serves on port 11434 by default
4. Add as relay: `mycellm serve --relay http://<mac-ip>:11434`

The M4 with 16GB RAM can run 8B models at ~30 tok/s via Metal.

:::note
Set `max_concurrent` appropriately for the device: `2` for an iPad or single-GPU Mac, `32` for a multi-GPU server or cloud API.
:::

## API reference

### `GET /v1/node/relay`

List all relay backends and their status.

```json
{
  "relays": [
    {
      "url": "http://ipad.lan:8080",
      "name": "ipad",
      "online": true,
      "models": ["llama3.2:3b", "phi-4-mini"],
      "model_count": 2
    }
  ]
}
```

### `POST /v1/node/relay/add`

```json
{"url": "http://ipad.lan:8080", "name": "iPad Pro", "max_concurrent": 2}
```

`max_concurrent` controls how many simultaneous requests mycellm sends to this device (default: 32). Set lower for constrained devices like iPads (`2`), higher for beefy GPU servers.

### `POST /v1/node/relay/remove`

```json
{"url": "http://ipad.lan:8080"}
```

### `POST /v1/node/relay/refresh`

Re-discover models from all relay backends. Returns count of new models found.

## How relay models appear on the network

Relay models are prefixed with `relay:` to distinguish them from locally-loaded models:

```
GET /v1/models

{
  "data": [
    {"id": "Qwen2.5-3B-Q8_0", "owned_by": "local"},
    {"id": "relay:llama3.2:3b", "owned_by": "relay:ipad"},
    {"id": "relay:phi-4-mini", "owned_by": "relay:ipad"}
  ]
}
```

To the rest of the network, these models are indistinguishable from locally-loaded models. Peers route inference requests to your node, and your node proxies them to the relay device.

## Concurrency

Each model source has different concurrency characteristics:

| Source | Concurrent requests | Why |
|--------|-------------------|-----|
| Local GGUF (llama.cpp) | 1 per model | C library context is not thread-safe |
| API Provider | 32 per model (default) | Cloud server handles backpressure |
| Device Relay | 32 per model (default) | Remote device handles backpressure |

A node with 2 local models can serve 2 concurrent users — one per model. Adding relay or API provider models adds more concurrent capacity without the hardware constraint.

Tune per device with `max_concurrent`:

```bash
# iPad relay — limited device, keep low
curl -X POST localhost:8420/v1/node/relay/add \
  -d '{"url": "http://ipad:8080", "max_concurrent": 2}'

# Cloud API — high throughput
curl -X POST localhost:8420/v1/node/models/load \
  -d '{"name": "gpt-4o", "backend": "openai", "api_base": "...", "max_concurrent": 64}'
```

## Automatic health checking

mycellm polls relay backends every 60 seconds to detect:

- New models added to the relay device
- Models removed from the relay device
- Relay device going offline/coming back online

If a relay goes offline, its models are marked unavailable and requests route elsewhere on the network.
