# mycellm — operating procedures (BINDING)

Rules for any agent/worker touching this repo. They override defaults.
Always write the name lowercase: **mycellm** (never "Mycellm"/"MyCellm").

## Repo shape
- Path: `/data/projects/mycellm/app` (the canonical repo; siblings `hyphae/`, `ios/`, `website/` are separate repos). **Default branch is `main`.**
- Remote: `github.com/mycellm/mycellm` (public, Apache-2.0). Version in `pyproject.toml` (currently 0.6.2).
- `src/mycellm/` (package), `web/` (React dashboard), `tests/` (pytest), `ops/`, `docker/`, `scripts/`. Stack + ports: `.drydock/architecture.md`.

## Build / test / lint (matches CI `.github/workflows/lint-and-test.yml`)
- Install: `pip install -e ".[dev]"`.
- Lint (Python): `ruff check src tests`. Lint (web, from `web/`): `npm run lint`.
- Tests: `pytest` (asyncio auto mode, testpaths=`tests`). Web build: `cd web && npm run build`.

## Deploy — HUMAN-GATED
- Deploy method is `./scripts/deploy-bootstrap.sh` — **blue-green** deploy to `api.mycellm.dev`: rsync source to `docker-box:/srv/docker/mycellm/app/`, build image (git-hash tag), health-check green on :8430, swap Caddy upstream, retire blue. `--rollback` reverts. Host detail: `.drydock/fleet-operations.md`.
- Workers do NOT deploy. Do the branch work; the human deploys.

## Never (without an explicit human decision)
- **Never bump the version or deploy/publish (PyPI, App Store, bootstrap) without explicit approval** — this is a released public package.
- Deploy, push to the public remote, tag a release, or edit prod config/secrets.
- Write the name as anything other than lowercase "mycellm".

## Gotchas
- Hard rules + routing/wedge traps: `.drydock/gotchas.md`. Also `KNOWN_ISSUES.md`, `BETA_TESTING.md`.

## SELF-READ (context is capped)
Your injected context is hard-capped at 8000 chars, filled in this order:
procedures → CLAUDE.md/AGENTS.md → gotchas → architecture → goals. In this repo
that budget is exhausted before the end, so **`.drydock/goals.md` reaches you
truncated or not at all**. It is present in your worktree — `cat` it, along with
`.drydock/architecture.md` and any `docs/*.md` those files point at, BEFORE you
plan. Nothing has been lost; it just cannot all be auto-injected.
