# Public Bootstrap

A **public bootstrap** is a mycellm node configured to act as an open gateway
for the network. It accepts anonymous inference requests on the standard
OpenAI-compatible endpoints and routes them to whichever seeders happen to be
online and reachable.

Anyone can run one. The canonical instance is `api.mycellm.dev`, but the same
image with `MYCELLM_PUBLIC=true` becomes a fully functional alternative
gateway.

## Why a separate mode?

The default mode (`MYCELLM_PUBLIC=false`) treats the node as a private device:
all `/v1/*` endpoints require an API key, and brute-force lockouts escalate
aggressively after a handful of failed attempts. That posture is wrong for an
open public gateway — it locks out legit users and is hostile to anonymous
exploration.

Public mode flips the defaults:

- Inference paths (`/v1/models`, `/v1/chat/*`, `/v1/completions`,
  `/v1/embeddings`, `/api/*`) are **unauthenticated**.
- Admin / node-management paths (`/v1/node/*`, `/v1/admin/*` except
  `/v1/admin/nodes/announce`) **still require** `MYCELLM_API_KEY`.
- The escalating lockout is replaced with a lenient sliding window so a
  scanner hitting one path can't lock out a legit user on another.
- A per-IP token bucket (`MYCELLM_PUBLIC_ANON_RATE_PER_MIN`, default 30/min)
  rate-limits anonymous inference. Authenticated callers (`Authorization:
  Bearer <api_key>`) bypass.

## How seeders attach

Seeders run normally — there is **no special "I serve a public network"
config**. They list the public bootstrap in `MYCELLM_BOOTSTRAP_PEERS` and
their existing QUIC dial does the rest:

```env
MYCELLM_BOOTSTRAP_PEERS=api.mycellm.dev:8420
```

The port `8420` is interpreted as "the public bootstrap host"; the seeder
dials its QUIC port (`8421/udp`) automatically. Capabilities are exchanged
via NodeHello during the handshake — no separate registration call is
required.

This design tolerates **symmetric NAT** at the seeder end. The seeder
initiates the connection outbound, holds the UDP pinhole open with 15 s
keepalive pings, and the bootstrap pushes inference requests **back down the
same QUIC session**. The bootstrap never has to dial home.

```text
   Seeder (home, behind NAT)
            │
            │ 1. outbound QUIC → bootstrap:8421/udp
            │ 2. NodeHello with capabilities
            │ 3. periodic ping holds NAT pinhole open
            ▼
   Public bootstrap (mycel_prime)
            │
            │ tracks live peers via PeerRegistry
            │ /v1/models = local + QUIC peers + HTTP fleet
            │ /v1/chat/completions routes:
            │   1. local model           (×1.5 score)
            │   2. direct QUIC peer       (×1.2 score)
            │   3. HTTP fleet (api_addr)  (×1.0 score)
            ▼
   Public caller (anon, no key)
            GET  /v1/models
            POST /v1/chat/completions    (streaming or not)
```

## P2P preference is preserved

Routing always prefers the most direct path. `ModelResolver` scores
candidates with a per-source multiplier:

| Source         | Multiplier | Notes                                |
|----------------|-----------:|--------------------------------------|
| `local`        | 1.5×       | Model loaded on this node            |
| `quic`         | 1.2×       | Direct QUIC peer (P2P)               |
| `fleet` (HTTP) | 1.0×       | HTTP-routed via `api_addr`           |

A consumer with a direct QUIC session to a seeder will always pick the P2P
path. The bootstrap is the **fallback**, never the preferred route. Public
bootstraps add reach, not centralisation.

## Failure modes and resilience

Several behaviours protect a public bootstrap from common failure modes:

- **Empty-gateway warning.** Every 5 min the node logs a `WARNING` if it has
  zero online seeders (HTTP-announced + QUIC peers) and zero local models.
  Surfaces the silent "everything 200s, but `auto` is the only model"
  failure.
- **Registry TTL sweep.** `node_registry` entries idle for >1 h are evicted
  from memory and the database every 5 min, so a long-lived bootstrap doesn't
  accumulate dead seeders.
- **Failover.** `ChainBuilder` returns sorted candidates; per-peer
  `failure_count` decays on success and grows on routing errors, so unhealthy
  seeders drift down the priority list.
- **Per-IP anon rate limit.** Stops one client from monopolising the gateway.
- **Lenient auth lockout.** No escalating cross-path bans on shared-NAT IPs.

## Metrics

Public bootstraps expose extra Prometheus metrics under `/metrics`:

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `mycellm_bootstrap_seeders_online` | gauge | — | Live seeders (QUIC peers + HTTP-announced fleet, last 120 s) |
| `mycellm_bootstrap_routed_total` | counter | `transport`, `outcome` | Requests routed downstream |
| `mycellm_bootstrap_anon_rate_limited_total` | counter | — | Anon requests rejected by per-IP rate limit |

## Deploying behind Caddy

A public bootstrap should not expose `8420/tcp` directly. Bind it to localhost
and front it with a TLS reverse proxy. The QUIC port (`8421/udp`) **must** be
publicly reachable so seeders can dial in.

```caddy
api.mycellm.dev {
    reverse_proxy 127.0.0.1:8420 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
        flush_interval -1
        transport http {
            versions 1.1
            read_timeout 600s
            write_timeout 600s
        }
    }
}
```

Two non-obvious bits:

- **`flush_interval -1`** is required for SSE streaming. Without it Caddy
  buffers chunks and the client sees nothing until the response ends.
- **`versions 1.1`** keeps the upstream connection on HTTP/1.1. HTTP/2 to a
  uvicorn upstream produces intermittent 502s under load.

## Running your own public bootstrap

Minimal Docker Compose:

```yaml
services:
  mycellm:
    image: mycellm:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8420:8420"
      - "8421:8421/udp"          # QUIC must be public
    environment:
      MYCELLM_PUBLIC: "true"
      MYCELLM_API_KEY: <admin key, kept private>
      MYCELLM_NODE_NAME: my_bootstrap
      MYCELLM_NETWORK_NAME: mycellm-public
      MYCELLM_EXTERNAL_HOST: <your public IP>
    mem_limit: 1g
```

That's it. Seeders that list your host in `MYCELLM_BOOTSTRAP_PEERS` will
connect, advertise their models, and the world will be able to call them via
your gateway — with P2P always preferred for any client that has a direct
session.
