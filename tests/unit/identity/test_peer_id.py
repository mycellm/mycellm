"""Tests for PeerId generation."""

from mycellm.identity.keys import generate_device_key
from mycellm.identity.peer_id import peer_id_from_bytes, peer_id_from_public_key


def test_peer_id_deterministic():
    key = generate_device_key()
    id1 = peer_id_from_public_key(key.public_key)
    id2 = peer_id_from_public_key(key.public_key)
    assert id1 == id2


def test_peer_id_length():
    key = generate_device_key()
    pid = peer_id_from_public_key(key.public_key)
    assert len(pid) == 32  # 32 hex chars = 16 bytes


def test_peer_id_unique():
    k1 = generate_device_key()
    k2 = generate_device_key()
    assert peer_id_from_public_key(k1.public_key) != peer_id_from_public_key(k2.public_key)


def test_peer_id_from_bytes_matches():
    key = generate_device_key()
    id1 = peer_id_from_public_key(key.public_key)
    id2 = peer_id_from_bytes(key.public_bytes)
    assert id1 == id2
