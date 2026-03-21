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

    # Node identity
    node_name: str = Field(default_factory=_generate_node_name)

    # Inference
    model_dir: Optional[Path] = None
    max_concurrent_inferences: int = 2

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
    # When set, all API endpoints (except /health) require Authorization: Bearer <key>
    api_key: str = ""

    # Telemetry — opt-in anonymous usage stats sent to bootstrap node
    # Includes: request/token counts, TPS, model names, uptime, credits earned
    # Does NOT include: prompts, IPs, user data, API keys
    telemetry: bool = False

    # Credit
    initial_credits: float = 100.0

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

    # Quality floor
    min_model_tier: str = ""  # Minimum model tier for this network

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
