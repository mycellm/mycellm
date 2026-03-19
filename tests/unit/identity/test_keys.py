"""Tests for key generation, serialization, and loading."""

import tempfile
from pathlib import Path

from mycellm.identity.keys import (
    AccountKey,
    DeviceKey,
    generate_account_key,
    generate_device_key,
)


def test_generate_account_key():
    key = generate_account_key()
    assert key.public_bytes is not None
    assert len(key.public_bytes) == 32


def test_generate_device_key():
    key = generate_device_key()
    assert key.public_bytes is not None
    assert len(key.public_bytes) == 32


def test_account_key_sign_verify():
    key = generate_account_key()
    data = b"test data to sign"
    sig = key.sign(data)
    assert len(sig) == 64  # Ed25519 signatures are 64 bytes

    # Verify using cryptography lib directly
    from cryptography.exceptions import InvalidSignature
    key.public_key.verify(sig, data)

    # Bad data should fail
    try:
        key.public_key.verify(sig, b"wrong data")
        assert False, "Should have raised InvalidSignature"
    except InvalidSignature:
        pass


def test_device_key_sign_verify():
    key = generate_device_key()
    data = b"device test data"
    sig = key.sign(data)
    assert len(sig) == 64
    key.public_key.verify(sig, data)


def test_account_key_save_load():
    key = generate_account_key()
    with tempfile.TemporaryDirectory() as tmpdir:
        keys_dir = Path(tmpdir)
        key.save(keys_dir)

        loaded = AccountKey.load(keys_dir)
        assert loaded.public_bytes == key.public_bytes

        # Verify loaded key can sign and original can verify
        sig = loaded.sign(b"roundtrip")
        key.public_key.verify(sig, b"roundtrip")


def test_device_key_save_load():
    key = generate_device_key()
    with tempfile.TemporaryDirectory() as tmpdir:
        keys_dir = Path(tmpdir)
        key.save(keys_dir, device_name="test-node")

        loaded = DeviceKey.load(keys_dir, device_name="test-node")
        assert loaded.public_bytes == key.public_bytes


def test_different_keys_are_unique():
    k1 = generate_account_key()
    k2 = generate_account_key()
    assert k1.public_bytes != k2.public_bytes

    d1 = generate_device_key()
    d2 = generate_device_key()
    assert d1.public_bytes != d2.public_bytes
