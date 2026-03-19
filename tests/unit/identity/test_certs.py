"""Tests for device certificates: creation, verification, expiry, tampering, revocation."""

import time

from mycellm.identity.certs import (
    DeviceCert,
    create_device_cert,
    verify_device_cert,
)
from mycellm.identity.keys import generate_account_key, generate_device_key
from mycellm.identity.peer_id import peer_id_from_bytes


def test_create_and_verify_cert():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device, device_name="node1", role="seeder")

    assert cert.device_name == "node1"
    assert cert.role == "seeder"
    assert cert.signature != b""
    assert verify_device_cert(cert)


def test_cert_peer_id_matches():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device)
    expected_id = peer_id_from_bytes(device.public_bytes)
    assert cert.peer_id == expected_id


def test_cert_cbor_roundtrip():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device, device_name="rt-test")
    data = cert.to_cbor()
    loaded = DeviceCert.from_cbor(data)

    assert loaded.device_name == "rt-test"
    assert loaded.account_pubkey == cert.account_pubkey
    assert loaded.device_pubkey == cert.device_pubkey
    assert loaded.signature == cert.signature
    assert verify_device_cert(loaded)


def test_cert_wrong_account_key_fails():
    account1 = generate_account_key()
    account2 = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account1, device)

    # Verify with correct account
    assert verify_device_cert(cert, account_pubkey=account1.public_bytes)

    # Verify with wrong account should fail
    assert not verify_device_cert(cert, account_pubkey=account2.public_bytes)


def test_cert_tampered_signature_fails():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device)

    # Tamper with signature
    cert.signature = b"\x00" * 64
    assert not verify_device_cert(cert)


def test_cert_tampered_role_fails():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device, role="seeder")
    assert verify_device_cert(cert)

    # Tamper with role after signing
    cert.role = "admin"
    assert not verify_device_cert(cert)


def test_cert_expired():
    account = generate_account_key()
    device = generate_device_key()

    # Create cert that expired 10 seconds ago
    cert = create_device_cert(account, device, ttl_seconds=-10)
    assert cert.is_expired()
    assert not verify_device_cert(cert)


def test_cert_not_expired():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device, ttl_seconds=3600)
    assert not cert.is_expired()
    assert verify_device_cert(cert)


def test_cert_no_expiry():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device)
    assert cert.expires_at == 0.0
    assert not cert.is_expired()
    assert verify_device_cert(cert)


def test_cert_revoked():
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device)
    cert.revoked = True
    assert not verify_device_cert(cert)


def test_cert_save_load(tmp_path):
    account = generate_account_key()
    device = generate_device_key()

    cert = create_device_cert(account, device, device_name="persist")
    cert.save(tmp_path)

    loaded = DeviceCert.load(tmp_path, "persist")
    assert loaded.device_name == "persist"
    assert loaded.signature == cert.signature
    assert verify_device_cert(loaded)
