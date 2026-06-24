"""Regression tests for dial_peer() socket lifetime.

A failed QUIC dial (dead/NAT'd peer, handshake timeout) must close the bound
UDP datagram endpoint, otherwise a seeder's reconnect loop leaks one UDP fd per
failed dial until the process hits its open-files limit (EMFILE) and the event
loop can no longer accept connections.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from mycellm.transport import quic


def _fake_loop_methods(transport, protocol):
    async def fake_getaddrinfo(host, port, type=None):
        return [(2, 2, 0, "", ("1.2.3.4", 9999))]

    async def fake_create_datagram_endpoint(factory, **kwargs):
        return transport, protocol

    return fake_getaddrinfo, fake_create_datagram_endpoint


@pytest.mark.asyncio
async def test_dial_peer_closes_transport_on_handshake_timeout():
    loop = asyncio.get_running_loop()
    transport = MagicMock(name="transport")
    protocol = MagicMock(name="protocol")
    protocol._handshake_complete = asyncio.Event()  # never set -> wait_for times out

    getaddrinfo, cde = _fake_loop_methods(transport, protocol)
    with patch.object(loop, "getaddrinfo", getaddrinfo), patch.object(
        loop, "create_datagram_endpoint", cde
    ):
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await quic.dial_peer("peer.example", 8421, connection_timeout=0.05)

    # The leaked-socket guard must tear the endpoint down on failure.
    transport.close.assert_called_once()
    protocol.close.assert_called_once()


@pytest.mark.asyncio
async def test_dial_peer_retains_transport_on_success():
    loop = asyncio.get_running_loop()
    transport = MagicMock(name="transport")
    protocol = MagicMock(name="protocol")
    protocol._handshake_complete = asyncio.Event()
    protocol._handshake_complete.set()  # handshake already complete

    getaddrinfo, cde = _fake_loop_methods(transport, protocol)
    with patch.object(loop, "getaddrinfo", getaddrinfo), patch.object(
        loop, "create_datagram_endpoint", cde
    ):
        result = await quic.dial_peer("peer.example", 8421, connection_timeout=1.0)

    assert result is protocol
    transport.close.assert_not_called()
    protocol.close.assert_not_called()
    # The dialed protocol must own its socket so its eventual close() releases it.
    assert protocol._owned_transport is transport


@pytest.mark.asyncio
async def test_close_releases_owned_transport_for_dialed_protocol():
    """A client-dialed protocol's close() must release its dedicated UDP socket.

    aioquic's protocol does not close its datagram transport on close(); without
    releasing _owned_transport, every dialed connection leaks one *:port UDP fd
    when the caller closes it (handshake reject / reconnect teardown).
    """
    proto = quic.MycellmQuicProtocol(MagicMock(name="quic_connection"))
    owned = MagicMock(name="owned_transport")
    proto._owned_transport = owned
    proto._is_closed = True  # skip the _quic.close()/transmit() path

    proto.close()

    owned.close.assert_called_once()
    assert proto._owned_transport is None


@pytest.mark.asyncio
async def test_close_does_not_touch_shared_server_transport():
    """A server-accepted protocol shares the one server socket and must NOT close it."""
    proto = quic.MycellmQuicProtocol(MagicMock(name="quic_connection"))
    proto._is_closed = True
    assert proto._owned_transport is None  # never set for server side

    proto.close()  # must be a no-op w.r.t. the (shared) transport — no crash
