"""ChainBuilder — route inference to the best peer (single hop in Phase 1)."""

from __future__ import annotations

import logging
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
    """Builds inference routing chains.

    Phase 1: Always returns a single-hop chain (list of length 1).
    Phase 2 will add pipeline parallelism (multi-hop).
    """

    def __init__(self, registry: PeerRegistry):
        self._registry = registry

    def route(self, model: str) -> list[PeerTarget]:
        """Select the best peer for a model inference request.

        Returns list[PeerTarget] of length 0 or 1 in Phase 1.
        """
        candidates = self._registry.peers_for_model(model)
        if not candidates:
            logger.debug(f"No peers available for model '{model}'")
            return []

        # Score candidates: prefer lower load, higher throughput
        scored = []
        for entry in candidates:
            if entry.connection and entry.connection.is_overloaded:
                continue
            score = entry.capabilities.est_tok_s
            if entry.failure_count > 0:
                score *= 0.5 ** entry.failure_count
            scored.append(PeerTarget(peer_id=entry.peer_id, entry=entry, score=score))

        if not scored:
            return []

        # Pick the best
        scored.sort(key=lambda t: t.score, reverse=True)
        best = scored[0]
        logger.debug(f"Routing model '{model}' to peer {best.peer_id} (score={best.score:.1f})")
        return [best]
