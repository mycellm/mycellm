"""Authenticated peer connection wrapping QUIC protocol."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from mycellm.protocol.capabilities import Capabilities
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.node_hello import NodeHello
from mycellm.transport.quic import MycellmQuicProtocol

logger = logging.getLogger("mycellm.transport.connection")


class PeerState(str, Enum):
    DISCOVERED = "discovered"
    DIALABLE = "dialable"
    AUTHENTICATED = "authenticated"
    ROUTABLE = "routable"
    SERVING = "serving"
    DISCONNECTED = "disconnected"


@dataclass
class PeerConnection:
    """An authenticated, framed connection to a peer."""

    peer_id: str
    protocol: MycellmQuicProtocol
    hello: NodeHello
    state: PeerState = PeerState.AUTHENTICATED
    connected_at: float = field(default_factory=time.time)
    last_ping: float = 0.0
    last_pong: float = 0.0
    _pending_responses: dict[str, asyncio.Future] = field(default_factory=dict)
    rtt_history: list[float] = field(default_factory=list)
    _rtt_max_samples: int = 10
    _max_concurrent: int = 4
    _active_requests: int = 0

    @property
    def capabilities(self) -> Capabilities:
        return self.hello.capabilities

    @property
    def role(self) -> str:
        return self.hello.cert.role

    @property
    def is_overloaded(self) -> bool:
        return self._active_requests >= self._max_concurrent

    async def send(self, msg: MessageEnvelope) -> None:
        """Send a message to this peer."""
        await self.protocol.send_message(msg)

    async def request(self, msg: MessageEnvelope, timeout: float = 120.0) -> MessageEnvelope:
        """Send a request and wait for the response."""
        if self.is_overloaded:
            from mycellm.protocol.errors import ErrorCode, ProtocolError
            raise ProtocolError(ErrorCode.OVERLOADED, "Peer at max concurrent requests")

        future: asyncio.Future[MessageEnvelope] = asyncio.get_event_loop().create_future()
        self._pending_responses[msg.id] = future
        self._active_requests += 1

        try:
            await self.protocol.send_message(msg)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._active_requests -= 1
            self._pending_responses.pop(msg.id, None)

    async def request_stream(self, msg: MessageEnvelope, timeout: float = 120.0):
        """Send a request and yield streaming response messages until INFERENCE_DONE."""
        if self.is_overloaded:
            from mycellm.protocol.errors import ErrorCode, ProtocolError
            raise ProtocolError(ErrorCode.OVERLOADED, "Peer at max concurrent requests")

        queue: asyncio.Queue[MessageEnvelope] = asyncio.Queue(maxsize=500)
        self._pending_responses[msg.id] = queue
        self._active_requests += 1

        try:
            await self.protocol.send_message(msg)
            while True:
                resp = await asyncio.wait_for(queue.get(), timeout=timeout)
                if resp.type == MessageType.INFERENCE_DONE:
                    return
                if resp.type == MessageType.ERROR:
                    raise RuntimeError(resp.payload.get("message", "peer error"))
                yield resp
        finally:
            self._active_requests -= 1
            self._pending_responses.pop(msg.id, None)

    def handle_response(self, msg: MessageEnvelope) -> bool:
        """Handle an incoming message that may be a response to a pending request.

        Returns True if it was consumed as a response.
        """
        pending = self._pending_responses.get(msg.id)
        if pending is None:
            return False
        # Queue-based (streaming) or Future-based (single response)
        if isinstance(pending, asyncio.Queue):
            try:
                pending.put_nowait(msg)
            except asyncio.QueueFull:
                pass
            return True
        if isinstance(pending, asyncio.Future) and not pending.done():
            pending.set_result(msg)
            return True
        return False

    async def ping(self) -> float:
        """Send a ping and measure round-trip time."""
        ping_msg = MessageEnvelope(
            type=MessageType.PING,
            from_peer=self.peer_id,
            payload={},
        )
        self.last_ping = time.time()

        try:
            await self.request(ping_msg, timeout=10.0)
            self.last_pong = time.time()
            rtt = self.last_pong - self.last_ping
            self.record_rtt(rtt)
            return rtt
        except asyncio.TimeoutError:
            self.state = PeerState.DISCONNECTED
            return -1.0

    def record_rtt(self, rtt: float) -> None:
        """Record an RTT measurement."""
        self.rtt_history.append(rtt)
        if len(self.rtt_history) > self._rtt_max_samples:
            self.rtt_history = self.rtt_history[-self._rtt_max_samples:]

    @property
    def avg_rtt(self) -> float:
        """Average RTT from recent measurements."""
        if not self.rtt_history:
            return 0.0
        return sum(self.rtt_history) / len(self.rtt_history)

    def close(self) -> None:
        """Close the connection."""
        self.state = PeerState.DISCONNECTED
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
