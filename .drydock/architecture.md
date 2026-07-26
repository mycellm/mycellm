# mycellm — Architecture

Bound repo = `/data/projects/mycellm/app` (`github.com/mycellm/mycellm`,
Apache-2.0, **v0.6.2**, branch `main`). Drydock reads `.drydock/` from THIS root.

## Siblings under `/data/projects/mycellm/` — separate git roots, out of scope
- `ios/` — Swift/mlx-swift node; `github.com/mycellm/mycellm-ios`.
- `website/` — Astro site for mycellm.ai + docs.mycellm.dev
  (`repo.zetaix.com/mycellm/site`).
- `hyphae/` — coding-agent harness; no remote in the clone.

## Stack (`src/mycellm/`)
Python ≥3.11, asyncio throughout. QUIC+TLS1.3 (`aioquic`), NodeHello binds
identity on handshake; Ed25519 certs (`cryptography`); versioned CBOR (`cbor2`);
Kademlia DHT (hints) + bootstrap list; FastAPI/uvicorn OpenAI-compatible API +
`/v1/node/*` + `/health`, SSE via `sse-starlette`; SQLite ledger
(`aiosqlite`/SQLAlchemy) + signed receipts.
Ports: API **8420**/tcp, QUIC **8421**/udp, DHT **8422**/udp (deploy green 8430).

`node.py` peer registry/routing + QUIC inference handler; `router/`
(`model_resolver.py`, `registry.py`, `health.py`); `transport/`, `nat/`, `dht/`;
`federation.py` (a node joins *and* hosts networks);
`inference/` backends `llamacpp|mlx|mlx_batched|mlx_vlm|relay`; `accounting/`,
`storage/`, `identity/`, `privacy.py` (on-device PII guard), `metrics.py`,
`training/`, `menubar/`, `api/`, `cli/`, `config/`. `web/` = React/Vite/Tailwind
fleet dashboard, built in.

## Fleet
docker-box `96.126.98.204` = public bootstrap **api.mycellm.dev**; Utopia
`10.1.1.210` = pure QUIC relay; Aurora (M1 Max 64GB) + Hokulea (M1 16GB) = MLX
seeders. Hosts, deploy, Caddy → `.drydock/fleet-operations.md`.
