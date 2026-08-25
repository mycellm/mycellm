"""Mycellm configuration via Pydantic Settings with XDG path support."""

from __future__ import annotations

import hashlib
import os
import platform
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Word lists for generating memorable node names
_ADJECTIVES = [
    "amber", "bold", "calm", "dark", "eager", "fast", "gold", "hazy",
    "iron", "keen", "lime", "mild", "nova", "opal", "peak", "quick",
    "rare", "sage", "teal", "vast", "warm", "zinc", "blue", "ruby",
    "jade", "onyx", "pure", "deep", "high", "soft", "wild", "cool",
]
_NOUNS = [
    "mycel", "spore", "grove", "nexus", "bloom", "coral", "drift", "ember",
    "frost", "glyph", "haven", "knoll", "lumen", "marsh", "north", "orbit",
    "prism", "quill", "ridge", "shard", "terra", "umbra", "vault", "wisp",
    "cedar", "delta", "flint", "helix", "brook", "crest", "dusk", "fern",
]


def _generate_node_name() -> str:
    """Generate a memorable node name from hostname, falling back to a hash-derived name."""
    hostname = platform.node().split(".")[0].lower().strip()

    # If hostname is usable (not generic), use it
    generic = {"localhost", "default", "unknown", "computer", "pc", "mac", ""}
    if hostname and hostname not in generic and not hostname.startswith("ip-"):
        return hostname

    # Generate a deterministic name from machine ID
    seed = hostname + os.getenv("USER", "") + str(os.getpid())
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    adj = _ADJECTIVES[h % len(_ADJECTIVES)]
    noun = _NOUNS[(h >> 8) % len(_NOUNS)]
    return f"{adj}-{noun}"


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _default_data_dir() -> Path:
    return _xdg_data_home() / "mycellm"


def _default_config_dir() -> Path:
    return _xdg_config_home() / "mycellm"


class MycellmSettings(BaseSettings):
    """Core settings for a mycellm node."""

    model_config = SettingsConfigDict(
        env_prefix="MYCELLM_",
        env_file=(".env", str(_default_config_dir() / ".env")),
        env_file_encoding="utf-8",
    )

    # Paths
    data_dir: Path = Field(default_factory=_default_data_dir)
    config_dir: Path = Field(default_factory=_default_config_dir)

    # Network
    api_host: str = "127.0.0.1"
    api_port: int = 8420
    quic_host: str = "127.0.0.1"
    quic_port: int = 8421
    dht_port: int = 8422

    # NAT traversal
    external_host: str = ""  # Explicit public IP override

    # QUIC tuning
    quic_idle_timeout: float = 60.0
    quic_connect_timeout: float = 10.0

    # Peer exchange — how often to broadcast connected peer list (seconds)
    peer_exchange_interval: int = 90

    # Node identity
    node_name: str = Field(default_factory=_generate_node_name)

    # Inference
    model_dir: Optional[Path] = None
    max_concurrent_inferences: int = 2
    default_ctx_len: int = 32768  # Default context window for loaded models (MYCELLM_DEFAULT_CTX_LEN)
    # --- KV-aware load preflight (Apple Silicon / MLX). When enabled, model-load
    # preflight estimates weights + KV(ctx x batch) against the Metal working-set
    # ceiling instead of the legacy on-disk-vs-total-RAM check. Validated against
    # measured MLX memory. See inference/memory_estimate.py. Default on since
    # 0.6.0 (clamp), matching what the fleet ran flag-enabled since 2026-06-28;
    # set MYCELLM_PREFLIGHT_KV_AWARE=false to fall back to the legacy check.
    preflight_kv_aware: bool = True         # MYCELLM_PREFLIGHT_KV_AWARE
    preflight_action: str = "clamp"         # warn | clamp | reject (MYCELLM_PREFLIGHT_ACTION)
    preflight_safety_fraction: float = 0.90  # usable fraction of the Metal ceiling
    preflight_overhead_gb: float = 1.0       # reserve for prefill transient + MLX pool/heap
    preflight_min_ctx_len: int = 2048        # never clamp below this
    # When true, /v1/chat/completions requests with no explicit `reasoning` block
    # default to {"exclude": true} — strip <think>...</think> from responses and
    # ask the chat template to suppress thinking on Qwen3-family models. The
    # public bootstrap demo sets this so visitors see clean answers; self-hosted
    # nodes default to false so devs see the full model output.
    hide_reasoning_by_default: bool = False  # MYCELLM_HIDE_REASONING_BY_DEFAULT
    flash_attn: bool = True  # Metal/CUDA optimized attention kernel
    # MLX continuous batching: when true (default), models configured with
    # backend="mlx" load via the BatchedMLXBackend (mlx-lm BatchGenerator), so a
    # seeder serves concurrent requests in one batch instead of one-at-a-time.
    # Set false (MYCELLM_MLX_CONTINUOUS_BATCHING=false) to force the legacy
    # single-stream MLX backend.
    mlx_continuous_batching: bool = True  # MYCELLM_MLX_CONTINUOUS_BATCHING
    # Runtime memory-pressure watcher (macOS): on kernel WARN clear the Metal
    # cache; on CRITICAL evict idle local models (newest-first survivor) before
    # the OOM killer hard-kills the process. Evicted models stay enabled and
    # restore on restart. (MYCELLM_MEMORY_WATCH_*)
    memory_watch_enabled: bool = True
    memory_watch_interval: float = 10.0
    memory_watch_evict: bool = True
    memory_watch_idle_seconds: float = 60.0
    # Prefill chunk size (tokens) for MLX generation. 0 = mlx-lm's default
    # (2048). Long prompts are prefilled in chunks with the Metal cache cleared
    # between them; the prefill memory transient scales with this, so
    # memory-tight nodes can lower it (e.g. 512) to trade prefill speed for
    # headroom. (MYCELLM_MLX_PREFILL_STEP_SIZE)
    mlx_prefill_step_size: int = 0
    kv_cache_quant: str = "q8_0"  # KV cache quantization: "none", "q8_0", "q4_0" (legacy, use k/v below)
    kv_cache_quant_k: str = ""  # Key cache quantization (default: use kv_cache_quant)
    kv_cache_quant_v: str = ""  # Value cache quantization (default: q4_0 for asymmetric)
    prompt_lookup: bool = False  # Enable LlamaPromptLookupDecoding for code-heavy generation
    n_threads: int = 0  # 0 = auto-detect (p-cores on Apple Silicon, physical cores on Linux)
    draft_model_path: str = ""  # Path to a small GGUF model for speculative decoding
    draft_pred_tokens: int = 8  # Number of tokens the draft model predicts per step

    # Relay backends — comma-separated OpenAI-compatible API endpoints
    # Format: "http://ipad.lan:8080,http://ollama.lan:11434"
    # Models from these endpoints are auto-discovered and announced to the network.
    relay_backends: str = ""

    # Bootstrap peers (comma-separated host:port)
    bootstrap_peers: str = ""

    # Database URL — optional override (MYCELLM_DB_URL env var)
    # Default: SQLite at data_dir/mycellm.db
    # PostgreSQL: "postgresql+asyncpg://user:pass@host/dbname"
    db_url: str = ""

    # Logging
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # HuggingFace token (MYCELLM_HF_TOKEN env var)
    # Unlocks gated models, higher rate limits, faster downloads
    hf_token: str = ""

    # Security — optional API key (MYCELLM_API_KEY env var)
    # When set, admin/node endpoints require Authorization: Bearer <key>.
    # When `public` mode is also set, inference endpoints are left unauthenticated
    # so the node can serve as an open public bootstrap gateway.
    api_key: str = ""

    # Public bootstrap mode (MYCELLM_PUBLIC env var).
    # When true:
    #   - /v1/models, /v1/chat/*, /v1/completions, /v1/embeddings and /api/* are
    #     unauthenticated regardless of api_key (anyone can query the gateway).
    #   - Admin + /v1/node/* endpoints still require api_key if set.
    #   - The escalating brute-force lockout is replaced with a lenient per-IP
    #     sliding window (to prevent self-DoS on a permissive public node).
    #   - Anonymous inference requests are subject to a per-IP token bucket
    #     (see `public_anon_rate_per_min`) to keep the node honest but usable.
    #   - Seeders whose announcements arrive here are auto-approved by the
    #     existing admin.py public-network logic.
    public: bool = False
    public_anon_rate_per_min: int = 30  # anon inference req/min per source IP

    # Telemetry — opt-in anonymous usage stats sent to bootstrap node
    # Includes: request/token counts, TPS, model names, uptime, credits earned
    # Does NOT include: prompts, IPs, user data, API keys
    telemetry: bool = False

    # Reverse proxies whose X-Forwarded-For header may be believed.
    #
    # ⚠️ WIDEN THIS DELIBERATELY, NEVER CASUALLY. Every per-IP limit — public
    # chat, node announces, the anon limiter — is keyed on the result. Trusting
    # an address you do not control lets a caller spoof a fresh IP per request
    # and turns every one of those limits into a suggestion.
    #
    # Loopback is safe everywhere because the TCP peer of an established
    # connection cannot be forged. A containerised deployment behind a local
    # reverse proxy sees the bridge gateway instead and must add it explicitly,
    # e.g. MYCELLM_TRUSTED_PROXIES="127.0.0.0/8,::1,192.168.80.0/24".
    trusted_proxies: str = "127.0.0.0/8,::1"

    # Credit
    initial_credits: float = 100.0

    # ── Async job queue ─────────────────────────────────────────────────
    # Work that waits for a device instead of failing when none is free.
    # ON by default: the queue only ever runs jobs that were explicitly
    # submitted to it, so an idle node with an empty table does nothing but a
    # 30s SELECT. Off means /v1/jobs answers 503 and nothing is scheduled.
    queue_enabled: bool = True
    # How many queued jobs this node runs at once. 1 is deliberate — a
    # personal device that starts three jobs because three were waiting is a
    # device someone force-quits.
    queue_max_concurrent: int = 1
    # Seconds between scheduler polls while work is pending.
    queue_poll_interval: float = 5.0

    # Admission control — seeder-side peer screening
    # Minimum reputation score to serve a peer (0.0 = no minimum)
    admission_min_score: float = 0.0
    # Require peers to have receipts (proof of seeding) after grace period
    admission_require_receipts: bool = False
    # Free requests before admission policy kicks in
    admission_grace_requests: int = 5

    # Privacy — no-log policy for inference content
    # When true, prompt/response content is never written to disk or logs
    no_log_inference: bool = True

    # Fleet throttle — max public requests this node will serve per hour (0 = unlimited)
    max_public_requests_per_hour: int = 0

    # Quality floor
    min_model_tier: str = ""  # Minimum model tier for this network

    # Fleet admin key — opt-in remote fleet management via QUIC relay
    # When set, allows a fleet admin to manage this node through the bootstrap
    fleet_admin_key: str = ""  # MYCELLM_FLEET_ADMIN_KEY env var

    # ---- Resilience ----------------------------------------------------------
    # In-app heartbeat watchdog. A wedged event loop (fd exhaustion, a blocked
    # synchronous call, a GIL-holding load that never returns) leaves the process
    # alive but deaf — launchd/systemd/docker only restart on *exit*, so a hang is
    # invisible to them. The watchdog runs in a dedicated OS thread and, on a
    # stalled loop or fd-ceiling breach, calls os._exit(non-zero) to convert the
    # hang into an exit the supervisor already restarts. See resilience.py.
    watchdog_enabled: bool = True            # MYCELLM_WATCHDOG_ENABLED
    watchdog_stall_seconds: float = 90.0     # loop-heartbeat staleness before abort
    watchdog_fd_pct: float = 0.85            # abort when open fds exceed this fraction of RLIMIT_NOFILE
    watchdog_check_interval: float = 5.0     # how often the watchdog thread checks

    # Network self-heal — detect a local/public address change (machine moved
    # networks, DHCP lease change, NAT rebind) and immediately re-announce +
    # re-probe NAT + reconnect, instead of waiting out the normal announce cycle.
    # Also keeps the node attached to every configured network's bootstrap.
    selfheal_enabled: bool = True            # MYCELLM_SELFHEAL_ENABLED
    selfheal_interval: float = 30.0          # how often to check address/membership

    # Model crash-loop guard — quarantine (disable in model_configs.json) a model
    # that fails to load this many times across restarts (OOM kill, repeated load
    # failure) so boot-restore stops reloading it into a crash loop.
    model_max_restore_attempts: int = 3      # MYCELLM_MODEL_MAX_RESTORE_ATTEMPTS

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mycellm.db"

    @property
    def certs_dir(self) -> Path:
        return self.data_dir / "certs"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        for d in [self.data_dir, self.keys_dir, self.certs_dir, self.config_dir]:
            d.mkdir(parents=True, exist_ok=True)
        if self.model_dir:
            self.model_dir.mkdir(parents=True, exist_ok=True)

    def get_bootstrap_list(self) -> list[tuple[str, int]]:
        """Parse bootstrap peers into (host, port) tuples."""
        if not self.bootstrap_peers:
            return []
        peers = []
        for entry in self.bootstrap_peers.split(","):
            entry = entry.strip()
            if not entry:
                continue
            host, _, port_str = entry.rpartition(":")
            peers.append((host, int(port_str)))
        return peers


@lru_cache
def get_settings() -> MycellmSettings:
    return MycellmSettings()
