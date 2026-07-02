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
PREFS_PATH = Path.home() / ".config" / "mycellm" / "menubar.json"

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
    peer_id: str = ""
    uptime_seconds: float = 0.0
    gpu: str = ""
    vram_gb: float = 0.0
    backend: str = ""
    models: list[str] = field(default_factory=list)
    model_details: list[dict] = field(default_factory=list)
    active: int = 0
    max_concurrent: int = 0
    tps: float = 0.0
    peers: int = 0
    balance: float | None = None
    earned: float | None = None


class History:
    """Ring buffer of (tps, active) samples for the dropdown time graph."""

    def __init__(self, maxlen: int = 120) -> None:
        self.maxlen = maxlen
        self._samples: list[tuple[float, int]] = []

    def append(self, snap: NodeSnapshot) -> None:
        self._samples.append((snap.tps if snap.reachable else 0.0,
                              snap.active if snap.reachable else 0))
        if len(self._samples) > self.maxlen:
            self._samples = self._samples[-self.maxlen :]

    @property
    def tps(self) -> list[float]:
        return [s[0] for s in self._samples]

    @property
    def active(self) -> list[int]:
        return [s[1] for s in self._samples]

    def __len__(self) -> int:
        return len(self._samples)


def scale_series(
    values: list[float], width: float, height: float, pad: float = 2.0,
    vmax: float | None = None,
) -> list[tuple[float, float]]:
    """Normalize a series to (x, y) points in a width×height box.

    Origin is bottom-left (AppKit unflipped coordinates). A flat or empty
    series maps to the baseline. Pass `vmax` to share a scale across series.
    """
    if not values:
        return []
    top = vmax if vmax is not None else max(values)
    if top <= 0:
        top = 1.0
    span_x = width - 2 * pad
    span_y = height - 2 * pad
    dx = span_x / max(len(values) - 1, 1)
    return [
        (pad + i * dx, pad + (min(v, top) / top) * span_y)
        for i, v in enumerate(values)
    ]


def load_prefs() -> dict:
    """Read menubar preferences; missing or corrupt file yields defaults."""
    try:
        return json.loads(PREFS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    except OSError:
        pass  # prefs are cosmetic; never let persistence kill the app


# Nodes before 0.5.1 don't include "version" in /v1/node/status; fall back
# to /v1/node/version once per node (it does a PyPI update check that can
# take ~5s, so cache the answer and cap the attempts).
_version_cache: dict[str, str] = {}
_version_attempts: dict[str, int] = {}


def _fallback_version(base: str, api_key: str = "") -> str:
    if base in _version_cache:
        return _version_cache[base]
    if _version_attempts.get(base, 0) >= 3:
        return ""
    _version_attempts[base] = _version_attempts.get(base, 0) + 1
    try:
        version = str(_get_json(f"{base}/v1/node/version", timeout=6.0, api_key=api_key).get("current", ""))
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    if version:
        _version_cache[base] = version
    return version


def fetch_snapshot(base_url: str, timeout: float = 4.0, api_key: str = "") -> NodeSnapshot:
    """Poll the local node; an unreachable node yields reachable=False.

    api_key: sent as X-API-Key. Without it, a key-protected node 401s and
    the monitor would wrongly report the node offline (and feed the API's
    unauthenticated-caller lockout for localhost).
    """
    base = base_url.rstrip("/")
    try:
        status = _get_json(f"{base}/v1/node/status", timeout, api_key)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return NodeSnapshot(reachable=False)

    hardware = status.get("hardware", {})
    snap = NodeSnapshot(
        reachable=True,
        node_name=str(status.get("node_name", "")),
        version=str(status.get("version", "")) or _fallback_version(base, api_key),
        role=str(status.get("role", "")),
        mode=str(status.get("mode", "")),
        peer_id=str(status.get("peer_id", "")),
        uptime_seconds=float(status.get("uptime_seconds", 0.0)),
        gpu=str(hardware.get("gpu", "")),
        vram_gb=float(hardware.get("vram_gb", 0.0)),
        backend=str(hardware.get("backend", "")),
        models=[m.get("name", "?") for m in status.get("models", [])],
        model_details=list(status.get("models", [])),
        active=int(status.get("inference", {}).get("active", 0)),
        max_concurrent=int(status.get("inference", {}).get("max_concurrent", 0)),
        tps=float(status.get("tps", 0.0)),
        peers=len(status.get("peers", [])),
    )
    try:
        credits = _get_json(f"{base}/v1/node/credits", timeout, api_key)
        snap.balance = float(credits.get("balance", 0.0))
        snap.earned = float(credits.get("earned", 0.0))
    except (urllib.error.URLError, OSError, ValueError):
        pass  # credits are optional decoration; status alone is fine
    return snap


def _get_json(url: str, timeout: float, api_key: str = "") -> dict:
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost API
        return json.loads(resp.read().decode())


def icon_variant(snap: NodeSnapshot, tick: int = 0, mono: bool = False) -> str:
    """Map node state to a mushroom variant.

    Color mode:
    - gray: node unreachable
    - gold: reachable but no models loaded (needs attention)
    - palette cycle: inference in flight (tick advances the cycle)
    - green: healthy and idle

    Monochrome (template) mode tells the same story with alpha instead of
    color: dim when offline, soft when reachable-but-empty, a full/soft
    pulse while inference is in flight, full when healthy.
    """
    if mono:
        if not snap.reachable:
            return "mono-dim"
        if snap.active > 0:
            return "mono" if tick % 2 == 0 else "mono-soft"
        if not snap.models:
            return "mono-soft"
        return "mono"
    if not snap.reachable:
        return "gray"
    if snap.active > 0:
        return ACTIVE_CYCLE[tick % len(ACTIVE_CYCLE)]
    if not snap.models:
        return "gold"
    return "green"


def icon_is_template(variant: str) -> bool:
    """Template (mono) icons are tinted by macOS for light/dark menu bars."""
    return variant.startswith("mono")


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


def peer_id_line(snap: NodeSnapshot, revealed: bool = False) -> str:
    if not snap.peer_id:
        return "Peer ID: —"
    if revealed:
        return f"Peer ID: {snap.peer_id}  (click to copy)"
    return f"Peer ID: {snap.peer_id[:8]}…{snap.peer_id[-4:]}  (click to reveal)"


def uptime_line(snap: NodeSnapshot) -> str:
    if not snap.reachable:
        return "—"
    secs = int(snap.uptime_seconds)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    up = f"{days}d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"
    version = f"v{snap.version}" if snap.version else "v?"
    return f"{version} · up {up} · {snap.mode or 'solo'}"


def hardware_line(snap: NodeSnapshot) -> str:
    if not snap.gpu:
        return "Hardware: —"
    parts = [snap.gpu]
    if snap.vram_gb:
        parts.append(f"{snap.vram_gb:g}GB")
    if snap.backend:
        parts.append(snap.backend)
    return "Hardware: " + " · ".join(parts)


def model_detail_lines(snap: NodeSnapshot) -> list[str]:
    lines = []
    for m in snap.model_details:
        bits = [m.get("name", "?")]
        if m.get("quant"):
            bits.append(str(m["quant"]))
        if m.get("ctx_len"):
            bits.append(f"ctx {m['ctx_len']}")
        if m.get("backend"):
            bits.append(str(m["backend"]))
        lines.append("  " + " · ".join(bits))
    return lines


def graph_caption(history: History, snap: NodeSnapshot) -> str:
    if not len(history):
        return "tok/s: gathering…"
    peak = max(history.tps)
    window_min = len(history) * 5 // 60  # poll cadence is 5s
    now = f"{snap.tps:.1f}" if snap.reachable else "—"
    return f"tok/s now {now} · peak {peak:.1f} · {max(window_min, 1)} min"
