# mycellm — Goals

Always lowercase **mycellm**. (`CLAUDE.md`'s header still reads "Mycellm" — a
known wart, not a licence to copy it.)

## What it is
Peer-to-peer distributed LLM inference — "BitTorrent for LLMs." Pools GPUs across
the internet into one OpenAI-compatible fabric: seed to earn credits, spend
credits to consume. No blockchain, no tokens, no cloud vendor.

## What winning looks like
`pip install mycellm` + `mycellm serve` joins a permissionless network; a
heterogeneous fleet (Linux/CUDA, Apple Silicon, iOS/iPad) seeds as first-class
QUIC peers from behind home NAT; api.mycellm.dev never silently empties;
`model: "auto"` is drop-in for real tools (Claude Code, aider).

## Current priorities
- **MCP server at `mcp.mycellm.dev`**, with the `.ai` name as an alias/redirect
  onto it. Greenfield — no MCP code, dep or route exists in the repo today
  (`grep -ri mcp src/ pyproject.toml` is empty).
- Perf: prove MLX (mxfp4/mxfp8) vs GGUF; bank the continuous-batching gains; iOS
  KV-cache-across-turns (prototype, not merged).
- Branch hygiene: ~20 stale `agent/*` (`.drydock/branch-triage.md`).

Already on `main`, don't re-open: streaming cross-model failover (`687df45`),
stale fleet entries excluded from routing (`eb661f0`), launchd fd limit 65536
(`12b5e4b`), announce backoff (`ea96509`).

## Non-goals / hard no
No cloud API fallback (`mycellm/auto` is fully local). No proprietary/closed
runtime dependency — decline with "mycellm is Apache-2 end-to-end, no proprietary
runtime dependencies" (check `mycellm-vendor-outreach` memory first). No
blockchain/crypto. Not a single-host GPU-density play.
