"""Peer health checking with rich metrics."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from mycellm.router.registry import PeerRegistry
from mycellm.transport.connection import PeerState

logger = logging.getLogger("mycellm.router.health")


@dataclass
class PeerHealthMetrics:
    """Rolling health metrics for a peer."""
    rtt_samples: list[float] = field(default_factory=list)
    success_history: list[bool] = field(default_factory=list)
    _max_rtt_samples: int = 10
    _max_success_samples: int = 20
    last_check: float = 0.0
    estimated_queue_depth: int = 0
    consecutive_failures: int = 0

    @property
    def avg_rtt(self) -> float:
        if not self.rtt_samples:
            return 0.0
        return sum(self.rtt_samples) / len(self.rtt_samples)

    @property
    def success_rate(self) -> float:
        if not self.success_history:
            return 1.0  # assume healthy until proven otherwise
        return sum(1 for s in self.success_history if s) / len(self.success_history)

    @property
    def health_score(self) -> float:
        """Combined health score (0-1). Higher is better."""
        # Success rate dominates (reliable+slow beats fast+flaky)
        sr = self.success_rate

        # RTT factor (normalize around 200ms, cap penalty)
        rtt_factor = 1.0
        if self.avg_rtt > 0:
            rtt_factor = max(0.3, min(1.0, 0.2 / max(self.avg_rtt, 0.01)))

        # Weighted: 70% reliability, 30% speed
        return sr * 0.7 + rtt_factor * 0.3

    def record_success(self, rtt: float) -> None:
        self.rtt_samples.append(rtt)
        if len(self.rtt_samples) > self._max_rtt_samples:
            self.rtt_samples = self.rtt_samples[-self._max_rtt_samples:]
        self.success_history.append(True)
        if len(self.success_history) > self._max_success_samples:
            self.success_history = self.success_history[-self._max_success_samples:]
        self.last_check = time.time()

    def record_failure(self) -> None:
        self.success_history.append(False)
        if len(self.success_history) > self._max_success_samples:
            self.success_history = self.success_history[-self._max_success_samples:]
        self.last_check = time.time()


class HealthChecker:
    """Periodic health checks on connected peers with rich metrics."""

    def __init__(self, registry: PeerRegistry, interval: float = 30.0, activity=None):
        self._registry = registry
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._metrics: dict[str, PeerHealthMetrics] = {}  # peer_id -> metrics
        self._activity = activity

    def get_metrics(self, peer_id: str) -> PeerHealthMetrics:
        """Get or create health metrics for a peer."""
        if peer_id not in self._metrics:
            self._metrics[peer_id] = PeerHealthMetrics()
        return self._metrics[peer_id]

    def get_health_score(self, peer_id: str) -> float:
        """Get the health score for a peer (0-1)."""
        metrics = self._metrics.get(peer_id)
        return metrics.health_score if metrics else 0.5  # neutral for unknown

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

    _FAILURE_THRESHOLD = 3  # consecutive ping failures before marking DISCONNECTED

    async def _check_all(self) -> None:
        # Check connected peers AND disconnected ones (recovery path).
        connected = self._registry.connected_peers()
        disconnected = [
            p for p in self._registry.all_peers()
            if p.state == PeerState.DISCONNECTED and p.connection is not None
        ]
        peers = connected + disconnected

        for entry in peers:
            if entry.connection is None:
                continue
            metrics = self.get_metrics(entry.peer_id)
            try:
                rtt = await entry.connection.ping()
                if rtt < 0:
                    metrics.record_failure()
                    metrics.consecutive_failures += 1
                    entry.failure_count += 1
                    if metrics.consecutive_failures >= self._FAILURE_THRESHOLD:
                        entry.state = PeerState.DISCONNECTED
                        logger.warning(
                            f"Peer {entry.peer_id[:8]} marked DISCONNECTED after "
                            f"{metrics.consecutive_failures} consecutive ping timeouts "
                            f"(health={metrics.health_score:.2f})"
                        )
                        if self._activity:
                            from mycellm.activity import EventType
                            self._activity.record(EventType.CONNECTION_HEALTH, peer_id=entry.peer_id[:16], status="timeout", health=round(metrics.health_score, 2))
                    else:
                        logger.debug(
                            f"Peer {entry.peer_id[:8]} ping miss "
                            f"{metrics.consecutive_failures}/{self._FAILURE_THRESHOLD} "
                            f"(health={metrics.health_score:.2f})"
                        )
                else:
                    metrics.record_success(rtt)
                    metrics.consecutive_failures = 0
                    entry.state = PeerState.ROUTABLE
                    entry.failure_count = 0
                    logger.debug(f"Peer {entry.peer_id[:8]} RTT={rtt*1000:.0f}ms health={metrics.health_score:.2f}")
            except Exception as e:
                metrics.record_failure()
                metrics.consecutive_failures += 1
                entry.failure_count += 1
                if metrics.consecutive_failures >= self._FAILURE_THRESHOLD:
                    entry.state = PeerState.DISCONNECTED
                    logger.warning(f"Peer {entry.peer_id[:8]} health check failed: {e}")
                    if self._activity:
                        from mycellm.activity import EventType
                        self._activity.record(EventType.CONNECTION_HEALTH, peer_id=entry.peer_id[:16], status="failed", health=round(metrics.health_score, 2))
                else:
                    logger.debug(
                        f"Peer {entry.peer_id[:8]} ping error "
                        f"{metrics.consecutive_failures}/{self._FAILURE_THRESHOLD}: {e}"
                    )
