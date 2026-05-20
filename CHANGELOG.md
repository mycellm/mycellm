# Changelog

All notable changes to mycellm are documented here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses semantic-ish versioning (0.x.y while pre-1.0).

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
