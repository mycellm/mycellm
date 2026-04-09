"""Tests for quality constraints and weighted pricing."""

from mycellm.router.model_resolver import (
    ModelResolver, QualityConstraints,
)
from mycellm.accounting.pricing import compute_cost, compute_reward
from mycellm.protocol.capabilities import ModelCapability
from mycellm.router.registry import PeerRegistry


def test_tier_pricing():
    base = compute_cost(100, model_size_b=7.0, tier="fast")
    capable = compute_cost(100, model_size_b=13.0, tier="capable")
    frontier = compute_cost(100, model_size_b=70.0, tier="frontier")
    assert capable > base
    assert frontier > capable


def test_tiny_tier_cheaper():
    tiny = compute_cost(100, model_size_b=1.0, tier="tiny")
    fast = compute_cost(100, model_size_b=7.0, tier="fast")
    assert tiny < fast


def test_reward_equals_cost():
    cost = compute_cost(100, model_size_b=13.0, tier="capable")
    reward = compute_reward(100, model_size_b=13.0, tier="capable")
    assert cost == reward


def test_constraints_min_tier():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local = [
        ModelCapability(name="tiny-model", param_count_b=1.0),
        ModelCapability(name="big-model", param_count_b=14.0),
    ]

    constraints = QualityConstraints(min_tier="capable")
    results = resolver.resolve("", local, constraints=constraints)
    # Only big-model should pass (14B = capable tier)
    names = [r.model_name for r in results]
    assert "big-model" in names
    assert "tiny-model" not in names


def test_constraints_min_params():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local = [
        ModelCapability(name="small-7b"),
        ModelCapability(name="big-70b"),
    ]

    constraints = QualityConstraints(min_params=10.0)
    results = resolver.resolve("", local, constraints=constraints)
    names = [r.model_name for r in results]
    assert "big-70b" in names
    assert "small-7b" not in names


def test_constraints_required_tags():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local = [
        ModelCapability(name="chat-model", tags=["chat"]),
        ModelCapability(name="code-model", tags=["chat", "code"]),
    ]

    constraints = QualityConstraints(required_tags=["code"])
    results = resolver.resolve("", local, constraints=constraints)
    names = [r.model_name for r in results]
    assert "code-model" in names
    assert "chat-model" not in names


def test_no_constraints_returns_all():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)
    local = [ModelCapability(name="a"), ModelCapability(name="b")]
    results = resolver.resolve("", local)
    assert len(results) == 2


def test_model_features():
    m = ModelCapability(name="test", features=["streaming", "function_calling"])
    d = m.to_dict()
    assert "streaming" in d.get("features", [])
    restored = ModelCapability.from_dict(d)
    assert "function_calling" in restored.features


def test_quality_constraints_defaults():
    c = QualityConstraints()
    assert c.min_tier == ""
    assert c.min_params == 0
    assert c.max_cost == 0
