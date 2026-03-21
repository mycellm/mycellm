---
title: "Encrypted Secrets"
---


API keys for remote model providers (OpenRouter, OpenAI, etc.) are encrypted at rest using your account's Ed25519 key.

## CLI

```bash
# Store a secret
mycellm secret set openrouter -v sk-or-abc123

# List stored secrets (names only, never values)
mycellm secret list

# Retrieve (with confirmation)
mycellm secret get openrouter

# Remove
mycellm secret remove openrouter
```

## Dashboard

**Settings** tab → **Encrypted Secrets** section. Store, list, and remove secrets with the web UI.

## Using secrets in model configs

Reference secrets by name instead of raw API keys:

```json
{
  "name": "claude-sonnet",
  "backend": "openai",
  "api_base": "https://openrouter.ai/api/v1",
  "api_key": "secret:openrouter",
  "api_model": "anthropic/claude-sonnet-4"
}
```

The `secret:` prefix is resolved at load time. The raw key never touches the config file.

## How it works

- Keys encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256)
- Encryption key derived from your account's Ed25519 private key via **HKDF-SHA256**
- Stored in `~/.local/share/mycellm/secrets.json` with `0600` permissions
- Different account = can't decrypt (secrets are bound to identity)
