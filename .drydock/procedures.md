# mycellm — operating procedures (BINDING)

Rules for any agent/worker touching this repo. They override defaults.
Always write the name lowercase: **mycellm** (never "Mycellm"/"MyCellm").

## Repo shape
- Path: `/data/projects/mycellm/app` (the canonical repo; siblings `hyphae/`, `ios/`, `website/` are separate repos). **Default branch is `main`.**
- Remote: `github.com/mycellm/mycellm` (public, Apache-2.0). Version in `pyproject.toml` (currently 0.6.2).
- `src/mycellm/` — Python package (FastAPI + aioquic + Kademlia; async throughout; CBOR; Ed25519).
- `web/` — React dashboard (Vite + Tailwind + TypeScript). `tests/` — pytest. `ops/`, `docker/`, `scripts/`.

## Build / test / lint (matches CI `.github/workflows/lint-and-test.yml`)
- Install: `pip install -e ".[dev]"`.
- Lint (Python): `ruff check src tests`. Lint (web, from `web/`): `npm run lint`.
- Tests: `pytest` (asyncio auto mode, testpaths=`tests`). Web build: `cd web && npm run build`.

## Deploy — HUMAN-GATED
- Deploy method is `./scripts/deploy-bootstrap.sh` — **blue-green** deploy to `bootstrap.mycellm.dev`: rsync source to `docker-box:/srv/docker/mycellm/app/`, build image (git-hash tag), health-check green on :8430, swap Caddy upstream, retire blue. `--rollback` reverts.
- Workers do NOT deploy. Do the branch work; the human deploys.

## Never (without an explicit human decision)
- **Never bump the version or deploy/publish (PyPI, App Store, bootstrap) without explicit approval** — this is a released public package.
- Deploy, push to the public remote, tag a release, or edit prod config/secrets.
- Write the name as anything other than lowercase "mycellm".

## Gotchas
- Default ports: API 8420, QUIC 8421, DHT 8422 (deploy green uses 8430).
- MLX extras (`mlx-lm`/`mlx-vlm`) install only on Apple Silicon — they no-op on Linux/Intel; don't treat their absence as breakage.
- Config uses Pydantic Settings + XDG paths; serialization is CBOR (`cbor2`); crypto is Ed25519 via `cryptography`.
- See `CLAUDE.md`, `KNOWN_ISSUES.md`, `BETA_TESTING.md` for more.
