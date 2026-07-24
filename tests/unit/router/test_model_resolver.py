"""Unit tests for ModelResolver."""

import time
from unittest.mock import MagicMock

from mycellm.api.admin import list_nodes
from mycellm.router.model_resolver import (
    FLEET_ONLINE_WINDOW_S,
    ModelResolver,
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


class _LiveConn:
    """Open-connection stand-in (is_live() only checks protocol._is_closed)."""

    class _Proto:
        _is_closed = False

    def __init__(self, closed: bool = False):
        self.protocol = self._Proto()
        self.protocol._is_closed = closed


def test_resolver_includes_peer_models():
    reg = PeerRegistry()
    caps = Capabilities(
        models=[ModelCapability(name="remote-model-13b")],
        est_tok_s=50.0,
    )
    reg.register("peer1", capabilities=caps)
    reg.get("peer1").state = PeerState.ROUTABLE
    reg.get("peer1").connection = _LiveConn()

    resolver = ModelResolver(reg)
    results = resolver.resolve("", [])

    assert len(results) >= 1
    assert any(r.model_name == "remote-model-13b" and r.source == "quic" for r in results)


def test_resolver_excludes_dead_peer_models():
    """A peer with a dropped session must not surface as an 'auto' candidate,
    so resolution never picks a model that cannot actually answer."""
    reg = PeerRegistry()
    caps = Capabilities(
        models=[ModelCapability(name="remote-model-13b")],
        est_tok_s=50.0,
    )
    reg.register("peer1", capabilities=caps)
    reg.get("peer1").state = PeerState.ROUTABLE
    reg.get("peer1").connection = _LiveConn(closed=True)  # zombie

    resolver = ModelResolver(reg)
    results = resolver.resolve("", [])

    assert all(r.source != "quic" for r in results)


def _fleet_entry(
    name: str = "fleet-model-7b", age_s: float = 0.0, peer_id: str = "abc123"
) -> dict:
    """An approved fleet-registry entry last seen `age_s` seconds ago."""
    return {
        "status": "approved",
        "peer_id": peer_id,
        "capabilities": {"models": [{"name": name}]},
        "last_seen": time.time() - age_s,
    }


def test_resolver_includes_fleet_models():
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    fleet = {"peer1": _fleet_entry()}
    results = resolver.resolve("", [], fleet_registry=fleet)

    assert any(r.model_name == "fleet-model-7b" and r.source == "fleet" for r in results)


def test_resolver_excludes_stale_fleet_models():
    """An approved entry that stopped announcing must not surface as an 'auto'
    candidate — same reasoning as a dropped QUIC session."""
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    fleet = {"peer1": _fleet_entry(age_s=FLEET_ONLINE_WINDOW_S + 60)}
    results = resolver.resolve("", [], fleet_registry=fleet)

    assert results == []


def test_resolver_excludes_fleet_entry_without_last_seen():
    """A registry entry with no last_seen is unusable — announce always stamps
    it — so it must not be routed to."""
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    entry = _fleet_entry()
    del entry["last_seen"]
    results = resolver.resolve("", [], fleet_registry={"peer1": entry})

    assert results == []


def test_resolver_keeps_fresh_fleet_model_alongside_stale():
    """Only the stale entry is dropped; a fresh peer still resolves."""
    reg = PeerRegistry()
    resolver = ModelResolver(reg)

    fleet = {
        "stale": _fleet_entry("stale-model-70b", age_s=FLEET_ONLINE_WINDOW_S + 1),
        "fresh": _fleet_entry("fresh-model-7b", age_s=5),
    }
    results = resolver.resolve("", [], fleet_registry=fleet)

    names = [r.model_name for r in results]
    assert names == ["fresh-model-7b"]


async def test_fleet_online_window_shared_with_admin_listing():
    """The /nodes `online` flag and routing use one threshold, so a node the
    dashboard shows as offline is never a routing candidate."""
    registry = {
        "stale": _fleet_entry("stale-model-70b", age_s=FLEET_ONLINE_WINDOW_S + 1, peer_id="stale"),
        "fresh": _fleet_entry("fresh-model-7b", age_s=1, peer_id="fresh"),
    }
    request = MagicMock()
    request.app.state.node.node_registry = registry

    listed = await list_nodes(request)
    online = {e["peer_id"]: e["online"] for e in listed["nodes"]}
    assert online == {"stale": False, "fresh": True}

    resolver = ModelResolver(PeerRegistry())
    resolved = resolver.resolve("", [], fleet_registry=registry)
    assert [r.model_name for r in resolved] == ["fresh-model-7b"]
