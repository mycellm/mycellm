"""NAT discovery types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NATType(str, Enum):
    """Classified NAT type based on STUN probing."""
    UNKNOWN = "unknown"
    OPEN = "open"               # No NAT — public IP (server, VPS)
    FULL_CONE = "full_cone"     # Any external host can send to mapped port
    RESTRICTED = "restricted"   # Only hosts we've sent to can reply (same IP)
    PORT_RESTRICTED = "port_restricted"  # Same IP + same port
    SYMMETRIC = "symmetric"     # Different mapping per destination — hole punch fails

    @property
    def can_hole_punch(self) -> bool:
        """Whether this NAT type supports UDP hole punching."""
        return self in (NATType.OPEN, NATType.FULL_CONE, NATType.RESTRICTED, NATType.PORT_RESTRICTED)


@dataclass
class Candidate:
    """A network address candidate for hole punching."""
    ip: str
    port: int
    type: str = "server_reflexive"  # "host" | "server_reflexive" | "relay"
    priority: int = 0

    def to_dict(self) -> dict:
        return {"ip": self.ip, "port": self.port, "type": self.type, "priority": self.priority}

    @classmethod
    def from_dict(cls, d: dict) -> Candidate:
        return cls(ip=d["ip"], port=d["port"], type=d.get("type", "server_reflexive"), priority=d.get("priority", 0))


@dataclass
class NATInfo:
    """Discovered NAT information for this node."""
    public_ip: str = ""
    public_port: int = 0
    nat_type: NATType = NATType.UNKNOWN
    local_ip: str = ""
    local_port: int = 0
    confidence: float = 0.0  # 0.0-1.0 based on agreement across STUN servers
    stun_servers_queried: int = 0
    observed_addr: str = ""  # From bootstrap NODE_HELLO_ACK

    @property
    def candidates(self) -> list[Candidate]:
        """Generate ICE-style candidates from discovered addresses."""
        candidates = []
        if self.local_ip and self.local_port:
            candidates.append(Candidate(ip=self.local_ip, port=self.local_port, type="host", priority=100))
        if self.public_ip and self.public_port:
            candidates.append(Candidate(ip=self.public_ip, port=self.public_port, type="server_reflexive", priority=50))
        return candidates

    def to_dict(self) -> dict:
        return {
            "public_ip": self.public_ip,
            "public_port": self.public_port,
            "nat_type": self.nat_type.value,
            "local_ip": self.local_ip,
            "local_port": self.local_port,
            "confidence": self.confidence,
            "candidates": [c.to_dict() for c in self.candidates],
        }
