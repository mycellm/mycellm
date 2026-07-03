"""Federated (coordinated LoRA) training for the mycellm fleet — F3 prototype.

Numerical core + round protocol only. The heavy pieces the plan defers —
real MLX/torch LoRA training on participant nodes, adapter distribution over
the F2 chunk transport, and credit rewards via a new receipt ``work_type`` —
are intentionally NOT wired here; this module de-risks the aggregation +
protocol contract so those can be built against a tested foundation.
"""

from mycellm.training.aggregate import (
    Adapter,
    AggregationError,
    ParticipantUpdate,
    adapter_delta,
    clip_delta,
    federated_average,
    l2_norm,
)
from mycellm.training.round import (
    RoundConfig,
    RoundResult,
    TrainingRound,
    adapter_fingerprint,
    build_train_round_payload,
    build_train_update_payload,
    parse_train_round_payload,
    parse_train_update_payload,
)

__all__ = [
    "Adapter",
    "AggregationError",
    "ParticipantUpdate",
    "adapter_delta",
    "clip_delta",
    "federated_average",
    "l2_norm",
    "RoundConfig",
    "RoundResult",
    "TrainingRound",
    "adapter_fingerprint",
    "build_train_round_payload",
    "build_train_update_payload",
    "parse_train_round_payload",
    "parse_train_update_payload",
]
