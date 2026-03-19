"""Simple pricing formulas for Phase 1."""

from __future__ import annotations


# Base rate: credits per token
BASE_RATE_PER_TOKEN = 0.001

# GPU multiplier (faster hardware earns more per unit time but same per token)
GPU_MULTIPLIERS = {
    "cpu": 1.0,
    "cuda": 1.0,
    "metal": 1.0,
    "rocm": 1.0,
}


def compute_cost(tokens: int, model_size_b: float = 7.0) -> float:
    """Compute cost in credits for consuming inference.

    Simple formula: tokens * base_rate * size_factor
    """
    size_factor = max(1.0, model_size_b / 7.0)
    return tokens * BASE_RATE_PER_TOKEN * size_factor


def compute_reward(tokens: int, model_size_b: float = 7.0) -> float:
    """Compute reward in credits for providing inference.

    Reward equals cost (zero-sum in Phase 1).
    """
    return compute_cost(tokens, model_size_b)
