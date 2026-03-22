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
| [Ollama](https://ollama.com) | macOS, Linux, Windows | Default port 11434 |
| [LM Studio](https://lmstudio.ai) | macOS, Linux, Windows | Enable API server in settings |
| [PocketPal AI](https://apps.apple.com/app/pocketpal-ai/id6502579498) | iOS, iPadOS | Runs llama.cpp with Metal |
| [LLM Farm](https://apps.apple.com/app/llm-farm/id6461209867) | iOS, iPadOS | Open source, Metal acceleration |
| [vLLM](https://docs.vllm.ai) | Linux (CUDA) | High-throughput serving |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md) | Any | `llama-server --port 8080` |
| [LocalAI](https://localai.io) | Any | Drop-in OpenAI replacement |

## iPad / iPhone as a relay

Apple Silicon devices (M1–M4) are excellent inference backends:

1. Install **PocketPal AI** or **LLM Farm** from the App Store
2. Download a model (e.g., Llama 3.2 3B, Phi-4 Mini)
3. Enable the API server (usually in app settings)
4. Note the device's local IP (Settings → Wi-Fi → tap network → IP)
5. Add as relay: `mycellm serve --relay http://<ipad-ip>:8080`

The M4 iPad Pro with 16GB RAM can run 8B models at ~30 tok/s via Metal.

:::note
iOS/iPadOS apps can't run in the background indefinitely. Keep the app in the foreground while serving as a relay, or use Guided Access to prevent the app from being suspended.
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
{"url": "http://ipad.lan:8080", "name": "iPad Pro", "api_key": ""}
```

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

## Automatic health checking

mycellm polls relay backends every 60 seconds to detect:

- New models added to the relay device
- Models removed from the relay device
- Relay device going offline/coming back online

If a relay goes offline, its models are marked unavailable and requests route elsewhere on the network.
