"""Peer health checking."""

from __future__ import annotations

import asyncio
import logging

from mycellm.router.registry import PeerRegistry
from mycellm.transport.connection import PeerState

logger = logging.getLogger("mycellm.router.health")


class HealthChecker:
    """Periodic health checks on connected peers."""

    def __init__(self, registry: PeerRegistry, interval: float = 30.0):
        self._registry = registry
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._check_all()

    async def _check_all(self) -> None:
        peers = self._registry.connected_peers()
        for entry in peers:
            if entry.connection is None:
                continue
            try:
                rtt = await entry.connection.ping()
                if rtt < 0:
                    entry.state = PeerState.DISCONNECTED
                    entry.failure_count += 1
                    logger.warning(f"Peer {entry.peer_id} ping timeout")
                else:
                    entry.state = PeerState.ROUTABLE
                    entry.failure_count = 0
                    logger.debug(f"Peer {entry.peer_id} RTT={rtt*1000:.0f}ms")
            except Exception as e:
                entry.state = PeerState.DISCONNECTED
                entry.failure_count += 1
                logger.warning(f"Peer {entry.peer_id} health check failed: {e}")
