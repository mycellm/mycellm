"""Comprehensive API integration tests using FakeNode."""

import pytest
from httpx import ASGITransport, AsyncClient
from mycellm.api.app import create_app


class FakeNode:
    """Full mock node for API testing."""

    def __init__(self):
        self.peer_id = "fakepeer1234567890abcdef"
        self.capabilities = type("C", (), {
            "models": [],
            "hardware": type("H", (), {"to_dict": lambda self: {"gpu": "test", "vram_gb": 0, "backend": "cpu"}})(),
            "role": "seeder",
            "network_ids": [],
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
            "node_name": "test-node",
            "api_key": "",
            "data_dir": type("P", (), {
                "__truediv__": lambda s, x: s,
                "exists": lambda s: False,
                "glob": lambda s, p: [],
            })(),
            "model_dir": None,
        })()
        self._start_time = 1000000.0
        self._running = True
        self.reputation = type("R", (), {})()
        self.device_key = None
        self.device_cert = None
        self.account_key = None
        self._peer_connections = {}

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
            "models": [m.to_dict() for m in self.inference.loaded_models],
            "inference": {"active": 0, "max_concurrent": 2},
        }

    def get_system_info(self):
        return {"cpu": {}, "memory": {}, "disk": {}, "gpu": {}, "os": {}, "python": "3.13", "mycellm_version": "0.1.0"}

    async def get_credits(self):
        return {"balance": 100.0, "earned": 0.0, "spent": 0.0}

    async def route_inference(self, model, messages, **kwargs):
        return None

    async def announce_capabilities(self):
        pass


@pytest.fixture
def app():
    return create_app(FakeNode())


async def test_health(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


async def test_node_status(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/status")
        assert r.status_code == 200
        assert r.json()["node_name"] == "test-node"


async def test_chat_no_model(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 200
        content = r.json()["choices"][0]["message"]["content"].lower()
        assert "no model" in content or "no models" in content


async def test_list_models_only_auto_virtual(app):
    """With no loaded models, /v1/models still exposes the virtual 'auto' model."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/models")
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "auto"
        assert data[0]["owned_by"] == "mycellm"


async def test_credits(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/credits")
        assert r.status_code == 200
        assert r.json()["balance"] == 100.0


async def test_activity_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/activity")
        assert r.status_code == 200
        assert "events" in r.json()
        assert "stats" in r.json()


async def test_connections_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/connections")
        assert r.status_code == 200
        assert r.json()["connections"] == []


async def test_federation_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/federation")
        assert r.status_code == 200
        assert r.json()["federation"] is False


async def test_load_status_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/load-status")
        assert r.status_code == 200
        assert "statuses" in r.json()


async def test_saved_configs_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/saved")
        assert r.status_code == 200
        assert "configs" in r.json()


async def test_model_search(app):
    """Model search endpoint should respond (may have 0 results without network)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/search?q=&limit=1")
        assert r.status_code == 200
        assert "models" in r.json()


async def test_suggested_models(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/suggested")
        assert r.status_code == 200
        assert "suggestions" in r.json()


async def test_local_models(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/local")
        assert r.status_code == 200
        assert "files" in r.json()


async def test_spa_routing(app):
    """SPA routes should serve index.html, not 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for path in ["/models", "/chat", "/credits", "/overview", "/network"]:
            r = await c.get(path)
            assert r.status_code == 200, f"{path} returned {r.status_code}"
            assert "text/html" in r.headers.get("content-type", ""), f"{path} not HTML"


async def test_chat_stop_sequences(app):
    """stop parameter should be accepted."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hello"}],
            "stop": ["\n"],
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
        })
        assert r.status_code == 200


async def test_node_system_info(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/system")
        assert r.status_code == 200
        data = r.json()
        assert "cpu" in data


async def test_node_peers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/peers")
        assert r.status_code == 200
        assert r.json()["peers"] == []


async def test_credit_history_empty(app):
    """Credit history should return empty list when no ledger."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/credits/history")
        assert r.status_code == 200
        assert r.json()["transactions"] == []


async def test_downloads_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/node/models/downloads")
        assert r.status_code == 200
        assert "downloads" in r.json()


async def test_load_model_missing_path(app):
    """Loading without model_path for llama.cpp should return error."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/node/models/load", json={"backend": "llama.cpp"})
        assert r.status_code == 200
        assert "error" in r.json()


async def test_unload_model_missing_name(app):
    """Unloading without model name should return error."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/node/models/unload", json={})
        assert r.status_code == 200
        assert "error" in r.json()
