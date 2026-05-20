"""Tests for multi-network membership and model scoping."""

from pathlib import Path
from unittest.mock import MagicMock

from mycellm.federation import (
    FederationManager,
    NetworkMembership,
)
from mycellm.protocol.capabilities import ModelCapability, Capabilities
from mycellm.router.registry import PeerRegistry
from mycellm.transport.connection import PeerState


def test_model_scope_default():
    m = ModelCapability(name="llama-7b")
    assert m.scope == "home"
    assert m.visible_networks == []


def test_model_scope_public():
    m = ModelCapability(name="llama-7b", scope="public")
    d = m.to_dict()
    assert d["scope"] == "public"
    restored = ModelCapability.from_dict(d)
    assert restored.scope == "public"


def test_model_scope_networks():
    m = ModelCapability(name="llama-7b", scope="networks", visible_networks=["net-a", "net-b"])
    d = m.to_dict()
    assert d["visible_networks"] == ["net-a", "net-b"]
    restored = ModelCapability.from_dict(d)
    assert restored.visible_networks == ["net-a", "net-b"]


def test_network_membership_roundtrip():
    m = NetworkMembership(
        network_id="abc123",
        network_name="Test Net",
        role="seeder",
        bootstrap_addrs=["1.2.3.4:8421"],
        models=["llama-7b"],
        quota={"max_req_per_min": 20},
    )
    d = m.to_dict()
    restored = NetworkMembership.from_dict(d)
    assert restored.network_id == "abc123"
    assert restored.models == ["llama-7b"]
    assert restored.quota == {"max_req_per_min": 20}


def test_federation_join_and_leave(tmp_path):
    fm = FederationManager(tmp_path)
    fm.init_network(b'\x00' * 32, network_name="home")

    # Join a second network
    m = fm.join_network("net-public", network_name="Public", role="seeder", models=["llama-7b"])
    assert m.network_name == "Public"
    assert "net-public" in fm.network_ids
    assert len(fm.network_ids) == 2  # home + public

    # Leave it
    assert fm.leave_network("net-public")
    assert "net-public" not in fm.network_ids
    assert len(fm.network_ids) == 1


def test_federation_cannot_leave_home(tmp_path):
    fm = FederationManager(tmp_path)
    identity = fm.init_network(b'\x00' * 32, network_name="home")
    assert not fm.leave_network(identity.network_id)


def test_federation_memberships_persist(tmp_path):
    fm = FederationManager(tmp_path)
    fm.init_network(b'\x00' * 32, network_name="home")
    fm.join_network("net-a", network_name="Net A")

    # Reload
    fm2 = FederationManager(tmp_path)
    fm2.init_network(b'\x00' * 32)
    assert "net-a" in fm2.network_ids
    assert len(fm2.memberships) == 1


def test_model_visibility_public():
    fm = FederationManager(Path("/tmp/test-fed-vis"))
    fm._identity = MagicMock()
    fm._identity.network_id = "home-net"
    assert fm.is_model_visible("m", "public", [], "any-net")


def test_model_visibility_home():
    fm = FederationManager(Path("/tmp/test-fed-vis"))
    fm._identity = MagicMock()
    fm._identity.network_id = "home-net"
    assert fm.is_model_visible("m", "home", [], "home-net")
    assert not fm.is_model_visible("m", "home", [], "other-net")


def test_model_visibility_networks():
    fm = FederationManager(Path("/tmp/test-fed-vis"))
    fm._identity = MagicMock()
    fm._identity.network_id = "home-net"
    assert fm.is_model_visible("m", "networks", ["partner-net"], "partner-net")
    assert not fm.is_model_visible("m", "networks", ["partner-net"], "random-net")


def test_registry_peers_for_network():
    reg = PeerRegistry()
    caps = Capabilities(models=[ModelCapability(name="m1")])
    reg.register("peer1", capabilities=caps)
    entry = reg.get("peer1")
    entry.state = PeerState.ROUTABLE
    entry.network_ids = ["net-a", "net-b"]

    reg.register("peer2", capabilities=caps)
    entry2 = reg.get("peer2")
    entry2.state = PeerState.ROUTABLE
    entry2.network_ids = ["net-b"]

    assert len(reg.peers_for_network("net-a")) == 1
    assert len(reg.peers_for_network("net-b")) == 2
    assert len(reg.peers_for_network("net-c")) == 0


def test_registry_models_visible_to_network():
    reg = PeerRegistry()
    caps = Capabilities(models=[
        ModelCapability(name="public-model", scope="public"),
        ModelCapability(name="home-model", scope="home"),
        ModelCapability(name="partner-model", scope="networks", visible_networks=["partner-net"]),
    ])
    reg.register("peer1", capabilities=caps)
    entry = reg.get("peer1")
    entry.state = PeerState.ROUTABLE
    entry.network_ids = ["home-net"]

    # Public model visible to anyone
    visible = reg.models_visible_to_network("random-net")
    names = [m for m, _ in visible]
    assert "public-model" in names
    assert "home-model" not in names

    # Home model visible to home network
    visible = reg.models_visible_to_network("home-net")
    names = [m for m, _ in visible]
    assert "public-model" in names
    assert "home-model" in names

    # Partner model visible to partner network
    visible = reg.models_visible_to_network("partner-net")
    names = [m for m, _ in visible]
    assert "partner-model" in names
    assert "home-model" not in names


def test_capabilities_network_ids():
    caps = Capabilities(network_ids=["net-a", "net-b"])
    d = caps.to_dict()
    assert d["network_ids"] == ["net-a", "net-b"]
    restored = Capabilities.from_dict(d)
    assert restored.network_ids == ["net-a", "net-b"]


def test_capabilities_network_ids_omitted_when_empty():
    caps = Capabilities()
    d = caps.to_dict()
    assert "network_ids" not in d
