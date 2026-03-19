"""Device certificate revocation list (local)."""

from __future__ import annotations

import json
from pathlib import Path


class RevocationList:
    """Local revocation list for device certificates."""

    def __init__(self, path: Path):
        self._path = path
        self._revoked: set[str] = set()  # Set of device pubkey hex strings
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self._revoked = set(data.get("revoked", []))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"revoked": sorted(self._revoked)}, indent=2))

    def revoke(self, device_pubkey_hex: str) -> None:
        self._revoked.add(device_pubkey_hex)
        self._save()

    def is_revoked(self, device_pubkey_hex: str) -> bool:
        return device_pubkey_hex in self._revoked

    def unrevoke(self, device_pubkey_hex: str) -> None:
        self._revoked.discard(device_pubkey_hex)
        self._save()

    @property
    def all_revoked(self) -> set[str]:
        return self._revoked.copy()
