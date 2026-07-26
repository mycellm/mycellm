# mycellm — fleet & deploy operations (overflow reference)

Not loaded into the standard Drydock context chain (procedures → CLAUDE.md →
gotchas → architecture → goals). Read it when a task actually touches a host.
**Workers do not deploy.** Everything below is human-gated.

## Nodes
| Node | Host | Role |
|------|------|------|
| `mycel_prime` | docker-box `96.126.98.204` = `api.mycellm.dev` | Public bootstrap/relay, `MYCELLM_PUBLIC=true`, no local models |
| Utopia | `10.1.1.210` | Pure QUIC relay/gateway; no local models, no HTTP relay backends. systemd `mycellm.service`, runs as user `cc` |
| Aurora | `10.1.1.81` / TS `100.100.1.81` | M1 Max 64GB seeder, big MLX models (e.g. 30B-A3B) |
| Hokulea | `10.1.1.11` | M1 16GB always-on seeder, small MLX models |

Nodes auto-join networks advertised by bootstrap peers during the QUIC handshake.
On the public bootstrap the HTTP API binds `127.0.0.1:8420` (only Caddy serves it
externally); QUIC 8421/udp is public. `MYCELLM_API_KEY` protects admin/node
paths; inference paths are auth-exempt in public mode and anon-rate-limited per IP.

## Bootstrap deploy (docker-box)
`./scripts/deploy-bootstrap.sh` — blue/green. Rsyncs the **local working tree**
(so it can deploy a branch) to `docker-box:/srv/docker/mycellm/app/` (flat copy,
NOT a git repo), builds a git-hash-tagged image, starts green on **:8430**,
health-checks, swaps the Caddy upstream, retires blue. `--rollback` reverts.
`DOMAIN` inside the script is **`api.mycellm.dev`** (the header comment still says
`bootstrap.mycellm.dev` — the comment is wrong, the variable is right).
Hotfix without a rebuild: `docker cp` the patched file into the container +
`docker compose restart mycellm`.

## Seeder Macs
rsync `src/mycellm/` → editable install → restart launchd.
- Aurora source `/Users/kalbo/mycellm/`, venv `~/mycellm/.venv/`, py3.13.
- Hokulea source `/Users/kalbo/mycellm-src/`, venv `~/mycellm-venv/`, py3.14 —
  **NOT `~/mycellm/`**, which is stale and makes a deploy a silent no-op.
- Restart: `launchctl kickstart -k gui/$(id -u)/com.mycellm.node` (KeepAlive will
  not respawn a clean kill). Logs `~/Library/Logs/mycellm.log`. Models reload
  from `~/.local/share/mycellm/model_configs.json`.
- **Aurora's launchd plist `EnvironmentVariables` override `.env`** — fix
  bootstrap peers/config in the plist; always check the plist for what is
  actually active.

## Utopia
Editable install from the shared source dir; `sudo systemctl restart mycellm`.

## Caddy (api.mycellm.dev)
`reverse_proxy 127.0.0.1:8420` with `flush_interval -1` (**required** for SSE —
without it Caddy buffers tokens), `versions 1.1` (HTTP/2 to uvicorn produced
intermittent content-length-0 502s), `read/write_timeout 600s`.

## Wedged-seeder diagnostics
- **QUIC liveness ≠ inference works.** Pings/handshakes run in different asyncio
  tasks than the inference handler; an exception there leaves the connection
  alive while replies never come. `/v1/node/status` "routable" and Prometheus
  `seeders_online` can all read healthy while chat is dead — trust `activity`
  (req/min).
- Silent-wedge signature: `ERROR Task exception was never retrieved` with a
  traceback inside a peer handler (e.g. `_handle_inference_request`). Historic
  cause: `from mycellm.config.settings import settings` — WRONG; the module
  exports `get_settings()`, so `from mycellm.config import get_settings`.
- **fd-exhaustion wedge**: PID alive, `curl localhost:8420/v1/models` empty,
  `lsof -p <pid> | wc -l` >> `ulimit -n` (Aurora's launchd limit was 256 before
  `12b5e4b`). Tell-tale: **log-file mtime older than process etime**. Recover
  with `launchctl kickstart -k gui/$(id -u)/com.mycellm.node`.
- Bootstrap `Model 'X' not found` is a **fallback string after a routing
  timeout** (peer 120s timeout → empty fleet → error), not proof routing never
  tried.

## Known noise
Seeders' HTTP `POST /v1/admin/nodes/announce` always fails against a public
bootstrap (API is loopback-bound). Capabilities ride NodeHello over QUIC anyway;
`ea96509` added per-bootstrap backoff. Don't chase the warning.
