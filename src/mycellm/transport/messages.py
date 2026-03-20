"""Typed message builders for all protocol message types."""

from __future__ import annotations

import uuid
from typing import Any

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode


def ping_message(from_peer: str) -> MessageEnvelope:
    return MessageEnvelope(type=MessageType.PING, from_peer=from_peer, payload={})


def pong_message(from_peer: str, request_id: str) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.PONG, from_peer=from_peer, id=request_id, payload={}
    )


def inference_request(
    from_peer: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False,
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.INFERENCE_REQ,
        from_peer=from_peer,
        payload={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        },
    )


def inference_response(
    from_peer: str,
    request_id: str,
    text: str,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    finish_reason: str = "stop",
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.INFERENCE_RESP,
        from_peer=from_peer,
        id=request_id,
        payload={
            "text": text,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish_reason,
        },
    )


def inference_stream_chunk(
    from_peer: str,
    request_id: str,
    text: str,
    finish_reason: str | None = None,
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.INFERENCE_STREAM,
        from_peer=from_peer,
        id=request_id,
        payload={"text": text, "finish_reason": finish_reason},
    )


def inference_done(from_peer: str, request_id: str) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.INFERENCE_DONE,
        from_peer=from_peer,
        id=request_id,
        payload={},
    )


def error_message(
    from_peer: str,
    request_id: str,
    code: ErrorCode,
    message: str = "",
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.ERROR,
        from_peer=from_peer,
        id=request_id,
        payload={"error_code": code.value, "error_message": message or code.value},
    )


def credit_receipt(
    from_peer: str,
    counterparty: str,
    amount: float,
    reason: str,
    signature: str = "",
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.CREDIT_RECEIPT,
        from_peer=from_peer,
        payload={
            "counterparty": counterparty,
            "amount": amount,
            "reason": reason,
            "signature": signature,
        },
    )


def peer_announce(
    from_peer: str,
    addresses: list[str],
    capabilities: dict[str, Any],
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.PEER_ANNOUNCE,
        from_peer=from_peer,
        payload={"addresses": addresses, "capabilities": capabilities},
    )


def peer_query(from_peer: str, model: str = "") -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.PEER_QUERY,
        from_peer=from_peer,
        payload={"model": model},
    )


def peer_response(
    from_peer: str,
    request_id: str,
    peers: list[dict[str, Any]],
) -> MessageEnvelope:
    return MessageEnvelope(
        type=MessageType.PEER_RESPONSE,
        from_peer=from_peer,
        id=request_id,
        payload={"peers": peers},
    )


def signed_credit_receipt(
    from_peer: str,
    consumer_id: str,
    seeder_id: str,
    model: str,
    tokens: int,
    cost: float,
    timestamp: float,
    signature: str,
) -> MessageEnvelope:
    """Build a signed credit receipt message."""
    return MessageEnvelope(
        type=MessageType.CREDIT_RECEIPT,
        from_peer=from_peer,
        payload={
            "consumer_id": consumer_id,
            "seeder_id": seeder_id,
            "model": model,
            "tokens": tokens,
            "cost": cost,
            "timestamp": timestamp,
            "signature": signature,
        },
    )


def inference_relay(
    from_peer: str,
    target_peer: str,
    model: str,
    messages: list[dict[str, str]],
    via: list[str] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False,
) -> MessageEnvelope:
    """Build an inference relay message for multi-hop routing."""
    return MessageEnvelope(
        type=MessageType.INFERENCE_RELAY,
        from_peer=from_peer,
        payload={
            "target_peer": target_peer,
            "model": model,
            "messages": messages,
            "via": via or [],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        },
    )


def peer_exchange(
    from_peer: str,
    known_peers: list[dict],
) -> MessageEnvelope:
    """Share known peers with a connected peer."""
    return MessageEnvelope(
        type=MessageType.PEER_EXCHANGE,
        from_peer=from_peer,
        payload={"peers": known_peers},
    )
