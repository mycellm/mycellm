"""Platform-neutral state for the menu bar app: polling and icon selection.

Kept free of rumps/AppKit imports so it is unit-testable everywhere; the UI
layer in `app.py` is a thin shell over this module.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ICON_DIR = Path(__file__).parent / "icons"

# Protocol Palette cycle shown while inference is in flight. Order tells a
# story per the brand guide: compute (red) -> routing (blue) -> credits
# (gold) -> security (purple) -> back to healthy spore green.
ACTIVE_CYCLE = ("red", "blue", "gold", "purple", "green")


@dataclass
class NodeSnapshot:
    reachable: bool = False
    node_name: str = ""
    version: str = ""
    role: str = ""
    mode: str = ""
    models: list[str] = field(default_factory=list)
    active: int = 0
    tps: float = 0.0
    peers: int = 0
    balance: float | None = None
    earned: float | None = None


def fetch_snapshot(base_url: str, timeout: float = 4.0) -> NodeSnapshot:
    """Poll the local node; an unreachable node yields reachable=False."""
    base = base_url.rstrip("/")
    try:
        status = _get_json(f"{base}/v1/node/status", timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return NodeSnapshot(reachable=False)

    snap = NodeSnapshot(
        reachable=True,
        node_name=str(status.get("node_name", "")),
        version=str(status.get("version", "")),
        role=str(status.get("role", "")),
        mode=str(status.get("mode", "")),
        models=[m.get("name", "?") for m in status.get("models", [])],
        active=int(status.get("inference", {}).get("active", 0)),
        tps=float(status.get("tps", 0.0)),
        peers=len(status.get("peers", [])),
    )
    try:
        credits = _get_json(f"{base}/v1/node/credits", timeout)
        snap.balance = float(credits.get("balance", 0.0))
        snap.earned = float(credits.get("earned", 0.0))
    except (urllib.error.URLError, OSError, ValueError):
        pass  # credits are optional decoration; status alone is fine
    return snap


def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost API
        return json.loads(resp.read().decode())


def icon_variant(snap: NodeSnapshot, tick: int = 0) -> str:
    """Map node state to a mushroom color.

    - gray: node unreachable
    - gold: reachable but no models loaded (needs attention)
    - palette cycle: inference in flight (tick advances the cycle)
    - green: healthy and idle
    """
    if not snap.reachable:
        return "gray"
    if snap.active > 0:
        return ACTIVE_CYCLE[tick % len(ACTIVE_CYCLE)]
    if not snap.models:
        return "gold"
    return "green"


def icon_path(variant: str) -> str:
    return str(ICON_DIR / f"mushroom-{variant}.png")


def status_line(snap: NodeSnapshot) -> str:
    if not snap.reachable:
        return "Node offline"
    name = snap.node_name or "node"
    if snap.active > 0:
        return f"{name} — serving ({snap.active} active, {snap.tps:.1f} tok/s)"
    if not snap.models:
        return f"{name} — online, no models loaded"
    return f"{name} — online ({snap.role or 'node'})"


def models_line(snap: NodeSnapshot) -> str:
    if not snap.reachable or not snap.models:
        return "Models: —"
    shown = ", ".join(snap.models[:2])
    extra = len(snap.models) - 2
    return f"Models: {shown}" + (f" +{extra}" if extra > 0 else "")


def peers_line(snap: NodeSnapshot) -> str:
    if not snap.reachable:
        return "Peers: —"
    return f"Peers: {snap.peers}"


def credits_line(snap: NodeSnapshot) -> str:
    if snap.balance is None:
        return "Credits: —"
    return f"Credits: {snap.balance:,.2f} (earned {snap.earned:,.2f})"
