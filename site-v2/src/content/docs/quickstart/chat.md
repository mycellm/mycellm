---
title: "First Chat"
---


## Zero-config chat

After installing, just run:

```bash
mycellm chat
```

This automatically discovers available models:

1. **Local node** — checks `localhost:8420` for loaded models
2. **LAN bootstrap** — reads `MYCELLM_BOOTSTRAP_PEERS` from config
3. **Public network** — falls back to `api.mycellm.dev`

No configuration needed for first use.

## Chat REPL

```
  mycellm_  chat
  ────────────────────────────────────────
  Model: Qwen2.5-3B-Instruct-Q8_0
  Node:  http://10.1.1.210:8420
  Type /help for commands, /q to exit

╭──
│ What is distributed computing?
╰──

Distributed computing is a model where multiple computers work
together to solve a problem...

  Qwen2.5-3B-Instruct-Q8_0 · via node 99e58f4c · 485ms
```

Features:

- **Streaming** — tokens appear as they're generated
- **Markdown rendering** — code blocks with syntax highlighting
- **Per-message attribution** — model name, node hash, latency
- **Multi-turn** — conversation context maintained
- **Slash commands** — manage your node inline

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/status` | Node status (name, peers, models, uptime) |
| `/models` | List available models |
| `/credits` | Credit balance (earned/spent) |
| `/fleet` | Fleet nodes with online status |
| `/config` | Runtime configuration |
| `/use <model>` | Switch to a specific model |
| `/clear` | Clear conversation history |
| `/q` | Exit |

## Use as an API

Any tool that speaks the OpenAI protocol works:

### Python

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8420/v1",
    api_key="your-key",  # optional
)
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)
```

### curl

```bash
curl http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Environment variables

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-key
export OPENAI_MODEL=auto
```
