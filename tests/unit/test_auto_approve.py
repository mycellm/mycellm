"""Unit tests for auto-approve logic in admin API."""

import pytest
import time
from unittest.mock import MagicMock

from mycellm.api.admin import _is_public_network, _check_rate_limit, _announce_timestamps


@pytest.fixture(autouse=True)
def clear_rate_limit():
    """Clear rate limit state between tests."""
    _announce_timestamps.clear()
    yield
    _announce_timestamps.clear()


def _make_node(public=False):
    """Create a minimal mock node."""
    node = MagicMock()
    node.node_registry = {}
    if public:
        node.federation = MagicMock()
        node.federation.identity = MagicMock()
        node.federation.identity.public = True
    else:
        node.federation = MagicMock()
        node.federation.identity = MagicMock()
        node.federation.identity.public = False
    return node


def test_public_network_detection():
    """Public network flag correctly detected."""
    assert _is_public_network(_make_node(public=True)) is True
    assert _is_public_network(_make_node(public=False)) is False


def test_public_network_no_federation():
    """No federation manager returns False."""
    node = MagicMock()
    node.federation = None
    assert _is_public_network(node) is False


def test_rate_limit_allows_under_threshold():
    """Rate limit allows < 10 announcements per IP."""
    for i in range(10):
        assert _check_rate_limit("10.0.0.1") is True


def test_rate_limit_blocks_flood():
    """Rate limit blocks > 10 announcements per IP in window."""
    for i in range(10):
        _check_rate_limit("10.0.0.2")
    assert _check_rate_limit("10.0.0.2") is False


def test_rate_limit_different_ips():
    """Rate limit is per-IP."""
    for i in range(10):
        _check_rate_limit("10.0.0.3")
    # Different IP should still be allowed
    assert _check_rate_limit("10.0.0.4") is True


@pytest.mark.anyio
async def test_auto_approve_public_network():
    """Announce to public network auto-approves."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    # Create a FakeNode with public federation
    node = _make_fake_node(public=True)
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "testpeer123456789",
            "node_name": "test-node",
            "api_addr": "10.0.0.1:8420",
            "role": "seeder",
            "capabilities": {"hardware": {"gpu": "CPU", "vram_gb": 0, "backend": "cpu"}},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_status"] == "approved"


@pytest.mark.anyio
async def test_private_network_stays_pending():
    """Announce to private network stays pending."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_fake_node(public=False)
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "testpeer987654321",
            "node_name": "private-node",
            "api_addr": "10.0.0.2:8420",
            "role": "seeder",
            "capabilities": {"hardware": {"gpu": "CPU"}},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_status"] == "pending"


@pytest.mark.anyio
async def test_re_announce_preserves_approved():
    """Re-announcing an already-approved node preserves approved status."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_fake_node(public=True)
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First announce
        await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "reannounce123",
            "node_name": "re-node",
            "api_addr": "10.0.0.3:8420",
            "role": "seeder",
            "capabilities": {"hardware": {}},
        })

        # Second announce
        resp = await client.post("/v1/admin/nodes/announce", json={
            "peer_id": "reannounce123",
            "node_name": "re-node-updated",
            "api_addr": "10.0.0.3:8420",
            "role": "seeder",
            "capabilities": {"hardware": {}},
        })
        assert resp.json()["node_status"] == "approved"


def _make_fake_node(public=False):
    """Create a FakeNode suitable for API testing."""
    from mycellm.inference.manager import InferenceManager
    from mycellm.router.registry import PeerRegistry
    from mycellm.activity import ActivityTracker

    class FakeSettings:
        node_name = "test-bootstrap"
        api_key = ""
        bootstrap_peers = ""
        data_dir = MagicMock()

    class FakeNode:
        def __init__(self):
            self.peer_id = "bootstrappeer123456"
            self.capabilities = type("C", (), {
                "models": [],
                "hardware": type("H", (), {
                    "to_dict": lambda self: {"gpu": "test", "vram_gb": 0, "backend": "cpu"},
                    "gpu": "CPU", "vram_gb": 0, "backend": "cpu",
                })(),
                "role": "seeder",
            })()
            self.ledger = None
            self.inference = InferenceManager()
            self.registry = PeerRegistry()
            self.node_registry = {}
            self.activity = ActivityTracker()
            self.federation = MagicMock()
            self.federation.identity = MagicMock()
            self.federation.identity.public = public
            self.federation.identity.network_name = "test-net"
            self.federation.identity.network_id = "abc123" * 10
            self.federation.network_id = "abc123" * 10
            self.peer_manager = type("PM", (), {"get_connections": lambda self: []})()
            self._settings = FakeSettings()
            self._start_time = 1000000.0
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
                "node_name": "test-bootstrap",
                "peer_id": self.peer_id,
                "uptime_seconds": self.uptime,
                "role": "seeder",
                "hardware": {"gpu": "test", "vram_gb": 0, "backend": "cpu"},
                "credits": {"balance": 100.0, "earned": 0.0, "spent": 0.0},
                "peers": [],
                "models": [],
                "inference": {"active": 0, "max_concurrent": 2},
            }

        def get_system_info(self):
            return {"memory": {"total_gb": 16, "used_pct": 50}, "gpu": {"gpu": "CPU", "vram_gb": 0}}

        async def get_credits(self):
            return {"balance": 100.0, "earned": 0.0, "spent": 0.0}

    return FakeNode()
