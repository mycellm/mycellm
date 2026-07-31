# Mycellm

Distributed LLM inference protocol ("BitTorrent for LLMs").

## Project Layout

- `src/mycellm/` — Python package (installed as `mycellm`)
- `web/` — React dashboard source (Vite + Tailwind)
- `tests/` — pytest tests (unit, integration, e2e)

## Development

```bash
python3 -m venv .venv
PIP_USER=0 .venv/bin/python -m pip install -e ".[dev]"   # PIP_USER=0 required
.venv/bin/python -m pytest
.venv/bin/mycellm --help
```

`.venv/` is gitignored, so a fresh `git worktree` has no interpreter until you
run the above — CI/verify steps invoke `.venv/bin/ruff` and `.venv/bin/python`
directly. `PIP_USER=0` is not optional: a global `pip.conf` sets `user = true`,
which makes `pip install` inside a venv fail with "Can not perform a '--user'
install" *and still exit 0*, leaving an empty venv behind.

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
