"""PeerId generation from public keys using multihash convention."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def peer_id_from_public_key(public_key: Ed25519PublicKey) -> str:
    """Generate a PeerId from an Ed25519 public key.

    PeerId = hex(sha256(raw_public_key_bytes))[:32]
    Short enough for display, long enough for uniqueness.
    """
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    digest = hashlib.sha256(raw).hexdigest()
    return digest[:32]


def peer_id_from_bytes(public_key_bytes: bytes) -> str:
    """Generate a PeerId from raw 32-byte Ed25519 public key bytes."""
    digest = hashlib.sha256(public_key_bytes).hexdigest()
    return digest[:32]
