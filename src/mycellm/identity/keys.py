"""Ed25519 key generation, serialization, and management."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


@dataclass
class AccountKey:
    """Master account Ed25519 keypair."""

    private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)

    def save(self, keys_dir: Path) -> None:
        """Save account keypair to disk."""
        keys_dir.mkdir(parents=True, exist_ok=True)

        priv_path = keys_dir / "account.key"
        pub_path = keys_dir / "account.pub"

        priv_bytes = self.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        priv_path.write_bytes(priv_bytes)
        priv_path.chmod(0o600)

        pub_bytes = self.public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        pub_path.write_bytes(pub_bytes)

        # Also save raw public key hex for easy reference
        meta = {
            "public_key_hex": self.public_bytes.hex(),
            "type": "account",
        }
        (keys_dir / "account.json").write_text(json.dumps(meta, indent=2))

    @classmethod
    def load(cls, keys_dir: Path) -> AccountKey:
        """Load account keypair from disk."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        priv_bytes = (keys_dir / "account.key").read_bytes()
        private_key = load_pem_private_key(priv_bytes, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError("Expected Ed25519 private key")
        return cls(private_key=private_key)


@dataclass
class DeviceKey:
    """Device Ed25519 keypair (one per node)."""

    private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)

    def save(self, keys_dir: Path, device_name: str = "default") -> None:
        """Save device keypair to disk."""
        keys_dir.mkdir(parents=True, exist_ok=True)

        priv_path = keys_dir / f"device-{device_name}.key"
        pub_path = keys_dir / f"device-{device_name}.pub"

        priv_bytes = self.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        priv_path.write_bytes(priv_bytes)
        priv_path.chmod(0o600)

        pub_bytes = self.public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        pub_path.write_bytes(pub_bytes)

        meta = {
            "public_key_hex": self.public_bytes.hex(),
            "device_name": device_name,
            "type": "device",
        }
        (keys_dir / f"device-{device_name}.json").write_text(json.dumps(meta, indent=2))

    @classmethod
    def load(cls, keys_dir: Path, device_name: str = "default") -> DeviceKey:
        """Load device keypair from disk."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        priv_bytes = (keys_dir / f"device-{device_name}.key").read_bytes()
        private_key = load_pem_private_key(priv_bytes, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError("Expected Ed25519 private key")
        return cls(private_key=private_key)


def generate_account_key() -> AccountKey:
    """Generate a new account master keypair."""
    return AccountKey(private_key=Ed25519PrivateKey.generate())


def generate_device_key() -> DeviceKey:
    """Generate a new device keypair."""
    return DeviceKey(private_key=Ed25519PrivateKey.generate())
