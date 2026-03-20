"""Unit tests for ModelResolver."""

import pytest

from mycellm.router.model_resolver import (
    ModelResolver,
    ResolvedModel,
    estimate_param_count,
    derive_tier,
    derive_tags,
)
from mycellm.protocol.capabilities import ModelCapability, Capabilities
from mycellm.router.registry import PeerRegistry
from mycellm.transport.connection import PeerState


def test_estimate_param_count_standard():
    assert estimate_param_count("llama-3.1-8b") == 8.0
    assert estimate_param_count("llama-70b-chat") == 70.0
    assert estimate_param_count("phi-3-3.8B") == 3.8


def test_estimate_param_count_moe():
    assert estimate_param_count("mixtral-8x7b") == 56.0


def test_estimate_param_count_millions():
    assert estimate_param_count("phi-350M") == 0.35


def test_estimate_param_count_unknown():
    assert estimate_param_count("gpt-4o-mini") == 7.0  # default


def test_derive_tier():
    assert derive_tier(70.0) == "frontier"
    assert derive_tier(65.0) == "frontier"
    assert derive_tier(30.0) == "capable"
    assert derive_tier(13.0) == "capable"
    assert derive_tier(8.0) == "fast"
    assert derive_tier(3.0) == "fast"
    assert derive_tier(1.5) == "tiny"


def test_derive_tags_chat():
    tags = derive_tags("llama-3.1-8b")
    assert "chat" in tags


def test_derive_tags_code():
    tags = derive_tags("deepseek-coder-33b")
    assert "code" in tags
    assert "chat" in tags


def test_derive_tags_reasoning():
    tags = derive_tags("qwq-32b")
    assert "reasoning" in tags


def test_derive_tags_vision():
    tags = derive_tags("llava-v1.5-7b")
    assert "vision" in tags


def test_derive_tags_embedding():
    tags = derive_tags("bge-large-embedding")
    assert tags == ["embedding"]


def test_resolver_exact_match():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local_models = [ModelCapability(name="llama-8b", backend="llama.cpp")]
    results = resolver.resolve("llama-8b", local_models)

    assert len(results) >= 1
    assert results[0].model_name == "llama-8b"
    assert results[0].source == "local"


def test_resolver_empty_returns_best():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local_models = [
        ModelCapability(name="tiny-1b"),
        ModelCapability(name="llama-70b-chat"),
    ]
    results = resolver.resolve("", local_models)

    assert len(results) == 2
    # 70b should score higher (frontier tier)
    assert results[0].model_name == "llama-70b-chat"


def test_resolver_tag_match():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local_models = [
        ModelCapability(name="llama-8b"),
        ModelCapability(name="deepseek-coder-6.7b"),
    ]
    results = resolver.resolve("code", local_models)

    # Should match the coder model
    assert any(r.model_name == "deepseek-coder-6.7b" for r in results)


def test_resolver_tier_match():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    local_models = [
        ModelCapability(name="llama-70b-chat"),
        ModelCapability(name="phi-3-mini-3.8b"),
    ]
    results = resolver.resolve("frontier", local_models)

    assert results[0].model_name == "llama-70b-chat"


def test_resolver_includes_peer_models():
    reg = PeerRegistry()
    caps = Capabilities(
        models=[ModelCapability(name="remote-model-13b")],
        est_tok_s=50.0,
    )
    reg.register("peer1", capabilities=caps)
    reg.get("peer1").state = PeerState.ROUTABLE

    resolver = ModelResolver(reg)
    results = resolver.resolve("", [])

    assert len(results) >= 1
    assert any(r.model_name == "remote-model-13b" and r.source == "quic" for r in results)


def test_resolver_includes_fleet_models():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    fleet = {
        "peer1": {
            "status": "approved",
            "peer_id": "abc123",
            "capabilities": {"models": [{"name": "fleet-model-7b"}]},
        }
    }
    results = resolver.resolve("", [], fleet_registry=fleet)

    assert any(r.model_name == "fleet-model-7b" and r.source == "fleet" for r in results)
