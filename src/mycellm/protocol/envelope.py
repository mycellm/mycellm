"""Protocol message envelope with versioning.

Messages are CBOR-encoded. Messages larger than 1KB are zlib-compressed
automatically (prefixed with a 0x01 byte to distinguish from uncompressed
0x00-prefix messages). This helps with large inference responses over QUIC.
"""

from __future__ import annotations

import time
import uuid
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import cbor2


PROTOCOL_VERSION = 1
_COMPRESS_THRESHOLD = 1024  # bytes — compress CBOR payloads above this


class MessageType(str, Enum):
    """All protocol message types."""

    # Handshake
    NODE_HELLO = "node_hello"
    NODE_HELLO_ACK = "node_hello_ack"

    # Discovery
    PEER_ANNOUNCE = "peer_announce"
    PEER_QUERY = "peer_query"
    PEER_RESPONSE = "peer_response"

    # Inference
    INFERENCE_REQ = "inference_req"
    INFERENCE_RESP = "inference_resp"
    INFERENCE_STREAM = "inference_stream"
    INFERENCE_DONE = "inference_done"

    # Health
    PING = "ping"
    PONG = "pong"

    # Accounting
    CREDIT_RECEIPT = "credit_receipt"

    # Multi-hop
    INFERENCE_RELAY = "inference_relay"

    # Peer exchange
    PEER_EXCHANGE = "peer_exchange"

    # Fleet management (relayed via bootstrap)
    FLEET_COMMAND = "fleet_command"
    FLEET_RESPONSE = "fleet_response"

    # Error
    ERROR = "error"


@dataclass
class MessageEnvelope:
    """Protocol message envelope wrapping all peer-to-peer messages."""

    type: MessageType
    payload: dict[str, Any]
    from_peer: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: float = field(default_factory=time.time)
    v: int = PROTOCOL_VERSION

    def to_cbor(self) -> bytes:
        """Serialize to CBOR bytes, with zlib compression for large payloads.

        Wire format:
          0x00 + cbor_bytes  = uncompressed
          0x01 + zlib_bytes  = compressed
        """
        raw = cbor2.dumps({
            "v": self.v,
            "type": self.type.value,
            "id": self.id,
            "ts": self.ts,
            "from": self.from_peer,
            "payload": self.payload,
        })
        if len(raw) >= _COMPRESS_THRESHOLD:
            compressed = zlib.compress(raw, level=1)  # fast compression
            if len(compressed) < len(raw) - 16:  # only if meaningful savings
                return b'\x01' + compressed
        return b'\x00' + raw

    @classmethod
    def from_cbor(cls, data: bytes) -> MessageEnvelope:
        """Deserialize from CBOR bytes (handles compressed and uncompressed)."""
        if not data:
            raise ValueError("Empty message data")
        if data[0] == 0x01:
            data = zlib.decompress(data[1:])
        elif data[0] == 0x00:
            data = data[1:]
        # else: legacy uncompressed (no prefix) — parse as-is
        obj = cbor2.loads(data)
        return cls(
            v=obj["v"],
            type=MessageType(obj["type"]),
            id=obj["id"],
            ts=obj["ts"],
            from_peer=obj["from"],
            payload=obj["payload"],
        )

    def to_framed(self) -> bytes:
        """Encode with 4-byte big-endian length prefix for transport framing."""
        payload = self.to_cbor()
        length = len(payload)
        return length.to_bytes(4, "big") + payload

    @classmethod
    def read_frame(cls, data: bytes) -> tuple[MessageEnvelope | None, bytes]:
        """Read one framed message from buffer.

        Returns (message, remaining_buffer). Returns (None, data) if incomplete.
        """
        if len(data) < 4:
            return None, data
        length = int.from_bytes(data[:4], "big")
        if length > 10 * 1024 * 1024:  # 10MB sanity limit
            raise ValueError(f"Frame too large: {length}")
        if len(data) < 4 + length:
            return None, data
        msg = cls.from_cbor(data[4 : 4 + length])
        return msg, data[4 + length :]
