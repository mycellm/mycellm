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
    NodeSnapshot,
    credits_line,
    fetch_snapshot,
    icon_path,
    icon_variant,
    models_line,
    status_line,
)

STATUS_PAYLOAD = {
    "node_name": "aurora",
    "role": "seeder",
    "mode": "federated",
    "tps": 12.5,
    "models": [{"name": "Qwen2.5-Coder-7B"}, {"name": "Qwen2.5-VL-3B"}],
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
