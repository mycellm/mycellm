---
title: "CLI Reference"
---


## Global options

```bash
mycellm --version    # Show version
mycellm --help       # Show all commands
```

## Commands

### `mycellm init`

Initialize identity and join a network.

```bash
mycellm init                              # Join public network
mycellm init --create-network "My Org"    # Create private network
mycellm init --create-network "My Org" --public  # Create public network
mycellm init --invite <token>             # Join via invite token/URL
mycellm init --no-serve                   # Configure only, no prompts
mycellm init --serve                      # Start daemon after init
```

### `mycellm serve`

Start the node daemon.

```bash
mycellm serve                         # Start on localhost:8420
mycellm serve --host 0.0.0.0          # Listen on all interfaces
mycellm serve --port 9000             # Custom API port
mycellm serve --no-dht                # Disable DHT discovery
mycellm serve --priority low          # Run at nice +15
mycellm serve --watchdog              # Auto-restart on crash
mycellm serve --install-service       # Install as system service
mycellm serve --uninstall-service     # Remove system service
```

### `mycellm chat`

Interactive chat REPL.

```bash
mycellm chat                           # Auto-discover and chat
mycellm chat --model llama-7b          # Use specific model
mycellm chat --endpoint http://gpu:8420  # Connect to remote node
mycellm chat --api-key my-key          # Authenticate
```

### `mycellm status`

Show node health.

```bash
mycellm status
mycellm status --endpoint http://gpu:8420
```

### `mycellm account`

Manage account identity.

```bash
mycellm account create                 # Generate master keypair
mycellm account create --name "Alice"  # With display name
mycellm account show                   # Show public key
mycellm account export                 # Export public key to file
```

### `mycellm device`

Manage device certificates.

```bash
mycellm device create                  # Create device cert
mycellm device create --role seeder    # Specify role
mycellm device list                    # List all certs
mycellm device revoke <name>           # Revoke a cert
```

### `mycellm secret`

Manage encrypted API keys.

```bash
mycellm secret set openrouter -v sk-or-...  # Store secret
mycellm secret set hf-token                  # Prompted input
mycellm secret list                          # List names
mycellm secret get openrouter                # Retrieve (masked)
mycellm secret remove openrouter             # Delete
```
