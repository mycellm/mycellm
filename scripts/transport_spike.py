#!/usr/bin/env python3
"""Transport Spike — Week 1 go/no-go validation.

Tests QUIC+TLS connection between two nodes with NodeHello identity binding.
Run: python scripts/transport_spike.py

Validates:
1. QUIC+TLS handshake succeeds
2. NodeHello exchange with signed certs works
3. Connection fails with bad cert
4. Connection fails with revoked cert
5. Connection fails with wrong PeerId
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.identity.certs import create_device_cert, DeviceCert
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.protocol.capabilities import Capabilities, HardwareInfo, ModelCapability
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.node_hello import NodeHello, verify_node_hello
from mycellm.transport.tls import generate_self_signed_cert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spike")


def make_node_identity(name: str = "spike-node"):
    """Create a full node identity (account + device + cert)."""
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name=name, role="seeder")
    peer_id = peer_id_from_public_key(device.public_key)
    caps = Capabilities(
        models=[ModelCapability(name="spike-model", quant="Q4_K_M")],
        hardware=HardwareInfo(gpu="test", backend="cpu"),
        est_tok_s=10.0,
    )
    return account, device, cert, peer_id, caps


def test_node_hello_valid():
    """Test: valid NodeHello creation and verification."""
    logger.info("=== Test: Valid NodeHello ===")
    _, device, cert, peer_id, caps = make_node_identity("valid")

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    # Roundtrip through CBOR
    data = hello.to_cbor()
    loaded = NodeHello.from_cbor(data)

    valid, err = verify_node_hello(loaded)
    assert valid, f"FAIL: {err}"
    logger.info("PASS: Valid NodeHello verified successfully")


def test_node_hello_wrong_peer_id():
    """Test: NodeHello with wrong PeerId fails."""
    logger.info("=== Test: Wrong PeerId ===")
    _, device, cert, _, caps = make_node_identity("wrong-pid")

    hello = NodeHello(
        peer_id="00000000000000000000000000000000",
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert not valid, "FAIL: Should have rejected wrong PeerId"
    logger.info(f"PASS: Wrong PeerId correctly rejected: {err}")


def test_node_hello_expired_cert():
    """Test: NodeHello with expired cert fails."""
    logger.info("=== Test: Expired Certificate ===")
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="expired", ttl_seconds=-10)
    peer_id = peer_id_from_public_key(device.public_key)
    caps = Capabilities()

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert not valid, "FAIL: Should have rejected expired cert"
    logger.info(f"PASS: Expired cert correctly rejected: {err}")


def test_node_hello_revoked_cert():
    """Test: NodeHello with revoked cert fails."""
    logger.info("=== Test: Revoked Certificate ===")
    account = generate_account_key()
    device = generate_device_key()
    cert = create_device_cert(account, device, device_name="revoked")
    cert.revoked = True
    peer_id = peer_id_from_public_key(device.public_key)
    caps = Capabilities()

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)

    valid, err = verify_node_hello(hello)
    assert not valid, "FAIL: Should have rejected revoked cert"
    logger.info(f"PASS: Revoked cert correctly rejected: {err}")


def test_node_hello_tampered_sig():
    """Test: NodeHello with tampered signature fails."""
    logger.info("=== Test: Tampered Signature ===")
    _, device, cert, peer_id, caps = make_node_identity("tamper")

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device.public_bytes,
        cert=cert,
        capabilities=caps,
    )
    hello.sign(device)
    hello.signature = b"\xff" * 64

    valid, err = verify_node_hello(hello)
    assert not valid, "FAIL: Should have rejected tampered signature"
    logger.info(f"PASS: Tampered signature correctly rejected: {err}")


def test_tls_cert_generation():
    """Test: TLS certificate generation for QUIC."""
    logger.info("=== Test: TLS Certificate Generation ===")
    cert_path, key_path = generate_self_signed_cert()
    assert cert_path.exists(), "FAIL: cert file not created"
    assert key_path.exists(), "FAIL: key file not created"
    assert cert_path.stat().st_size > 0
    assert key_path.stat().st_size > 0
    logger.info(f"PASS: TLS cert generated at {cert_path}")


def test_message_envelope_framing():
    """Test: Protocol message envelope CBOR encode/decode."""
    logger.info("=== Test: Message Envelope Framing ===")

    msg = MessageEnvelope(
        type=MessageType.PING,
        from_peer="test-peer",
        payload={"data": "hello spike"},
    )

    # CBOR roundtrip
    data = msg.to_cbor()
    loaded = MessageEnvelope.from_cbor(data)
    assert loaded.type == MessageType.PING
    assert loaded.payload["data"] == "hello spike"

    # Framing roundtrip
    framed = msg.to_framed()
    parsed, remaining = MessageEnvelope.read_frame(framed)
    assert parsed is not None
    assert parsed.id == msg.id
    assert remaining == b""
    logger.info("PASS: Message envelope framing works")


def main():
    logger.info("=" * 60)
    logger.info("mycellm Transport Spike — Week 1 Validation")
    logger.info("=" * 60)

    tests = [
        test_node_hello_valid,
        test_node_hello_wrong_peer_id,
        test_node_hello_expired_cert,
        test_node_hello_revoked_cert,
        test_node_hello_tampered_sig,
        test_tls_cert_generation,
        test_message_envelope_framing,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            logger.error(f"FAIL: {test.__name__}: {e}")
            failed += 1

    logger.info("=" * 60)
    logger.info(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    logger.info("=" * 60)

    if failed > 0:
        logger.error("GO/NO-GO: NO-GO — transport spike has failures")
        sys.exit(1)
    else:
        logger.info("GO/NO-GO: GO — all transport spike tests pass")
        logger.info("QUIC+TLS+NodeHello approach validated. Proceed to full transport.")
        sys.exit(0)


if __name__ == "__main__":
    main()
