"""UDP hole punching for direct P2P QUIC connections.

Coordinates hole punch attempts through the bootstrap relay:
1. Node A sends PUNCH_REQUEST to bootstrap
2. Bootstrap relays PUNCH_INITIATE to Node B with A's candidates
3. Node B responds with PUNCH_RESPONSE containing its candidates
4. Both nodes simultaneously send UDP probes to each other's candidates
5. On success, establish QUIC connection on the punched hole
6. Report PUNCH_RESULT for monitoring
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass, field

from mycellm.nat.types import Candidate, NATInfo

logger = logging.getLogger("mycellm.nat")

# Probe settings
PROBE_TIMEOUT = 10.0    # seconds to wait for hole punch
PROBE_INTERVAL = 0.25   # seconds between probes
PROBE_MAGIC = b"mycellm-punch-v1"  # identifies our probes


@dataclass
class PunchAttempt:
    """Tracks an active hole punch attempt."""
    target_peer_id: str
    our_candidates: list[Candidate]
    their_candidates: list[Candidate] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    result_addr: tuple[str, int] | None = None
    success: bool = False
    error: str = ""


class HolePuncher:
    """Manages UDP hole punch attempts for direct P2P connectivity."""

    def __init__(self, nat_info: NATInfo | None = None):
        self.nat_info = nat_info
        self.active_attempts: dict[str, PunchAttempt] = {}  # peer_id -> attempt
        self._local_sock: socket.socket | None = None

    async def initiate(
        self,
        target_peer_id: str,
        their_candidates: list[Candidate],
        our_candidates: list[Candidate] | None = None,
    ) -> tuple[str, int] | None:
        """Attempt to punch through to a remote peer.

        Returns (ip, port) of the successful path, or None if failed.
        """
        if our_candidates is None and self.nat_info:
            our_candidates = self.nat_info.candidates
        if not our_candidates:
            logger.debug(f"Hole punch: no local candidates for {target_peer_id[:16]}")
            return None
        if not their_candidates:
            logger.debug(f"Hole punch: no remote candidates for {target_peer_id[:16]}")
            return None

        attempt = PunchAttempt(
            target_peer_id=target_peer_id,
            our_candidates=our_candidates,
            their_candidates=their_candidates,
        )
        self.active_attempts[target_peer_id] = attempt

        try:
            result = await asyncio.wait_for(
                self._probe(attempt),
                timeout=PROBE_TIMEOUT,
            )
            if result:
                attempt.result_addr = result
                attempt.success = True
                logger.info(f"Hole punch SUCCESS: {target_peer_id[:16]} via {result[0]}:{result[1]}")
            return result
        except asyncio.TimeoutError:
            attempt.error = "timeout"
            logger.info(f"Hole punch TIMEOUT: {target_peer_id[:16]} after {PROBE_TIMEOUT}s")
            return None
        except Exception as e:
            attempt.error = str(e)
            logger.warning(f"Hole punch ERROR: {target_peer_id[:16]}: {e}")
            return None
        finally:
            self.active_attempts.pop(target_peer_id, None)

    async def _probe(self, attempt: PunchAttempt) -> tuple[str, int] | None:
        """Send UDP probes to all remote candidates simultaneously."""
        # Create a UDP socket for probing
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", 0))

        result_future: asyncio.Future = loop.create_future()

        # Listen for incoming probes (response to ours)
        async def listen():
            while not result_future.done():
                try:
                    data, addr = await loop.sock_recvfrom(sock, 1024)
                    if data.startswith(PROBE_MAGIC):
                        if not result_future.done():
                            result_future.set_result(addr)
                except Exception:
                    break

        # Send probes to all candidates
        async def probe():
            nonce = os.urandom(8)
            payload = PROBE_MAGIC + nonce
            while not result_future.done():
                for c in attempt.their_candidates:
                    try:
                        sock.sendto(payload, (c.ip, c.port))
                    except Exception:
                        pass
                await asyncio.sleep(PROBE_INTERVAL)

        listen_task = asyncio.create_task(listen())
        probe_task = asyncio.create_task(probe())

        try:
            addr = await result_future
            return addr
        finally:
            listen_task.cancel()
            probe_task.cancel()
            sock.close()

    def get_stats(self) -> dict:
        """Return hole punch statistics."""
        return {
            "active_attempts": len(self.active_attempts),
            "nat_type": self.nat_info.nat_type.value if self.nat_info else "unknown",
            "can_hole_punch": self.nat_info.nat_type.can_hole_punch if self.nat_info else False,
        }
