"""Unit tests for PeerManager."""

from unittest.mock import MagicMock

from mycellm.transport.peer_manager import PeerManager, ManagedPeer, PeerConnectionState


def test_managed_peer_backoff():
    """Backoff delay increases exponentially with cap at 60s."""
    peer = ManagedPeer("127.0.0.1", 8421)
    assert peer.backoff_delay() == 5.0  # attempt 0
    peer.reconnect_attempts = 1
    assert peer.backoff_delay() == 10.0
    peer.reconnect_attempts = 2
    assert peer.backoff_delay() == 20.0
    peer.reconnect_attempts = 3
    assert peer.backoff_delay() == 40.0
    peer.reconnect_attempts = 4
    assert peer.backoff_delay() == 60.0  # cap
    peer.reconnect_attempts = 10
    assert peer.backoff_delay() == 60.0  # still capped


def test_managed_peer_addr():
    peer = ManagedPeer("10.0.0.1", 9000)
    assert peer.addr == "10.0.0.1:9000"


def test_peer_connection_state_values():
    assert PeerConnectionState.CONNECTING == "connecting"
    assert PeerConnectionState.HANDSHAKING == "handshaking"
    assert PeerConnectionState.ROUTABLE == "routable"
    assert PeerConnectionState.DISCONNECTED == "disconnected"


def test_peer_manager_get_connections_empty():
    node = MagicMock()
    pm = PeerManager(node)
    assert pm.get_connections() == []


def test_peer_manager_get_connections_with_peer():
    node = MagicMock()
    pm = PeerManager(node)
    peer = ManagedPeer("1.2.3.4", 8421)
    peer.state = PeerConnectionState.DISCONNECTED
    peer.reconnect_attempts = 3
    pm._managed_peers["1.2.3.4:8421"] = peer

    conns = pm.get_connections()
    assert len(conns) == 1
    assert conns[0]["address"] == "1.2.3.4:8421"
    assert conns[0]["state"] == "disconnected"
    assert conns[0]["reconnect_attempts"] == 3
