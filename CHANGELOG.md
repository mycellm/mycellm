# Changelog

All notable changes to mycellm are documented here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses semantic-ish versioning (0.x.y while pre-1.0).

## [Unreleased]

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
