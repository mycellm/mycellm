"""Tests for opt-in telemetry."""

import pytest
import time
from unittest.mock import MagicMock

from mycellm.activity import ActivityTracker, EventType


def _make_node(telemetry=False, public=True):
    """Create a FakeNode for API testing."""
    from mycellm.inference.manager import InferenceManager
    from mycellm.router.registry import PeerRegistry

    _telem = telemetry

    class FakeSettings:
        node_name = "telem-node"
        api_key = ""
        bootstrap_peers = ""
        data_dir = MagicMock()
        config_dir = MagicMock()
        telemetry = _telem
        db_url = ""
        hf_token = ""
        log_level = "INFO"

    class FakeNode:
        def __init__(self):
            self.peer_id = "telempeer123456"
            self.capabilities = type("C", (), {
                "models": [],
                "hardware": type("H", (), {
                    "to_dict": lambda self: {"gpu": "CPU", "vram_gb": 0, "backend": "cpu"},
                    "gpu": "CPU", "vram_gb": 0, "backend": "cpu",
                })(),
                "role": "seeder",
            })()
            self.ledger = None
            self.inference = InferenceManager()
            self.registry = PeerRegistry()
            self.node_registry = {}
            self.node_registry_repo = None
            self.activity = ActivityTracker()
            self.federation = MagicMock()
            self.federation.identity = MagicMock()
            self.federation.identity.public = public
            self.federation.identity.network_name = "test-net"
            self.federation.identity.network_id = "abc123" * 10
            self.federation.network_id = "abc123" * 10
            self.peer_manager = type("PM", (), {"get_connections": lambda self: []})()
            self._settings = FakeSettings()
            self._settings.telemetry = telemetry
            self._start_time = time.time() - 3600
            self._running = True
            self.reputation = type("R", (), {})()
            self.device_key = None
            self.device_cert = None
            self.account_key = None
            self.secret_store = None

        @property
        def uptime(self):
            return time.time() - self._start_time

        def get_status(self):
            return {
                "node_name": "telem-node", "peer_id": self.peer_id,
                "uptime_seconds": self.uptime, "role": "seeder",
                "hardware": {"gpu": "CPU", "vram_gb": 0, "backend": "cpu"},
                "peers": [], "models": [],
                "inference": {"active": 0, "max_concurrent": 2},
            }

        def get_system_info(self):
            return {"memory": {"total_gb": 16, "used_pct": 30}}

        async def get_credits(self):
            return {"balance": 100.0, "earned": 0.0, "spent": 0.0}

    return FakeNode()


@pytest.mark.anyio
async def test_announce_includes_telemetry_when_enabled():
    """Announce payload includes telemetry block when opted in."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_node(telemetry=True)
    # Simulate a node announcing with telemetry
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "remote-node-1",
            "node_name": "gpu-box",
            "api_addr": "10.0.0.5:8420",
            "role": "seeder",
            "capabilities": {"hardware": {"gpu": "RTX 4090"}},
            "telemetry": {
                "requests_total": 500,
                "tokens_total": 25000,
                "tps": 15.3,
                "models_loaded": ["llama-7b", "mistral-7b"],
                "uptime_seconds": 7200,
                "credits_earned": 12.5,
            },
        })
        assert resp.status_code == 200

    # Telemetry should be stored in registry
    entry = node.node_registry.get("remote-node-1", {})
    assert "telemetry" in entry
    assert entry["telemetry"]["requests_total"] == 500
    assert entry["telemetry"]["tps"] == 15.3


@pytest.mark.anyio
async def test_announce_without_telemetry():
    """Announce without telemetry block works fine."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_node()
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "no-telem-1",
            "node_name": "quiet-node",
            "api_addr": "10.0.0.6:8420",
            "role": "seeder",
            "capabilities": {"hardware": {"gpu": "CPU"}},
        })
        assert resp.status_code == 200

    entry = node.node_registry.get("no-telem-1", {})
    assert "telemetry" not in entry


@pytest.mark.anyio
async def test_public_stats_aggregates_telemetry():
    """Public stats endpoint aggregates telemetry from announcing nodes."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_node()
    # Pre-populate registry with a node that has telemetry
    node.node_registry["telem-peer"] = {
        "peer_id": "telem-peer",
        "node_name": "gpu-contributor",
        "status": "approved",
        "role": "seeder",
        "last_seen": time.time(),
        "capabilities": {"models": [{"name": "llama-7b"}]},
        "system": {"memory": {"total_gb": 32}, "gpu": {"vram_gb": 24}},
        "telemetry": {
            "requests_total": 1000,
            "tokens_total": 50000,
            "tps": 25.0,
            "models_loaded": ["llama-7b"],
        },
    }
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        assert resp.status_code == 200
        data = resp.json()

    # Network totals should include telemetry
    assert data["activity"]["total_requests"] >= 1000
    assert data["activity"]["total_tokens"] >= 50000
    assert data["compute"]["total_tps"] >= 25.0

    # Top contributors should include tps from telemetry
    assert len(data["top_contributors"]) >= 1
    contrib = next((c for c in data["top_contributors"] if c["name"] == "gpu-contributor"), None)
    assert contrib is not None
    assert contrib["tps"] == 25.0


@pytest.mark.anyio
async def test_telemetry_toggle_endpoint():
    """Telemetry GET/POST toggle works."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_node(telemetry=False)
    # Mock config_dir so .env write doesn't fail
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        node._settings.config_dir = Path(td)
        app = create_app(node)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Check initial state
            resp = await client.get("/v1/node/settings/telemetry")
            assert resp.json()["enabled"] is False

            # Enable
            resp = await client.post("/v1/node/settings/telemetry", json={"enabled": True})
            assert resp.json()["enabled"] is True
            assert node._settings.telemetry is True

            # .env should be written
            env_content = (Path(td) / ".env").read_text()
            assert "MYCELLM_TELEMETRY=true" in env_content

            # Disable
            resp = await client.post("/v1/node/settings/telemetry", json={"enabled": False})
            assert resp.json()["enabled"] is False


@pytest.mark.anyio
async def test_fleet_hardware_includes_telemetry_tps():
    """Fleet hardware endpoint shows per-node TPS from telemetry."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_node()
    node.node_registry["fast-peer"] = {
        "peer_id": "fast-peer",
        "node_name": "fast-node",
        "status": "approved",
        "role": "seeder",
        "last_seen": time.time(),
        "capabilities": {"models": [], "hardware": {"gpu": "RTX 4090", "vram_gb": 24, "backend": "cuda"}},
        "system": {"memory": {"total_gb": 64}, "gpu": {"gpu": "RTX 4090", "vram_gb": 24}},
        "telemetry": {"tps": 42.5},
    }
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/fleet/hardware")
        data = resp.json()

    fast = next((n for n in data["nodes"] if n["name"] == "fast-node"), None)
    assert fast is not None
    assert fast["tps"] == 42.5
    assert data["aggregate"]["total_tps"] >= 42.5


def test_telemetry_setting_default_off():
    """Telemetry defaults to False."""
    from mycellm.config.settings import MycellmSettings
    s = MycellmSettings()
    assert s.telemetry is False
