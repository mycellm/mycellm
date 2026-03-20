"""PeerManager — owns lifecycle of all outbound QUIC connections."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

from mycellm.protocol.capabilities import Capabilities
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.node_hello import NodeHello
from mycellm.transport.connection import PeerConnection, PeerState

logger = logging.getLogger("mycellm.transport.peer_manager")


class PeerConnectionState(str, Enum):
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    ROUTABLE = "routable"
    DISCONNECTED = "disconnected"


class ManagedPeer:
    """Tracks state for a single outbound peer connection."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.state = PeerConnectionState.DISCONNECTED
        self.connection: PeerConnection | None = None
        self.peer_id: str = ""
        self.reconnect_attempts: int = 0
        self.last_connect_attempt: float = 0.0
        self.last_connected: float = 0.0
        self.consecutive_ping_failures: int = 0
        self._reconnect_task: asyncio.Task | None = None

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"

    def backoff_delay(self) -> float:
        """Exponential backoff: 5s -> 10s -> 20s -> 40s -> 60s cap."""
        delay = min(5 * (2 ** self.reconnect_attempts), 60)
        return float(delay)


class PeerManager:
    """Manages outbound QUIC connections with automatic reconnection."""

    def __init__(
        self,
        node,  # MycellmNode — forward ref to avoid circular import
        heartbeat_interval: float = 15.0,
        max_ping_failures: int = 3,
    ):
        self._node = node
        self._heartbeat_interval = heartbeat_interval
        self._max_ping_failures = max_ping_failures
        self._managed_peers: dict[str, ManagedPeer] = {}  # "host:port" -> ManagedPeer
        self._tasks: list[asyncio.Task] = []
        self._running = False

    @property
    def managed_peers(self) -> dict[str, ManagedPeer]:
        return self._managed_peers

    async def start(self, bootstrap_peers: list[tuple[str, int]]) -> None:
        """Start managing connections to bootstrap peers."""
        self._running = True
        for host, port in bootstrap_peers:
            key = f"{host}:{port}"
            if key not in self._managed_peers:
                peer = ManagedPeer(host, port)
                self._managed_peers[key] = peer
                task = asyncio.create_task(self._manage_peer(peer))
                self._tasks.append(task)

    async def stop(self) -> None:
        """Stop all managed connections."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        for peer in self._managed_peers.values():
            if peer.connection:
                peer.connection.close()
                peer.connection = None
            peer.state = PeerConnectionState.DISCONNECTED

    def add_peer(self, host: str, port: int) -> None:
        """Add a new peer to manage (e.g. discovered via DHT)."""
        key = f"{host}:{port}"
        if key not in self._managed_peers and self._running:
            peer = ManagedPeer(host, port)
            self._managed_peers[key] = peer
            task = asyncio.create_task(self._manage_peer(peer))
            self._tasks.append(task)

    def get_connections(self) -> list[dict]:
        """Get diagnostic info for all managed peers."""
        result = []
        for key, peer in self._managed_peers.items():
            info = {
                "address": key,
                "state": peer.state.value,
                "peer_id": peer.peer_id or None,
                "reconnect_attempts": peer.reconnect_attempts,
                "consecutive_ping_failures": peer.consecutive_ping_failures,
            }
            if peer.last_connected > 0:
                info["connected_since"] = peer.last_connected
                info["uptime_seconds"] = time.time() - peer.last_connected if peer.state == PeerConnectionState.ROUTABLE else 0
            if peer.connection and peer.state == PeerConnectionState.ROUTABLE:
                info["rtt_ms"] = round((peer.connection.last_pong - peer.connection.last_ping) * 1000, 1) if peer.connection.last_pong > peer.connection.last_ping else None
            result.append(info)
        return result

    async def _manage_peer(self, peer: ManagedPeer) -> None:
        """Main loop for a single peer: connect, heartbeat, reconnect."""
        while self._running:
            try:
                if peer.state == PeerConnectionState.DISCONNECTED:
                    await self._connect_peer(peer)

                if peer.state == PeerConnectionState.ROUTABLE:
                    await self._heartbeat_loop(peer)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Peer {peer.addr} management error: {e}")
                peer.state = PeerConnectionState.DISCONNECTED

            if not self._running:
                return

            # Wait before reconnecting
            delay = peer.backoff_delay()
            logger.debug(f"Reconnecting to {peer.addr} in {delay:.0f}s (attempt {peer.reconnect_attempts + 1})")
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            peer.reconnect_attempts += 1

    async def _connect_peer(self, peer: ManagedPeer) -> None:
        """Attempt to connect and authenticate with a peer."""
        from mycellm.transport.quic import dial_peer
        from mycellm.transport.auth import build_node_hello, verify_hello_message
        from mycellm.cli.banner import styled_tag

        peer.state = PeerConnectionState.CONNECTING
        peer.last_connect_attempt = time.time()

        try:
            protocol = await dial_peer(
                peer.host, peer.port,
                connection_timeout=10.0,
                idle_timeout=60.0,
            )
        except Exception as e:
            logger.debug(f"QUIC dial to {peer.addr} failed: {e}")
            peer.state = PeerConnectionState.DISCONNECTED
            return

        # Set up message handler to dispatch to node
        async def handler(msg: MessageEnvelope, stream_id: int):
            await self._node._handle_peer_message(protocol, msg, stream_id)
        protocol.set_message_handler(handler)

        peer.state = PeerConnectionState.HANDSHAKING

        try:
            hello_msg = build_node_hello(
                self._node.device_key, self._node.device_cert, self._node.capabilities
            )
            ack = await protocol.send_and_wait(hello_msg, timeout=10.0)

            if ack.type == MessageType.ERROR:
                logger.warning(f"{styled_tag('P2P')} Peer {peer.addr} rejected: {ack.payload}")
                protocol.close()
                peer.state = PeerConnectionState.DISCONNECTED
                return

            if ack.type == MessageType.NODE_HELLO_ACK:
                hello_data = ack.payload.get("hello")
                if hello_data:
                    peer_hello = NodeHello.from_cbor(hello_data)

                    # Check for observed_addr
                    observed = ack.payload.get("observed_addr")
                    if observed:
                        logger.debug(f"Observed address from {peer.addr}: {observed}")

                    conn = PeerConnection(
                        peer_id=peer_hello.peer_id,
                        protocol=protocol,
                        hello=peer_hello,
                        state=PeerState.ROUTABLE,
                    )
                    peer.connection = conn
                    peer.peer_id = peer_hello.peer_id
                    peer.state = PeerConnectionState.ROUTABLE
                    peer.reconnect_attempts = 0
                    peer.consecutive_ping_failures = 0
                    peer.last_connected = time.time()

                    # Register with node
                    self._node._peer_connections[peer_hello.peer_id] = conn
                    self._node.registry.register(
                        peer_hello.peer_id,
                        connection=conn,
                        capabilities=peer_hello.capabilities,
                    )

                    logger.info(
                        f"{styled_tag('P2P')} Connected to {peer.addr} "
                        f"(peer: {peer_hello.peer_id[:16]}...)"
                    )
                    return

        except asyncio.TimeoutError:
            logger.debug(f"Handshake timeout with {peer.addr}")
        except Exception as e:
            logger.debug(f"Handshake failed with {peer.addr}: {e}")

        try:
            protocol.close()
        except Exception:
            pass
        peer.state = PeerConnectionState.DISCONNECTED

    async def _heartbeat_loop(self, peer: ManagedPeer) -> None:
        """Send periodic heartbeats. Returns when connection is lost."""
        from mycellm.cli.banner import styled_tag

        while self._running and peer.state == PeerConnectionState.ROUTABLE:
            await asyncio.sleep(self._heartbeat_interval)

            if not peer.connection or peer.connection.protocol._is_closed:
                logger.info(f"{styled_tag('P2P')} Connection to {peer.addr} closed")
                self._disconnect_peer(peer)
                return

            try:
                rtt = await peer.connection.ping()
                if rtt < 0:
                    peer.consecutive_ping_failures += 1
                    logger.debug(f"Ping timeout to {peer.addr} ({peer.consecutive_ping_failures}/{self._max_ping_failures})")
                else:
                    peer.consecutive_ping_failures = 0
                    logger.debug(f"Heartbeat {peer.addr} RTT={rtt*1000:.0f}ms")
            except Exception:
                peer.consecutive_ping_failures += 1

            if peer.consecutive_ping_failures >= self._max_ping_failures:
                logger.warning(f"{styled_tag('P2P')} Lost connection to {peer.addr} (ping failures)")
                self._disconnect_peer(peer)
                return

    def _disconnect_peer(self, peer: ManagedPeer) -> None:
        """Clean up a disconnected peer."""
        if peer.peer_id:
            self._node._peer_connections.pop(peer.peer_id, None)
            entry = self._node.registry.get(peer.peer_id)
            if entry:
                entry.state = PeerState.DISCONNECTED

        if peer.connection:
            try:
                peer.connection.close()
            except Exception:
                pass
            peer.connection = None

        peer.state = PeerConnectionState.DISCONNECTED
