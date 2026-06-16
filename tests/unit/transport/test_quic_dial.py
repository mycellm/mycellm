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
