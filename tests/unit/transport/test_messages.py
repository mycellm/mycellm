"""Tests for typed message builders."""

from mycellm.protocol.envelope import MessageType
from mycellm.protocol.errors import ErrorCode
from mycellm.transport.messages import (
    credit_receipt,
    error_message,
    inference_done,
    inference_request,
    inference_response,
    inference_stream_chunk,
    peer_announce,
    peer_query,
    ping_message,
    pong_message,
)


def test_ping_pong():
    ping = ping_message("peer1")
    assert ping.type == MessageType.PING
    assert ping.from_peer == "peer1"

    pong = pong_message("peer2", ping.id)
    assert pong.type == MessageType.PONG
    assert pong.id == ping.id


def test_inference_request():
    msg = inference_request("peer1", "llama-7b", [{"role": "user", "content": "hi"}])
    assert msg.type == MessageType.INFERENCE_REQ
    assert msg.payload["model"] == "llama-7b"
    assert len(msg.payload["messages"]) == 1


def test_inference_response():
    msg = inference_response("peer2", "req123", "Hello!", "llama-7b", 10, 5)
    assert msg.type == MessageType.INFERENCE_RESP
    assert msg.id == "req123"
    assert msg.payload["text"] == "Hello!"


def test_inference_stream():
    chunk = inference_stream_chunk("peer2", "req123", "Hello")
    assert chunk.type == MessageType.INFERENCE_STREAM
    assert chunk.payload["text"] == "Hello"

    done = inference_done("peer2", "req123")
    assert done.type == MessageType.INFERENCE_DONE


def test_error_message():
    msg = error_message("peer1", "req123", ErrorCode.MODEL_UNAVAILABLE, "no such model")
    assert msg.type == MessageType.ERROR
    assert msg.payload["error_code"] == "model_unavailable"


def test_credit_receipt():
    msg = credit_receipt("peer1", "peer2", 0.5, "inference")
    assert msg.type == MessageType.CREDIT_RECEIPT
    assert msg.payload["amount"] == 0.5


def test_peer_announce():
    msg = peer_announce("peer1", ["10.0.0.1:8421"], {"role": "seeder"})
    assert msg.type == MessageType.PEER_ANNOUNCE


def test_all_messages_serialize():
    """Ensure all message builders produce valid CBOR-serializable envelopes."""
    msgs = [
        ping_message("p1"),
        pong_message("p1", "r1"),
        inference_request("p1", "m", []),
        inference_response("p1", "r1", "text"),
        inference_stream_chunk("p1", "r1", "t"),
        inference_done("p1", "r1"),
        error_message("p1", "r1", ErrorCode.TIMEOUT),
        credit_receipt("p1", "p2", 1.0, "test"),
        peer_announce("p1", [], {}),
        peer_query("p1", "model"),
    ]
    for msg in msgs:
        data = msg.to_cbor()
        from mycellm.protocol.envelope import MessageEnvelope
        loaded = MessageEnvelope.from_cbor(data)
        assert loaded.type == msg.type
