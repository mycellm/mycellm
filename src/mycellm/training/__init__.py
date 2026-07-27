"""Federated (coordinated LoRA) training for the mycellm fleet — F3 prototype.

Numerical core, round protocol, and the QUIC session that carries a round
between peers. The heavy pieces the plan defers — real MLX/torch LoRA training
on participant nodes (a TrainingParticipant plugs one in as its `trainer`),
adapter distribution over the F2 chunk transport, and credit rewards via a new
receipt ``work_type`` — are intentionally NOT wired here.
"""

from mycellm.training.aggregate import (
    Adapter,
    AggregationError,
    ParticipantUpdate,
    adapter_delta,
    clip_delta,
    federated_average,
    l2_norm,
    validate_update,
)
from mycellm.training.round import (
    RoundConfig,
    RoundResult,
    TrainingRound,
    adapter_fingerprint,
    build_train_result_payload,
    build_train_round_payload,
    build_train_update_payload,
    parse_train_result_payload,
    parse_train_round_payload,
    parse_train_update_payload,
)
from mycellm.training.session import (
    LocalTrainer,
    LocalUpdate,
    RoundOutcome,
    TrainingCoordinator,
    TrainingParticipant,
)

__all__ = [
    "Adapter",
    "AggregationError",
    "ParticipantUpdate",
    "adapter_delta",
    "clip_delta",
    "federated_average",
    "l2_norm",
    "validate_update",
    "RoundConfig",
    "RoundResult",
    "TrainingRound",
    "adapter_fingerprint",
    "build_train_result_payload",
    "build_train_round_payload",
    "build_train_update_payload",
    "parse_train_result_payload",
    "parse_train_round_payload",
    "parse_train_update_payload",
    "LocalTrainer",
    "LocalUpdate",
    "RoundOutcome",
    "TrainingCoordinator",
    "TrainingParticipant",
]
