"""Encrypted secrets store — API keys at rest, keyed to account identity.

Secrets are encrypted using Fernet (AES-128-CBC + HMAC-SHA256) with a key
derived from the account's Ed25519 private key via HKDF-SHA256. This means:
  - Secrets are unreadable without the account key
  - Moving the data dir without the key dir renders secrets useless
  - Each account has a unique encryption key

Storage format: JSON file with Fernet-encrypted values.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

logger = logging.getLogger("mycellm.secrets")

_HKDF_INFO = b"mycellm-secrets-v1"


def _derive_fernet_key(account_private_key) -> bytes:
    """Derive a Fernet key from an Ed25519 private key via HKDF."""
    raw_private = account_private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    derived = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(raw_private)
    return base64.urlsafe_b64encode(derived)


class SecretStore:
    """Encrypted key-value store for API keys and tokens."""

    def __init__(self, secrets_path: Path, account_key=None):
        self._path = secrets_path
        self._fernet: Fernet | None = None
        self._secrets: dict[str, str] = {}

        if account_key is not None:
            self._init_cipher(account_key)
            self._load()

    def _init_cipher(self, account_key) -> None:
        """Initialize the Fernet cipher from an AccountKey."""
        fernet_key = _derive_fernet_key(account_key.private_key)
        self._fernet = Fernet(fernet_key)

    def _load(self) -> None:
        """Load and decrypt secrets from disk."""
        if not self._path.exists():
            self._secrets = {}
            return

        try:
            data = json.loads(self._path.read_text())
            self._secrets = {}
            for name, encrypted_value in data.items():
                try:
                    decrypted = self._fernet.decrypt(encrypted_value.encode()).decode()
                    self._secrets[name] = decrypted
                except InvalidToken:
                    logger.warning(f"Failed to decrypt secret '{name}' (wrong key?)")
        except Exception as e:
            logger.warning(f"Failed to load secrets: {e}")
            self._secrets = {}

    def _save(self) -> None:
        """Encrypt and save all secrets to disk."""
        encrypted = {}
        for name, value in self._secrets.items():
            encrypted[name] = self._fernet.encrypt(value.encode()).decode()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(encrypted, indent=2))
        self._path.chmod(0o600)

    def set(self, name: str, value: str) -> None:
        """Store a secret."""
        self._secrets[name] = value
        self._save()

    def get(self, name: str, default: str = "") -> str:
        """Retrieve a secret by name."""
        return self._secrets.get(name, default)

    def remove(self, name: str) -> bool:
        """Remove a secret. Returns True if it existed."""
        if name not in self._secrets:
            return False
        del self._secrets[name]
        self._save()
        return True

    def list_names(self) -> list[str]:
        """List all secret names (not values)."""
        return sorted(self._secrets.keys())

    def has(self, name: str) -> bool:
        return name in self._secrets

    def resolve(self, value: str) -> str:
        """Resolve a value that might be a secret reference.

        If value starts with 'secret:', look up the rest as a secret name.
        Otherwise return as-is. This allows model configs to reference
        secrets without storing raw keys:

            {"api_key": "secret:openrouter"}  → resolves to the stored secret
            {"api_key": "sk-or-abc123"}       → returned as-is
        """
        if value.startswith("secret:"):
            secret_name = value[7:]
            resolved = self.get(secret_name)
            if not resolved:
                logger.warning(f"Secret reference '{secret_name}' not found")
            return resolved
        return value
