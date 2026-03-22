---
title: "Chat REPL"
---


The `mycellm chat` command provides an interactive terminal for chatting and managing your node.

## Auto-discovery

`mycellm chat` works without any configuration:

1. Checks local node (`localhost:8420`) for models
2. Falls back to configured bootstrap (from `.env`)
3. Falls back to public network (`api.mycellm.dev`)

```bash
# Just works — finds models automatically
mycellm chat
```

## Slash commands

Type `/` followed by a command name:

| Command | Description |
|---------|-------------|
| `/status` | Node name, peer ID, uptime, hardware |
| `/models` | All available models with ownership |
| `/credits` | Balance, earned, spent |
| `/fleet` | Fleet nodes with online status and models |
| `/config` | Runtime configuration values |
| `/relay` | List relay backends |
| `/relay add <url>` | Add a relay (e.g., `/relay add http://ipad:8080`) |
| `/relay remove <url>` | Remove a relay |
| `/relay refresh` | Re-discover models from all relays |
| `/use <model>` | Switch active model mid-conversation |
| `/clear` | Reset conversation history |
| `/help` | List all commands |
| `/q` | Exit |

## Features

- **Streaming** with Rich Markdown rendering (syntax-highlighted code blocks)
- **Green-bordered input** for visual clarity
- **Animated dots** while waiting for first token
- **Per-message attribution**: model name, anonymized node hash, latency
- **Multi-turn context** maintained throughout the session
- **Ctrl+C** cleanly interrupts streaming without crashing
