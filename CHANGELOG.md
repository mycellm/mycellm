# Changelog

All notable changes to mycellm are documented here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses semantic-ish versioning (0.x.y while pre-1.0).

## [0.7.0] — 2026-08-15

### Added
- **Models can be installed from a URL or an MLX manifest, not just Hugging
  Face.** `POST /v1/node/models/download` previously accepted `repo_id` +
  `filename` and built a huggingface.co URL — so a model on an internal mirror,
  a private build, or anything an admin wanted to place across a fleet had no
  route in at all. Two new forms:

      {"url": "…", "sha256": "…", "filename": "…"}          single file
      {"name": "…", "files": [{path, url, sha256, size}, …]} MLX directory

  `sha256` is required on both, and only here. Every other download is checked
  against a hash the node looks up itself (HF publishes `lfs.oid`); a
  caller-supplied URL has no such attestation, so without a digest this would be
  the only way to place unverified weights on a node. Verification is not
  advisory on this path either — there is no second source to fall back on, so
  unverifiable means failed.

  The manifest form also closes an asymmetry: the Python node could not install
  an MLX model at all, while the iOS node has had directory installs since its
  build 16 — on a fleet where every node is Apple Silicon and MLX is the native
  format. The worker stages in `.staging/`, publishes by rename so an
  interrupted install never looks complete, and verifies each file as it lands
  so a bad shard fails before the next multi-gigabyte fetch starts.

### Security
- **The Hugging Face token is no longer sent to third-party hosts.**
  `_hf_headers()` was applied to every download request regardless of
  destination, which would have handed the node's HF credential to whatever
  host an admin named in a URL — and to anything it redirected to.

### Fixed
- **GGUF embedding models are tagged `embedding` instead of `chat`.**
  `derive_tags()` keyed on the substring "embed", which catches models that say
  so (`nomic-embed-text`, `text-embedding-3`) but misses every family that names
  its architecture instead — `all-MiniLM-L6-v2`, `bge-small-en`, `gte-base`,
  `multilingual-e5-large`, `mxbai`, `mpnet`. That was survivable while
  embeddings only arrived through `mlx-embeddings`, whose backend type is
  authoritative and overrides the name, but a GGUF embedding model loaded into
  llama.cpp got `["chat"]` — so `/v1/embeddings` wouldn't resolve to it and
  auto-routing would hand it a chat request it cannot serve. The family list now
  lives in `_EMBEDDING_FAMILIES` with a shared `is_embedding_model_name()`
  helper, mirrored by `EmbeddingModels.families` in the iOS node — which has no
  backend signal to fall back on, so the name is its whole decision. Keep the
  two lists in step: a model must not be an embedding model on one node and a
  chat model on another.
- **Dashboard no longer leaks the local admin api_key to remote node
  addresses.** `remote()` (used by the Model tab's device switcher) called
  the target node's origin directly with the browser's Authorization
  header attached; a fleet peer could self-report an attacker-controlled
  `api_addr` and harvest the admin's credential. It now proxies through a
  new same-origin route (`POST /v1/node/proxy`), which relays only to
  addresses already approved in the node registry and never forwards the
  local api_key outbound.
- **Dashboard SSE connections no longer put the API key in the URL.** The
  activity and log streams now open via `fetch` with an
  `Authorization: Bearer …` header (same accessor `ApiClient` already uses
  for regular requests), instead of an `EventSource` URL carrying
  `?api_key=…` — which could land in browser history, proxy logs, or
  `Referer` headers. The server-side `api_key` query param is unchanged for
  other clients.
- **Model resolution is now capability-aware, so embeddings and chat stop
  crossing wires.** `resolve_model_name("")` picked whichever model happened
  to load first, regardless of what it could actually do. `/v1/embeddings`
  now prefers a loaded embedding-capable model (derived from
  `ModelCapability`/backend type, e.g. `mlx-embeddings`, plus the existing
  name heuristics) even when it isn't first in load order; empty/"auto" chat
  requests now exclude embedding-only candidates instead of risking a crash
  in the backend's chat guard when no other model is loaded. Explicit model
  names and the "embedding" tag still resolve exactly as before.

## [0.6.3] — 2026-07-30

### Added
- **Downloads are verified against Hugging Face's published content hashes.**
  Both download paths (node API + `hf:` model specs) stream to a `.part` file,
  then check the raw sha256 (LFS weights) or git blob sha1 (small files) from
  the repo tree listing before renaming into place. A mismatch deletes the
  file and fails the download; when the tree API is unreachable the download
  completes as `unverified`. Download progress gains a `verified` field.
- **Exact streaming usage.** `/v1/chat/completions` honors
  `stream_options: {"include_usage": true}` with a trailing empty-choices
  usage chunk carrying real backend token counts (never fabricated). llama.cpp
  usage was previously hardcoded 0/0 — now a tokenized-prompt estimate plus
  per-delta completion count. `/v1/models` entries expose the effective
  (post-preflight-clamp) `context_length` so coding agents can auto-compact
  deterministically.
- **Bounded KV cache.** Per-model `max_kv_size` load option plumbs mlx-lm's
  rotating KV cache through the batched backend; preflight estimates KV at
  `min(ctx_len, max_kv_size)` and clamps the bound (not the context window)
  when memory is tight. Persisted in `model_configs.json`.

### Fixed
- **Streamed output can no longer leak stop-string prefixes.** All MLX
  streaming paths withhold a text tail that could still become a stop string
  until the next token disambiguates it, flushing it with the terminal chunk
  if generation ends first. Previously a multi-token stop marker completing
  across chunk boundaries had already streamed its prefix to the client.
- **Truncated tool calls are salvaged.** A `<tool_call>` envelope cut off by
  `max_tokens` (mid-closing-tag or right after the JSON body) now parses into
  a proper `tool_calls` response instead of surfacing as broken text.
- MLX non-streaming completion token counts are measured after stop
  truncation (previously counted the stop marker).

- **Runtime memory-pressure watcher.** Kernel WARN clears the MLX Metal
  cache; CRITICAL evicts idle local models (newest-first survivor, escalating
  to all models on consecutive criticals) before the OOM killer hard-kills
  the node. Evicted models stay enabled and restore on the next boot.
  `MYCELLM_MEMORY_WATCH_*`, default on.
- **Empirical preflight calibration.** `scripts/bench_context_calibration.py`
  sweeps real context lengths, linear-fits measured vs predicted KV memory,
  and (with `--apply`) writes a per-node `kv_factor` the preflight estimator
  picks up. Hokulea M1 validation: analytical model confirmed (slope 0.87 +
  0.66GB constant transient inside the 1GB overhead reserve).
- **Configurable prefill chunking.** `MYCELLM_MLX_PREFILL_STEP_SIZE` plumbs
  mlx-lm's prefill_step_size through both MLX backends so memory-tight nodes
  can shrink the prefill transient.

- **Native MLX embeddings.** New `mlx-embeddings` backend serves
  `/v1/embeddings` on Apple Silicon (MiniLM/BERT/XLM-R) with length-grouped
  batching — short texts never pay a long text's padding.
- **Opt-in MLX speculative decoding.** Per-model `draft_model` /
  `num_draft_tokens` load options wire mlx-lm draft decoding into the
  single-request path. Measured on M1: slower than baseline for servable
  pairings and unsupported for Qwen3.5 hybrids (see NOTES.md) — shipped off
  by default for future hardware/MTP support.

### Acknowledgements
- Several features in this release were inspired by the engineering
  write-ups in [oMLX](https://github.com/jundot/omlx) v0.5.4rc1 — the
  context calibration benchmark, memory-pressure recovery, stop-sequence
  and tool-call streaming fixes, exact usage reporting, and embedding
  batch grouping. Implementations are original (see NOTICE).
- The verified-download pipeline was inspired by
  [turbo-fieldfare](https://github.com/drumih/turbo-fieldfare)'s
  hash-verified streaming installer.
- Native MLX embeddings are powered by
  [mlx-embeddings](https://github.com/Blaizzy/mlx-embeddings) by
  Prince Canuma.

### Changed
- Dependency floors raised: `llama-cpp-python>=0.3.34`, `mlx-vlm>=0.6.8`,
  new optional `mlx-embeddings>=0.1.0`
  (validated with mlx 0.32.0 + mlx-lm 0.31.3 on M1).

## [0.6.2] — 2026-07-02

### Added
- **Join keys are now enforced on hosted networks.** Set a key with
  `mycellm network host <name> --join-key K` or
  `mycellm network set-key <id-prefix> K` and peers must present it in
  their `NodeHello` (new `join_keys` map) to be accepted into that network
  — claims without the right key are dropped while the connection stays up
  for the peer's other networks. This closes the hole where any
  valid-cert peer could claim a private network id and gain its routing
  and home-model visibility. Networks without a key, and clients that
  predate join keys, behave exactly as before. Peer trust resolution now
  also uses the gate-filtered network list rather than raw hello claims.
  Members store their key on the membership (`join_key`) and present it
  automatically when dialing.

### Fixed
- **Menu bar monitor authenticates its polls.** With an API key set, the
  monitor's keyless status polls were rejected (401) and the node showed as
  offline (gray) despite being healthy; the polls also fed the API's
  unauthenticated-caller tracking. The monitor now sends `X-API-Key` from
  the node's config.
- **A valid API key can no longer be locked out.** The per-IP brute-force
  lockout was checked *before* key validation, so an unauthenticated process
  sharing the caller's IP (e.g. a keyless localhost poller) could lock
  127.0.0.1 and make the node's own authenticated health checks 429 — the
  external watchdog then restart-looped a perfectly healthy node. Key
  validation now runs first (constant-time compare); the lockout applies to
  unauthenticated callers only.

## [0.6.1] — 2026-07-02

### Added
- **Multi-network hosting — the coordinator is now part of the node.** One
  `mycellm serve` process can *host* private networks alongside its home
  network and public participation, all on the standard QUIC port. Hosted
  network identities live in `federation/hosted/`, are advertised in the
  node's `NodeHello` automatically, and members are accepted exactly like
  before (valid device cert + declared network id). This replaces the
  separate private-coordinator process previously needed to run a private
  fleet network.
- **`mycellm network` CLI**: `list` (home + hosted + joined), `host <name>
  [--public] [--join-key] [--import PATH]` (create a hosted network, or
  import an existing `network.json` preserving its network_id — the
  coordinator migration path), `invite [--network <id-prefix>] [--max-uses]
  [--expires-hours]` (signed invite for any hosted network), and
  `drop <id-prefix>`. The running node picks up changes on restart.
- **`NetworkIdentity.join_key`** (reserved): a per-network join secret is
  stored and distributed with the identity but not yet enforced at the
  handshake — enforcement lands together with the iOS join-key UI.

### Changed
- **Home-scoped models are visible to networks the node hosts.** Members you
  invite to a network you host can use your local models; the public network
  and unrelated networks still cannot. Explicit `networks`/`public` scopes
  are unchanged.
- **Peer trust honors hosted networks**: a member of a network this node
  hosts inherits that network's `trust_level`.

## [0.6.0] — 2026-07-01

### Added
- **Multi-network membership (P0).** A node can now belong to multiple
  networks at once and routing respects network isolation end-to-end:
  `NodeHello` carries the node's `network_ids`, the peer registry stores
  them, and `peers_for_model` only offers peers that share at least one
  network with the requester. Peers with no declared networks are treated
  as public/legacy and stay eligible, so single-network deployments and
  un-upgraded peers keep working unchanged.
- **Per-network model scoping.** `ModelCapability` gains `scope`
  (`home` | `networks` | `public`) and `visible_networks`, so a model can
  be advertised to the home network only, to an explicit list of networks,
  or to everyone (`models_visible_to_network`).
- **`max_completion_tokens`** accepted on `/v1/chat/completions` as an
  alias for `max_tokens` (OpenAI compatibility; newer SDKs send only the
  new name).

### Changed
- **KV-aware load preflight (v2) is now the default** on MLX backends
  (`preflight_kv_aware=true`, `preflight_action=clamp`). Estimates
  weights + KV(ctx × slots) + overhead against the Metal working-set
  ceiling and clamps `ctx_len` to the largest context that fits (never
  below `preflight_min_ctx_len`) instead of loading into a likely OOM.
  Validated against measured MLX memory on fleet nodes (flag-enabled in
  production since 2026-06-28); the legacy available-RAM check remains as
  the fallback and as the sole check for non-MLX backends. Set
  `MYCELLM_PREFLIGHT_KV_AWARE=false` to opt out.

### Fixed
- **Fleet commands reach iOS nodes.** The fleet-command relay sends on a
  *bidirectional* QUIC stream (`send_and_wait(..., bidirectional=True)`);
  iOS Network.framework's NWMultiplexGroup only surfaces peer-initiated
  bidirectional streams, so unidirectional sends were never delivered.
  The request stream's receive side is closed after the reply so half-open
  streams don't accumulate against MAX_STREAMS. (Previously deployed as a
  hot-patch on the private coordinator; now upstream.)
- **Local address discovery no longer blocks the event loop.**
  `_discover_local_addresses` (getaddrinfo) runs via `to_thread`; on hosts
  with slow/misconfigured DNS it could stall the node for seconds at
  startup and on network self-heal.
- **Preflight weight sizing for HF repo-ids.** `memory_estimate` resolves
  weights from the local directory *or* the HuggingFace cache — previously
  a repo-id model path reported `weights 0.0GB` and under-counted the peak
  estimate.

## [0.5.3] — 2026-06-24

### Fixed
- **Residual UDP file-descriptor leak that wedged nodes.** The 0.5.2 fix
  closed the dialed datagram endpoint on `dial_peer`'s *failure* path, but
  `MycellmQuicProtocol.close()` never closed the underlying UDP socket on
  the *success/teardown* path — it closed the QUIC connection and sent
  `CONNECTION_CLOSE`, but aioquic does not release the datagram transport
  for you. So every client-dialed connection leaked one ephemeral `*:port`
  UDP fd whenever the caller closed it (handshake rejects, reconnect
  teardown, peer churn) — seen live as ~1 leaked UDP fd/min climbing to
  thousands (aurora) / 20k+ (hokulea), eventually `EMFILE` so the event
  loop can no longer accept and the API goes deaf while the process still
  looks "running". Now `dial_peer` tags the dialed protocol with
  `_owned_transport` and `close()` releases it. Server-accepted protocols
  share the single server socket and never set `_owned_transport`, so the
  server socket is untouched.

## [0.5.2] — 2026-06-21

### Added
- **Heartbeat watchdog + crash-loop guard + network self-heal.** A node
  that wedges (event loop stalled, or file descriptors near the rlimit)
  now turns the wedge into a non-zero process exit (`os._exit(70)`) so the
  existing supervisor restart policy (launchd KeepAlive / systemd
  `Restart=on-failure` / docker `restart:`) actually relaunches it — a
  wedge never *exited* before, so the policy never fired and the node
  looked alive while serving nothing. The watchdog runs on an OS thread
  (so it ticks even while the loop is blocked), with `defer()` to suppress
  the stall check around slow model loads. A per-model crash-loop guard
  bumps a persisted attempt counter before each load and quarantines a
  model that fails to load `model_max_restore_attempts` (3) times, so
  boot-restore stops reloading an OOM-ing model. A network self-heal loop
  detects local/public address changes and forces an immediate
  re-announce + NAT re-probe, and dials every joined federation network's
  bootstrap. All thresholds are env-overridable (`MYCELLM_*`). The systemd
  template now sets `StartLimitIntervalSec=0` so the restart limiter does
  not defeat self-heal.

### Fixed
- **UDP file-descriptor leak that wedged the node API (root cause).**
  `transport/quic.py::dial_peer` created a datagram endpoint and then
  awaited the QUIC handshake; on handshake timeout/cancel it raised
  *without closing the transport*, and the caller never received the
  protocol handle to close it either. A seeder's reconnect loop leaked one
  UDP socket per failed dial until `EMFILE`, after which the event loop
  could no longer accept connections — the process stayed listening but
  answered nothing ("looks alive, serves nothing"). Fixed by closing the
  protocol + transport on any failure path before re-raising.
- **Resilient auto-routing + accurate live node counts.** Auto model
  resolution now degrades gracefully across fleet-announced nodes and
  reports accurate live node counts (route failover covered by new tests).
- **Model-load preflight no longer blocks the event loop.** The RAM
  preflight (`vm_stat` subprocess + on-disk size walks) moved off the loop
  into a single `asyncio.to_thread`; localhost `/v1/node/status` probes
  during a real model reload now run at ~2 ms median / 4 ms p95 (was up to
  seconds), and the restore watchdog defer dropped 600s → 180s.
- **SQLAlchemy pool exhaustion wedging the node API** (seen live: after
  ~3h of menu bar status polls a node hit "QueuePool limit of size 5
  overflow 10 reached" on every DB-touching endpoint while background
  tasks kept running, so it looked alive but served nothing). Two-part
  fix: **(1)** SQLite engines now use `NullPool` — a connection per
  checkout, nothing to exhaust; SQLite connections are a cheap file open,
  and the pool was vulnerable to orphaned checkouts when a client timeout
  cancelled a request mid-greenlet. **(2)** `/v1/node/status` credits are
  served from a 30s read-through cache (`get_account_cached`) instead of
  hitting the ledger on every poll; the refresh is shielded from caller
  cancellation, refresh failures serve the last snapshot, and
  credit/debit invalidate the cache so balances stay fresh after
  settlements.

## [0.5.1] — 2026-06-10

### Added
- **Menu bar: monochrome icon option** ("Monochrome Icon" in the dropdown,
  persisted to `~/.config/mycellm/menubar.json`). A black-and-alpha
  template rendering of the 8-bit mushroom that macOS tints to match the
  menu bar (light and dark mode), for people who like their icons uniform.
  Cap spots are punched fully transparent; the stem sits at ~59% alpha.
  Node state is told by opacity instead of color: full when healthy, soft
  when reachable with nothing loaded, a full/soft pulse during inference,
  dim when offline. The renderer that builds all menu bar icons from the
  brand SVGs is now checked in (`scripts/render_menubar_icons.py`).

### Fixed
- **Menu bar monitor no longer shows a "Python" icon in the Dock.** The
  monitor runs the framework `Python.app` binary, which macOS treats as a
  regular app; it now declares itself an accessory
  (`NSApplicationActivationPolicyAccessory`) so it lives only in the menu
  bar, like other status-item apps.
- **Menu bar dropdown showed `v?` instead of the node version** —
  `/v1/node/status` never included `version`, so the uptime line couldn't
  know it. The node now reports `version` in status, and the menu bar also
  falls back to `/v1/node/version` (cached, attempts capped) so it shows
  the right version against pre-0.5.1 nodes too.
- **Pre-load RAM check now compares against *available* memory on macOS**
  (was `hw.memsize` — total physical RAM — so a 30GB load on a 64GB box with
  50GB in use passed the check silently). Available memory comes from
  `vm_stat` (free + inactive + purgeable pages), parsed as text to stay
  independent of Mach host_statistics struct layouts. The estimate is also
  KV-aware now: weights ×1.1 plus a KV-cache term for the requested
  `ctx_len` — exact fp16 K+V from `config.json` attention geometry for MLX
  dirs, a size-proportional heuristic for GGUF. Still warning-only
  ("Loading anyway"); the warning just stops lying. Inspired by reviewing
  oMLX 0.4.3's Memory Guard fixes (jundot/omlx#1763).

## [0.5.0] — 2026-06-10

### Added
- **macOS menu bar monitor** (`mycellm menubar`, requires the `menubar`
  extra: `pip install "mycellm[menubar]"`). The brand mushroom lives in the
  menu bar and tells the node's story by color: Spore Green when healthy and
  idle, cycling through the Protocol Palette (red → blue → gold → purple →
  green) while inference is in flight, Ledger Gold when reachable with no
  models loaded, gray when the node is offline. The dropdown shows status,
  a 10-minute time graph (tok/s line over active-inference bars, drawn
  natively via AppKit), loaded models with quant/ctx/backend details, peers,
  credits, peer ID (click to reveal, click again to copy), version/uptime/
  mode, and hardware; links to the local web dashboard (management stays
  there); and offers Launch-at-Login (per-user LaunchAgent) and Hide. The
  monitor is a separate process — hiding or quitting it never affects the
  node.
- **OpenAI-compatible embeddings** (`POST /v1/embeddings`). Accepts `input`
  as a string or list of strings and returns the standard
  `{object: "list", data: [{object: "embedding", index, embedding}], model,
  usage}` shape. Served by the llama.cpp backend via `create_embedding` —
  the model must be loaded with the new `"embedding": true` load option
  (llama.cpp only produces embeddings when the context is created with the
  flag; without it the API returns a 400 telling you to reload with it) —
  and by the OpenAI-compat backend, which relays to the upstream
  `/embeddings` endpoint. `InferenceManager.embed()` queues on the same
  per-model Lock/Semaphore as generation. Backends without embedding support
  (MLX text/batched/VLM) raise the new typed `EmbeddingsNotSupportedError`,
  which the API maps to a 400 with an actionable message; token-array
  `input` is rejected with a 400.

## [0.4.2] — 2026-06-09

### Fixed
- **Chat terminator tokens leaking into MLX completions** (e.g. a literal
  `<|im_end|>` at the end of Qwen2.5-Coder responses). Some model configs
  don't register their chat template's turn terminator as `eos_token_id`
  (mlx-community Qwen2.5-Coder ships `eos=<|endoftext|>` while the template
  ends turns with `<|im_end|>`), and `mlx_lm`/`mlx-vlm` only stop on
  registered eos ids, so the marker detokenized straight into the output.
  All three MLX backends (mlx, mlx-batched, mlx-vlm) now treat well-known
  chat terminators (`<|im_end|>`, `<|eot_id|>`, `<|end|>`, `<|endoftext|>`)
  plus the tokenizer's declared `eos_token` as implicit stop strings, and
  stop-string truncation now cuts at the earliest match when several stops
  occur. `</s>` is deliberately not implicit (legitimate in generated HTML);
  it is honored only when the tokenizer declares it as eos.

## [0.4.1] — 2026-06-08

### Fixed
- **MLX node crash loop** — `abort()` in
  `mlx::core::detail::CompilerCache::~CompilerCache()`. MLX keeps a
  thread-local compiler cache whose destructor runs at pthread exit; the MLX
  and MLX-VLM backends spawned a fresh thread per streamed request (and used
  the default `to_thread` pool), so every streamed inference eventually tore
  down an MLX-touching thread and aborted the whole process (a ~hourly crash
  loop on the vision node under load). All MLX work (load, generate, stream)
  now runs on a single persistent `ThreadPoolExecutor(max_workers=1)` per
  backend, so no MLX thread is ever destroyed during operation. MLX requests
  serialize on that worker (correct for single-stream MLX/Metal).

## [0.4.0] — 2026-06-07

### Added
- **MLX vision-language (multimodal) inference** for Apple Silicon. New
  `MLXVLMBackend` (`mlx-vlm`) loads vision models (Qwen2.5-VL, Gemma 3, …);
  `InferenceManager` auto-routes a `backend="mlx"` model whose `config.json`
  declares a vision tower to it. Single-stream (mlx-vlm has no batched path).
- **Multimodal chat content** end-to-end: `/v1/chat/completions` `content`
  now accepts OpenAI content-part arrays (`text` + `image_url`) as well as a
  plain string. Text backends flatten images out via the new
  `content_to_text` / `flatten_message_content` helpers.
- **Vision model catalog entries** (Qwen2.5-VL 7B/3B, Gemma 3 4B) behind a
  new `modalities` family field; the recommender maps vision MLX variants to
  the `mlx-vlm` backend.
- **`scripts/bench_solo_fastpath.py`** — benchmarks the batched-MLX solo
  decode path (single-stream vs BatchGenerator-at-1).
- **Tracker-authoritative credit system.** A network's source-of-truth node
  (the public prime) keeps a per-network ledger (`NetworkAccount`) settled from
  **consumer co-signed** receipts: the seeder signs, the consumer co-signs the
  same canonical bytes, and the tracker verifies both, replay/rate-limits, then
  debits consumer / credits seeder. New `POST /v1/public/receipts` (ingest) and
  `GET /v1/public/credits/{peer_id}` (authoritative balance + served count).
  Receipts now also flow for **streaming** completions (previously only
  non-streamed served earned). A tracker self-designates treasury credit on
  networks it is the authority for.

### Changed
- **Pinned `mlx-lm>=0.31.3`** (was `>=0.31.0`) — avoids the 0.31.2 cache
  broadcast regression (mlx-lm #1139) that surfaces under continuous batching.
- `BatchedMLXBackend` gained an **opt-in single-stream solo fast path**
  (`solo_fast_path`, default **off** — benchmarked at only ~1.5% over
  BatchGenerator-at-1 on mlx-lm 0.31.3, so not worth the default complexity).

### Fixed
- Public gateway no longer mishandles multimodal `content` arrays — the length
  check and sensitive-data scan operate on extracted text instead of assuming
  a string (previously would have raised on a list payload).

## [0.3.0] — 2026-05-20

### Added
- **MLX inference backend** for Apple Silicon (M-series). Uses Apple's MLX
  framework via `mlx-lm`; typically faster than llama.cpp's Metal path for
  the same quantization and uses unified memory more efficiently.
- **OpenAI tool/function calling support** across the entire stack:
  llama.cpp backend, MLX backend, openai_compat relay backend, and QUIC
  peer routing all carry `tools` and `tool_choice` end-to-end.
- **`/v1/models/capabilities`** rich-metadata endpoint (param count,
  quantization, context, throughput, queue depth, grammar support, source).
- **Grammar pass-through** to llama.cpp via GBNF (`grammar` field on the
  chat-completions request).
- **Request priority + group cancellation** for speculative work
  (`priority`, `request_group`, `DELETE /v1/node/requests/group/{id}`).
- **Platform-aware model recommender** with MLX search support.
- **Backend-aware router scoring** (MLX × 1.30 on Apple Silicon).
- **`MYCELLM_DEFAULT_CTX_LEN`** setting (default 32768) replaces the prior
  hard-coded 4096 fallback in auto-load + restore paths. Explicit `ctx_len`
  in load requests still wins.
- **Reasoning ("thinking") suppression** for chat completions. New
  `reasoning` block on `/v1/chat/completions` (OpenAI o-series style):
  - `{"exclude": true}` strips `<think>...</think>` blocks and asks the
    chat template to suppress thinking on Qwen3-family models.
  - `{"exclude": false}` includes reasoning in the response.
  - Omitted falls back to `MYCELLM_HIDE_REASONING_BY_DEFAULT` (public
    bootstraps should set this so demo visitors see clean answers).
  - Non-streaming responses surface a `reasoning_content` field on the
    assistant message; streaming responses emit `delta.reasoning_content`
    events alongside `delta.content`, mirroring OpenAI's spec.
  - Per-model `supports_thinking` advertised on `/v1/models/capabilities`
    so clients can gate UI affordances.
  - Recognised model families today: Qwen3 hybrid, Qwen3-Coder/Instruct,
    DeepSeek-R1, GLM-4.x-Thinking, Gemini 2.0 Thinking, GPT-O1/O3/O4.
    New families add one line in `inference/reasoning_dialects.py`.
- **GitHub Actions CI** for lint + tests on push/PR, contributed by
  Nathan Pierce ([@NorseGaud](https://github.com/NorseGaud)).
- **Ruff** Python linting + **ESLint** JavaScript/TypeScript linting,
  contributed by Nathan Pierce ([@NorseGaud](https://github.com/NorseGaud)).
- **`web/src/lib/logClientError.ts`** structured client-error logger,
  contributed by Nathan Pierce ([@NorseGaud](https://github.com/NorseGaud)).

### Fixed
- `generate()` on both llamacpp and openai_compat backends now delegates to
  `generate_stream()` to avoid:
  - `llama_decode` errors on 32B models when KV state carries from a prior
    sequence
  - httpx non-streaming timeouts on slow OpenAI-compat upstreams (a 32B
    model that needs ~110s for a first token loses against the 120s default)
- Multi-tool requests where the model emits `<tool_call>` XML text or
  ` ```json` fences instead of structured `tool_calls` JSON: parser
  normalises both formats and reattaches as proper OpenAI `tool_calls`.
- Single-tool calls now coerce `tool_choice` to the named function so Qwen
  models reliably return JSON tool_calls instead of inline text.
- Streaming requests with `tools` automatically fall back to non-streaming
  (Qwen llama-cpp's streaming + tool_calls path is unstable).
- `load_model` honours the `ctx_len` kwarg (previously always defaulted to
  `n_ctx=4096`).
- `relay:` prefix normalised at API display boundaries.
- Peer resilience: tolerate transient ping failures and restore
  `DISCONNECTED` peers.
- Auto-load MLX models can take a Hugging Face repo id directly
  (`mlx-community/Qwen3-1.7B-4bit`) instead of requiring a local path.
- Streaming routing path uses `ModelResolver` for `auto` model selection
  instead of falling back to the first-loaded model.
- Fleet routing preserves `tool_calls` across the QUIC relay; streaming
  first-chunk handling corrected.
- Integration + e2e tests that asserted `/v1/models` returns an empty
  list now correctly assert the virtual `auto` entry is present
  (Nathan Pierce, [@NorseGaud](https://github.com/NorseGaud)).
- Cleaned up unused imports / variables and one-line if-statements
  across ~50 files via Ruff (Nathan Pierce,
  [@NorseGaud](https://github.com/NorseGaud)).
- Empty JS catch blocks now route through `logClientError()`
  (Nathan Pierce, [@NorseGaud](https://github.com/NorseGaud)).

### Acknowledgements
- **Nathan Pierce** ([@NorseGaud](https://github.com/NorseGaud)) for
  [PR #1](https://github.com/mycellm/mycellm/pull/1): GitHub Actions
  CI, Ruff + ESLint setup, ~50 files of lint cleanup, and the
  integration-test fix for the virtual `auto` model.
- The tool-calling reliability work was empirically validated against
  scenarios published in [antoinezambelli/forge](https://github.com/antoinezambelli/forge)
  (MIT). Their evaluation harness helped surface and prioritise the bugs
  fixed in this release.

## [0.2.5] — 2026-04-09

### Added
- Public bootstrap router (mycel_prime on docker-box). NAT-tolerant
  routing, auth model, Caddy SSE config.

## Earlier

For releases prior to 0.2.5, see commit history (`git log`).
