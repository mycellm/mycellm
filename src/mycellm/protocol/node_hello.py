"""NodeHello message — app-layer identity binding over QUIC+TLS."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mycellm.identity.certs import DeviceCert, verify_device_cert
from mycellm.identity.keys import DeviceKey
from mycellm.identity.peer_id import peer_id_from_bytes
from mycellm.protocol.capabilities import Capabilities


@dataclass
class NodeHello:
    """Identity binding message exchanged after QUIC+TLS connection.

    Proves the connecting node controls the claimed device key
    and holds a valid certificate from an account.
    """

    peer_id: str
    device_pubkey: bytes  # 32-byte raw Ed25519
    cert: DeviceCert
    capabilities: Capabilities
    nonce: bytes = field(default_factory=lambda: os.urandom(32))
    timestamp: float = field(default_factory=time.time)
    signature: bytes = b""  # Sig by device key over (nonce + timestamp + peer_id)
    observed_addr: str = ""
    network_ids: list[str] = field(default_factory=list)  # networks this node belongs to

    def signable_data(self) -> bytes:
        """Data that gets signed by device key."""
        return cbor2.dumps({
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "peer_id": self.peer_id,
        })

    def sign(self, device_key: DeviceKey) -> None:
        """Sign this hello with the device key."""
        self.signature = device_key.sign(self.signable_data())

    def to_cbor(self) -> bytes:
        return cbor2.dumps({
            "peer_id": self.peer_id,
            "device_pubkey": self.device_pubkey,
            "cert": self.cert.to_cbor(),
            "capabilities": self.capabilities.to_dict(),
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "observed_addr": self.observed_addr,
            "network_ids": self.network_ids,
        })

    @classmethod
    def from_cbor(cls, data: bytes) -> NodeHello:
        obj = cbor2.loads(data)
        return cls(
            peer_id=obj["peer_id"],
            device_pubkey=obj["device_pubkey"],
            cert=DeviceCert.from_cbor(obj["cert"]),
            capabilities=Capabilities.from_dict(obj["capabilities"]),
            nonce=obj["nonce"],
            timestamp=obj["timestamp"],
            signature=obj["signature"],
            observed_addr=obj.get("observed_addr", ""),
            network_ids=obj.get("network_ids", []),
        )


def verify_node_hello(hello: NodeHello, max_age_seconds: float = 300.0) -> tuple[bool, str]:
    """Verify a NodeHello message.

    Checks:
    1. Cert signed by valid account key
    2. Cert not expired/revoked
    3. Cert device key matches presented device key
    4. PeerId == hash(device pubkey)
    5. Signature valid over nonce+timestamp
    6. Timestamp not too old

    Returns (is_valid, error_message).
    """
    # Check timestamp freshness
    age = abs(time.time() - hello.timestamp)
    if age > max_age_seconds:
        return False, "NodeHello timestamp too old"

    # Check cert validity (signature + expiry + revocation)
    if not verify_device_cert(hello.cert):
        return False, "Device certificate invalid"

    # Check cert device key matches presented key
    if hello.cert.device_pubkey != hello.device_pubkey:
        return False, "Device key mismatch between cert and hello"

    # Check PeerId
    expected_peer_id = peer_id_from_bytes(hello.device_pubkey)
    if hello.peer_id != expected_peer_id:
        return False, "PeerId does not match device public key"

    # Verify signature
    try:
        pub = Ed25519PublicKey.from_public_bytes(hello.device_pubkey)
        pub.verify(hello.signature, hello.signable_data())
    except (InvalidSignature, ValueError) as e:
        return False, f"NodeHello signature invalid: {e}"

    return True, ""
