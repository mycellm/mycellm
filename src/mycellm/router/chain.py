"""ChainBuilder — route inference to the best peer (single hop in Phase 1, failover in Phase 2)."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from mycellm.router.registry import PeerEntry, PeerRegistry

logger = logging.getLogger("mycellm.router")


@dataclass
class PeerTarget:
    """A peer selected for inference routing."""
    peer_id: str
    entry: PeerEntry
    score: float = 0.0


class ChainBuilder:
    """Builds inference routing chains with failover support."""

    def __init__(self, registry: PeerRegistry, health_checker=None):
        self._registry = registry
        self._health_checker = health_checker

    def route(self, model: str) -> list[PeerTarget]:
        """Select peers for a model inference request.

        Returns all viable candidates sorted by score (best first).
        Supports failover: if first fails, try next in list.
        """
        candidates = self._registry.peers_for_model(model)
        if not candidates:
            logger.debug(f"No peers available for model '{model}'")
            return []

        scored = []
        for entry in candidates:
            if entry.connection and entry.connection.is_overloaded:
                continue
            score = self._score_peer(entry)
            scored.append(PeerTarget(peer_id=entry.peer_id, entry=entry, score=score))

        if not scored:
            return []

        scored.sort(key=lambda t: t.score, reverse=True)
        logger.debug(
            f"Routing model '{model}': {len(scored)} candidates, "
            f"best={scored[0].peer_id[:8]} (score={scored[0].score:.1f})"
        )
        return scored

    def route_weighted(self, model: str) -> list[PeerTarget]:
        """Select peers with weighted-random primary, rest as fallback.

        Distributes load proportional to capability scores.
        """
        scored = self.route(model)
        if len(scored) <= 1:
            return scored

        # Normalize scores to probabilities
        total = sum(t.score for t in scored)
        if total <= 0:
            return scored

        weights = [t.score / total for t in scored]

        # Weighted random selection for primary
        primary_idx = random.choices(range(len(scored)), weights=weights, k=1)[0]

        # Reorder: primary first, then rest by score
        result = [scored[primary_idx]]
        for i, target in enumerate(scored):
            if i != primary_idx:
                result.append(target)

        return result

    def route_multihop(self, model: str, max_hops: int = 3) -> list[PeerTarget]:
        """Build a multi-hop routing chain.

        If direct peers can serve the model, returns single-hop.
        Otherwise, finds relay peers that are connected to model-serving peers.
        """
        # First try direct route
        direct = self.route(model)
        if direct:
            return direct

        # Try to find relay paths through connected peers
        # A relay peer is one that is connected and whose peers might serve the model
        connected = self._registry.connected_peers()
        if not connected:
            return []

        # For now, return connected peers as potential relays
        # The actual model resolution happens at each hop
        relays = []
        for entry in connected:
            if entry.connection and not entry.connection.is_overloaded:
                score = self._score_peer(entry) * 0.5  # relay penalty
                relays.append(PeerTarget(
                    peer_id=entry.peer_id, entry=entry, score=score
                ))

        relays.sort(key=lambda t: t.score, reverse=True)
        return relays[:max_hops]

    def _score_peer(self, entry: PeerEntry) -> float:
        """Score a peer for routing priority."""
        score = max(entry.capabilities.est_tok_s, 1.0)

        # Failure penalty (exponential)
        if entry.failure_count > 0:
            score *= 0.5 ** entry.failure_count

        # Health bonus: prefer peers with recent successful pings
        if entry.connection:
            avg_rtt = getattr(entry.connection, 'avg_rtt', 0.0)
            if avg_rtt > 0:
                # Lower RTT = higher score (normalize around 100ms)
                rtt_factor = max(0.1, 1.0 - (avg_rtt - 0.1))
                score *= rtt_factor

        # Health metrics scoring
        if self._health_checker:
            health_score = self._health_checker.get_health_score(entry.peer_id)
            score *= (0.5 + health_score)  # range 0.5-1.5x

        return score
