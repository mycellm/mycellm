# mycellm — Gotchas & hard rules

**Naming:** ALWAYS lowercase `mycellm` — prose, code, config, commits. Name and
logo are trademarks (`TRADEMARK.md`).

**Release:** never bump a version, tag, publish to PyPI/GitHub, or deploy without
explicit per-step approval — approval for one version does not authorise the
next. Find the root cause, STOP, explain, wait. Feature branches only; `main` is
production. `/srv/docker/mycellm/app/` on the bootstrap is a flat rsync copy,
not a git repo — never `git pull` there.

**`auto` routing (caused real outages):** `model: "auto"` resolves to the
highest-tier model and the chat UI always sends `auto`, so one dead big-model
seeder once killed all chat while small seeders idled. Failover must walk the
full ranked list across *models*, not retry `resolved[0]`; normalise both `""`
and `"auto"` in `route_inference` *and* `route_inference_stream`; gate on a live
QUIC conn (`PeerEntry.is_live()`), not registry state. Both fixes are on `main` —
extend, don't rebuild.

**Wedged seeders:** QUIC liveness ≠ inference works — a node can read healthy on
`/v1/node/status` while chat is dead. Trust `activity` (req/min). Signatures and
recovery: `.drydock/fleet-operations.md`.

**Backends:** MLX is Apple-Silicon-only, extras no-op on Linux/Intel (not
breakage). `mlx_batched` is default, gated by `MYCELLM_MLX_CONTINUOUS_BATCHING`;
`model_configs.json` still says `"mlx"`. Relay discovery must skip
`relay:`-prefixed and non-local models or names multiply.

**Tests:** ruff is pinned `<0.16`; the suite (680 pass) is hermetic via
`tests/conftest.py`, which scrubs ambient `MYCELLM_*` and the XDG `.env` — keep
new fixtures hermetic. Read the code path before probing live nodes.
