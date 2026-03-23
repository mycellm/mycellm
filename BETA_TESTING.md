# mycellm Beta Testing Guide

Welcome to the mycellm beta! You're helping build a distributed LLM inference network.

## Quick Start

### Desktop (Mac/Linux)
```bash
pip install mycellm
mycellm init
mycellm serve
```

### iOS (iPad/iPhone)
1. iOS app coming soon — currently in development.
2. Launch — app auto-connects to public network
3. Go to **Models** tab → download a suggested model
4. Chat in **On-Device** mode (local) or **Network** mode (public)

## What to Test

### Core functionality
- [ ] Install and `mycellm serve` starts without errors
- [ ] Chat via `mycellm chat` or the web at [mycellm.ai](https://mycellm.ai)
- [ ] iOS app: download model, load, chat on-device
- [ ] iOS app: network chat via public gateway

### Network
- [ ] Your node appears on the public network (check [stats](https://mycellm.ai/stats))
- [ ] Cross-node inference: your model is used to serve other people's requests
- [ ] Credits earned/spent tracking (Dashboard → Credits card)

### Private networks
- [ ] Create a private network: `POST /v1/node/federation/invite`
- [ ] Join from another node with the invite token
- [ ] Inference routes within the private network

### Edge cases
- [ ] What happens when you close the iOS app? (node goes offline, reconnects on reopen)
- [ ] What happens when the bootstrap is unreachable? (LAN P2P continues)
- [ ] Try sending a message with an API key in it (should be blocked by Sensitive Data Guard)

## Reporting Issues

File issues at: https://github.com/mycellm/mycellm/issues

Include:
- Platform (macOS/Linux/iOS, device model)
- mycellm version (`mycellm --version`)
- Steps to reproduce
- Logs (if CLI: `mycellm serve --log-level DEBUG`)

## Known Limitations

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for the full list. Key points:

1. **Your prompts are visible to the seeder node** that runs inference. Don't send passwords or secrets on the public network.
2. **iOS app is foreground-only** — goes offline when you switch apps.
3. **One bootstrap server** — if it's down, the public network is unreachable.
4. **Credit system is for tracking, not payment** — no monetary value.

## Architecture

```
You (consumer) → QUIC → Bootstrap (relay) → QUIC → Seeder (GPU)
                                                    ↓
                                              llama.cpp / vLLM
                                                    ↓
                                              Tokens stream back
```

Your node can be both consumer and seeder simultaneously. Load a model and your hardware serves the network.

## Thank You

Every GPU counts. The more nodes join, the more models are available, the faster inference becomes. You're building the future of distributed AI.

— Michael ([@mijkal](https://github.com/mijkal))
