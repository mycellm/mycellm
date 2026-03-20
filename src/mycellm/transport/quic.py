"""QUIC transport server and client using aioquic."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from aioquic.asyncio import QuicConnectionProtocol, connect, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import (
    ConnectionTerminated,
    HandshakeCompleted,
    QuicEvent,
    StreamDataReceived,
)

from mycellm.protocol.envelope import MessageEnvelope

logger = logging.getLogger("mycellm.transport")


class MycellmQuicProtocol(QuicConnectionProtocol):
    """QUIC protocol handler for mycellm peer connections."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buffers: dict[int, bytes] = {}
        self._message_handler: Callable[[MessageEnvelope, int], Awaitable[None]] | None = None
        self._handshake_complete = asyncio.Event()
        self._peer_addr: tuple[str, int] | None = None
        self._response_futures: dict[str, asyncio.Future] = {}
        self._is_closed = False

    def set_message_handler(
        self, handler: Callable[[MessageEnvelope, int], Awaitable[None]]
    ) -> None:
        self._message_handler = handler

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            self._handshake_complete.set()
            logger.debug("QUIC handshake completed")

        elif isinstance(event, StreamDataReceived):
            stream_id = event.stream_id
            buf = self._buffers.get(stream_id, b"")
            buf += event.data

            if event.end_stream:
                self._buffers.pop(stream_id, None)
                self._dispatch_message(buf, stream_id)
            else:
                self._buffers[stream_id] = buf

        elif isinstance(event, ConnectionTerminated):
            self._is_closed = True
            logger.info(
                f"Connection terminated: error={event.error_code} "
                f"reason={event.reason_phrase}"
            )
            # Cancel all pending futures
            for fut in self._response_futures.values():
                if not fut.done():
                    fut.cancel()
            self._response_futures.clear()

    def _dispatch_message(self, data: bytes, stream_id: int) -> None:
        try:
            msg = MessageEnvelope.from_cbor(data)
        except Exception as e:
            logger.error(f"Failed to parse message on stream {stream_id}: {e}")
            return
        self._dispatch_single(msg, stream_id)

    def _dispatch_single(self, msg: MessageEnvelope, stream_id: int) -> None:
        # Check if it's a response to a pending request
        fut = self._response_futures.get(msg.id)
        if fut and not fut.done():
            fut.set_result(msg)
            return
        # Otherwise dispatch to handler
        if self._message_handler:
            asyncio.ensure_future(self._message_handler(msg, stream_id))

    async def send_message(self, msg: MessageEnvelope) -> int:
        """Send a message on a new unidirectional stream (fire-and-forget).

        Returns the stream ID used.
        """
        await self._handshake_complete.wait()
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=True)
        data = msg.to_cbor()
        self._quic.send_stream_data(stream_id, data, end_stream=True)
        self.transmit()
        return stream_id

    async def send_and_wait(
        self, msg: MessageEnvelope, timeout: float = 30.0
    ) -> MessageEnvelope:
        """Send a message and wait for a response with matching ID.

        The response arrives on a separate stream (server sends it as a new message).
        Matching is done by message ID.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[MessageEnvelope] = loop.create_future()
        self._response_futures[msg.id] = fut
        try:
            await self.send_message(msg)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._response_futures.pop(msg.id, None)

    async def reply_on_stream(self, stream_id: int, msg: MessageEnvelope) -> None:
        """Send a reply as a new message (uses sender's unidirectional stream).

        Despite the name, replies are sent on a new stream because
        QUIC unidirectional streams are one-way. The message ID matches
        the request for correlation.
        """
        await self.send_message(msg)

    def close(self) -> None:
        if not self._is_closed:
            self._is_closed = True
            self._quic.close()
            self.transmit()


async def create_quic_server(
    host: str,
    port: int,
    cert_path: Path,
    key_path: Path,
    message_handler: Callable[[MycellmQuicProtocol, MessageEnvelope, int], Awaitable[None]],
    on_connection: Callable[[MycellmQuicProtocol], Awaitable[None]] | None = None,
) -> asyncio.BaseTransport:
    """Start a QUIC server."""
    configuration = QuicConfiguration(
        is_client=False,
        alpn_protocols=["mycellm-v1"],
    )
    configuration.load_cert_chain(str(cert_path), str(key_path))

    def protocol_factory(*args, **kwargs):
        proto = MycellmQuicProtocol(*args, **kwargs)

        async def handler(msg: MessageEnvelope, stream_id: int):
            await message_handler(proto, msg, stream_id)

        proto.set_message_handler(handler)
        if on_connection:
            asyncio.ensure_future(on_connection(proto))
        return proto

    return await serve(
        host, port, configuration=configuration, create_protocol=protocol_factory
    )


@asynccontextmanager
async def connect_to_peer(
    host: str,
    port: int,
    message_handler: Callable[[MessageEnvelope, int], Awaitable[None]] | None = None,
) -> AsyncIterator[MycellmQuicProtocol]:
    """Connect to a peer via QUIC. Use as async context manager."""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=["mycellm-v1"],
    )
    # Self-signed certs — identity verified at app layer via NodeHello
    configuration.verify_mode = ssl.CERT_NONE

    async with connect(
        host,
        port,
        configuration=configuration,
        create_protocol=MycellmQuicProtocol,
    ) as protocol:
        if message_handler:
            protocol.set_message_handler(message_handler)
        yield protocol


async def dial_peer(
    host: str,
    port: int,
    connection_timeout: float = 10.0,
    idle_timeout: float = 60.0,
    message_handler: Callable[[MessageEnvelope, int], Awaitable[None]] | None = None,
) -> MycellmQuicProtocol:
    """Dial a peer and return the protocol. Caller owns the lifetime.

    Unlike connect_to_peer(), this is NOT a context manager.
    The caller is responsible for calling protocol.close() when done.
    """
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=["mycellm-v1"],
        idle_timeout=idle_timeout,
    )
    configuration.verify_mode = ssl.CERT_NONE

    # Resolve host
    infos = await asyncio.get_event_loop().getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    if not infos:
        raise ConnectionError(f"Could not resolve {host}:{port}")
    addr = infos[0]
    server_addr = addr[4]

    connection = QuicConnection(configuration=configuration, server_name=host)

    loop = asyncio.get_event_loop()
    transport, protocol = await asyncio.wait_for(
        loop.create_datagram_endpoint(
            lambda: MycellmQuicProtocol(connection, stream_handler=None),
            local_addr=("0.0.0.0", 0),
        ),
        timeout=connection_timeout,
    )

    protocol.connect(server_addr)

    if message_handler:
        protocol.set_message_handler(message_handler)

    await asyncio.wait_for(protocol._handshake_complete.wait(), timeout=connection_timeout)
    return protocol
