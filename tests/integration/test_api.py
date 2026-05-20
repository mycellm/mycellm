"""Integration test: API endpoints without a running node."""

import pytest
from httpx import ASGITransport, AsyncClient

from mycellm.api.app import create_app


class FakeNode:
    """Minimal node mock for API testing."""

    def __init__(self):
        self.peer_id = "fakepeer1234567890abcdef"
        self.capabilities = type("C", (), {
            "models": [],
            "hardware": type("H", (), {"to_dict": lambda self: {"gpu": "test", "vram_gb": 0, "backend": "cpu"}})(),
            "role": "seeder",
        })()
        self.ledger = None

        from mycellm.inference.manager import InferenceManager
        from mycellm.router.registry import PeerRegistry
        from mycellm.activity import ActivityTracker

        self.inference = InferenceManager()
        self.registry = PeerRegistry()
        self.node_registry = {}
        self.activity = ActivityTracker()
        self.model_resolver = None
        self.federation = None
        self.peer_manager = type("PM", (), {"get_connections": lambda self: []})()
        self._settings = type("S", (), {
            "node_name": "test-node", "api_key": "",
            "data_dir": type("P", (), {"__truediv__": lambda s, x: s})(),
        })()
        self._start_time = 1000000.0
        self._running = True
        self.reputation = type("R", (), {})()
        self.device_key = None
        self.device_cert = None
        self.account_key = None

    @property
    def uptime(self):
        import time
        return time.time() - self._start_time

    def get_status(self):
        return {
            "node_name": "test-node",
            "peer_id": self.peer_id,
            "uptime_seconds": self.uptime,
            "role": "seeder",
            "hardware": {"gpu": "test", "vram_gb": 0, "backend": "cpu"},
            "credits": {"balance": 100.0, "earned": 0.0, "spent": 0.0},
            "peers": [],
            "models": [],
            "inference": {"active": 0, "max_concurrent": 2},
        }

    async def get_credits(self):
        return {"balance": 100.0, "earned": 0.0, "spent": 0.0}

    async def route_inference(self, model, messages, **kwargs):
        return None

    async def announce_capabilities(self):
        pass


@pytest.fixture
def node():
    return FakeNode()


@pytest.fixture
def app(node):
    return create_app(node)


async def test_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


async def test_node_status(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_name"] == "test-node"
        assert data["peer_id"] == "fakepeer1234567890abcdef"


async def test_chat_completions_no_model(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["choices"]) == 1
        assert "No model" in data["choices"][0]["message"]["content"]


async def test_list_models_only_auto_virtual(app):
    """With no loaded models, /v1/models still exposes the virtual 'auto' model."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "auto"
        assert data[0]["owned_by"] == "mycellm"


async def test_credits(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/node/credits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 100.0
