"""Training-round protocol messages + the coordinator's round state machine.

A round is one request/response cycle:

  coordinator --TRAIN_ROUND--> participants   (base adapter + hyperparams)
  participant --TRAIN_UPDATE-> coordinator     (local delta + sample count)
  coordinator --TRAIN_RESULT-> participants    (new global adapter manifest)

The coordinator collects updates until a quorum is reached or the round
deadline passes, aggregates with federated_average (norm-clipping + optional
eval-gating first), and publishes the result. This module owns the pure
state machine and the wire payloads; the QUIC wiring that carries them
(broadcast/collect/publish) lives in training/session.py so this stays
unit-testable without a transport.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from mycellm.training.aggregate import (
    Adapter,
    AggregationError,
    ParticipantUpdate,
    clip_delta,
    federated_average,
    l2_norm,
)
from mycellm.training.codec import decode_adapter, encode_adapter


@dataclass
class RoundConfig:
    """Hyperparameters the coordinator hands each participant for a round."""

    job_id: str
    round_index: int
    base_model: str  # model the adapter attaches to (e.g. "Qwen2.5-3B")
    # Local-training hyperparams (participant applies these to its own data).
    local_epochs: int = 1
    learning_rate: float = 2e-4
    batch_size: int = 4
    # Coordinator-side round controls.
    min_participants: int = 1
    deadline_s: float = 300.0
    max_delta_norm: float = 0.0  # 0 = no clipping
    server_lr: float = 1.0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "round_index": self.round_index,
            "base_model": self.base_model,
            "local_epochs": self.local_epochs,
            "learning_rate": self.learning_rate,
            "batch_size": self.batch_size,
            "min_participants": self.min_participants,
            "deadline_s": self.deadline_s,
            "max_delta_norm": self.max_delta_norm,
            "server_lr": self.server_lr,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RoundConfig:
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


def build_train_round_payload(config: RoundConfig, base: Adapter) -> dict:
    """TRAIN_ROUND payload: config + the base adapter every participant starts from."""
    return {"config": config.to_dict(), "base_adapter": encode_adapter(base)}


def parse_train_round_payload(payload: dict) -> tuple[RoundConfig, Adapter]:
    return RoundConfig.from_dict(payload["config"]), decode_adapter(payload["base_adapter"])


def build_train_update_payload(update: ParticipantUpdate) -> dict:
    """TRAIN_UPDATE payload a participant returns."""
    return {
        "peer_id": update.peer_id,
        "num_samples": update.num_samples,
        "delta": encode_adapter(update.delta),
        "metrics": update.metrics,
    }


def parse_train_update_payload(payload: dict) -> ParticipantUpdate:
    return ParticipantUpdate(
        peer_id=payload["peer_id"],
        num_samples=int(payload["num_samples"]),
        delta=decode_adapter(payload["delta"]),
        metrics=dict(payload.get("metrics") or {}),
    )


@dataclass
class RoundResult:
    job_id: str
    round_index: int
    adapter: Adapter
    contributors: list[str]
    dropped: list[str]  # peer_ids excluded (no samples / clipped-out / gated)
    total_samples: int


def build_train_result_payload(result: RoundResult) -> dict:
    """TRAIN_RESULT payload: the new global adapter plus who produced it.

    The fingerprint travels alongside the adapter so a participant can check
    that what arrived is what the coordinator named before adopting it.
    """
    return {
        "job_id": result.job_id,
        "round_index": result.round_index,
        "adapter": encode_adapter(result.adapter),
        "fingerprint": adapter_fingerprint(result.adapter),
        "contributors": list(result.contributors),
        "dropped": list(result.dropped),
        "total_samples": result.total_samples,
    }


def parse_train_result_payload(payload: dict) -> RoundResult:
    return RoundResult(
        job_id=payload["job_id"],
        round_index=int(payload["round_index"]),
        adapter=decode_adapter(payload["adapter"]),
        contributors=list(payload.get("contributors") or []),
        dropped=list(payload.get("dropped") or []),
        total_samples=int(payload["total_samples"]),
    )


@dataclass
class TrainingRound:
    """Coordinator-side collection + aggregation for a single round."""

    config: RoundConfig
    base: Adapter
    _updates: dict[str, ParticipantUpdate] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.time)

    def submit(self, update: ParticipantUpdate) -> None:
        """Record a participant's update (last write wins per peer)."""
        self._updates[update.peer_id] = update

    def has_quorum(self) -> bool:
        usable = [u for u in self._updates.values() if u.num_samples > 0]
        return len(usable) >= self.config.min_participants

    def is_expired(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self._started_at) >= self.config.deadline_s

    def ready(self, *, now: float | None = None) -> bool:
        """Round can be aggregated: quorum met, or deadline passed with any signal."""
        if self.has_quorum():
            return True
        return self.is_expired(now=now) and any(
            u.num_samples > 0 for u in self._updates.values()
        )

    def aggregate(self) -> RoundResult:
        """Clip, (optionally) gate, and federated-average the collected updates."""
        dropped: list[str] = []
        prepared: list[ParticipantUpdate] = []
        for u in self._updates.values():
            if u.num_samples <= 0:
                dropped.append(u.peer_id)
                continue
            delta = u.delta
            if self.config.max_delta_norm > 0 and l2_norm(delta) > self.config.max_delta_norm:
                delta = clip_delta(delta, self.config.max_delta_norm)
            prepared.append(
                ParticipantUpdate(u.peer_id, delta, u.num_samples, u.metrics)
            )

        if not prepared:
            raise AggregationError("round has no usable updates to aggregate")

        adapter = federated_average(
            self.base, prepared, learning_rate=self.config.server_lr
        )
        return RoundResult(
            job_id=self.config.job_id,
            round_index=self.config.round_index,
            adapter=adapter,
            contributors=[u.peer_id for u in prepared],
            dropped=dropped,
            total_samples=sum(u.num_samples for u in prepared),
        )


def adapter_fingerprint(adapter: Adapter) -> str:
    """Stable short hash of an adapter's contents — identifies a global version.

    Deterministic across peers (sorted keys, canonical bytes) so a TRAIN_RESULT
    can name the exact adapter every participant should now hold, and the value
    doubles as the CAS/DHT key for distributing it (reuses F2's model transport).
    """
    import hashlib

    h = hashlib.sha256()
    for name in sorted(adapter):
        t = adapter[name]
        h.update(name.encode())
        h.update(str(t.dtype).encode())
        h.update(np.ascontiguousarray(t).tobytes())
    return h.hexdigest()[:16]
