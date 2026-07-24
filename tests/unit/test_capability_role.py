"""Regression tests for capability role staying in sync with loaded models.

Bug: the runtime load/unload API paths updated `capabilities.models` but not
`capabilities.role`, so a node that loaded a model via the API/fleet kept
advertising `role="consumer"` until the next restart (only the startup restore
path derived the role). These tests pin the invariant:

    role == "seeder" if any model is loaded else "consumer"

for the load and unload handlers.
"""

import pytest
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

from mycellm.api import node as node_api


@dataclass
class FakeModelInfo:
    name: str
    scope: str = "home"
    quant: str = ""
    ctx_len: int = 4096
    backend: str = "openai"
    tags: list = field(default_factory=list)
    tier: str = ""
    param_count_b: float = 0.0
    visible_networks: list = field(default_factory=list)


class FakeInference:
    def __init__(self, loaded=None):
        self._model_info = {n: FakeModelInfo(name=n) for n in (loaded or [])}

    @property
    def loaded_models(self):
        return list(self._model_info.values())

    async def load_model(self, model_path, **kwargs):
        name = kwargs.get("name") or "remote-model"
        self._model_info[name] = FakeModelInfo(name=name, backend=kwargs.get("backend_type", "openai"))
        return name

    async def unload_model(self, model_name):
        self._model_info.pop(model_name, None)


class FakeCapabilities:
    def __init__(self, role):
        self.models = []
        self.role = role


class FakeActivity:
    def record(self, *args, **kwargs):
        pass


class FakeNode:
    def __init__(self, loaded=None, role="consumer"):
        self.inference = FakeInference(loaded)
        self.capabilities = FakeCapabilities(role)
        self.activity = FakeActivity()
        self.announce_capabilities = AsyncMock()


class FakeRequest:
    def __init__(self, node, body):
        self._body = body
        self.app = type("App", (), {"state": type("S", (), {"node": node})()})()

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_api_backend_load_promotes_to_seeder():
    """Loading a remote (openai) backend flips a consumer to seeder."""
    node = FakeNode(loaded=[], role="consumer")
    req = FakeRequest(node, {
        "name": "claude-sonnet",
        "backend": "openai",
        "api_base": "https://example/v1",
        "api_model": "anthropic/claude-sonnet-4",
    })

    resp = await node_api.load_model(req)

    assert resp["status"] == "loaded"
    assert node.capabilities.role == "seeder"
    node.announce_capabilities.assert_awaited()


@pytest.mark.asyncio
async def test_unload_last_model_demotes_to_consumer():
    """Unloading the only loaded model returns the node to consumer."""
    node = FakeNode(loaded=["m1"], role="seeder")
    req = FakeRequest(node, {"model": "m1"})

    resp = await node_api.unload_model(req)

    assert resp["status"] == "unloaded"
    assert node.capabilities.role == "consumer"
    node.announce_capabilities.assert_awaited()


@pytest.mark.asyncio
async def test_unload_one_of_many_stays_seeder():
    """Unloading one model while others remain keeps the node a seeder."""
    node = FakeNode(loaded=["m1", "m2"], role="seeder")
    req = FakeRequest(node, {"model": "m1"})

    await node_api.unload_model(req)

    assert node.capabilities.role == "seeder"
    assert [m.name for m in node.capabilities.models] == ["m2"]
