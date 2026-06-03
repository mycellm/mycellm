"""Tests for peer registry and chain builder."""

from mycellm.protocol.capabilities import Capabilities, ModelCapability, HardwareInfo
from mycellm.router.registry import PeerRegistry
from mycellm.router.chain import ChainBuilder
from mycellm.transport.connection import PeerState


def _make_caps(models: list[str], tok_s: float = 50.0) -> Capabilities:
    return Capabilities(
        models=[ModelCapability(name=m) for m in models],
        hardware=HardwareInfo(gpu="test", backend="cpu"),
        est_tok_s=tok_s,
        role="seeder",
    )


class _LiveConn:
    """Minimal open-connection stand-in. is_live() only inspects
    ``connection.protocol._is_closed``, so this is enough to model a peer
    with a live QUIC session."""

    class _Proto:
        _is_closed = False

    def __init__(self, closed: bool = False):
        self.protocol = self._Proto()
        self.protocol._is_closed = closed
        self.is_overloaded = False


def _activate(reg, peer_id, state=PeerState.ROUTABLE, closed: bool = False):
    """Mark a registered peer routable WITH a live connection — the production
    invariant: a peer is only routable/online once it has an open session."""
    entry = reg.get(peer_id)
    entry.state = state
    entry.connection = _LiveConn(closed=closed)
    return entry


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

    # Mark as routable (with a live connection — the routing invariant)
    for p in ["peer1", "peer2", "peer3"]:
        _activate(reg, p)

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
        _activate(reg, p)

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
        _activate(reg, p)

    reg.get("failing").failure_count = 3  # Score = 100 * 0.5^3 = 12.5

    cb = ChainBuilder(reg)
    targets = cb.route("model-a")
    assert targets[0].peer_id == "stable"


def test_connected_peers():
    reg = PeerRegistry()
    reg.register("auth", capabilities=_make_caps([]))
    reg.register("disc", capabilities=_make_caps([]))
    _activate(reg, "auth", state=PeerState.AUTHENTICATED)
    reg.get("disc").state = PeerState.DISCOVERED

    connected = reg.connected_peers()
    assert len(connected) == 1
    assert connected[0].peer_id == "auth"


def test_connected_peers_excludes_dead_connection():
    """A peer stuck ROUTABLE but whose session has dropped (zombie) must not
    count as connected/online."""
    reg = PeerRegistry()
    reg.register("zombie", capabilities=_make_caps([]))
    _activate(reg, "zombie", closed=True)  # ROUTABLE state, but closed conn

    assert reg.connected_peers() == []


def test_peers_for_model_excludes_dead_connection():
    """Routing must skip a model whose only seeder has a dead connection,
    even though it still appears in the model index."""
    reg = PeerRegistry()
    reg.register("zombie", capabilities=_make_caps(["model-z"]))
    _activate(reg, "zombie", closed=True)

    assert reg.peers_for_model("model-z") == []

    # Bring the connection back to life -> routable again.
    _activate(reg, "zombie", closed=False)
    assert [e.peer_id for e in reg.peers_for_model("model-z")] == ["zombie"]
