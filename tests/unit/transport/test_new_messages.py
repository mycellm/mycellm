"""Tests for new Phase 3 message types."""

import pytest

from mycellm.transport.messages import (
    signed_credit_receipt,
    inference_relay,
    peer_exchange,
)
from mycellm.protocol.envelope import MessageType


def test_signed_credit_receipt():
    msg = signed_credit_receipt(
        "peer-a", "consumer-1", "seeder-1",
        "llama-7b", 100, 0.1, 1700000000.0, "sig123",
    )
    assert msg.type == MessageType.CREDIT_RECEIPT
    assert msg.payload["consumer_id"] == "consumer-1"
    assert msg.payload["seeder_id"] == "seeder-1"
    assert msg.payload["tokens"] == 100
    assert msg.payload["signature"] == "sig123"


def test_inference_relay():
    msg = inference_relay(
        "relay-peer", "target-peer", "llama-7b",
        [{"role": "user", "content": "hi"}],
        via=["hop1"],
    )
    assert msg.type == MessageType.INFERENCE_RELAY
    assert msg.payload["target_peer"] == "target-peer"
    assert msg.payload["via"] == ["hop1"]
    assert msg.payload["model"] == "llama-7b"


def test_peer_exchange():
    msg = peer_exchange("peer-a", [
        {"peer_id": "peer-b", "addresses": ["1.2.3.4:8421"]},
    ])
    assert msg.type == MessageType.PEER_EXCHANGE
    assert len(msg.payload["peers"]) == 1


def test_new_message_types_serialize():
    """All new message types serialize to/from CBOR."""
    from mycellm.protocol.envelope import MessageEnvelope

    messages = [
        signed_credit_receipt("a", "b", "c", "m", 1, 0.1, 1.0, "sig"),
        inference_relay("a", "b", "m", [{"role": "user", "content": "hi"}]),
        peer_exchange("a", [{"peer_id": "b"}]),
    ]

    for msg in messages:
        data = msg.to_cbor()
        restored = MessageEnvelope.from_cbor(data)
        assert restored.type == msg.type
        assert restored.payload == msg.payload
