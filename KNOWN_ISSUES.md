# mycellm — Known Issues

*Last updated: May 20, 2026 (v0.3.0)*

## Fixed in 0.3.0

### ~~OpenAI `tools=` parameter silently dropped~~
`POST /v1/chat/completions` accepted requests carrying `tools` and
`tool_choice` but the chat-completions Pydantic model didn't define
those fields, so they were dropped before reaching the backend. Models
never saw tool definitions and clients got empty replies, repeating
retries, or `ToolCallError: Retries exhausted` from harnesses like
[forge](https://github.com/antoinezambelli/forge). **Fixed**: tools and
tool_choice flow end-to-end through the API layer, both inference
backends (llama.cpp and MLX), the openai_compat relay, and QUIC peer
routing.

### ~~Server crash on `tools=` against 7B+ models~~
On nodes serving Qwen2.5-7B and larger, sending a request with the
`tools` parameter could crash the entire uvicorn process — clients saw
`RemoteProtocolError: Server disconnected without sending a response`
and subsequent requests got connection-refused until the service was
restarted. Root cause was a combination of the silent-drop bug above
plus an unrelated `llama_decode = -3` C-level fault in
`llama-cpp-python`'s non-streaming `create_chat_completion()` path when
KV state from a prior sequence persisted. **Fixed**: `generate()` now
delegates to `generate_stream()` internally on both backends,
sidestepping the C-level fault entirely.

### ~~Tool calls emitted as XML / JSON-fence text instead of structured `tool_calls`~~
Qwen-family models on local hardware often produce tool calls as
`<tool_call>{"name":..., "arguments":...}</tool_call>` XML or
```` ```json\n{...}\n``` ```` markdown fences in the `content` field,
even when `tools` is provided. Clients expecting OpenAI-format
`tool_calls` JSON failed to parse them. **Fixed**: the API layer
recognises both formats and normalises into proper `tool_calls` entries
(OpenAI spec: `function.arguments` as a JSON-encoded string). Single
tool definitions also auto-coerce `tool_choice` to the named function
for reliable JSON output.

### ~~Slow OpenAI-compat remote backends timed out on `generate()`~~
The `openai_compat` relay backend's non-streaming `generate()` POSTed
and waited for a complete response. On a remote 32B model that takes
~110s to emit its first token, this raced the 120s default httpx
timeout and lost. **Fixed**: `generate()` now streams internally and
reassembles into an `InferenceResult`. SSE keepalive pings keep the
connection alive regardless of first-token latency.

### ~~`load_model` ignored `ctx_len` kwarg~~
`InferenceManager.load_model(ctx_len=N, ...)` would still hand
`n_ctx=4096` to llama-cpp because the kwarg wasn't read. Loaded models
silently truncated long inputs. **Fixed**: `ctx_len` is honored and
defaults to `MYCELLM_DEFAULT_CTX_LEN` (32768).

### ~~Streaming path used first-loaded model instead of resolver~~
`/v1/chat/completions` with `stream: true` and `model: "auto"` (or any
unresolved name) bypassed `ModelResolver` and just picked whichever
model was loaded first. **Fixed**: streaming now uses the same resolver
the non-streaming path uses, including quality constraints.

### ~~`relay:` prefix accumulation on relayed models~~
Model names accumulated `relay:` prefixes (`relay:relay:relay:foo`)
when routed across multiple hops. **Fixed**: prefix normalised at API
display boundaries.

### ~~Auto-loaded MLX models required local path, not HF repo id~~
The MLX autoload path rejected `mlx-community/Qwen3-1.7B-4bit`-style
repo ids and required a fully resolved local cache path. **Fixed**: MLX
backend accepts HF repo ids directly; the backend resolves them.

### ~~Peer flapping under transient network drops~~
A single failed ping demoted a peer to `DISCONNECTED` and never
recovered it. **Fixed**: tolerant ping logic restores `DISCONNECTED`
peers on next successful response.

## Fixed in 0.2.5

### ~~Public bootstrap nodes locked out their own users~~
When a node was set as a public network bootstrap (`MYCELLM_PUBLIC=true`),
`/v1/models` and `/v1/chat/*` were still gated behind `MYCELLM_API_KEY`, and
the escalating brute-force lockout would 429 anyone who had previously sent
a request without a key. The "public" gateway was effectively closed.
**Fixed**: in public mode, inference paths are unauthenticated (admin
endpoints still require the key) and the lockout is replaced with a lenient
sliding window. A per-IP token bucket
(`MYCELLM_PUBLIC_ANON_RATE_PER_MIN`, default 30/min) protects against abuse.

### ~~Freshly-handshaked peers were invisible to routing~~
`PeerRegistry.peers_for_model()` filtered to `ROUTABLE`/`SERVING` only, but
new peers were registered while still in `AUTHENTICATED` and only later
promoted to `ROUTABLE` — leaving a window where they couldn't be routed
to. On a public bootstrap recovering from restart this manifested as
"3 peers connected, 0 models served". **Fixed**: connection state is
promoted before registration, and `peers_for_model()` accepts
`AUTHENTICATED` too.

### ~~Streaming path skipped P2P routing~~
`/v1/chat/completions` with `stream:true` only tried the HTTP fleet
fallback when no local model was available, never the QUIC chain.
**Fixed**: `_stream_response` now calls `route_inference_stream()` before
the fleet HTTP fallback, mirroring the non-streaming path.

### ~~Seeders couldn't announce to non-LAN bootstraps~~
`_announce_to_bootstrap()` had a hard `if not is_lan: continue` filter so
seeders never registered with public bootstraps over HTTP. **Fixed**: the
filter is removed; capabilities are still primarily exchanged via QUIC
NodeHello (which already worked).

## Fixed in 0.2.4

### ~~Ollama SDK clients get 404 for all models~~
Tools using the Ollama SDK (OpenClaw, ollama-python, etc.) call `/api/show` and `/api/tags` instead of the OpenAI-compatible `/v1/models`. mycellm had no Ollama-compatible endpoints, returning HTML (SPA fallback) or 404. **Fixed**: Added full Ollama API compatibility at `/api/tags`, `/api/show`, and `/api/chat`.

## Fixed in 0.2.3

### ~~Relay model prefix multiplication~~
When relaying models between nodes, model names accumulated `relay:` prefixes (e.g., `relay:relay:relay:model`). **Fixed**: Relay discovery skips models with existing `relay:` prefix and non-local models from remote endpoints.

### ~~OpenAI clients can't find `auto` model~~
`GET /v1/models` didn't list the virtual `auto` model, and `GET /v1/models/auto` returned 404. **Fixed**: `auto` is now listed in model endpoints and retrievable by ID.

## Fixed in 0.2.1

### ~~QUIC framing bug broke all peer inference~~
Unidirectional QUIC streams were incorrectly parsed as iOS-style length-prefixed frames. Large inference responses arriving in multiple packets were silently dropped, causing "returned no result" on every gateway request. **Fixed**: framing only applies to bidirectional streams.

### ~~No P2P discovery between peers~~
Peers only knew about the bootstrap — not each other. LAN nodes behind the same NAT had no way to connect directly. **Fixed**: bootstrap now broadcasts peer exchange messages with addresses and capabilities. Peers auto-connect on LAN.

### ~~Gateway streaming was fake~~
The public gateway returned the full inference response as a single SSE chunk, even when `stream: true` was set. Users waited for the entire response before seeing any text. **Fixed**: true token-by-token streaming over QUIC — the gateway yields SSE chunks as they arrive from peers.

## Security & Privacy

### Prompts are visible to seeder nodes
When you send a message on the public network, the seeder node that runs inference sees your full prompt. This is inherent to the distributed architecture — the node needs the prompt to generate a response.

**Mitigation**: The [Sensitive Data Guard](https://docs.mycellm.dev/config/privacy-guard/) scans for API keys, passwords, and PII before sending. Use on-device inference for sensitive content.

### Credit system is not Sybil-resistant
New node identities receive 100 seed credits. An attacker could generate unlimited credits by creating new identities. Receipt validation is enforced locally — the bootstrap does not verify receipt signatures server-side.

**Impact**: Low for beta. Credits have no monetary value. Future: require proof-of-work or reputation-based credit issuance.

### No TLS certificate pinning on QUIC
Both iOS and Python disable TLS certificate verification on QUIC connections. Identity is verified at the application layer via Ed25519-signed NodeHello. A MitM attacker could observe (but not forge) authenticated traffic.

**Future**: Pin the bootstrap's public key or implement TLS channel binding with NodeHello.

## Infrastructure

### Single bootstrap node
The public network has one bootstrap server at `bootstrap.mycellm.dev`. If it goes down, new nodes cannot join and the public gateway is unavailable. Existing P2P connections on LAN continue working.

**Future**: Multiple bootstrap servers with DHT-based discovery fallback.

### Fleet management is partially implemented
Fleet commands `node.status`, `model.list`, and `model.scope` work. `model.load`, `model.unload`, and `set_mode` are not yet implemented on the iOS app.

### Multi-IP connection churn
When peer exchange shares all of a peer's LAN addresses, other nodes attempt connections to each address. Invalid addresses (wrong interfaces, Tailscale IPs) connect then idle-timeout, creating log noise. The peer_manager deduplicates by peer_id for new connections, but stale connections still churn.

**Future**: Address scoring — prefer addresses that previously succeeded.

## iOS App

### Foreground-only operation
iOS suspends apps after ~30 seconds in background. The QUIC connection drops, and the node goes offline. It reconnects when the app returns to foreground. For always-on nodes, use a Mac/Linux server.

### iOS does not stream inference from remote peers
The iOS app sends `stream: true` to the local node's OpenAI-compatible API, but when inference is routed to a remote peer over QUIC, the response arrives as a single blob. True token-by-token streaming over QUIC works on the Python gateway but is not yet wired in the iOS QUIC client.

### TLSConfig stub
`generateSelfSignedIdentity()` returns nil — the QUIC client TLS identity is not set. Transport encryption still works (aioquic generates ephemeral certs), but there's no persistent client certificate.

### Model sharding not implemented
The About page mentions model sharding across GPUs. This is a roadmap item — currently one model per node, loaded entirely into memory.

## Protocol

### DHT discovery is optional
The Kademlia DHT is not tested at scale. Use `--no-dht` if you experience issues. The bootstrap peer exchange is the primary discovery mechanism for connecting peers.

### Hole punching not wired end-to-end
STUN NAT discovery and hole punch primitives are implemented, but the bootstrap does not coordinate hole punch signaling between peers. Symmetric NAT (common on home routers) cannot be traversed. Peers behind the same NAT discover each other via peer exchange and connect directly on LAN.

**Workaround**: Peers always maintain an outbound QUIC connection to the bootstrap. The bootstrap uses this bidirectional connection to relay inference requests — no inbound port required.
