"""Network isolation must apply to this node's own requests, not just relayed ones.

⚠️ REGRESSION. `_handle_inference_relay` has always passed the requesting
peer's networks to `ChainBuilder.route`, so a *relayed* request on network A
could not be served by a peer only on network B. The two paths that serve this
node's *own* requests — `route_inference` and `route_inference_stream` —
called `route(model)` with no networks at all, so the isolation the relay path
enforces did not apply to locally-originated traffic.

The registry-level filter was correct the whole time and had no caller on this
path: the same advertised-but-not-enforced shape as the embedding tags and
`routing: "ensemble"`.
"""

import pytest

from mycellm.protocol.capabilities import Capabilities, ModelCapability
from mycellm.router.chain import ChainBuilder
from mycellm.router.registry import PeerRegistry
from mycellm.transport.connection import PeerState


class FakeProto:
    _is_closed = False


class FakeConn:
    is_overloaded = False
    avg_rtt = 0.01
    state = PeerState.ROUTABLE

    def __init__(self):
        self.protocol = FakeProto()


def add_peer(reg, peer_id, model, networks):
    caps = Capabilities(models=[ModelCapability(name=model)])
    reg.register(peer_id, connection=FakeConn(), capabilities=caps,
                 network_ids=networks)
    reg.get(peer_id).state = PeerState.ROUTABLE
    return reg.get(peer_id)


@pytest.fixture
def registry():
    return PeerRegistry()


class TestFilterSemantics:
    """`peers_for_model(network_ids=...)` is the mechanism the fix relies on."""

    def test_peer_on_another_network_is_excluded(self, registry):
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        assert registry.peers_for_model("llama3", network_ids=["net-A"]) == []

    def test_peer_sharing_a_network_is_included(self, registry):
        add_peer(registry, "peer_a", "llama3", ["net-A"])
        got = registry.peers_for_model("llama3", network_ids=["net-A"])
        assert [p.peer_id for p in got] == ["peer_a"]

    def test_peer_with_no_networks_is_public_and_always_eligible(self, registry):
        # Un-upgraded / single-network deployments must keep working.
        add_peer(registry, "legacy", "llama3", [])
        got = registry.peers_for_model("llama3", network_ids=["net-A"])
        assert [p.peer_id for p in got] == ["legacy"]

    def test_none_means_no_restriction(self, registry):
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        got = registry.peers_for_model("llama3", network_ids=None)
        assert [p.peer_id for p in got] == ["peer_b"]


class TestChainBuilderHonoursNetworks:
    def test_route_excludes_foreign_network_peers(self, registry):
        add_peer(registry, "peer_a", "llama3", ["net-A"])
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        cb = ChainBuilder(registry)

        picked = {t.peer_id for t in cb.route("llama3", network_ids=["net-A"])}
        assert picked == {"peer_a"}, "a net-B-only peer must not be routable from net-A"

    def test_route_without_networks_still_returns_everything(self, registry):
        # The un-federated case: no networks declared, no restriction.
        add_peer(registry, "peer_a", "llama3", ["net-A"])
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        cb = ChainBuilder(registry)
        assert len(cb.route("llama3")) == 2


class TestOwnNetworkIds:
    """The accessor that decides what locally-originated routing is scoped to."""

    class _Fed:
        def __init__(self, ids):
            self.network_ids = ids

    class _Node:
        # Bind the real implementation to a stand-in with just `federation`.
        from mycellm.node import MycellmNode
        _own_network_ids = MycellmNode._own_network_ids

        def __init__(self, federation):
            self.federation = federation

    def test_returns_declared_networks(self):
        n = self._Node(self._Fed(["net-A", "net-B"]))
        assert n._own_network_ids() == ["net-A", "net-B"]

    def test_no_networks_means_unrestricted_not_empty(self):
        # Empty list would filter out every peer that declares any network.
        # None is what `peers_for_model` reads as "no restriction".
        n = self._Node(self._Fed([]))
        assert n._own_network_ids() is None

    def test_missing_federation_is_unrestricted(self):
        n = self._Node(None)
        assert n._own_network_ids() is None


class TestTheLeakIsClosed:
    def test_local_request_cannot_reach_a_foreign_network_peer(self, registry):
        """End to end at the routing layer, with the accessor in the loop."""
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        cb = ChainBuilder(registry)

        node = TestOwnNetworkIds._Node(TestOwnNetworkIds._Fed(["net-A"]))
        targets = cb.route("llama3", network_ids=node._own_network_ids())

        assert targets == [], "net-A node must not route its own request to a net-B peer"

    def test_unfederated_node_is_unaffected(self, registry):
        add_peer(registry, "peer_b", "llama3", ["net-B"])
        cb = ChainBuilder(registry)
        node = TestOwnNetworkIds._Node(TestOwnNetworkIds._Fed([]))
        targets = cb.route("llama3", network_ids=node._own_network_ids())
        assert [t.peer_id for t in targets] == ["peer_b"]
