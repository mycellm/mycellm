"""Federation — network identity and invite tokens for multi-network peering."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cbor2

logger = logging.getLogger("mycellm.federation")


@dataclass
class NetworkIdentity:
    """Unique identity for a mycellm network."""
    network_id: str  # SHA256 of bootstrap account pubkey
    network_name: str = ""
    bootstrap_addrs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    public: bool = False  # Whether this network allows anonymous joining

    def to_dict(self) -> dict:
        return {
            "network_id": self.network_id,
            "network_name": self.network_name,
            "bootstrap_addrs": self.bootstrap_addrs,
            "created_at": self.created_at,
            "public": self.public,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NetworkIdentity:
        return cls(
            network_id=d["network_id"],
            network_name=d.get("network_name", ""),
            bootstrap_addrs=d.get("bootstrap_addrs", []),
            created_at=d.get("created_at", 0),
            public=d.get("public", False),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> NetworkIdentity:
        return cls.from_dict(json.loads(path.read_text()))


@dataclass
class NetworkMembership:
    """A node's membership in a network."""
    network_id: str
    network_name: str = ""
    role: str = "seeder"
    bootstrap_addrs: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)  # model names to share (empty = all home-scoped)
    quota: dict = field(default_factory=dict)  # {"max_req_per_min": 20}
    joined_at: float = field(default_factory=time.time)
    invite_token_id: str = ""  # token used to join (for audit)

    def to_dict(self) -> dict:
        return {
            "network_id": self.network_id,
            "network_name": self.network_name,
            "role": self.role,
            "bootstrap_addrs": self.bootstrap_addrs,
            "models": self.models,
            "quota": self.quota,
            "joined_at": self.joined_at,
            "invite_token_id": self.invite_token_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NetworkMembership:
        return cls(
            network_id=d["network_id"],
            network_name=d.get("network_name", ""),
            role=d.get("role", "seeder"),
            bootstrap_addrs=d.get("bootstrap_addrs", []),
            models=d.get("models", []),
            quota=d.get("quota", {}),
            joined_at=d.get("joined_at", 0),
            invite_token_id=d.get("invite_token_id", ""),
        )


@dataclass
class InviteToken:
    """Signed invite token for joining a network."""
    network_id: str
    allowed_roles: list[str] = field(default_factory=lambda: ["seeder"])
    max_uses: int = 0  # 0 = unlimited
    uses: int = 0
    expires_at: float = 0  # 0 = never
    created_at: float = field(default_factory=time.time)
    token_id: str = ""
    signature: str = ""

    def __post_init__(self):
        if not self.token_id:
            self.token_id = hashlib.sha256(
                f"{self.network_id}:{self.created_at}:{id(self)}".encode()
            ).hexdigest()[:16]

    @property
    def is_valid(self) -> bool:
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        if self.max_uses > 0 and self.uses >= self.max_uses:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "network_id": self.network_id,
            "allowed_roles": self.allowed_roles,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InviteToken:
        return cls(
            token_id=d.get("token_id", ""),
            network_id=d["network_id"],
            allowed_roles=d.get("allowed_roles", ["seeder"]),
            max_uses=d.get("max_uses", 0),
            uses=d.get("uses", 0),
            expires_at=d.get("expires_at", 0),
            created_at=d.get("created_at", 0),
            signature=d.get("signature", ""),
        )

    def sign(self, device_key) -> None:
        """Sign this token with the bootstrap node's device key."""
        data = cbor2.dumps({
            "token_id": self.token_id,
            "network_id": self.network_id,
            "allowed_roles": self.allowed_roles,
            "max_uses": self.max_uses,
            "expires_at": self.expires_at,
        })
        self.signature = device_key.sign(data).hex()

    def verify(self, pubkey_bytes: bytes) -> bool:
        """Verify token signature against a public key."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            data = cbor2.dumps({
                "token_id": self.token_id,
                "network_id": self.network_id,
                "allowed_roles": self.allowed_roles,
                "max_uses": self.max_uses,
                "expires_at": self.expires_at,
            })
            pub = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
            pub.verify(bytes.fromhex(self.signature), data)
            return True
        except Exception:
            return False

    def to_portable(self) -> str:
        """Encode as a portable string for sharing (base64 JSON)."""
        import base64
        return base64.urlsafe_b64encode(json.dumps(self.to_dict()).encode()).decode()

    @classmethod
    def from_portable(cls, s: str) -> InviteToken:
        import base64
        return cls.from_dict(json.loads(base64.urlsafe_b64decode(s)))


class FederationManager:
    """Manages network identity and invite tokens."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._tokens_dir = data_dir / "federation" / "tokens"
        self._identity: NetworkIdentity | None = None
        self._tokens: dict[str, InviteToken] = {}
        self._memberships_dir = data_dir / "federation" / "memberships"
        self._memberships: dict[str, NetworkMembership] = {}  # network_id -> membership

    @property
    def network_id(self) -> str:
        return self._identity.network_id if self._identity else ""

    @property
    def identity(self) -> NetworkIdentity | None:
        return self._identity

    @property
    def network_ids(self) -> list[str]:
        """All network IDs this node belongs to (home + joined)."""
        ids = []
        if self._identity:
            ids.append(self._identity.network_id)
        ids.extend(self._memberships.keys())
        return ids

    @property
    def memberships(self) -> list[NetworkMembership]:
        return list(self._memberships.values())

    def join_network(
        self,
        network_id: str,
        network_name: str = "",
        role: str = "seeder",
        bootstrap_addrs: list[str] | None = None,
        models: list[str] | None = None,
        quota: dict | None = None,
        invite_token_id: str = "",
    ) -> NetworkMembership:
        """Join an additional network."""
        self._memberships_dir.mkdir(parents=True, exist_ok=True)

        membership = NetworkMembership(
            network_id=network_id,
            network_name=network_name or f"network-{network_id[:8]}",
            role=role,
            bootstrap_addrs=bootstrap_addrs or [],
            models=models or [],
            quota=quota or {},
            invite_token_id=invite_token_id,
        )
        self._memberships[network_id] = membership

        path = self._memberships_dir / f"{network_id[:16]}.json"
        path.write_text(json.dumps(membership.to_dict(), indent=2))
        logger.info(f"Joined network: {membership.network_name} ({network_id[:12]}...)")
        return membership

    def leave_network(self, network_id: str) -> bool:
        """Leave a joined network."""
        if network_id == self.network_id:
            logger.warning("Cannot leave home network")
            return False
        membership = self._memberships.pop(network_id, None)
        if not membership:
            return False
        path = self._memberships_dir / f"{network_id[:16]}.json"
        if path.exists():
            path.unlink()
        logger.info(f"Left network: {membership.network_name}")
        return True

    def get_membership(self, network_id: str) -> NetworkMembership | None:
        return self._memberships.get(network_id)

    def is_model_visible(self, model_name: str, model_scope: str, model_networks: list[str], requesting_network: str) -> bool:
        """Check if a model is visible to a requesting network."""
        if model_scope == "public":
            return True
        if model_scope == "networks":
            return requesting_network in model_networks
        # scope == "home" — visible only within home network
        return requesting_network == self.network_id

    def init_network(
        self,
        account_pubkey: bytes,
        network_name: str = "",
        bootstrap_addrs: list[str] | None = None,
        public: bool = False,
    ) -> NetworkIdentity:
        """Initialize or load network identity."""
        self._tokens_dir.mkdir(parents=True, exist_ok=True)
        id_path = self._data_dir / "federation" / "network.json"

        if id_path.exists():
            self._identity = NetworkIdentity.load(id_path)
            logger.info(f"Loaded network: {self._identity.network_name} ({self._identity.network_id[:12]}...)")
        else:
            network_id = hashlib.sha256(account_pubkey).hexdigest()
            self._identity = NetworkIdentity(
                network_id=network_id,
                network_name=network_name or f"mycellm-{network_id[:8]}",
                bootstrap_addrs=bootstrap_addrs or [],
                public=public,
            )
            id_path.parent.mkdir(parents=True, exist_ok=True)
            self._identity.save(id_path)
            logger.info(f"Created network: {self._identity.network_name} ({network_id[:12]}...)")

        # Load existing tokens
        for f in self._tokens_dir.glob("*.json"):
            try:
                token = InviteToken.from_dict(json.loads(f.read_text()))
                self._tokens[token.token_id] = token
            except Exception:
                pass

        # Load memberships
        self._memberships_dir.mkdir(parents=True, exist_ok=True)
        for f in self._memberships_dir.glob("*.json"):
            try:
                m = NetworkMembership.from_dict(json.loads(f.read_text()))
                self._memberships[m.network_id] = m
            except Exception:
                pass
        if self._memberships:
            logger.info(f"Loaded {len(self._memberships)} network membership(s)")

        return self._identity

    def create_invite(
        self,
        device_key,
        roles: list[str] | None = None,
        max_uses: int = 0,
        expires_hours: float = 0,
    ) -> InviteToken:
        """Create a signed invite token."""
        token = InviteToken(
            network_id=self.network_id,
            allowed_roles=roles or ["seeder"],
            max_uses=max_uses,
            expires_at=time.time() + expires_hours * 3600 if expires_hours > 0 else 0,
        )
        token.sign(device_key)

        self._tokens[token.token_id] = token
        token_path = self._tokens_dir / f"{token.token_id}.json"
        token_path.write_text(json.dumps(token.to_dict(), indent=2))

        return token

    def validate_invite(self, portable_token: str, bootstrap_pubkey: bytes) -> tuple[bool, str]:
        """Validate an invite token. Returns (valid, error_message)."""
        try:
            token = InviteToken.from_portable(portable_token)
        except Exception as e:
            return False, f"Invalid token format: {e}"

        if token.network_id != self.network_id:
            return False, "Token is for a different network"

        if not token.is_valid:
            return False, "Token has expired or reached max uses"

        if not token.verify(bootstrap_pubkey):
            return False, "Invalid token signature"

        return True, ""

    def use_invite(self, token_id: str) -> bool:
        """Record a use of an invite token."""
        token = self._tokens.get(token_id)
        if not token or not token.is_valid:
            return False
        token.uses += 1
        token_path = self._tokens_dir / f"{token_id}.json"
        token_path.write_text(json.dumps(token.to_dict(), indent=2))
        return True

    def list_tokens(self) -> list[dict]:
        return [t.to_dict() for t in self._tokens.values()]
