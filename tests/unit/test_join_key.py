"""Join-key enforcement — protected hosted networks require the key in NodeHello."""

from mycellm.federation import FederationManager, NetworkMembership
from mycellm.identity.certs import create_device_cert
from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.protocol.capabilities import Capabilities
from mycellm.protocol.node_hello import NodeHello
from mycellm.transport.auth import build_node_hello

import cbor2

PUBKEY = b"\x02" * 32


def _fm(tmp_path):
    fm = FederationManager(tmp_path)
    fm.init_network(PUBKEY, network_name="home-net")
    return fm


def _hello_kwargs():
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="t")
    return device, cert


# ---- protocol ---------------------------------------------------------------

def test_node_hello_join_keys_roundtrip():
    device, cert = _hello_kwargs()
    hello = NodeHello(
        peer_id="p" * 32,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=Capabilities(),
        network_ids=["n1"],
        join_keys={"n1": "sekrit"},
    )
    hello.sign(device)
    decoded = NodeHello.from_cbor(hello.to_cbor())
    assert decoded.join_keys == {"n1": "sekrit"}
    assert decoded.network_ids == ["n1"]


def test_node_hello_join_keys_omitted_when_empty():
    device, cert = _hello_kwargs()
    hello = NodeHello(
        peer_id="p" * 32,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=Capabilities(),
    )
    hello.sign(device)
    assert "join_keys" not in cbor2.loads(hello.to_cbor())
    # Old-format hello (no join_keys field) decodes to {}
    assert NodeHello.from_cbor(hello.to_cbor()).join_keys == {}


def test_build_node_hello_carries_join_keys():
    device, cert = _hello_kwargs()
    msg = build_node_hello(device, cert, Capabilities(), join_keys={"n1": "k"})
    hello = NodeHello.from_cbor(msg.payload["hello"])
    assert hello.join_keys == {"n1": "k"}


# ---- filter -----------------------------------------------------------------

def test_filter_protected_hosted_network(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab", join_key="sekrit")
    nid = hosted.network_id

    # Right key → claim accepted
    assert fm.filter_claimed_network_ids([nid], {nid: "sekrit"}) == [nid]
    # Missing key → dropped
    assert fm.filter_claimed_network_ids([nid], {}) == []
    # Wrong key → dropped
    assert fm.filter_claimed_network_ids([nid], {nid: "nope"}) == []
    # Other claims survive alongside a dropped one
    assert fm.filter_claimed_network_ids([nid, "x" * 64], {}) == ["x" * 64]


def test_filter_unprotected_and_foreign_pass_through(tmp_path):
    fm = _fm(tmp_path)
    open_net = fm.host_network(PUBKEY, "open-lab")  # no join_key
    claims = [open_net.network_id, "f" * 64, fm.network_id]
    assert fm.filter_claimed_network_ids(claims, {}) == claims


def test_filter_protected_home_network(tmp_path):
    fm = _fm(tmp_path)
    fm.set_join_key(fm.network_id, "homekey")
    assert fm.filter_claimed_network_ids([fm.network_id], {}) == []
    assert fm.filter_claimed_network_ids(
        [fm.network_id], {fm.network_id: "homekey"}
    ) == [fm.network_id]


def test_set_join_key_persists(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    assert fm.set_join_key(hosted.network_id, "k2")
    fm2 = FederationManager(tmp_path)
    fm2.init_network(PUBKEY)
    assert fm2.filter_claimed_network_ids([hosted.network_id], {}) == []
    assert not fm2.set_join_key("e" * 64, "x")  # unknown network


# ---- membership side --------------------------------------------------------

def test_membership_join_key_roundtrip(tmp_path):
    fm = _fm(tmp_path)
    fm.join_network("a" * 64, network_name="theirs", join_key="sekrit")
    assert fm.membership_join_keys == {"a" * 64: "sekrit"}

    fm2 = FederationManager(tmp_path)
    fm2.init_network(PUBKEY)
    assert fm2.membership_join_keys == {"a" * 64: "sekrit"}


def test_membership_join_key_omitted_when_empty():
    m = NetworkMembership(network_id="a" * 64)
    assert "join_key" not in m.to_dict()
    assert NetworkMembership.from_dict(m.to_dict()).join_key == ""
