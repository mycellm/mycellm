"""Tests for running a federated round over the transport (training/session.py).

The peers talk through LoopbackProtocol, an in-memory stand-in for
MycellmQuicProtocol that keeps the parts a round depends on: every message
crosses the link as CBOR, each send opens a new stream (QUIC's numbering —
bidirectional 0/4/8..., unidirectional 2/6/10...), replies come back matched by
message id, and reply_on_stream() answers framed on the stream the request
arrived on. So test_two_peer_round_end_to_end really is a round's worth of
wire traffic, not a function call in disguise.
"""

from __future__ import annotations

import asyncio

import pytest

# Training is an optional extra (`pip install -e .[training]`); skip the whole
# module rather than error-collect when numpy isn't installed.
np = pytest.importorskip("numpy")

from mycellm.protocol.envelope import MessageEnvelope, MessageType  # noqa: E402
from mycellm.protocol.errors import ErrorCode  # noqa: E402
from mycellm.training import (  # noqa: E402
    AggregationError,
    LocalUpdate,
    ParticipantUpdate,
    RoundConfig,
    TrainingCoordinator,
    TrainingParticipant,
    adapter_fingerprint,
    build_train_update_payload,
)
from mycellm.training.codec import decode_adapter, encode_adapter  # noqa: E402
from mycellm.transport.messages import error_message, train_result, train_update  # noqa: E402


# --- in-memory transport --------------------------------------------------


class LoopbackProtocol:
    """MycellmQuicProtocol's contract, backed by a direct link to one peer."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.peer: LoopbackProtocol | None = None
        self.sent: list[MessageEnvelope] = []
        self.received: list[MessageEnvelope] = []
        self.sent_streams: list[int] = []
        self.closed_streams: list[int] = []
        self._handler = None
        self._response_futures: dict[str, asyncio.Future] = {}
        self._next_stream = 0
        self._tasks: list[asyncio.Task] = []

    def set_message_handler(self, handler) -> None:
        self._handler = handler

    async def send_message(self, msg: MessageEnvelope, bidirectional: bool = False) -> int:
        stream_id = self._next_stream * 4 + (0 if bidirectional else 2)
        self._next_stream += 1
        self.sent.append(msg)
        self.sent_streams.append(stream_id)
        self.peer._deliver(msg.to_cbor(), stream_id)
        return stream_id

    def close_stream(self, stream_id: int) -> None:
        self.closed_streams.append(stream_id)

    async def send_and_wait(
        self, msg: MessageEnvelope, timeout: float = 30.0, bidirectional: bool = False
    ) -> MessageEnvelope:
        fut: asyncio.Future[MessageEnvelope] = asyncio.get_running_loop().create_future()
        self._response_futures[msg.id] = fut
        stream_id = None
        try:
            stream_id = await self.send_message(msg, bidirectional=bidirectional)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._response_futures.pop(msg.id, None)
            if bidirectional and stream_id is not None:
                self.close_stream(stream_id)

    async def reply_on_stream(self, stream_id: int, msg: MessageEnvelope) -> None:
        if stream_id % 4 == 0:
            self.sent.append(msg)
            self.peer._deliver(msg.to_cbor(), stream_id)
        else:
            await self.send_message(msg)

    def _deliver(self, data: bytes, stream_id: int) -> None:
        msg = MessageEnvelope.from_cbor(data)
        self.received.append(msg)
        fut = self._response_futures.get(msg.id)
        if fut is not None and not fut.done():
            fut.set_result(msg)
            return
        if self._handler is not None:
            self._tasks.append(asyncio.ensure_future(self._handler(self, msg, stream_id)))

    async def settle(self) -> None:
        """Await handler tasks (fire-and-forget announces land here)."""
        tasks, self._tasks = self._tasks, []
        if tasks:
            await asyncio.gather(*tasks)

    async def close(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class LoopbackConnection:
    """Stands in for PeerConnection — the coordinator only uses .protocol."""

    def __init__(self, protocol: LoopbackProtocol) -> None:
        self.protocol = protocol


def _link(name: str) -> tuple[LoopbackProtocol, LoopbackProtocol]:
    near, far = LoopbackProtocol(f"{name}-near"), LoopbackProtocol(f"{name}-far")
    near.peer, far.peer = far, near
    return near, far


# --- fixtures -------------------------------------------------------------


def _adapter(**kw) -> dict[str, np.ndarray]:
    return {k: np.asarray(v, dtype=np.float32) for k, v in kw.items()}


def _trainer(delta, num_samples, *, dtype=np.float32, fails: str = "", delay: float = 0.0):
    """A stub local trainer; records the round it was handed."""
    seen: list[tuple[RoundConfig, dict]] = []

    async def train(config: RoundConfig, base: dict) -> LocalUpdate:
        seen.append((config, base))
        if delay:
            await asyncio.sleep(delay)
        if fails:
            raise RuntimeError(fails)
        return LocalUpdate(
            delta={"w": np.asarray(delta, dtype=dtype)},
            num_samples=num_samples,
            metrics={"loss": 0.5},
        )

    train.seen = seen
    return train


def _join(
    coordinator_peers: dict, peer_id: str, trainer
) -> tuple[TrainingParticipant, LoopbackProtocol]:
    """Wire a participant to the coordinator over its own loopback link."""
    coord_side, peer_side = _link(peer_id)
    participant = TrainingParticipant(peer_id, trainer)
    peer_side.set_message_handler(participant.handle_message)
    coordinator_peers[peer_id] = LoopbackConnection(coord_side)
    return participant, peer_side


def _config(**kw) -> RoundConfig:
    defaults = {
        "job_id": "job-1",
        "round_index": 0,
        "base_model": "Qwen2.5-3B",
        "min_participants": 2,
        "deadline_s": 5.0,
    }
    return RoundConfig(**{**defaults, **kw})


# --- the round ------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_peer_round_end_to_end():
    """Announce → two updates → FedAvg → both peers adopt the global adapter."""
    peers: dict = {}
    a_trainer = _trainer([2.0, 0.0], 5)
    b_trainer = _trainer([0.0, 4.0], 5)
    peer_a, a_side = _join(peers, "peerA", a_trainer)
    peer_b, b_side = _join(peers, "peerB", b_trainer)

    base = _adapter(w=[0.0, 0.0])
    outcome = await TrainingCoordinator("coord").run_round(_config(), base, peers)

    # Equal sample counts → plain mean of the two deltas.
    np.testing.assert_allclose(outcome.result.adapter["w"], [1.0, 2.0])
    assert sorted(outcome.result.contributors) == ["peerA", "peerB"]
    assert outcome.result.total_samples == 10
    assert outcome.failures == {}
    assert sorted(outcome.notified) == ["peerA", "peerB"]
    assert outcome.fingerprint == adapter_fingerprint(outcome.result.adapter)

    # Each participant trained against the round the coordinator announced.
    for trainer in (a_trainer, b_trainer):
        config, sent_base = trainer.seen[0]
        assert config.job_id == "job-1"
        assert config.base_model == "Qwen2.5-3B"
        np.testing.assert_allclose(sent_base["w"], base["w"])

    # TRAIN_RESULT is fire-and-forget; let the handler tasks run.
    await a_side.settle()
    await b_side.settle()
    for participant in (peer_a, peer_b):
        result = participant.results["job-1"]
        np.testing.assert_allclose(result.adapter["w"], [1.0, 2.0])
        assert result.round_index == 0
        assert sorted(result.contributors) == ["peerA", "peerB"]
        assert participant.status()["known_jobs"] == {"job-1": 0}

    # The base adapter the coordinator holds is untouched by the round.
    np.testing.assert_allclose(base["w"], [0.0, 0.0])


@pytest.mark.asyncio
async def test_round_follows_fleet_relay_stream_conventions():
    """Announce/result go out bidirectional; the update echoes the announce id."""
    peers: dict = {}
    _join(peers, "peerA", _trainer([1.0, 1.0], 4))
    _join(peers, "peerB", _trainer([1.0, 1.0], 4))

    await TrainingCoordinator("coord").run_round(_config(), _adapter(w=[0.0, 0.0]), peers)

    coord_side = peers["peerA"].protocol
    announce, result = coord_side.sent
    assert announce.type == MessageType.TRAIN_ROUND
    assert announce.from_peer == "coord"
    assert result.type == MessageType.TRAIN_RESULT
    assert result.payload["fingerprint"] == adapter_fingerprint(
        decode_adapter(result.payload["adapter"])
    )

    # Both were sent on client-initiated bidirectional streams (id % 4 == 0),
    # as the fleet relay does, and each stream was released after use.
    assert all(sid % 4 == 0 for sid in coord_side.sent_streams)
    assert sorted(coord_side.closed_streams) == sorted(coord_side.sent_streams)

    # The participant replied on the announce's stream, carrying its id so
    # send_and_wait() matches it.
    update = coord_side.received[0]
    assert update.type == MessageType.TRAIN_UPDATE
    assert update.id == announce.id
    assert update.payload["peer_id"] == "peerA"
    assert update.payload["num_samples"] == 4


@pytest.mark.asyncio
async def test_fp16_deltas_survive_the_wire():
    """Adapters ship fp16 to halve wire size; aggregation still upcasts cleanly."""
    peers: dict = {}
    _join(peers, "peerA", _trainer([1.5, -0.5], 10, dtype=np.float16))
    _join(peers, "peerB", _trainer([0.5, 0.5], 10, dtype=np.float16))

    outcome = await TrainingCoordinator("coord").run_round(
        _config(), _adapter(w=[0.0, 0.0]), peers
    )
    np.testing.assert_allclose(outcome.result.adapter["w"], [1.0, 0.0])
    assert outcome.result.adapter["w"].dtype == np.float32


# --- degraded rounds ------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_participant_is_dropped_not_fatal():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    _join(peers, "peerB", _trainer([9.9, 9.9], 5, fails="CUDA is on fire"))

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1), _adapter(w=[0.0, 0.0]), peers
    )
    np.testing.assert_allclose(outcome.result.adapter["w"], [2.0, 0.0])
    assert outcome.result.contributors == ["peerA"]
    assert "CUDA is on fire" in outcome.failures["peerB"]
    # A peer that failed the round still learns the new global adapter.
    assert sorted(outcome.notified) == ["peerA", "peerB"]


@pytest.mark.asyncio
async def test_participant_without_trainer_reports_model_unavailable():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    _join(peers, "peerB", None)

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1), _adapter(w=[0.0, 0.0]), peers
    )
    assert outcome.result.contributors == ["peerA"]
    assert outcome.failures["peerB"] == "node has no local trainer"


@pytest.mark.asyncio
async def test_slow_participant_times_out_and_round_goes_on():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    _, slow_side = _join(peers, "peerB", _trainer([1.0, 1.0], 5, delay=5.0))

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1, deadline_s=0.05), _adapter(w=[0.0, 0.0]), peers
    )
    assert outcome.result.contributors == ["peerA"]
    assert "no update within" in outcome.failures["peerB"]
    await slow_side.close()


@pytest.mark.asyncio
async def test_quorum_not_reached_raises_rather_than_publishing():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5, fails="oom"))
    _join(peers, "peerB", _trainer([0.0, 4.0], 5, fails="oom"))

    with pytest.raises(AggregationError, match="0 usable"):
        await TrainingCoordinator("coord").run_round(
            _config(), _adapter(w=[0.0, 0.0]), peers
        )
    # Nothing was published — no peer saw a TRAIN_RESULT.
    for conn in peers.values():
        assert [m.type for m in conn.protocol.sent] == [MessageType.TRAIN_ROUND]


@pytest.mark.asyncio
async def test_mismatched_adapter_shape_drops_only_that_peer():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    _join(peers, "peerB", _trainer([1.0, 2.0, 3.0], 5))  # wrong width

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1), _adapter(w=[0.0, 0.0]), peers
    )
    assert outcome.result.contributors == ["peerA"]
    assert "shape" in outcome.failures["peerB"]


@pytest.mark.asyncio
async def test_update_claiming_another_peer_is_rejected():
    """A peer may only contribute as itself — no stuffing the average."""
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    coord_side, liar_side = _link("peerB")
    peers["peerB"] = LoopbackConnection(coord_side)

    async def forge(protocol, msg, stream_id):
        forged = ParticipantUpdate(
            peer_id="peerA",  # not who we are
            delta={"w": np.asarray([99.0, 99.0], dtype=np.float32)},
            num_samples=10_000,
        )
        await protocol.reply_on_stream(
            stream_id, train_update("peerB", msg.id, build_train_update_payload(forged))
        )

    liar_side.set_message_handler(forge)

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1), _adapter(w=[0.0, 0.0]), peers
    )
    np.testing.assert_allclose(outcome.result.adapter["w"], [2.0, 0.0])
    assert outcome.result.contributors == ["peerA"]
    assert outcome.failures["peerB"] == "update claims peer peerA"


@pytest.mark.asyncio
async def test_error_reply_is_recorded_with_its_message():
    peers: dict = {}
    _join(peers, "peerA", _trainer([2.0, 0.0], 5))
    coord_side, grump_side = _link("peerB")
    peers["peerB"] = LoopbackConnection(coord_side)

    async def refuse(protocol, msg, stream_id):
        await protocol.reply_on_stream(
            stream_id, error_message("peerB", msg.id, ErrorCode.OVERLOADED, "busy serving")
        )

    grump_side.set_message_handler(refuse)

    outcome = await TrainingCoordinator("coord").run_round(
        _config(min_participants=1), _adapter(w=[0.0, 0.0]), peers
    )
    assert outcome.failures["peerB"] == "busy serving"


# --- result adoption ------------------------------------------------------


@pytest.mark.asyncio
async def test_result_with_wrong_fingerprint_is_not_adopted():
    """A TRAIN_RESULT whose adapter isn't the one named is dropped, not adopted."""
    participant = TrainingParticipant("peerA", _trainer([1.0, 1.0], 5))
    near, far = _link("peerA")
    far.set_message_handler(participant.handle_message)

    tampered = {
        "job_id": "job-1",
        "round_index": 0,
        "adapter": encode_adapter(_adapter(w=[7.0, 7.0])),
        "fingerprint": adapter_fingerprint(_adapter(w=[1.0, 2.0])),
        "contributors": ["peerA"],
        "dropped": [],
        "total_samples": 5,
    }
    await near.send_message(train_result("coord", tampered), bidirectional=True)
    await far.settle()

    assert participant.results == {}
    assert participant.status()["known_jobs"] == {}


@pytest.mark.asyncio
async def test_malformed_round_announce_gets_an_error_reply():
    participant = TrainingParticipant("peerA", _trainer([1.0, 1.0], 5))
    near, far = _link("peerA")
    far.set_message_handler(participant.handle_message)

    bogus = MessageEnvelope(
        type=MessageType.TRAIN_ROUND, from_peer="coord", payload={"config": {}}
    )
    reply = await near.send_and_wait(bogus, timeout=5.0, bidirectional=True)
    assert reply.type == MessageType.ERROR
    assert reply.payload["error_code"] == ErrorCode.INVALID_MESSAGE.value


@pytest.mark.asyncio
async def test_participant_ignores_unrelated_messages():
    participant = TrainingParticipant("peerA", _trainer([1.0, 1.0], 5))
    ping = MessageEnvelope(type=MessageType.PING, from_peer="coord", payload={})
    assert await participant.handle_message(None, ping, 0) is False
