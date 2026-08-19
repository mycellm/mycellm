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
        # Set by dial_peer for client-dialed connections, which each own a
        # dedicated ephemeral UDP socket. close() releases it. Server-accepted
        # protocols share the single server socket and leave this None.
        self._owned_transport: asyncio.BaseTransport | None = None

    def set_message_handler(
        self, handler: Callable[[MessageEnvelope, int], Awaitable[None]]
    ) -> None:
        self._message_handler = handler

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            self._handshake_complete.set()
            try:
                self._peer_addr = self._quic._network_paths[0].addr
            except (IndexError, AttributeError):
                pass
            logger.debug("QUIC handshake completed")

        elif isinstance(event, StreamDataReceived):
            stream_id = event.stream_id
            buf = self._buffers.get(stream_id, b"")
            buf += event.data

            # Limit buffer size to prevent DoS
            if len(buf) > 10 * 1024 * 1024:  # 10MB max per stream
                logger.warning(f"Stream {stream_id} exceeded 10MB buffer limit, dropping")
                self._buffers.pop(stream_id, None)
                return

            if event.end_stream:
                # ⚠️ DRAIN FRAMES BEFORE FALLING BACK TO "one message per
                # stream". A streamed reply ends by closing its frame stream,
                # and QUIC may deliver the final frame and the FIN in the same
                # event — parsing the whole buffer as a single CBOR message
                # then fails, and the frame in it (usually INFERENCE_DONE) is
                # lost. The client waits out its idle timeout for a message
                # that arrived and was thrown away.
                self._buffers[stream_id] = buf
                if stream_id % 4 <= 1:
                    self._try_framed_dispatch(stream_id)
                leftover = self._buffers.pop(stream_id, b"")
                if leftover:
                    # Not framed (or a trailing partial): the legacy shape.
                    self._dispatch_message(leftover, stream_id)
            elif stream_id % 4 <= 1:
                # Length-prefixed framing on bidirectional streams only
                # (iOS NWConnection sends on a single bidirectional stream)
                self._buffers[stream_id] = buf
                self._try_framed_dispatch(stream_id)
            else:
                # Unidirectional stream — just buffer until end_stream.
                # QUIC may fragment large messages across multiple events;
                # treating raw CBOR as length-prefixed corrupts the buffer.
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

    def _try_framed_dispatch(self, stream_id: int) -> None:
        """Try to parse length-prefixed framed messages from buffer.

        iOS Network.framework sends on a single bidirectional stream with
        4-byte big-endian length prefix per message (MessageEnvelope.to_framed()).
        """
        buf = self._buffers.get(stream_id, b"")
        while len(buf) >= 4:
            length = int.from_bytes(buf[:4], "big")
            if length > 10 * 1024 * 1024:
                # ⚠️ NOT NECESSARILY A BAD FRAME — MUCH MORE LIKELY AN UNFRAMED
                # ONE. A bare CBOR map begins 0xA1..0xBF, which read as a length
                # prefix is hundreds of megabytes. Dropping the buffer here
                # discarded a legitimate single-message stream from an older
                # peer that happened to arrive fragmented. Leave it for the
                # end-of-stream path to parse whole.
                logger.debug(
                    f"Stream {stream_id}: {length} byte prefix is not a frame; "
                    f"treating as an unframed message"
                )
                return
            if len(buf) < 4 + length:
                break  # incomplete frame
            frame_data = buf[4:4 + length]
            buf = buf[4 + length:]
            self._dispatch_message(frame_data, stream_id)
        self._buffers[stream_id] = buf

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

    async def send_message(
        self, msg: MessageEnvelope, bidirectional: bool = False
    ) -> int:
        """Send a message on a new stream. Unidirectional (fire-and-forget) by
        default; pass bidirectional=True for request/response so peers that only
        deliver peer-initiated *bidirectional* streams (iOS Network.framework's
        NWMultiplexGroup) actually receive it. Returns the stream ID used.
        """
        await self._handshake_complete.wait()
        stream_id = self._quic.get_next_available_stream_id(
            is_unidirectional=not bidirectional
        )
        data = msg.to_cbor()
        self._quic.send_stream_data(stream_id, data, end_stream=True)
        self.transmit()
        return stream_id

    async def open_frame_stream(self, bidirectional: bool = True) -> int:
        """Open a stream that will carry SEVERAL length-prefixed messages.

        ⚠️ ONE MESSAGE PER STREAM DOES NOT WORK FOR STREAMING. `send_message`
        opens a new stream and ends it per message, which is fine for
        request/response but wrong for a token stream: a 40-frame reply became
        40 streams, QUIC orders only WITHIN a stream so they raced, and iOS's
        NWMultiplexGroup delivered 7 of them. The client saw a truncated,
        scrambled answer and then waited out its idle timeout for frames that
        had already been sent.

        A streamed reply is ONE stream carrying framed messages, ended once at
        the end — ordering and delivery then come from QUIC itself rather than
        from luck.
        """
        await self._handshake_complete.wait()
        return self._quic.get_next_available_stream_id(
            is_unidirectional=not bidirectional
        )

    def send_frame(self, stream_id: int, msg: MessageEnvelope) -> None:
        """Write one length-prefixed message to an open frame stream."""
        self._quic.send_stream_data(stream_id, msg.to_framed(), end_stream=False)
        self.transmit()

    def end_frame_stream(self, stream_id: int) -> None:
        """Close the sending side, which is the client's end-of-stream signal."""
        self._quic.send_stream_data(stream_id, b"", end_stream=True)
        self.transmit()

    def close_stream(self, stream_id: int) -> None:
        """Free a bidirectional request stream's receive side after the reply
        (which arrives by id on a separate stream), so half-open streams don't
        accumulate and exhaust the peer's MAX_STREAMS limit."""
        try:
            self._quic.stop_stream(stream_id, 0)
            self.transmit()
        except Exception:
            pass

    async def send_and_wait(
        self, msg: MessageEnvelope, timeout: float = 30.0, bidirectional: bool = False
    ) -> MessageEnvelope:
        """Send a message and wait for a response with matching ID.

        The response arrives on a separate stream (server sends it as a new message).
        Matching is done by message ID.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[MessageEnvelope] = loop.create_future()
        self._response_futures[msg.id] = fut
        stream_id = None
        try:
            stream_id = await self.send_message(msg, bidirectional=bidirectional)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._response_futures.pop(msg.id, None)
            if bidirectional and stream_id is not None:
                self.close_stream(stream_id)

    async def reply_on_stream(self, stream_id: int, msg: MessageEnvelope) -> None:
        """Send a reply. Uses framed format on bidirectional streams (iOS),
        or a new unidirectional stream for standard clients.
        """
        # Bidirectional streams (client-initiated: 0, 4, 8, ...) — send framed on same stream
        if stream_id % 4 == 0:
            data = msg.to_framed()
            self._quic.send_stream_data(stream_id, data)
            self.transmit()
        else:
            # Unidirectional — reply on a new stream
            await self.send_message(msg)

    def close(self) -> None:
        if not self._is_closed:
            self._is_closed = True
            self._quic.close()
            self.transmit()
        # Release the underlying UDP socket for client-dialed connections.
        # aioquic's protocol does NOT close its datagram transport on close(),
        # so without this every dialed connection leaks one ephemeral *:port
        # UDP fd when the caller closes it (handshake reject, reconnect
        # teardown, etc.) — the residual socket leak that wedges nodes. Only
        # client dials set _owned_transport; server-accepted protocols share
        # the one server socket and are untouched. transmit() above already
        # flushed CONNECTION_CLOSE synchronously, so closing now is safe.
        if self._owned_transport is not None:
            try:
                self._owned_transport.close()
            except Exception:
                pass
            self._owned_transport = None


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
        server_name=host,
    )
    configuration.verify_mode = ssl.CERT_NONE

    # Resolve host
    infos = await asyncio.get_event_loop().getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    if not infos:
        raise ConnectionError(f"Could not resolve {host}:{port}")
    addr = infos[0]
    server_addr = addr[4]

    connection = QuicConnection(configuration=configuration)

    loop = asyncio.get_event_loop()
    transport, protocol = await asyncio.wait_for(
        loop.create_datagram_endpoint(
            lambda: MycellmQuicProtocol(connection, stream_handler=None),
            local_addr=("0.0.0.0", 0),
        ),
        timeout=connection_timeout,
    )
    # This dialed protocol owns its dedicated UDP socket; tag it so
    # protocol.close() releases the fd (the success/teardown path). The
    # failure path below also closes transport explicitly.
    protocol._owned_transport = transport

    # Once the datagram endpoint exists it owns a bound UDP socket (an ephemeral
    # *:port). If anything below raises — handshake timeout, cancellation, a dead
    # or NAT'd peer — the caller never receives `protocol` and so can never close
    # it, orphaning that UDP fd. Guard the rest and tear the transport down on any
    # failure (incl. CancelledError) so the socket is always released. Without
    # this, a seeder's reconnect loop leaks one UDP fd per failed dial until the
    # process hits its open-files limit and the event loop can no longer accept.
    try:
        protocol.connect(server_addr)

        if message_handler:
            protocol.set_message_handler(message_handler)

        await asyncio.wait_for(protocol._handshake_complete.wait(), timeout=connection_timeout)
        return protocol
    except BaseException:
        try:
            protocol.close()
        except Exception:
            pass
        transport.close()
        raise
