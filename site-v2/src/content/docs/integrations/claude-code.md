---
title: "Claude Code"
---

[Claude Code](https://claude.ai/claude-code) and similar AI coding CLIs (aider, Continue.dev, Cursor) can use mycellm as their LLM backend via the OpenAI-compatible API.

:::note
For OpenClaw and its derivatives (NemoClaw, ClawdBot), see the dedicated [OpenClaw integration guide](/integrations/openclaw/).
:::

## Environment setup

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-mycellm-key
```

Then run your coding CLI as normal — it will route through mycellm.

## How it works

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Claude Code     │────▶│  mycellm node    │────▶│  GPU peer    │
│  aider, etc.     │     │  :8420           │     │  (fleet)     │
│                  │◀────│  /v1/chat/       │◀────│  llama.cpp   │
│  your terminal   │     │  completions     │     │  or API      │
└──────────────────┘     └──────────────────┘     └──────────────┘
```

The coding CLI thinks it's talking to OpenAI. mycellm routes the request to the best available model on the network — could be a local GGUF model, a QUIC-connected peer, or a fleet node.

## Model selection

For coding tasks, you may want to specify a capable model:

```bash
export OPENAI_MODEL=Mistral-Small-24B-Instruct-2501-Q4_K_M
```

Or use mycellm's quality routing:

```json
{
  "mycellm": {
    "min_tier": "capable",
    "required_tags": ["code"]
  }
}
```

## Running headless

If your mycellm node is on a remote GPU server:

```bash
# On the GPU server
mycellm serve --host 0.0.0.0

# On your laptop
export OPENAI_BASE_URL=http://gpu-server:8420/v1
claude-code  # or aider, continue, cursor, etc.
```

## Other coding tools

The same env vars work with any OpenAI-compatible coding tool:

- **aider** — `export OPENAI_API_BASE=http://localhost:8420/v1`
- **Continue.dev** — set base URL in VS Code extension settings
- **Cursor** — custom API endpoint in settings
- **Tabby** — configure as OpenAI-compatible backend
