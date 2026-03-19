"""Mycellm configuration via Pydantic Settings with XDG path support."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Paths
    data_dir: Path = Field(default_factory=_default_data_dir)
    config_dir: Path = Field(default_factory=_default_config_dir)

    # Network
    api_host: str = "0.0.0.0"
    api_port: int = 8420
    quic_host: str = "0.0.0.0"
    quic_port: int = 8421
    dht_port: int = 8422

    # Node identity
    node_name: Optional[str] = None

    # Inference
    model_dir: Optional[Path] = None
    max_concurrent_inferences: int = 2

    # Bootstrap peers (comma-separated host:port)
    bootstrap_peers: str = ""

    # Credit
    initial_credits: float = 100.0

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
