"""Tests for QUIC framing fix — unidirectional streams must not be parsed as length-prefixed."""

import asyncio
import pytest
from unittest.mock import MagicMock

try:
    from aioquic.quic.events import StreamDataReceived, HandshakeCompleted
    HAS_AIOQUIC = True
except ImportError:
    HAS_AIOQUIC = False

from mycellm.transport.quic import MycellmQuicProtocol

pytestmark = pytest.mark.skipif(not HAS_AIOQUIC, reason="aioquic not installed")


def _make_protocol():
    """Create a MycellmQuicProtocol with mocked QUIC internals."""
    quic = MagicMock()
    quic.get_next_available_stream_id.return_value = 2
    proto = MycellmQuicProtocol.__new__(MycellmQuicProtocol)
    proto._quic = quic
    proto._buffers = {}
    proto._response_futures = {}
    proto._message_handler = None
    proto._handshake_complete = asyncio.Event()
    proto._peer_addr = None
    proto._is_closed = False
    return proto


def test_unidirectional_stream_buffers_until_end():
    """Data on unidirectional streams should buffer, not try framed dispatch."""
    proto = _make_protocol()
    dispatched = []

    def mock_dispatch(data, stream_id):
        dispatched.append((data, stream_id))

    proto._dispatch_message = mock_dispatch

    # Simulate CBOR data arriving in chunks on a unidirectional stream (id % 4 == 2)
    cbor_data = b"\xa5\x01\x02\x03\x04\x05"  # fake CBOR (starts with map marker)

    # First chunk — no end_stream
    event1 = StreamDataReceived(data=cbor_data[:3], end_stream=False, stream_id=2)
    proto.quic_event_received(event1)

    # Should buffer, not dispatch
    assert len(dispatched) == 0
    assert 2 in proto._buffers
    assert proto._buffers[2] == cbor_data[:3]

    # Second chunk — end_stream
    event2 = StreamDataReceived(data=cbor_data[3:], end_stream=True, stream_id=2)
    proto.quic_event_received(event2)

    # Now should dispatch full message
    assert len(dispatched) == 1
    assert dispatched[0][0] == cbor_data
    assert 2 not in proto._buffers


def test_bidirectional_stream_uses_framed_dispatch():
    """Data on bidirectional streams should try length-prefixed framing (iOS path)."""
    proto = _make_protocol()

    # Spy on _try_framed_dispatch
    framed_calls = []

    def spy(stream_id):
        framed_calls.append(stream_id)

    proto._try_framed_dispatch = spy

    event = StreamDataReceived(data=b"\x00\x00\x00\x05hello", end_stream=False, stream_id=0)
    proto.quic_event_received(event)

    assert len(framed_calls) == 1
    assert framed_calls[0] == 0


def test_handshake_captures_peer_addr():
    """HandshakeCompleted should populate _peer_addr from network paths."""
    proto = _make_protocol()
    path = MagicMock()
    path.addr = ("10.1.1.81", 54321)
    proto._quic._network_paths = [path]

    event = HandshakeCompleted(alpn_protocol="mycellm-v1", early_data_accepted=False, session_resumed=False)
    proto.quic_event_received(event)

    assert proto._peer_addr == ("10.1.1.81", 54321)
    assert proto._handshake_complete.is_set()
