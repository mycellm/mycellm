---
title: "OpenClaw"
---

[OpenClaw](https://openclaw.ai) is a popular open-source AI agent framework used by developers for autonomous workflows, code generation, and task automation. Its derivatives — including NemoClaw, ClawdBot, and others — all support the OpenAI-compatible API format.

## Setup

Point OpenClaw at your mycellm node:

```json
{
  "providers": {
    "mycellm": {
      "baseUrl": "http://localhost:8420/v1",
      "api": "openai-completions",
      "models": [
        {
          "id": "auto",
          "name": "mycellm Auto",
          "contextWindow": 32768,
          "maxTokens": 4096
        }
      ]
    }
  }
}
```

Place this in your agent's `models.json` configuration.

## Environment variables

Alternatively, set the standard OpenAI env vars:

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-mycellm-key  # optional if no auth
```

## Using the public network

No node needed — use the public gateway directly:

```json
{
  "providers": {
    "mycellm-public": {
      "baseUrl": "https://api.mycellm.dev/v1/public",
      "api": "openai-completions",
      "models": [
        {
          "id": "auto",
          "name": "mycellm Public",
          "contextWindow": 32768,
          "maxTokens": 1024
        }
      ]
    }
  }
}
```

:::note
The public gateway is rate-limited (5,000 tokens/day). For higher limits, [run your own node](/quickstart/join/) — contributors who seed compute earn credits for inference on bigger models.
:::

## Multiple providers with fallback

Keep mycellm as primary and a paid provider as fallback:

```json
{
  "providers": {
    "mycellm": {
      "baseUrl": "http://localhost:8420/v1",
      "api": "openai-completions",
      "models": [
        {"id": "Qwen2.5-3B-Instruct-Q8_0", "name": "Qwen 3B (local)"},
        {"id": "Mistral-Small-24B-Q4_K_M", "name": "Mistral 24B (fleet)"}
      ]
    },
    "openrouter": {
      "baseUrl": "https://openrouter.ai/api/v1",
      "api": "openai-completions",
      "apiKey": "sk-or-...",
      "models": [
        {"id": "auto", "name": "OpenRouter Fallback"}
      ]
    }
  }
}
```

mycellm is listed first, so OpenClaw uses it by default. If mycellm is unavailable, it falls back to OpenRouter.

## Derivatives

The same configuration works with OpenClaw derivatives:

- **NemoClaw** — same `models.json` format
- **ClawdBot** — same `models.json` format
- Any agent built on the OpenClaw framework

## Private data

For sensitive workflows, use the `--private` trust flag or run a private mycellm network:

```bash
# CLI
mycellm chat --private

# API
{"mycellm": {"trust": "local"}}
```

This ensures prompts never leave your machine.
