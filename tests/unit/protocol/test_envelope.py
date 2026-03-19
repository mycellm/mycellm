"""Tests for protocol message envelope."""

from mycellm.protocol.envelope import MessageEnvelope, MessageType, PROTOCOL_VERSION


def test_envelope_cbor_roundtrip():
    msg = MessageEnvelope(
        type=MessageType.PING,
        from_peer="abc123",
        payload={"data": "hello"},
    )
    data = msg.to_cbor()
    loaded = MessageEnvelope.from_cbor(data)

    assert loaded.type == MessageType.PING
    assert loaded.from_peer == "abc123"
    assert loaded.payload == {"data": "hello"}
    assert loaded.v == PROTOCOL_VERSION
    assert loaded.id == msg.id


def test_envelope_framing():
    msg = MessageEnvelope(
        type=MessageType.PONG,
        from_peer="xyz",
        payload={},
    )
    framed = msg.to_framed()

    # Should have 4-byte length prefix
    length = int.from_bytes(framed[:4], "big")
    assert len(framed) == 4 + length

    # Read back
    parsed, remaining = MessageEnvelope.read_frame(framed)
    assert parsed is not None
    assert parsed.type == MessageType.PONG
    assert remaining == b""


def test_envelope_framing_incomplete():
    msg = MessageEnvelope(type=MessageType.PING, payload={})
    framed = msg.to_framed()

    # Give only partial data
    parsed, remaining = MessageEnvelope.read_frame(framed[:3])
    assert parsed is None
    assert remaining == framed[:3]

    # Give length but incomplete payload
    parsed, remaining = MessageEnvelope.read_frame(framed[:6])
    assert parsed is None


def test_envelope_multiple_frames():
    msg1 = MessageEnvelope(type=MessageType.PING, payload={"n": 1})
    msg2 = MessageEnvelope(type=MessageType.PONG, payload={"n": 2})

    buf = msg1.to_framed() + msg2.to_framed()

    parsed1, buf = MessageEnvelope.read_frame(buf)
    assert parsed1.payload["n"] == 1

    parsed2, buf = MessageEnvelope.read_frame(buf)
    assert parsed2.payload["n"] == 2
    assert buf == b""


def test_all_message_types():
    for mt in MessageType:
        msg = MessageEnvelope(type=mt, payload={})
        data = msg.to_cbor()
        loaded = MessageEnvelope.from_cbor(data)
        assert loaded.type == mt
