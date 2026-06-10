"""Menu bar monitor: state logic, icon selection, and launch-agent plumbing.

The rumps UI layer (mycellm.menubar.app) is macOS-only and intentionally
thin; everything decision-shaped lives in state.py and is covered here.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from mycellm.menubar import launchagent
from mycellm.menubar.state import (
    ACTIVE_CYCLE,
    ICON_DIR,
    History,
    NodeSnapshot,
    credits_line,
    fetch_snapshot,
    graph_caption,
    hardware_line,
    icon_path,
    icon_variant,
    model_detail_lines,
    models_line,
    peer_id_line,
    scale_series,
    status_line,
    uptime_line,
)

STATUS_PAYLOAD = {
    "node_name": "aurora",
    "peer_id": "4524bdbb147bfd8b2aadf1669cea6048",
    "uptime_seconds": 93784.0,
    "role": "seeder",
    "mode": "federated",
    "tps": 12.5,
    "hardware": {"gpu": "Apple M1 Max", "vram_gb": 64.0, "backend": "metal"},
    "models": [
        {"name": "Qwen2.5-Coder-7B", "quant": "4bit", "ctx_len": 8192, "backend": "mlx"},
        {"name": "Qwen2.5-VL-3B", "quant": "", "ctx_len": 4096, "backend": "mlx-vlm"},
    ],
    "inference": {"active": 1, "max_concurrent": 2},
    "peers": [{"peer_id": "a"}, {"peer_id": "b"}],
}
CREDITS_PAYLOAD = {"balance": 151.09, "earned": 53.73, "spent": 2.64}


def _urlopen_returning(payloads):
    """Mock urlopen yielding canned JSON bodies per URL substring."""

    def fake_urlopen(url, timeout=None):
        for fragment, payload in payloads.items():
            if fragment in url:
                cm = mock.MagicMock()
                cm.__enter__.return_value.read.return_value = json.dumps(
                    payload
                ).encode()
                return cm
        raise OSError(f"unexpected URL {url}")

    return fake_urlopen


class TestIconVariant:
    def test_offline_is_gray(self):
        assert icon_variant(NodeSnapshot(reachable=False)) == "gray"

    def test_healthy_idle_is_green(self):
        snap = NodeSnapshot(reachable=True, models=["m"], active=0)
        assert icon_variant(snap) == "green"

    def test_no_models_is_gold(self):
        snap = NodeSnapshot(reachable=True, models=[], active=0)
        assert icon_variant(snap) == "gold"

    def test_active_inference_cycles_palette(self):
        snap = NodeSnapshot(reachable=True, models=["m"], active=2)
        seen = [icon_variant(snap, tick) for tick in range(len(ACTIVE_CYCLE) * 2)]
        assert seen[: len(ACTIVE_CYCLE)] == list(ACTIVE_CYCLE)
        assert seen[len(ACTIVE_CYCLE) :] == list(ACTIVE_CYCLE)  # wraps around

    def test_every_variant_has_an_icon_file(self):
        variants = set(ACTIVE_CYCLE) | {"gray", "gold", "green"}
        for variant in variants:
            path = Path(icon_path(variant))
            assert path.is_file(), f"missing icon for {variant}"
            assert path.parent == ICON_DIR


class TestFetchSnapshot:
    def test_full_snapshot(self):
        payloads = {"/v1/node/status": STATUS_PAYLOAD, "/v1/node/credits": CREDITS_PAYLOAD}
        with mock.patch("urllib.request.urlopen", _urlopen_returning(payloads)):
            snap = fetch_snapshot("http://localhost:8420")
        assert snap.reachable
        assert snap.node_name == "aurora"
        assert snap.models == ["Qwen2.5-Coder-7B", "Qwen2.5-VL-3B"]
        assert snap.active == 1
        assert snap.peers == 2
        assert snap.balance == pytest.approx(151.09)

    def test_unreachable_node(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            snap = fetch_snapshot("http://localhost:8420")
        assert not snap.reachable
        assert icon_variant(snap) == "gray"

    def test_credits_failure_does_not_break_status(self):
        payloads = {"/v1/node/status": STATUS_PAYLOAD}
        with mock.patch("urllib.request.urlopen", _urlopen_returning(payloads)):
            snap = fetch_snapshot("http://localhost:8420")
        assert snap.reachable
        assert snap.balance is None
        assert credits_line(snap) == "Credits: —"


class TestMenuLines:
    def test_status_line_states(self):
        assert status_line(NodeSnapshot(reachable=False)) == "Node offline"
        busy = NodeSnapshot(reachable=True, node_name="aurora", models=["m"],
                            active=2, tps=30.0)
        assert "serving (2 active" in status_line(busy)
        idle = NodeSnapshot(reachable=True, node_name="aurora", models=["m"],
                            role="seeder")
        assert status_line(idle) == "aurora — online (seeder)"

    def test_models_line_truncates(self):
        snap = NodeSnapshot(reachable=True, models=["a", "b", "c", "d"])
        assert models_line(snap) == "Models: a, b +2"

    def test_credits_line_formats(self):
        snap = NodeSnapshot(reachable=True, balance=1234.5, earned=53.7)
        assert credits_line(snap) == "Credits: 1,234.50 (earned 53.70)"

    def test_peer_id_partial_and_revealed(self):
        snap = NodeSnapshot(reachable=True, peer_id="4524bdbb147bfd8b2aadf1669cea6048")
        partial = peer_id_line(snap)
        assert "4524bdbb…6048" in partial
        assert "reveal" in partial
        assert "4524bdbb147bfd8b2aadf1669cea6048" not in partial
        revealed = peer_id_line(snap, revealed=True)
        assert "4524bdbb147bfd8b2aadf1669cea6048" in revealed
        assert "copy" in revealed
        assert peer_id_line(NodeSnapshot()) == "Peer ID: —"

    def test_uptime_line(self):
        snap = NodeSnapshot(reachable=True, version="0.4.2",
                            uptime_seconds=93784, mode="federated")
        assert uptime_line(snap) == "v0.4.2 · up 1d 02:03 · federated"
        short = NodeSnapshot(reachable=True, version="0.4.2", uptime_seconds=3700)
        assert "up 01:01" in uptime_line(short)
        assert uptime_line(NodeSnapshot()) == "—"

    def test_hardware_line(self):
        snap = NodeSnapshot(reachable=True, gpu="Apple M1 Max",
                            vram_gb=64.0, backend="metal")
        assert hardware_line(snap) == "Hardware: Apple M1 Max · 64GB · metal"
        assert hardware_line(NodeSnapshot()) == "Hardware: —"

    def test_model_detail_lines(self):
        snap = NodeSnapshot(reachable=True, model_details=[
            {"name": "Qwen2.5-Coder-7B", "quant": "4bit", "ctx_len": 8192,
             "backend": "mlx"},
            {"name": "tiny", "quant": "", "ctx_len": 0, "backend": ""},
        ])
        lines = model_detail_lines(snap)
        assert lines[0] == "  Qwen2.5-Coder-7B · 4bit · ctx 8192 · mlx"
        assert lines[1] == "  tiny"


class TestHistoryAndGraph:
    def test_history_ring_buffer(self):
        history = History(maxlen=3)
        for tps in (1.0, 2.0, 3.0, 4.0):
            history.append(NodeSnapshot(reachable=True, tps=tps, active=1))
        assert history.tps == [2.0, 3.0, 4.0]
        assert len(history) == 3

    def test_history_records_zero_when_offline(self):
        history = History()
        history.append(NodeSnapshot(reachable=False, tps=99.0, active=5))
        assert history.tps == [0.0]
        assert history.active == [0]

    def test_scale_series_normalizes_to_box(self):
        points = scale_series([0.0, 5.0, 10.0], 100.0, 40.0, pad=2.0)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        assert xs == [2.0, 50.0, 98.0]
        assert ys[0] == 2.0  # baseline
        assert ys[2] == 38.0  # full height
        assert ys[1] == pytest.approx(20.0)

    def test_scale_series_shared_vmax_and_clamp(self):
        points = scale_series([2.0, 8.0], 100.0, 40.0, pad=2.0, vmax=4.0)
        assert points[0][1] == pytest.approx(20.0)  # 2/4 of the span
        assert points[1][1] == 38.0  # clamped at vmax

    def test_scale_series_flat_and_empty(self):
        assert scale_series([], 100.0, 40.0) == []
        flat = scale_series([0.0, 0.0], 100.0, 40.0)
        assert all(y == 2.0 for _, y in flat)

    def test_graph_caption(self):
        history = History()
        assert "gathering" in graph_caption(history, NodeSnapshot())
        for tps in (5.0, 30.2, 12.4):
            history.append(NodeSnapshot(reachable=True, tps=tps))
        snap = NodeSnapshot(reachable=True, tps=12.4)
        caption = graph_caption(history, snap)
        assert "now 12.4" in caption
        assert "peak 30.2" in caption


class TestLaunchAgent:
    def test_install_and_remove(self, tmp_path):
        assert not launchagent.is_installed(tmp_path)
        path = launchagent.install("http://localhost:8420", tmp_path)
        assert path.name == "com.mycellm.menubar.plist"
        assert launchagent.is_installed(tmp_path)

        import plistlib

        with open(path, "rb") as fh:
            data = plistlib.load(fh)
        assert data["Label"] == "com.mycellm.menubar"
        assert data["RunAtLoad"] is True
        assert data["ProgramArguments"][1:] == ["menubar", "--api", "http://localhost:8420"]

        with mock.patch("subprocess.run") as run:
            assert launchagent.remove(tmp_path)
        run.assert_called_once()
        assert not launchagent.is_installed(tmp_path)

    def test_remove_when_absent(self, tmp_path):
        assert launchagent.remove(tmp_path) is False
