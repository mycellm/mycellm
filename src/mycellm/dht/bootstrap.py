"""Bootstrap peer list management."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("mycellm.dht")


def load_bootstrap_peers(config_dir: Path) -> list[tuple[str, int]]:
    """Load bootstrap peers from config file and environment."""
    peers: list[tuple[str, int]] = []

    # From config file
    bootstrap_file = config_dir / "bootstrap.json"
    if bootstrap_file.exists():
        data = json.loads(bootstrap_file.read_text())
        for entry in data.get("peers", []):
            if isinstance(entry, str):
                host, _, port_str = entry.rpartition(":")
                peers.append((host, int(port_str)))
            elif isinstance(entry, dict):
                peers.append((entry["host"], entry["port"]))

    return peers


def save_bootstrap_peers(config_dir: Path, peers: list[tuple[str, int]]) -> None:
    """Save bootstrap peers to config file."""
    config_dir.mkdir(parents=True, exist_ok=True)
    data = {"peers": [f"{host}:{port}" for host, port in peers]}
    (config_dir / "bootstrap.json").write_text(json.dumps(data, indent=2))
