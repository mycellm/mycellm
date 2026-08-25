"""Framed multi-message streams, and the legacy single-message shape.

A streamed reply is many messages on ONE stream (`open_frame_stream` /
`send_frame` / `end_frame_stream`); everything else is still one message per
stream. The receiver has to handle both, including the awkward cases where they
look alike, because a mixed-version fleet produces both at once.
"""

from mycellm.protocol.envelope import MessageEnvelope, MessageType


class _Recorder:
    """Stands in for the QUIC protocol's dispatch side."""

    def __init__(self):
        self.messages = []

    def __call__(self, msg, stream_id):
        self.messages.append(msg)


def _protocol(recorder):
    from mycellm.transport.quic import MycellmQuicProtocol
    p = MycellmQuicProtocol.__new__(MycellmQuicProtocol)
    p._buffers = {}
    p._response_futures = {}
    p._message_handler = None
    p._dispatch_single = lambda msg, sid: recorder(msg, sid)
    return p


def _chunk(seq, text, rid="r1"):
    from mycellm.transport.messages import inference_stream_chunk
    return inference_stream_chunk("peer", rid, text, seq=seq)


def _Event(stream_id, data, end_stream=False):
    """A real aioquic event — the handler dispatches on isinstance, so a
    look-alike is silently ignored and every assertion passes vacuously."""
    from aioquic.quic.events import StreamDataReceived
    return StreamDataReceived(stream_id=stream_id, data=data, end_stream=end_stream)


class TestFramedDispatch:
    def test_several_frames_on_one_stream_all_dispatch(self):
        rec = _Recorder()
        p = _protocol(rec)
        payload = b"".join(_chunk(i, f"t{i}").to_framed() for i in range(3))
        p._buffers[0] = payload
        p._try_framed_dispatch(0)
        assert [m.payload["text"] for m in rec.messages] == ["t0", "t1", "t2"]

    def test_a_frame_split_across_events_is_held_until_complete(self):
        rec = _Recorder()
        p = _protocol(rec)
        whole = _chunk(0, "hello").to_framed()
        p._buffers[0] = whole[:-4]
        p._try_framed_dispatch(0)
        assert rec.messages == [], "an incomplete frame must not dispatch"
        p._buffers[0] = whole
        p._try_framed_dispatch(0)
        assert len(rec.messages) == 1

    def test_an_unframed_message_is_not_dropped(self):
        """⚠️ A BARE CBOR MAP READ AS A LENGTH PREFIX IS HUNDREDS OF MEGABYTES.

        The receiver used to treat that as a corrupt frame and DISCARD the
        buffer, losing a legitimate single-message stream from an older peer
        that happened to arrive fragmented. It must be left for the
        end-of-stream path to parse whole.
        """
        rec = _Recorder()
        p = _protocol(rec)
        raw = MessageEnvelope(type=MessageType.PING, payload={}, from_peer="x", id="p1").to_cbor()
        p._buffers[0] = raw
        p._try_framed_dispatch(0)
        assert rec.messages == []
        assert p._buffers[0] == raw, "the buffer must survive for the whole-message path"


class TestEndOfStream:
    def test_a_final_frame_arriving_with_the_fin_is_not_lost(self):
        """⚠️ THE BUG THIS FILE EXISTS FOR.

        A streamed reply ends by closing its frame stream, and QUIC may deliver
        the last frame and the FIN in one event. Parsing the whole buffer as a
        single CBOR message then fails and the frame — usually INFERENCE_DONE —
        is thrown away, so the client waits out its idle timeout for a message
        that did arrive.
        """
        rec = _Recorder()
        p = _protocol(rec)
        from mycellm.transport.messages import inference_done
        payload = _chunk(0, "last").to_framed() + inference_done("peer", "r1").to_framed()
        p.quic_event_received(_Event(0, payload, end_stream=True))
        types = [m.type for m in rec.messages]
        assert MessageType.INFERENCE_DONE in types, f"DONE was lost: {types}"
        assert [m.payload.get("text") for m in rec.messages if m.type == MessageType.INFERENCE_STREAM] == ["last"]

    def test_a_legacy_single_message_stream_still_works(self):
        # One CBOR message, whole, with the FIN — every non-streaming reply.
        rec = _Recorder()
        p = _protocol(rec)
        msg = MessageEnvelope(type=MessageType.PONG, payload={}, from_peer="x", id="q1")
        p.quic_event_received(_Event(2, msg.to_cbor(), end_stream=True))
        assert [m.type for m in rec.messages] == [MessageType.PONG]

    def test_frames_then_a_clean_close_dispatches_each_exactly_once(self):
        rec = _Recorder()
        p = _protocol(rec)
        p.quic_event_received(_Event(0, _chunk(0, "a").to_framed()))
        p.quic_event_received(_Event(0, _chunk(1, "b").to_framed()))
        p.quic_event_received(_Event(0, b"", end_stream=True))
        assert [m.payload["text"] for m in rec.messages] == ["a", "b"]

    def test_an_empty_close_does_not_log_a_parse_failure(self):
        # Closing an already-drained frame stream must be a no-op, not an
        # attempt to parse zero bytes as a message.
        rec = _Recorder()
        p = _protocol(rec)
        p.quic_event_received(_Event(0, _chunk(0, "only").to_framed()))
        before = len(rec.messages)
        p.quic_event_received(_Event(0, b"", end_stream=True))
        assert len(rec.messages) == before
