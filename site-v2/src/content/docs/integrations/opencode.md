---
title: "OpenCode"
---


[OpenCode](https://github.com/opencode-ai/opencode) is an open-source AI coding assistant. Point it at your mycellm node for distributed inference.

## Setup

```bash
# Set mycellm as the LLM backend
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-mycellm-key  # optional
export OPENAI_MODEL=auto

# Start OpenCode
opencode
```

## Configuration file

In your OpenCode config (`~/.config/opencode/config.json` or project `.opencode.json`):

```json
{
  "provider": "openai",
  "model": "auto",
  "apiBase": "http://localhost:8420/v1",
  "apiKey": "your-mycellm-key"
}
```

## Using with a remote mycellm node

If your mycellm node is on another machine (e.g., a GPU server):

```bash
export OPENAI_BASE_URL=http://gpu-server:8420/v1
opencode
```

## Using with the public network

No mycellm node needed — use the public gateway directly:

```bash
export OPENAI_BASE_URL=https://api.mycellm.dev/v1/public
opencode
```

:::note
The public gateway is rate-limited (5,000 tokens/day). For higher limits, [run your own node](/quickstart/join/) — contributors who seed compute earn credits for inference on bigger models.
:::

## Tips

- Use `auto` as the model name — mycellm routes to the best available
- If you have multiple models loaded, specify one by name for consistency
- The mycellm dashboard (`:8420`) shows which models are available
