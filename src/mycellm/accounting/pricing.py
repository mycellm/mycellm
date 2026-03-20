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

# Tier multipliers — incentivize better models
TIER_MULTIPLIERS = {
    "frontier": 3.0,
    "capable": 1.5,
    "fast": 1.0,
    "tiny": 0.5,
}


def compute_cost(tokens: int, model_size_b: float = 7.0, tier: str = "") -> float:
    """Compute cost in credits for consuming inference.

    Simple formula: tokens * base_rate * size_factor * tier_factor
    """
    size_factor = max(1.0, model_size_b / 7.0)
    tier_factor = TIER_MULTIPLIERS.get(tier, 1.0)
    return tokens * BASE_RATE_PER_TOKEN * size_factor * tier_factor


def compute_reward(tokens: int, model_size_b: float = 7.0, tier: str = "") -> float:
    """Compute reward in credits for providing inference.

    Reward equals cost (zero-sum in Phase 1).
    """
    return compute_cost(tokens, model_size_b, tier)
