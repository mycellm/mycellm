"""NodeHello authentication over QUIC transport."""

from __future__ import annotations

import logging

from mycellm.identity.certs import DeviceCert
from mycellm.identity.keys import DeviceKey
from mycellm.identity.peer_id import peer_id_from_public_key
from mycellm.protocol.capabilities import Capabilities
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode, ProtocolError
from mycellm.protocol.node_hello import NodeHello, verify_node_hello

logger = logging.getLogger("mycellm.transport.auth")


def build_node_hello(
    device_key: DeviceKey,
    cert: DeviceCert,
    capabilities: Capabilities,
) -> MessageEnvelope:
    """Build a signed NodeHello message envelope."""
    peer_id = peer_id_from_public_key(device_key.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device_key.public_bytes,
        cert=cert,
        capabilities=capabilities,
    )
    hello.sign(device_key)

    return MessageEnvelope(
        type=MessageType.NODE_HELLO,
        from_peer=peer_id,
        payload={"hello": hello.to_cbor()},
    )


def verify_hello_message(msg: MessageEnvelope) -> tuple[NodeHello, str]:
    """Verify a received NodeHello message envelope.

    Returns (hello, error_msg). error_msg is empty if valid.
    """
    if msg.type != MessageType.NODE_HELLO:
        raise ProtocolError(ErrorCode.AUTH_FAILED, "Expected NODE_HELLO message")

    hello_bytes = msg.payload.get("hello")
    if not hello_bytes:
        raise ProtocolError(ErrorCode.AUTH_FAILED, "Missing hello payload")

    hello = NodeHello.from_cbor(hello_bytes)
    valid, error = verify_node_hello(hello)

    if not valid:
        logger.warning(f"NodeHello verification failed from {hello.peer_id}: {error}")
        raise ProtocolError(ErrorCode.AUTH_FAILED, error)

    logger.info(f"Authenticated peer {hello.peer_id} (role={hello.cert.role})")
    return hello, ""


def build_hello_ack(
    device_key: DeviceKey,
    cert: DeviceCert,
    capabilities: Capabilities,
    request_id: str = "",
) -> MessageEnvelope:
    """Build a NodeHelloAck response (server sends its own hello back)."""
    peer_id = peer_id_from_public_key(device_key.public_key)

    hello = NodeHello(
        peer_id=peer_id,
        device_pubkey=device_key.public_bytes,
        cert=cert,
        capabilities=capabilities,
    )
    hello.sign(device_key)

    envelope = MessageEnvelope(
        type=MessageType.NODE_HELLO_ACK,
        from_peer=peer_id,
        payload={"hello": hello.to_cbor()},
    )
    if request_id:
        envelope.id = request_id
    return envelope
