# Join the Network

## Public network

```bash
mycellm init
mycellm serve
```

Your node auto-announces to the bootstrap, gets auto-approved (public network), and appears on the [stats page](https://stats.mycellm.dev) within 60 seconds.

## Private network

Create your own network:

```bash
mycellm init --create-network "My Org"
mycellm serve
```

Then invite others:

```bash
# On the network creator's node, create an invite via the dashboard
# or API, then share the token:
mycellm init --invite <token>
```

## Docker

```bash
docker run -d --name mycellm \
  -p 8420:8420 \
  -p 8421:8421/udp \
  -v mycellm-data:/data/mycellm \
  -e MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421 \
  ghcr.io/mycellm/mycellm
```

The Docker image auto-initializes on first run.

## Load a model

Open the dashboard at `http://localhost:8420` → **Models** tab → **Browse HuggingFace** to search and download GGUF models.

Or via API:

```bash
# Connect a remote API model (OpenRouter, Ollama, etc.)
curl -X POST http://localhost:8420/v1/node/models/load \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-sonnet",
    "backend": "openai",
    "api_base": "https://openrouter.ai/api/v1",
    "api_key": "secret:openrouter",
    "api_model": "anthropic/claude-sonnet-4"
  }'
```

## Earning credits

Your node earns credits whenever it serves inference to the network. Credits are used to consume inference from other nodes. The system is zero-sum — serving earns what consuming costs.

Check your balance:

```bash
mycellm chat
/credits
```
