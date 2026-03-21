"""Reputation scoring for peers based on interaction history."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field


HALF_LIFE_DAYS = 7.0
DECAY_RATE = math.log(2) / (HALF_LIFE_DAYS * 86400)  # per-second decay


@dataclass
class PeerReputation:
    """Tracks reputation metrics for a single peer."""
    peer_id: str
    tokens_served: int = 0
    total_requests: int = 0
    successful_requests: int = 0
    total_response_time: float = 0.0
    receipt_count: int = 0
    last_interaction: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.5  # neutral for new peers
        return self.successful_requests / self.total_requests

    @property
    def avg_response_time(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_response_time / self.successful_requests

    @property
    def score(self) -> float:
        """Compute reputation score with exponential time decay."""
        age = time.time() - self.last_interaction
        decay = math.exp(-DECAY_RATE * age)

        # Base score from success rate (0-1)
        base = self.success_rate

        # Volume bonus (log scale, caps at ~3x for 1000+ requests)
        volume = min(1.0, math.log1p(self.total_requests) / math.log1p(100))

        # Response time penalty (normalize around 2s)
        speed = 1.0
        if self.avg_response_time > 0:
            speed = max(0.3, min(1.0, 2.0 / max(self.avg_response_time, 0.1)))

        # Receipt bonus (verified interactions are worth more)
        receipt_factor = min(1.5, 1.0 + (self.receipt_count * 0.01))

        return base * (0.5 + volume * 0.5) * speed * receipt_factor * decay

    def record_success(self, tokens: int, response_time: float) -> None:
        self.total_requests += 1
        self.successful_requests += 1
        self.tokens_served += tokens
        self.total_response_time += response_time
        self.last_interaction = time.time()

    def record_failure(self) -> None:
        self.total_requests += 1
        self.last_interaction = time.time()

    def record_receipt(self) -> None:
        self.receipt_count += 1
        self.last_interaction = time.time()

    def to_dict(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "tokens_served": self.tokens_served,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "success_rate": round(self.success_rate, 3),
            "avg_response_time": round(self.avg_response_time, 3),
            "receipt_count": self.receipt_count,
            "score": round(self.score, 3),
            "last_interaction": self.last_interaction,
        }


class AdmissionResult:
    """Result of a peer admission check."""
    __slots__ = ("allowed", "reason", "score")

    def __init__(self, allowed: bool, reason: str = "", score: float = 0.0):
        self.allowed = allowed
        self.reason = reason
        self.score = score


class ReputationTracker:
    """Tracks reputation scores for all known peers."""

    def __init__(self):
        self._peers: dict[str, PeerReputation] = {}

    def get(self, peer_id: str) -> PeerReputation:
        if peer_id not in self._peers:
            self._peers[peer_id] = PeerReputation(peer_id=peer_id)
        return self._peers[peer_id]

    def record_success(self, peer_id: str, tokens: int, response_time: float) -> None:
        self.get(peer_id).record_success(tokens, response_time)

    def record_failure(self, peer_id: str) -> None:
        self.get(peer_id).record_failure()

    def record_receipt(self, peer_id: str) -> None:
        self.get(peer_id).record_receipt()

    def score(self, peer_id: str) -> float:
        return self.get(peer_id).score

    def all_scores(self) -> list[dict]:
        return [rep.to_dict() for rep in sorted(
            self._peers.values(), key=lambda r: r.score, reverse=True
        )]

    def check_admission(
        self,
        peer_id: str,
        min_score: float = 0.0,
        require_receipts: bool = False,
        grace_requests: int = 5,
    ) -> AdmissionResult:
        """Check if a peer should be served based on reputation.

        Policy:
          - New peers (< grace_requests interactions): always allowed
            (everyone starts somewhere)
          - Peers with receipts (they've served others): always allowed
          - Peers below min_score after grace period: rejected
          - Peers who only consume and never seed: rejected after grace

        Args:
            peer_id: The requesting peer.
            min_score: Minimum reputation score (0.0 = no minimum).
            require_receipts: If True, reject peers with zero receipts
                after the grace period.
            grace_requests: Number of free requests before enforcement.

        Returns:
            AdmissionResult with allowed flag and reason.
        """
        rep = self.get(peer_id)
        score = rep.score

        # Grace period for new peers — let them try the network
        if rep.total_requests < grace_requests:
            return AdmissionResult(True, "grace_period", score)

        # Peers who have served others (have receipts) are good citizens
        if rep.receipt_count > 0:
            return AdmissionResult(True, "has_receipts", score)

        # Score check
        if min_score > 0 and score < min_score:
            return AdmissionResult(
                False,
                f"reputation_too_low ({score:.2f} < {min_score})",
                score,
            )

        # Require receipts — reject freeloaders who only consume
        if require_receipts and rep.receipt_count == 0 and rep.total_requests >= grace_requests:
            return AdmissionResult(
                False,
                "no_contribution (consuming without seeding)",
                score,
            )

        return AdmissionResult(True, "ok", score)
