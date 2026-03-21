---
title: "Configuration"
---


All settings are configured via environment variables prefixed with `MYCELLM_`. They can be set in:

- `~/.config/mycellm/.env` (written by `mycellm init`)
- Shell environment
- Docker `-e` flags

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MYCELLM_API_KEY` | *(none)* | API key for dashboard/API authentication |
| `MYCELLM_BOOTSTRAP_PEERS` | *(none)* | Bootstrap node addresses (`host:port`, comma-separated) |
| `MYCELLM_HF_TOKEN` | *(none)* | HuggingFace token for gated models + higher rate limits |
| `MYCELLM_DB_URL` | SQLite | Database URL (`postgresql+asyncpg://user:pass@host/db`) |
| `MYCELLM_TELEMETRY` | `false` | Opt-in anonymous usage stats |
| `MYCELLM_LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MYCELLM_MODEL_DIR` | `~/.local/share/mycellm/models` | GGUF model download directory |
| `MYCELLM_DATA_DIR` | `~/.local/share/mycellm` | All persistent data |
| `MYCELLM_CONFIG_DIR` | `~/.config/mycellm` | Configuration directory |
| `MYCELLM_API_HOST` | `127.0.0.1` | API bind address |
| `MYCELLM_API_PORT` | `8420` | API port |
| `MYCELLM_QUIC_PORT` | `8421` | QUIC transport port (UDP) |
| `MYCELLM_EXTERNAL_HOST` | *(none)* | Public IP override for NAT traversal |
| `MYCELLM_NODE_NAME` | hostname | Display name for this node |
| `MYCELLM_INITIAL_CREDITS` | `100.0` | Starting credit balance |

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8420 | TCP | HTTP API + Dashboard |
| 8421 | UDP | QUIC P2P transport |
| 8422 | UDP | DHT discovery (optional) |

## File Layout

```
~/.local/share/mycellm/
├── keys/              # Ed25519 account + device keys
├── certs/             # Device certificates
├── tls/               # Self-signed TLS for QUIC
├── models/            # Downloaded GGUF files
├── federation/        # Network identity + memberships
├── secrets.json       # Encrypted API keys (Fernet)
├── mycellm.db         # SQLite database
└── model_configs.json # Saved model configurations

~/.config/mycellm/
└── .env               # Environment overrides
```

## PostgreSQL

For larger networks, switch from SQLite to PostgreSQL:

```bash
MYCELLM_DB_URL="postgresql+asyncpg://user:pass@localhost/mycellm"
```

Install the PostgreSQL driver:

```bash
pip install "mycellm[postgres]"
```
