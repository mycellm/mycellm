<p align="center">
  <img src="https://raw.githubusercontent.com/mycellm/mycellm/main/docs/site/docs/assets/mycellm-logo.svg" width="80" alt="mycellm">
</p>

<h1 align="center">mycellm_</h1>

<p align="center">
  <strong>Pool GPUs worldwide. Earn credits. No cloud required.</strong><br>
  <em>A peer-to-peer inference network with credits, privacy, and federation.</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://pypi.org/project/mycellm/"><img src="https://img.shields.io/pypi/v/mycellm.svg?color=green" alt="PyPI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python"></a>
  <a href="https://mycellm.ai"><img src="https://img.shields.io/badge/website-mycellm.ai-spore" alt="Website"></a>
</p>

<p align="center">
  <a href="https://mycellm.ai">Website</a> ·
  <a href="https://docs.mycellm.dev/quickstart/install">Docs</a> ·
  <a href="https://github.com/mycellm/mycellm-ios">iOS App</a> ·
  <a href="https://mycellm.ai/join">Join the network</a>
</p>

---

<p align="center">
  <img src="https://raw.githubusercontent.com/mycellm/mycellm/main/docs/screenshots/dashboard-overview.png" alt="mycellm dashboard — fleet overview with network health, hardware cards, and QUIC peer topology" width="100%">
</p>

## What is mycellm?

mycellm pools GPUs across the internet into a single inference network. Contribute compute and earn credits. Chat with open models for free. No blockchain, no tokens, no cloud vendor — just peers serving peers.

- **Credit economy** — earn credits by seeding, spend them consuming. Ed25519-signed receipts for every request. No cryptocurrency.
- **Sensitive Data Guard** — outgoing prompts are scanned on-device for API keys, passwords, and PII. Sensitive queries route to your local model automatically.
- **Private networks** — create invite-only inference networks for your team, lab, or org. Federation bridges multiple networks. Fleet management for enterprise.
- **OpenAI-compatible API** — drop-in replacement at `/v1/chat/completions`. Works with Claude Code, aider, Continue.dev, or any tool that accepts an OpenAI base URL.
- **iOS app** — native app for iPad and iPhone. Your iPad serves inference at 30+ tokens/sec on Metal and earns credits as a full network peer.
- **No cloud, no lock-in** — QUIC transport with NAT traversal. Works across the internet, not just your LAN. Your hardware, your models.

## Quick Start

```bash
# Install
pip install mycellm

# Create identity and join the public network
mycellm init

# Start serving (auto-detects GPU)
mycellm serve
```

Your node is now live. Load a model and start earning credits:

```bash
# Interactive chat
mycellm chat

# Or use the OpenAI-compatible API
curl http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello!"}]}'
```

## One-liner Install

```bash
curl -fsSL https://mycellm.ai/install.sh | sh
```

Or with Docker:

```bash
docker run -p 8420:8420 -p 8421:8421/udp ghcr.io/mycellm/mycellm serve
```

## How It Works

```
You (consumer) ──QUIC──▶ Bootstrap (relay) ──QUIC──▶ Seeder (GPU)
                                                       │
                                                  llama.cpp / vLLM
                                                       │
                                              Tokens stream back ◀──
```

1. **Consumers** send prompts via the API or chat interface
2. **Bootstrap** relays requests to available seeders via QUIC
3. **Seeders** run inference on their local GPU and stream tokens back
4. **Credits** flow to seeders — signed Ed25519 receipts for every request
5. **NAT traversal** enables direct P2P connections when possible

## Why mycellm?

| | mycellm | Cloud APIs | Local-only tools | Blockchain projects |
|---|---|---|---|---|
| **Works over internet** | QUIC + NAT traversal | N/A | LAN only | Varies |
| **No vendor lock-in** | Your hardware | Their hardware | Your hardware | Token buy-in |
| **Credit accounting** | Signed receipts | Pay per token | None | Token economics |
| **Private networks** | Invite-only federation | N/A | N/A | Public by default |
| **Privacy** | PII scanning + local redirect | Trust the provider | Full control | Trust the miner |
| **Mobile nodes** | Native iOS app | N/A | N/A | N/A |
| **Cost** | Free (contribute compute) | $$$ | Free | Buy tokens |

## Architecture

| Layer | Purpose | Tech |
|-------|---------|------|
| **Canopy** | Client access | iOS app, CLI chat, web UI, OpenAI API |
| **Mycelium** | Routing & discovery | QUIC transport, Kademlia DHT, STUN/ICE |
| **Roots** | Inference compute | llama.cpp (Metal/CUDA/ROCm/CPU), vLLM |
| **Ledger** | Accounting | Ed25519 signed receipts, per-network credit tracking |

## Features

<p align="center">
  <img src="https://raw.githubusercontent.com/mycellm/mycellm/main/docs/screenshots/dashboard-models.png" alt="mycellm models — fleet device management with HuggingFace model browser" width="100%">
</p>

### Inference
- **llama.cpp** backend with Metal, CUDA, ROCm, and CPU support
- **MLX** backend for Apple Silicon (M-series) — typically faster than Metal-via-llama.cpp for the same quantization, uses unified memory more efficiently
- **OpenAI tool/function calling** — `tools` and `tool_choice` pass through local backends, the OpenAI-compat relay, and QUIC peer routing
- **Streaming** token generation via SSE
- **Grammar-constrained output** via GBNF (`grammar` field on chat completions)
- **Model management** — download from HuggingFace, load/unload, scope control, platform-aware recommender
- **Thermal throttling** — auto-adjusts on mobile devices

### Networking
- **QUIC** transport with bidirectional streams (NWConnectionGroup on iOS, aioquic on Python)
- **NAT traversal** — STUN discovery + UDP hole punching for direct P2P
- **HTTP fallback** — works when QUIC is blocked
- **Bootstrap relay** — always works, even behind symmetric NAT

### Security & Privacy
- **Sensitive Data Guard** — scans every outgoing prompt for API keys, passwords, credit cards, and PII. High-severity matches are automatically redirected to your local model — sensitive data never leaves your device.
  - Gateway: returns 422 with explanation for flagged requests
  - Override: `X-Privacy-Override: acknowledged` header
- **Ed25519 identity** — account key → device cert → peer ID. Every node has a cryptographic identity.
- **Signed receipts** — cryptographic proof of inference served. Verifiable accounting without a blockchain.
- **Fleet management** — remote node control with admin key auth

### Multi-Network
- **Public network** — open to all, auto-approved
- **Private networks** — invite-only with Ed25519-signed tokens
- **Federation** — gateway nodes bridge multiple networks
- **Fleet** — enterprise management with remote commands
- **Trust levels** — strict (verify all), relaxed (verify, don't enforce), honor (trusted LAN)

## Use Cases

### AI Coding Assistants
mycellm works as a drop-in backend for OpenAI-compatible coding tools:

- **[OpenClaw](https://openclaw.ai)** — autonomous AI agent framework. Point it at `http://localhost:8420/v1` and your fleet serves the inference.
- **[OpenCode](https://github.com/opencode-ai/opencode)** — open-source coding assistant. Set `OPENAI_BASE_URL` to your mycellm node.
- **Claude Code / aider / Continue.dev** — any tool that accepts an OpenAI base URL.

No API keys to manage, no usage limits, no vendor lock-in. Your hardware, your models.

### Homelab GPU Fleet
Pool every GPU in your house into one inference endpoint. An M1 Max Mac Studio, an old gaming PC with an RTX 3090, an iPad Pro — they all join the same network and share the load. The dashboard lets you manage models across all devices from a single browser tab.

### Research Labs & Universities
Create a private mycellm network for your lab. Students and researchers get free inference from shared departmental GPUs. Ed25519 identity ensures accountability. Credit-based access prevents one user from monopolizing the cluster.

### At Scale
When dozens of nodes contribute compute, mycellm's quality-aware routing shines:
- **Tier routing** — route to the best model that fits the request (1B for quick tasks, 70B for complex reasoning)
- **Automatic failover** — if a node goes offline, requests route to the next best
- **Credit economics** — contributors earn credits, consumers spend them, freeloaders get throttled

## iOS App

Native app for iPad and iPhone. Your iPad is a full peer on the network — serve inference at 30+ tokens/sec on Metal, earn credits, and chat with privacy protection.

- **On-device inference** — llama.cpp on Metal, optimized for M-series iPads
- **Network + local routing** — toggle between network and on-device per message, with automatic fallback
- **Chat persistence** — threaded conversations with full metadata (model, node, tokens/sec, route). Export and share threads. Private ephemeral sessions.
- **Sensitive Data Guard** — prompts are scanned on-device; sensitive queries route to your local model
- **Serves an OpenAI API** — your iPad exposes `/v1/chat/completions` on your LAN for other tools to use

Requires iOS 17.0+. Also works on iPhone. [Source on GitHub.](https://github.com/mycellm/mycellm-ios)

## Configuration

```bash
# Environment variables
MYCELLM_API_HOST=0.0.0.0         # API listen address
MYCELLM_API_PORT=8420            # API port
MYCELLM_QUIC_PORT=8421           # QUIC transport port
MYCELLM_LOG_LEVEL=INFO           # Logging level
MYCELLM_FLEET_ADMIN_KEY=...      # Fleet management key (optional)
MYCELLM_NO_DHT=true              # Disable Kademlia DHT
MYCELLM_DEFAULT_CTX_LEN=32768    # Default context window for loaded models
MYCELLM_RELAY_BACKENDS=...       # Comma-separated OpenAI-compatible relay URLs
```

See [docs/config](https://docs.mycellm.dev/config/settings/) for full reference.

## API

OpenAI-compatible. Works with any client that supports the OpenAI API format.

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8420/v1", api_key="unused")
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/v1/models` | List available models |
| POST | `/v1/chat/completions` | Chat (streaming + non-streaming) |
| GET | `/v1/node/status` | Node status |
| GET | `/v1/node/peers` | Connected peers |
| POST | `/v1/node/models/load` | Load a model |
| POST | `/v1/node/federation/invite` | Create network invite |
| POST | `/v1/node/federation/join` | Join a network |

See [API docs](https://docs.mycellm.dev/api/overview/) for the full reference.

## Private Networks

Create a private network for your team, lab, or organization:

```bash
# On the bootstrap node
mycellm init --bootstrap --name "my-org"

# Generate an invite
mycellm network invite --max-uses 10

# On member nodes
mycellm network join mcl_invite_eyJ...
```

## Contributing

mycellm is open source under the Apache 2.0 license.

```bash
git clone https://github.com/mycellm/mycellm
cd mycellm
pip install -e ".[dev]"
pytest
```

## Built with AI

This project was developed in collaboration with [Claude Code](https://claude.ai/code) by Anthropic. Claude served as a pair-programming partner throughout architecture design, implementation, and testing. All technical decisions, project direction, and code review are my own.

## Credits

Built by [Michael Gifford-Santos](https://github.com/mijkal).

- **AI pair programming**: [Claude Code](https://claude.ai/code) by Anthropic
- **Protocol**: QUIC + CBOR + Ed25519
- **Inference**: [llama.cpp](https://github.com/ggerganov/llama.cpp) by Georgi Gerganov
- **DHT**: [kademlia](https://github.com/bmuller/kademlia) by Brian Muller
- **iOS inference**: [llama.swift](https://github.com/mattt/llama.swift) by Mattt

## License

Apache 2.0 — see [LICENSE](LICENSE).

"mycellm" and the mycellm logo are trademarks of Michael Gifford-Santos.
See [TRADEMARK.md](TRADEMARK.md) for usage guidelines.

---

<p align="center">
  <sub>mycellm_ — /my·SELL·em/ — mycelium + LLM</sub>
</p>
