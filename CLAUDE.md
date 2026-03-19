# Mycellm

Distributed LLM inference protocol ("BitTorrent for LLMs").

## Project Layout

- `src/mycellm/` — Python package (installed as `mycellm`)
- `web/` — React dashboard source (Vite + Tailwind)
- `tests/` — pytest tests (unit, integration, e2e)

## Development

```bash
pip install -e ".[dev]"
pytest
mycellm --help
```

## Architecture

- **Identity**: Ed25519 keypairs, account/device certs
- **Transport**: QUIC + TLS 1.3 via aioquic, NodeHello for identity binding
- **Protocol**: CBOR-encoded message envelopes with versioning
- **Discovery**: Kademlia DHT (hints only) + bootstrap list
- **Inference**: llama-cpp-python backend
- **API**: FastAPI (OpenAI-compatible + node management)
- **Accounting**: Local SQLite credit tracking with signed receipts

## Brand

- CLI: ASCII mushroom banner on startup, ANSI-colored log tags
- Colors: Spore Green (#22C55E), Compute Red (#EF4444), Relay Blue (#3B82F6), Ledger Gold (#FACC15), Poison Purple (#A855F7)

## Key Conventions

- All crypto uses `cryptography` library (Ed25519)
- Message serialization: CBOR (`cbor2`)
- Config: Pydantic Settings, XDG paths
- Async throughout (asyncio)
- Default API port: 8420
- Default QUIC port: 8421
- Default DHT port: 8422
