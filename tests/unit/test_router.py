"""Tests for peer registry and chain builder."""

from mycellm.protocol.capabilities import Capabilities, ModelCapability, HardwareInfo
from mycellm.router.registry import PeerRegistry, PeerEntry
from mycellm.router.chain import ChainBuilder
from mycellm.transport.connection import PeerState


def _make_caps(models: list[str], tok_s: float = 50.0) -> Capabilities:
    return Capabilities(
        models=[ModelCapability(name=m) for m in models],
        hardware=HardwareInfo(gpu="test", backend="cpu"),
        est_tok_s=tok_s,
        role="seeder",
    )


def test_registry_register_and_lookup():
    reg = PeerRegistry()
    caps = _make_caps(["llama-7b", "qwen-7b"])
    entry = reg.register("peer1", capabilities=caps)
    assert entry.peer_id == "peer1"
    assert reg.get("peer1") is not None


def test_registry_model_index():
    reg = PeerRegistry()
    reg.register("peer1", capabilities=_make_caps(["llama-7b"]))
    reg.register("peer2", capabilities=_make_caps(["qwen-7b"]))
    reg.register("peer3", capabilities=_make_caps(["llama-7b", "qwen-7b"]))

    # Mark as routable
    for p in ["peer1", "peer2", "peer3"]:
        reg.get(p).state = PeerState.ROUTABLE

    llama_peers = reg.peers_for_model("llama-7b")
    assert len(llama_peers) == 2
    qwen_peers = reg.peers_for_model("qwen-7b")
    assert len(qwen_peers) == 2


def test_registry_unregister():
    reg = PeerRegistry()
    reg.register("peer1", capabilities=_make_caps(["llama-7b"]))
    reg.unregister("peer1")
    assert reg.get("peer1") is None
    assert reg.peers_for_model("llama-7b") == []


def test_chain_builder_routes_to_best():
    reg = PeerRegistry()
    reg.register("slow", capabilities=_make_caps(["model-a"], tok_s=10.0))
    reg.register("fast", capabilities=_make_caps(["model-a"], tok_s=100.0))

    for p in ["slow", "fast"]:
        reg.get(p).state = PeerState.ROUTABLE

    cb = ChainBuilder(reg)
    targets = cb.route("model-a")
    assert len(targets) == 2  # Returns all candidates sorted by score
    assert targets[0].peer_id == "fast"  # Best first


def test_chain_builder_no_model():
    reg = PeerRegistry()
    cb = ChainBuilder(reg)
    assert cb.route("nonexistent") == []


def test_chain_builder_penalizes_failures():
    reg = PeerRegistry()
    reg.register("failing", capabilities=_make_caps(["model-a"], tok_s=100.0))
    reg.register("stable", capabilities=_make_caps(["model-a"], tok_s=50.0))

    for p in ["failing", "stable"]:
        reg.get(p).state = PeerState.ROUTABLE

    reg.get("failing").failure_count = 3  # Score = 100 * 0.5^3 = 12.5

    cb = ChainBuilder(reg)
    targets = cb.route("model-a")
    assert targets[0].peer_id == "stable"


def test_connected_peers():
    reg = PeerRegistry()
    reg.register("auth", capabilities=_make_caps([]))
    reg.register("disc", capabilities=_make_caps([]))
    reg.get("auth").state = PeerState.AUTHENTICATED
    reg.get("disc").state = PeerState.DISCOVERED

    connected = reg.connected_peers()
    assert len(connected) == 1
    assert connected[0].peer_id == "auth"
