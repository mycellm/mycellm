"""Tests for node-side fleet command handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass, field

from mycellm.protocol.envelope import MessageType
from mycellm.transport.messages import fleet_command


@dataclass
class FakeModelInfo:
    name: str
    scope: str = "home"
    quant: str = ""
    ctx_len: int = 4096
    backend: str = "llama.cpp"
    tags: list = field(default_factory=list)
    tier: str = ""
    param_count_b: float = 0.0
    visible_networks: list = field(default_factory=list)
    features: list = field(default_factory=list)
    throughput_tok_s: float = 0.0

    def to_dict(self):
        return {"name": self.name, "scope": self.scope, "backend": self.backend, "ctx_len": self.ctx_len, "quant": self.quant}


class FakeInference:
    def __init__(self):
        self._model_info = {"test-model": FakeModelInfo(name="test-model", scope="home")}
        self.active_count = 0
        self._max_concurrent = 2

    @property
    def loaded_models(self):
        return list(self._model_info.values())

    async def load_model(self, model_path, **kwargs):
        name = kwargs.get("name", "new-model")
        self._model_info[name] = FakeModelInfo(name=name)
        return name

    async def unload_model(self, model_name):
        self._model_info.pop(model_name, None)


class FakeSettings:
    fleet_admin_key = "correct_key_123"
    node_name = "test-node"
    bootstrap_peers = ""
    log_level = "INFO"
    no_log_inference = True
    telemetry = False
    max_public_requests_per_hour = 0
    relay_backends = ""
    api_key = "api_secret"
    hf_token = "hf_secret"


class FakeCapabilities:
    models = []
    role = "seeder"
    hardware = MagicMock(to_dict=lambda: {"gpu": "CPU"})


class FakeActivity:
    tps = 0


class FakeRegistry:
    def connected_peers(self):
        return []


class FakeNode:
    """Minimal mock of MycellmNode for fleet command testing."""

    def __init__(self):
        self._settings = FakeSettings()
        self.peer_id = "test_peer_id_1234"
        self.inference = FakeInference()
        self.capabilities = FakeCapabilities()
        self.activity = FakeActivity()
        self.registry = FakeRegistry()
        self.uptime = 100.0

    def get_status(self):
        return {
            "node_name": self._settings.node_name,
            "peer_id": self.peer_id,
            "uptime_seconds": self.uptime,
            "role": self.capabilities.role,
            "mode": "seeder",
            "tps": 0,
            "hardware": {"gpu": "CPU"},
            "credits": {"balance": 0, "earned": 0, "spent": 0},
            "peers": [],
            "models": [m.to_dict() for m in self.inference.loaded_models],
            "inference": {"active": 0, "max_concurrent": 2},
        }

    def get_operational_mode(self):
        return "seeder"

    async def get_credits(self):
        return {"balance": 100.0, "earned": 50.0, "spent": 10.0}

    async def announce_capabilities(self):
        pass


def _make_node():
    """Create a FakeNode with the fleet command handler mixed in."""
    from mycellm.node import MycellmNode
    node = FakeNode()
    # Bind the fleet methods from MycellmNode
    import types
    node._handle_fleet_command = types.MethodType(MycellmNode._handle_fleet_command, node)
    node._execute_fleet_command = types.MethodType(MycellmNode._execute_fleet_command, node)
    node._FLEET_COMMANDS = MycellmNode._FLEET_COMMANDS
    return node


@pytest.mark.asyncio
async def test_valid_fleet_key_executes_command():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "node.status", {}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    protocol.reply_on_stream.assert_called_once()
    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.type == MessageType.FLEET_RESPONSE
    assert reply.payload["success"] is True
    assert "node_name" in reply.payload["data"]


@pytest.mark.asyncio
async def test_invalid_fleet_key_rejected():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "node.status", {}, "wrong_key")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is False
    assert "Invalid fleet admin key" in reply.payload["error"]


@pytest.mark.asyncio
async def test_no_fleet_key_configured_rejected():
    node = _make_node()
    node._settings.fleet_admin_key = ""
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "node.status", {}, "any_key")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is False
    assert "not configured" in reply.payload["error"]


@pytest.mark.asyncio
async def test_disallowed_command_rejected():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "node.shutdown", {}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is False
    assert "not allowed" in reply.payload["error"]


@pytest.mark.asyncio
async def test_node_config_no_secrets():
    """node.config should not expose api_key, hf_token, or fleet_admin_key values."""
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "node.config", {}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    data = reply.payload["data"]
    assert reply.payload["success"] is True
    # Should have boolean indicators, not actual values
    assert data["api_key_set"] is True
    assert data["hf_token_set"] is True
    # Should not contain actual secret values
    assert "api_secret" not in str(data)
    assert "hf_secret" not in str(data)
    assert "correct_key_123" not in str(data)


@pytest.mark.asyncio
async def test_model_list():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "model.list", {}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    data = reply.payload["data"]
    assert reply.payload["success"] is True
    assert len(data["models"]) == 1
    assert data["models"][0]["name"] == "test-model"


@pytest.mark.asyncio
async def test_model_load():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "model.load", {
        "name": "new-model", "backend": "openai",
        "api_base": "http://example.com", "api_model": "gpt-4",
    }, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is True
    assert reply.payload["data"]["model"] == "new-model"
    assert "new-model" in node.inference._model_info


@pytest.mark.asyncio
async def test_model_unload():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "model.unload", {"model": "test-model"}, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is True
    assert "test-model" not in node.inference._model_info


@pytest.mark.asyncio
async def test_model_scope():
    node = _make_node()
    protocol = MagicMock()
    protocol.reply_on_stream = AsyncMock()

    msg = fleet_command("admin_peer", "model.scope", {
        "model": "test-model", "scope": "public",
    }, "correct_key_123")
    await node._handle_fleet_command(protocol, msg, stream_id=0)

    reply = protocol.reply_on_stream.call_args[0][1]
    assert reply.payload["success"] is True
    assert node.inference._model_info["test-model"].scope == "public"
