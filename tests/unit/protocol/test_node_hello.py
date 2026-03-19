"""Tests for NodeHello creation and verification."""

import time

from mycellm.identity.certs import create_device_cert
from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.protocol.capabilities import Capabilities, HardwareInfo, ModelCapability
from mycellm.protocol.node_hello import NodeHello, verify_node_hello


def _make_identity():
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="test")
    caps = Capabilities(
        models=[ModelCapability(name="test-model")],
        hardware=HardwareInfo(gpu="test", backend="cpu"),
    )
    return account, device, cert, caps


def test_node_hello_create_and_verify():
    account, device, cert, caps = _make_identity()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert valid, f"Verification failed: {err}"


def test_node_hello_cbor_roundtrip():
    _, device, cert, caps = _make_identity()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    data = hello.to_cbor()
    loaded = NodeHello.from_cbor(data)

    valid, err = verify_node_hello(loaded)
    assert valid, f"Roundtrip verification failed: {err}"


def test_node_hello_wrong_peer_id():
    _, device, cert, caps = _make_identity()

    hello = NodeHello(
        peer_id="wrong_peer_id_0000000000000000",
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert not valid
    assert "PeerId" in err


def test_node_hello_wrong_device_key():
    account, device, cert, caps = _make_identity()
    other_device = generate_device_key()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=other_device.public_bytes,  # Wrong key
        cert=cert,
        capabilities=caps,
    )
    hello.sign(other_device)

    valid, err = verify_node_hello(hello)
    assert not valid


def test_node_hello_expired_cert():
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, ttl_seconds=-10)  # Already expired
    caps = Capabilities()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert not valid
    assert "certificate" in err.lower() or "invalid" in err.lower()


def test_node_hello_stale_timestamp():
    _, device, cert, caps = _make_identity()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
        timestamp=time.time() - 600,  # 10 minutes old
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello, max_age_seconds=300)
    assert not valid
    assert "timestamp" in err.lower()


def test_node_hello_tampered_signature():
    _, device, cert, caps = _make_identity()
    peer_id = peer_id_from_public_key(device.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)
    hello.signature = b"\x00" * 64

    valid, err = verify_node_hello(hello)
    assert not valid
    assert "signature" in err.lower()
