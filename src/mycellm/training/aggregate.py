"""Federated averaging (FedAvg) for coordinated LoRA/adapter training.

This is the numerical core of mycellm's F3 (P2P training) feature: participants
each fine-tune a LoRA adapter locally on their own data, send back the adapter
*delta* (their weights minus the round's base), and the coordinator combines
them into a new global adapter. There is NO distributed gradient descent and no
live tensor exchange during a step — a round is a single request/response cycle,
which is what makes it workable over a high-latency, intermittently-connected
peer fleet (Macs today; see spec-evolution.md F3).

Everything here is pure numpy and framework-agnostic: an "adapter" is just a
mapping of parameter name -> ndarray. The MLX/torch bridge (loading a real LoRA
into these arrays and back) lives at the node edge, so the aggregation math is
testable without a GPU or any ML framework.

The weighting is sample-count-weighted FedAvg (McMahan et al. 2017): a
participant that trained on more examples pulls the global adapter proportionally
harder. With one participant this reduces to "adopt their update"; with equal
sample counts it's a plain mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# A LoRA adapter as a flat name -> tensor map. Names are the adapter's parameter
# keys (e.g. "layers.0.self_attn.q_proj.lora_a"); tensors are float ndarrays.
Adapter = dict[str, np.ndarray]


class AggregationError(ValueError):
    """A participant update is structurally incompatible with the round."""


@dataclass
class ParticipantUpdate:
    """One participant's contribution to a training round.

    `delta` is the adapter change (trained - base) so that averaging composes
    cleanly regardless of the base, and a dropped/zero update is a genuine
    no-op. `num_samples` is the count of local training examples, used as the
    FedAvg weight. `peer_id` identifies the contributor for accounting + audit.
    """

    peer_id: str
    delta: Adapter
    num_samples: int
    # Optional local eval metrics the coordinator can use to gate a bad update
    # out of the average (e.g. loss went up) before it poisons the global model.
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.num_samples < 0:
            raise AggregationError(f"num_samples must be >= 0, got {self.num_samples}")


def validate_update(base: Adapter, update: ParticipantUpdate) -> None:
    """Raise AggregationError unless `update` matches the round's base adapter.

    Called by federated_average before averaging, and by the coordinator's
    transport layer on arrival so one malformed participant is dropped from the
    round instead of failing the whole aggregation.
    """
    missing = set(base) - set(update.delta)
    extra = set(update.delta) - set(base)
    if missing or extra:
        raise AggregationError(
            f"peer {update.peer_id[:8]} adapter keys mismatch "
            f"(missing={sorted(m[:24] for m in missing)}, "
            f"extra={sorted(e[:24] for e in extra)})"
        )
    for name, tensor in update.delta.items():
        if tensor.shape != base[name].shape:
            raise AggregationError(
                f"peer {update.peer_id[:8]} tensor '{name}' shape "
                f"{tensor.shape} != base {base[name].shape}"
            )


def federated_average(
    base: Adapter,
    updates: list[ParticipantUpdate],
    *,
    learning_rate: float = 1.0,
) -> Adapter:
    """Combine participant deltas into a new global adapter.

    new = base + learning_rate * (Σ nᵢ·deltaᵢ) / (Σ nᵢ)

    `learning_rate` (server-side, aka server momentum of 1.0 = plain FedAvg)
    lets the coordinator take a partial step toward the aggregate — useful for
    damping noisy early rounds. Updates with num_samples==0 are ignored (a
    participant that trained on nothing has no signal). Raises AggregationError
    if the round has no usable signal so the caller can retry rather than
    silently returning the unchanged base.
    """
    usable = [u for u in updates if u.num_samples > 0]
    if not usable:
        raise AggregationError("no participant updates with samples > 0")

    for u in usable:
        validate_update(base, u)

    total = sum(u.num_samples for u in usable)
    result: Adapter = {}
    for name, base_tensor in base.items():
        acc = np.zeros_like(base_tensor, dtype=np.float64)
        for u in usable:
            acc += u.num_samples * u.delta[name].astype(np.float64)
        averaged = acc / total
        result[name] = (base_tensor + learning_rate * averaged).astype(base_tensor.dtype)
    return result


def adapter_delta(base: Adapter, trained: Adapter) -> Adapter:
    """Compute trained - base per tensor (what a participant sends back)."""
    if set(base) != set(trained):
        raise AggregationError("base/trained adapter keys differ")
    return {name: trained[name] - base[name] for name in base}


def l2_norm(adapter: Adapter) -> float:
    """Flat L2 norm across all tensors — a cheap magnitude for a delta.

    Used to spot a runaway participant (huge delta = likely divergence or an
    attempted poison) so the coordinator can clip or drop it before averaging.
    """
    return float(np.sqrt(sum(float(np.sum(t.astype(np.float64) ** 2)) for t in adapter.values())))


def clip_delta(delta: Adapter, max_norm: float) -> Adapter:
    """Scale a delta down so its L2 norm is at most max_norm (else unchanged).

    Norm-clipping bounds any single participant's influence per round — the
    cooperative-trust analogue of gradient clipping, and the first line of
    defense the F3 plan calls for before secure aggregation exists.
    """
    norm = l2_norm(delta)
    if norm <= max_norm or norm == 0.0:
        return delta
    scale = max_norm / norm
    return {name: (t.astype(np.float64) * scale).astype(t.dtype) for name, t in delta.items()}
