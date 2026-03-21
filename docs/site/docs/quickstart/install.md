# Installation

## Requirements

- Python 3.11+
- macOS, Linux, or Windows (WSL)
- Optional: GPU with 8GB+ VRAM for serving models

## Install

=== "pip (recommended)"

    ```bash
    pip install mycellm
    ```

=== "Docker"

    ```bash
    docker pull ghcr.io/mycellm/mycellm
    ```

=== "From source"

    ```bash
    git clone https://repo.zetaix.com/mycellm/mycellm.git
    cd mycellm
    pip install -e ".[dev]"
    ```

=== "One-liner"

    ```bash
    curl -fsSL https://mycellm.dev/install.sh | sh
    ```

## Initialize

```bash
mycellm init
```

This creates your cryptographic identity and joins the public network. You'll be asked about telemetry (anonymous usage stats — opt-in).

What `init` does:

1. Creates Ed25519 account key (`~/.local/share/mycellm/keys/`)
2. Creates device key + certificate
3. Writes bootstrap config to `~/.config/mycellm/.env`
4. Connects to `bootstrap.mycellm.dev:8421`

## Start the node

```bash
mycellm serve
```

Your node is now part of the network. Open `http://localhost:8420` for the dashboard.

### Run as a service

```bash
mycellm serve --install-service
```

This installs a launchd (macOS) or systemd (Linux) service that auto-starts on boot and restarts on crash.

## Verify

```bash
mycellm status
mycellm --version
```

## Next steps

- [Chat with the network](chat.md)
- [Load a model](../cli/reference.md)
- [Configure your node](../config/settings.md)
