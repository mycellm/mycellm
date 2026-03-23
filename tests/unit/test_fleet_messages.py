"""Tests for fleet command/response message builders."""

from mycellm.protocol.envelope import MessageType
from mycellm.transport.messages import fleet_command, fleet_response


def test_fleet_command_builds_correct_envelope():
    msg = fleet_command("peer1", "model.list", {"filter": "active"}, "secret123")
    assert msg.type == MessageType.FLEET_COMMAND
    assert msg.from_peer == "peer1"
    assert msg.payload["command"] == "model.list"
    assert msg.payload["params"] == {"filter": "active"}
    assert msg.payload["fleet_admin_key"] == "secret123"
    assert msg.id  # auto-generated
    assert msg.ts > 0


def test_fleet_command_defaults():
    msg = fleet_command("peer1", "node.status")
    assert msg.payload["params"] == {}
    assert msg.payload["fleet_admin_key"] == ""


def test_fleet_response_success():
    msg = fleet_response("peer2", "req123", True, data={"status": "ok"})
    assert msg.type == MessageType.FLEET_RESPONSE
    assert msg.from_peer == "peer2"
    assert msg.id == "req123"
    assert msg.payload["success"] is True
    assert msg.payload["data"] == {"status": "ok"}
    assert msg.payload["error"] == ""


def test_fleet_response_error():
    msg = fleet_response("peer2", "req456", False, error="Invalid key")
    assert msg.payload["success"] is False
    assert msg.payload["data"] == {}
    assert msg.payload["error"] == "Invalid key"


def test_fleet_messages_cbor_roundtrip():
    """Fleet messages should survive CBOR serialization."""
    from mycellm.protocol.envelope import MessageEnvelope

    cmd = fleet_command("peer1", "model.load", {"model": "test"}, "key123")
    data = cmd.to_cbor()
    loaded = MessageEnvelope.from_cbor(data)
    assert loaded.type == MessageType.FLEET_COMMAND
    assert loaded.payload["command"] == "model.load"
    assert loaded.payload["fleet_admin_key"] == "key123"

    resp = fleet_response("peer2", "req1", True, {"models": ["a", "b"]})
    data = resp.to_cbor()
    loaded = MessageEnvelope.from_cbor(data)
    assert loaded.type == MessageType.FLEET_RESPONSE
    assert loaded.payload["success"] is True
    assert loaded.payload["data"]["models"] == ["a", "b"]
