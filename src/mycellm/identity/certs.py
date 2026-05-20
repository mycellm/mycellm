"""Device certificates — signed by account master key."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mycellm.identity.keys import AccountKey, DeviceKey


@dataclass
class DeviceCert:
    """Certificate binding a device key to an account, signed by account master key."""

    account_pubkey: bytes  # 32-byte raw Ed25519
    device_pubkey: bytes  # 32-byte raw Ed25519
    device_name: str
    role: str = "seeder"  # seeder, consumer, relay
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 = no expiry
    revoked: bool = False
    signature: bytes = b""  # Ed25519 sig by account key over cert payload

    @property
    def peer_id(self) -> str:
        return _peer_id_from_raw(self.device_pubkey)

    def to_cbor_payload(self) -> bytes:
        """Encode the signable portion (everything except signature)."""
        return cbor2.dumps({
            "account_pubkey": self.account_pubkey,
            "device_pubkey": self.device_pubkey,
            "device_name": self.device_name,
            "role": self.role,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        })

    def to_cbor(self) -> bytes:
        """Encode the full certificate including signature."""
        return cbor2.dumps({
            "account_pubkey": self.account_pubkey,
            "device_pubkey": self.device_pubkey,
            "device_name": self.device_name,
            "role": self.role,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "signature": self.signature,
        })

    @classmethod
    def from_cbor(cls, data: bytes) -> DeviceCert:
        """Decode a certificate from CBOR."""
        obj = cbor2.loads(data)
        return cls(
            account_pubkey=obj["account_pubkey"],
            device_pubkey=obj["device_pubkey"],
            device_name=obj["device_name"],
            role=obj.get("role", "seeder"),
            created_at=obj.get("created_at", 0.0),
            expires_at=obj.get("expires_at", 0.0),
            revoked=obj.get("revoked", False),
            signature=obj.get("signature", b""),
        )

    def is_expired(self) -> bool:
        if self.expires_at == 0.0:
            return False
        return time.time() > self.expires_at

    def save(self, certs_dir: Path | str) -> None:
        certs_dir = Path(certs_dir)
        certs_dir.mkdir(parents=True, exist_ok=True)
        (certs_dir / f"device-{self.device_name}.cert").write_bytes(self.to_cbor())

    @classmethod
    def load(cls, certs_dir: Path | str, device_name: str = "default") -> DeviceCert:
        certs_dir = Path(certs_dir)
        return cls.from_cbor((certs_dir / f"device-{device_name}.cert").read_bytes())


def create_device_cert(
    account_key: AccountKey,
    device_key: DeviceKey,
    device_name: str = "default",
    role: str = "seeder",
    ttl_seconds: Optional[float] = None,
) -> DeviceCert:
    """Create and sign a device certificate."""
    now = time.time()
    expires_at = (now + ttl_seconds) if ttl_seconds else 0.0

    cert = DeviceCert(
        account_pubkey=account_key.public_bytes,
        device_pubkey=device_key.public_bytes,
        device_name=device_name,
        role=role,
        created_at=now,
        expires_at=expires_at,
    )

    payload = cert.to_cbor_payload()
    cert.signature = account_key.sign(payload)
    return cert


def verify_device_cert(cert: DeviceCert, account_pubkey: Optional[bytes] = None) -> bool:
    """Verify a device certificate's signature and validity.

    Args:
        cert: The certificate to verify.
        account_pubkey: If provided, also verify the cert was signed by this account key.

    Returns:
        True if valid, False otherwise.
    """
    if cert.revoked:
        return False

    if cert.is_expired():
        return False

    # Verify account key matches if provided
    if account_pubkey and cert.account_pubkey != account_pubkey:
        return False

    # Verify signature
    try:
        pub = Ed25519PublicKey.from_public_bytes(cert.account_pubkey)
        payload = cert.to_cbor_payload()
        pub.verify(cert.signature, payload)
        return True
    except (InvalidSignature, ValueError):
        return False


def _peer_id_from_raw(pubkey_bytes: bytes) -> str:
    """Compute peer ID from raw public key bytes."""
    import hashlib

    return hashlib.sha256(pubkey_bytes).hexdigest()[:32]
