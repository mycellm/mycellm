# Claude Code / OpenClaw

Claude Code and similar AI coding CLIs can use mycellm as their LLM backend via the OpenAI-compatible API.

## Environment setup

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-mycellm-key
```

Then run your coding CLI as normal — it will route through mycellm.

## How it works

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Claude Code /   │────▶│  mycellm node    │────▶│  GPU peer    │
│  OpenClaw        │     │  :8420           │     │  (aurora)    │
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

```python
# In your tool's config, if it supports extra params:
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
claude-code  # or opencode, aider, continue, etc.
```
