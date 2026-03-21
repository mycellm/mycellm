"""Unit tests for public stats API endpoint."""

import pytest
import time
from unittest.mock import MagicMock

from mycellm.inference.manager import InferenceManager
from mycellm.router.registry import PeerRegistry
from mycellm.activity import ActivityTracker


class FakeSettings:
    node_name = "stats-node"
    api_key = "secret-key"  # Auth is set to test /public/ exemption
    bootstrap_peers = ""
    data_dir = MagicMock()


class StatsNode:
    def __init__(self):
        self.peer_id = "statspeer123456789"
        self.capabilities = type("C", (), {
            "models": [],
            "hardware": type("H", (), {
                "to_dict": lambda self: {"gpu": "test", "vram_gb": 8, "backend": "cuda"},
                "gpu": "RTX 4090", "vram_gb": 24, "backend": "cuda",
            })(),
            "role": "seeder",
        })()
        self.ledger = None
        self.inference = InferenceManager()
        self.registry = PeerRegistry()
        self.node_registry = {
            "peer1": {
                "peer_id": "peer1",
                "node_name": "gpu-node-1",
                "status": "approved",
                "role": "seeder",
                "last_seen": time.time(),
                "capabilities": {
                    "hardware": {"gpu": "RTX 3090", "vram_gb": 24, "backend": "cuda"},
                    "models": [{"name": "llama-7b"}],
                },
                "system": {"memory": {"total_gb": 32}, "gpu": {"vram_gb": 24}},
            },
            "peer2": {
                "peer_id": "peer2",
                "node_name": "pending-node",
                "status": "pending",
                "role": "seeder",
                "last_seen": time.time(),
                "capabilities": {},
                "system": {},
            },
        }
        self.activity = ActivityTracker()
        self.federation = MagicMock()
        self.federation.identity = MagicMock()
        self.federation.identity.public = True
        self.federation.identity.network_name = "mycellm-public"
        self.federation.identity.network_id = "abc123def456" * 5
        self.federation.network_id = "abc123def456" * 5
        self.peer_manager = type("PM", (), {"get_connections": lambda self: []})()
        self._settings = FakeSettings()
        self._start_time = time.time() - 3600
        self._running = True
        self.reputation = type("R", (), {})()
        self.device_key = None
        self.device_cert = None
        self.account_key = None

    @property
    def uptime(self):
        return time.time() - self._start_time

    def get_status(self):
        return {
            "node_name": "stats-node", "peer_id": self.peer_id,
            "uptime_seconds": self.uptime, "role": "seeder",
            "hardware": {"gpu": "test", "vram_gb": 8, "backend": "cuda"},
            "peers": [], "models": [],
            "inference": {"active": 0, "max_concurrent": 2},
        }

    def get_system_info(self):
        return {"memory": {"total_gb": 64, "used_pct": 30}, "gpu": {"gpu": "RTX 4090", "vram_gb": 24}}

    async def get_credits(self):
        return {"balance": 100.0, "earned": 0.0, "spent": 0.0}


@pytest.fixture
def stats_app():
    from mycellm.api.app import create_app
    return create_app(StatsNode())


@pytest.mark.anyio
async def test_public_stats_no_auth(stats_app):
    """Public stats endpoint returns 200 without any auth header."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=stats_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["network_name"] == "mycellm-public"
        assert data["nodes"]["total"] >= 1


@pytest.mark.anyio
async def test_public_stats_hides_sensitive_data(stats_app):
    """Public stats does not expose IPs, keys, or peer IDs."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=stats_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        data = resp.json()
        text = str(data)

        # No IPs
        assert "10.0.0" not in text
        assert "192.168" not in text
        # No API keys
        assert "secret-key" not in text
        assert "api_key" not in text
        # No peer IDs in top-level response
        assert "statspeer" not in text


@pytest.mark.anyio
async def test_public_stats_includes_aggregate_compute(stats_app):
    """Public stats includes VRAM and RAM totals."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=stats_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        data = resp.json()
        assert "compute" in data
        assert data["compute"]["total_vram_gb"] > 0
        assert data["compute"]["total_ram_gb"] > 0


@pytest.mark.anyio
async def test_public_stats_counts_approved_only(stats_app):
    """Only approved nodes counted in total — pending excluded."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=stats_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        data = resp.json()
        # 1 self + 1 approved peer = 2 (pending excluded)
        assert data["nodes"]["total"] == 2


@pytest.mark.anyio
async def test_authed_endpoint_blocked_without_key():
    """Non-public endpoints require auth when API key is set."""
    from unittest.mock import patch
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    # Patch get_settings to return api_key so middleware activates
    mock_settings = MagicMock()
    mock_settings.api_key = "test-secret"
    with patch("mycellm.config.get_settings", return_value=mock_settings):
        app = create_app(StatsNode())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/status")
        assert resp.status_code == 401


@pytest.mark.anyio
async def test_public_stats_models_list(stats_app):
    """Public stats includes model names from fleet."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=stats_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/public/stats")
        data = resp.json()
        assert "llama-7b" in data["models"]["names"]
