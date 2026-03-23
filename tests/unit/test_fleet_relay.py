"""Tests for fleet command relay API endpoints."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.transport.messages import fleet_response


def _make_app_with_node(peer_connections=None):
    """Create a mock FastAPI request with a node that has given peer connections."""
    node = MagicMock()
    node.peer_id = "bootstrap_peer_id"
    node._peer_connections = peer_connections or {}

    request = MagicMock()
    request.app.state.node = node
    request.json = AsyncMock()
    return request, node


@pytest.mark.asyncio
async def test_fleet_command_no_quic_connection():
    from mycellm.api.admin import fleet_relay_command

    request, node = _make_app_with_node({})
    request.json.return_value = {
        "node_peer_id": "missing_peer",
        "command": "node.status",
        "params": {},
        "fleet_admin_key": "key123",
    }

    result = await fleet_relay_command(request)
    assert "error" in result
    assert "No QUIC connection" in result["error"]


@pytest.mark.asyncio
async def test_fleet_command_relayed_successfully():
    from mycellm.api.admin import fleet_relay_command

    # Mock a QUIC connection with send_and_wait
    mock_protocol = MagicMock()
    resp_msg = fleet_response("target_peer", "req1", True, data={"node_name": "aurora"})
    mock_protocol.send_and_wait = AsyncMock(return_value=resp_msg)

    mock_conn = MagicMock()
    mock_conn.protocol = mock_protocol

    request, node = _make_app_with_node({"target_peer_id": mock_conn})
    request.json.return_value = {
        "node_peer_id": "target_peer_id",
        "command": "node.status",
        "params": {},
        "fleet_admin_key": "key123",
    }

    result = await fleet_relay_command(request)
    assert result["success"] is True
    assert result["data"]["node_name"] == "aurora"

    # Verify the fleet command was sent via QUIC
    mock_protocol.send_and_wait.assert_called_once()
    sent_msg = mock_protocol.send_and_wait.call_args[0][0]
    assert sent_msg.type == MessageType.FLEET_COMMAND
    assert sent_msg.payload["command"] == "node.status"
    assert sent_msg.payload["fleet_admin_key"] == "key123"


@pytest.mark.asyncio
async def test_fleet_command_timeout():
    from mycellm.api.admin import fleet_relay_command

    mock_protocol = MagicMock()
    mock_protocol.send_and_wait = AsyncMock(side_effect=asyncio.TimeoutError())

    mock_conn = MagicMock()
    mock_conn.protocol = mock_protocol

    request, node = _make_app_with_node({"target_peer_id": mock_conn})
    request.json.return_value = {
        "node_peer_id": "target_peer_id",
        "command": "model.list",
        "params": {},
        "fleet_admin_key": "key123",
    }

    result = await fleet_relay_command(request)
    assert "error" in result
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_fleet_command_missing_peer_id():
    from mycellm.api.admin import fleet_relay_command

    request, node = _make_app_with_node({})
    request.json.return_value = {"command": "node.status"}

    result = await fleet_relay_command(request)
    assert result["error"] == "node_peer_id required"


@pytest.mark.asyncio
async def test_fleet_command_missing_command():
    from mycellm.api.admin import fleet_relay_command

    request, node = _make_app_with_node({})
    request.json.return_value = {"node_peer_id": "peer1"}

    result = await fleet_relay_command(request)
    assert result["error"] == "command required"


@pytest.mark.asyncio
async def test_fleet_peers_endpoint():
    from mycellm.api.admin import fleet_peers

    mock_conn = MagicMock()
    mock_conn.state.value = "routable"
    mock_conn.protocol = MagicMock()
    mock_conn.protocol._is_closed = False

    request, node = _make_app_with_node({"peer_abc": mock_conn})

    result = await fleet_peers(request)
    assert len(result["peers"]) == 1
    assert result["peers"][0]["peer_id"] == "peer_abc"
    assert result["peers"][0]["connected"] is True
