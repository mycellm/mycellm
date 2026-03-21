"""Receipt verification and cross-node settlement utilities.

Receipts are the cryptographic proof that inference was served.
They bind a specific request to a seeder, consumer, model, token
count, and cost — signed by the seeder's Ed25519 device key.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger("mycellm.accounting")


def build_receipt_data(
    consumer_id: str,
    seeder_id: str,
    model: str,
    tokens: int,
    cost: float,
    request_id: str = "",
    timestamp: float = 0.0,
) -> bytes:
    """Build canonical CBOR receipt data for signing/verification."""
    return cbor2.dumps({
        "consumer": consumer_id,
        "seeder": seeder_id,
        "model": model,
        "tokens": tokens,
        "cost": cost,
        "request_id": request_id,
        "ts": timestamp or time.time(),
    })


def sign_receipt(device_key, receipt_data: bytes) -> str:
    """Sign receipt data with a device key. Returns hex signature."""
    return device_key.sign(receipt_data).hex()


def verify_receipt_signature(
    receipt_data: bytes,
    signature_hex: str,
    seeder_pubkey_bytes: bytes,
) -> bool:
    """Verify a receipt signature against the seeder's public key.

    Args:
        receipt_data: The canonical CBOR receipt data.
        signature_hex: Hex-encoded Ed25519 signature.
        seeder_pubkey_bytes: 32-byte raw Ed25519 public key of the seeder.

    Returns:
        True if signature is valid.
    """
    try:
        pub = Ed25519PublicKey.from_public_bytes(seeder_pubkey_bytes)
        pub.verify(bytes.fromhex(signature_hex), receipt_data)
        return True
    except (InvalidSignature, ValueError, Exception) as e:
        logger.debug(f"Receipt signature verification failed: {e}")
        return False


class ReceiptValidator:
    """Validates receipts with replay protection and rate limiting."""

    def __init__(self, max_receipts_per_minute: int = 100):
        self._seen_request_ids: dict[str, float] = {}  # request_id -> timestamp
        self._credit_rate: dict[str, list[float]] = defaultdict(list)  # peer_id -> [timestamps]
        self._max_rate = max_receipts_per_minute
        self._request_id_ttl = 3600.0  # 1 hour dedup window

    def check_replay(self, request_id: str) -> bool:
        """Check if a request_id has been seen before. Returns True if NEW (not replay)."""
        if not request_id:
            return True  # no request_id = legacy, allow

        now = time.time()
        # Prune old entries
        self._seen_request_ids = {
            k: v for k, v in self._seen_request_ids.items()
            if now - v < self._request_id_ttl
        }

        if request_id in self._seen_request_ids:
            logger.warning(f"Replay detected: request_id={request_id}")
            return False

        self._seen_request_ids[request_id] = now
        return True

    def check_credit_rate(self, peer_id: str) -> bool:
        """Check if a peer is self-crediting too fast. Returns True if OK."""
        now = time.time()
        timestamps = self._credit_rate[peer_id]
        # Prune old entries
        self._credit_rate[peer_id] = [t for t in timestamps if now - t < 60.0]

        if len(self._credit_rate[peer_id]) >= self._max_rate:
            logger.warning(f"Credit rate limit for {peer_id[:16]}: {len(self._credit_rate[peer_id])}/min")
            return False

        self._credit_rate[peer_id].append(now)
        return True
