"""Typed message builders for all protocol message types."""

from __future__ import annotations

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
    tools: list | None = None,
    tool_choice: Any = None,
    reasoning_exclude: bool | None = None,
) -> MessageEnvelope:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    # Forward reasoning suppression to the peer so hybrid models like
    # Qwen3 can pass enable_thinking=False to their chat template and
    # not waste the token budget on <think>...</think> that gateways
    # then strip — which leaves an empty response on small max_tokens.
    # None lets the peer decide per its own default.
    if reasoning_exclude is not None:
        payload["reasoning_exclude"] = reasoning_exclude
    return MessageEnvelope(
        type=MessageType.INFERENCE_REQ,
        from_peer=from_peer,
        payload=payload,
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
    tool_calls: list | None = None,
) -> MessageEnvelope:
    payload: dict[str, Any] = {"text": text, "finish_reason": finish_reason}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return MessageEnvelope(
        type=MessageType.INFERENCE_STREAM,
        from_peer=from_peer,
        id=request_id,
        payload=payload,
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


def fleet_command(
    from_peer: str,
    command: str,
    params: dict[str, Any] | None = None,
    fleet_admin_key: str = "",
) -> MessageEnvelope:
    """Build a fleet management command (relayed via bootstrap to a node)."""
    return MessageEnvelope(
        type=MessageType.FLEET_COMMAND,
        from_peer=from_peer,
        payload={
            "command": command,
            "params": params or {},
            "fleet_admin_key": fleet_admin_key,
        },
    )


def fleet_response(
    from_peer: str,
    request_id: str,
    success: bool,
    data: dict[str, Any] | None = None,
    error: str = "",
) -> MessageEnvelope:
    """Build a fleet management response."""
    return MessageEnvelope(
        type=MessageType.FLEET_RESPONSE,
        from_peer=from_peer,
        id=request_id,
        payload={
            "success": success,
            "data": data or {},
            "error": error,
        },
    )


def train_round(from_peer: str, payload: dict[str, Any]) -> MessageEnvelope:
    """Announce a federated training round to one participant (F3).

    `payload` comes from ``mycellm.training.round.build_train_round_payload()``
    (round config + the encoded base adapter) and stays opaque here so the
    transport layer keeps no numpy dependency.
    """
    return MessageEnvelope(
        type=MessageType.TRAIN_ROUND,
        from_peer=from_peer,
        payload=payload,
    )


def train_update(
    from_peer: str,
    request_id: str,
    payload: dict[str, Any],
) -> MessageEnvelope:
    """A participant's reply to TRAIN_ROUND (its adapter delta + sample count).

    Echoes the announce's `request_id` so the coordinator's send_and_wait()
    matches it, the same way fleet_response answers fleet_command. Payload is
    built by ``mycellm.training.round.build_train_update_payload()``.
    """
    return MessageEnvelope(
        type=MessageType.TRAIN_UPDATE,
        from_peer=from_peer,
        id=request_id,
        payload=payload,
    )


def train_result(from_peer: str, payload: dict[str, Any]) -> MessageEnvelope:
    """Publish an aggregated global adapter to participants (no reply expected).

    Payload is built by ``mycellm.training.round.build_train_result_payload()``.
    """
    return MessageEnvelope(
        type=MessageType.TRAIN_RESULT,
        from_peer=from_peer,
        payload=payload,
    )
