"""NAT discovery via STUN and observed addresses.

Determines this node's public IP:port mapping and NAT type by querying
multiple STUN servers and comparing with the bootstrap's observed_addr.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import os
from collections import Counter

from mycellm.nat.types import NATInfo, NATType

logger = logging.getLogger("mycellm.nat")

# Public STUN servers (UDP, port 3478 default)
DEFAULT_STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.stunprotocol.org", 3478),
]

# STUN message constants
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101
STUN_MAGIC_COOKIE = 0x2112A442
STUN_ATTR_XOR_MAPPED_ADDRESS = 0x0020
STUN_ATTR_MAPPED_ADDRESS = 0x0001


class NATDiscovery:
    """Discovers NAT type and public address using STUN."""

    def __init__(self, stun_servers: list[tuple[str, int]] | None = None):
        self.stun_servers = stun_servers or DEFAULT_STUN_SERVERS
        self.info = NATInfo()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self, local_port: int = 0, interval: float = 300.0) -> None:
        """Start periodic NAT discovery."""
        self._running = True
        self.info.local_ip = _get_local_ip()
        self.info.local_port = local_port
        await self.probe_once()
        self._task = asyncio.create_task(self._periodic_probe(interval))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def probe_once(self) -> NATInfo:
        """Query STUN servers and determine NAT info."""
        results: list[tuple[str, int]] = []

        for host, port in self.stun_servers:
            try:
                mapped = await asyncio.wait_for(
                    _stun_binding_request(host, port),
                    timeout=3.0,
                )
                if mapped:
                    results.append(mapped)
            except (asyncio.TimeoutError, OSError) as e:
                logger.debug(f"STUN query to {host}:{port} failed: {e}")

        self.info.stun_servers_queried = len(results)

        if not results:
            self.info.nat_type = NATType.UNKNOWN
            self.info.confidence = 0.0
            logger.warning("NAT discovery: no STUN servers responded")
            return self.info

        # Most common IP:port mapping
        ip_counts = Counter(r[0] for r in results)
        port_counts = Counter(r[1] for r in results)
        most_common_ip = ip_counts.most_common(1)[0][0]
        most_common_port = port_counts.most_common(1)[0][0]

        self.info.public_ip = most_common_ip
        self.info.public_port = most_common_port

        # Classify NAT type
        unique_ports = len(set(r[1] for r in results))
        if unique_ports == 1:
            # Same port from all servers — likely cone NAT or open
            if self.info.local_ip == most_common_ip:
                self.info.nat_type = NATType.OPEN
            else:
                self.info.nat_type = NATType.FULL_CONE
        elif unique_ports == len(results):
            # Different port per server — symmetric NAT
            self.info.nat_type = NATType.SYMMETRIC
        else:
            # Mixed — likely port-restricted
            self.info.nat_type = NATType.PORT_RESTRICTED

        self.info.confidence = len(results) / len(self.stun_servers)

        logger.info(
            f"NAT discovery: {self.info.nat_type.value} "
            f"({self.info.public_ip}:{self.info.public_port}, "
            f"confidence={self.info.confidence:.0%}, "
            f"hole_punch={'yes' if self.info.nat_type.can_hole_punch else 'no'})"
        )
        return self.info

    def set_observed_addr(self, addr: str) -> None:
        """Set the observed address from bootstrap NODE_HELLO_ACK."""
        self.info.observed_addr = addr
        if ":" in addr:
            ip, port_str = addr.rsplit(":", 1)
            if not self.info.public_ip:
                self.info.public_ip = ip
                try:
                    self.info.public_port = int(port_str)
                except ValueError:
                    pass

    async def _periodic_probe(self, interval: float) -> None:
        while self._running:
            await asyncio.sleep(interval)
            if self._running:
                await self.probe_once()


async def _stun_binding_request(host: str, port: int) -> tuple[str, int] | None:
    """Send a STUN Binding Request and parse the mapped address from the response."""
    # Build STUN Binding Request
    txn_id = os.urandom(12)
    header = struct.pack("!HHI", STUN_BINDING_REQUEST, 0, STUN_MAGIC_COOKIE) + txn_id
    assert len(header) == 20

    # Resolve host
    infos = await asyncio.get_event_loop().getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    if not infos:
        return None
    remote_addr = infos[0][4]

    # Send and receive
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _STUNProtocol(txn_id),
        remote_addr=remote_addr,
    )
    try:
        transport.sendto(header)
        return await asyncio.wait_for(protocol.result, timeout=2.0)
    finally:
        transport.close()


class _STUNProtocol(asyncio.DatagramProtocol):
    def __init__(self, txn_id: bytes):
        self.txn_id = txn_id
        self.result: asyncio.Future = asyncio.get_event_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if len(data) < 20:
            return
        msg_type, msg_len, magic = struct.unpack_from("!HHI", data, 0)
        if msg_type != STUN_BINDING_RESPONSE or magic != STUN_MAGIC_COOKIE:
            return
        resp_txn = data[8:20]
        if resp_txn != self.txn_id:
            return

        # Parse attributes
        offset = 20
        while offset + 4 <= len(data):
            attr_type, attr_len = struct.unpack_from("!HH", data, offset)
            offset += 4
            if offset + attr_len > len(data):
                break

            if attr_type == STUN_ATTR_XOR_MAPPED_ADDRESS:
                result = self._parse_xor_mapped(data, offset, attr_len)
                if result and not self.result.done():
                    self.result.set_result(result)
                    return
            elif attr_type == STUN_ATTR_MAPPED_ADDRESS:
                result = self._parse_mapped(data, offset, attr_len)
                if result and not self.result.done():
                    self.result.set_result(result)
                    return

            # Pad to 4-byte boundary
            offset += attr_len + (4 - attr_len % 4) % 4

    def _parse_xor_mapped(self, data: bytes, offset: int, length: int) -> tuple[str, int] | None:
        if length < 8:
            return None
        family = data[offset + 1]
        if family != 0x01:  # IPv4 only
            return None
        xport = struct.unpack_from("!H", data, offset + 2)[0] ^ (STUN_MAGIC_COOKIE >> 16)
        xip = struct.unpack_from("!I", data, offset + 4)[0] ^ STUN_MAGIC_COOKIE
        ip = socket.inet_ntoa(struct.pack("!I", xip))
        return (ip, xport)

    def _parse_mapped(self, data: bytes, offset: int, length: int) -> tuple[str, int] | None:
        if length < 8:
            return None
        family = data[offset + 1]
        if family != 0x01:
            return None
        port = struct.unpack_from("!H", data, offset + 2)[0]
        ip_int = struct.unpack_from("!I", data, offset + 4)[0]
        ip = socket.inet_ntoa(struct.pack("!I", ip_int))
        return (ip, port)

    def error_received(self, exc: Exception) -> None:
        if not self.result.done():
            self.result.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if not self.result.done():
            self.result.set_exception(ConnectionError("STUN connection lost"))


def _get_local_ip() -> str:
    """Get the local IP address (the one used for default routing)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
