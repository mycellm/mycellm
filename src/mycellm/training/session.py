"""Carry a federated training round over the QUIC transport.

round.py owns the pure state machine and the wire payloads; this module is the
wiring that moves them between peers. It follows the same request/response
conventions as the fleet relay (``api.admin.fleet_relay_command`` →
``Node._handle_fleet_command``):

  coordinator  protocol.send_and_wait(TRAIN_ROUND, bidirectional=True)
  participant  protocol.reply_on_stream(stream_id, TRAIN_UPDATE)  # echoes id
  coordinator  protocol.send_message(TRAIN_RESULT, bidirectional=True)

Bidirectional streams throughout: participants may be iOS nodes, whose
Network.framework only delivers peer-initiated *bidirectional* streams (see
MycellmQuicProtocol.send_message). A round is request/response only — no live
gradient exchange — so one announce and one reply per participant is the whole
conversation, and the aggregate is published as a fire-and-forget announce.

Nothing here reaches into the node: TrainingParticipant.handle_message() takes
the same (protocol, msg, stream_id) triple as Node._handle_peer_message's other
arms, so hooking it up is a single dispatch arm.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode
from mycellm.training.aggregate import (
    Adapter,
    AggregationError,
    ParticipantUpdate,
    validate_update,
)
from mycellm.training.round import (
    RoundConfig,
    RoundResult,
    TrainingRound,
    adapter_fingerprint,
    build_train_result_payload,
    build_train_round_payload,
    build_train_update_payload,
    parse_train_result_payload,
    parse_train_round_payload,
    parse_train_update_payload,
)
from mycellm.transport.messages import error_message, train_result, train_round, train_update

if TYPE_CHECKING:  # avoids pulling aioquic in for a type name
    from mycellm.transport.connection import PeerConnection

logger = logging.getLogger("mycellm.training")


@dataclass
class LocalUpdate:
    """What a node's local trainer produces for one round.

    `delta` is trained-minus-base (see aggregate.adapter_delta); `num_samples`
    is the FedAvg weight; `metrics` are optional local eval numbers the
    coordinator may later gate on.
    """

    delta: Adapter
    num_samples: int
    metrics: dict[str, float] = field(default_factory=dict)


# Given the round's hyperparams and base adapter, train locally and return the
# delta. The real MLX/torch LoRA bridge plugs in here; tests pass a stub.
LocalTrainer = Callable[[RoundConfig, Adapter], Awaitable[LocalUpdate]]


@dataclass
class RoundOutcome:
    """What a coordinated round produced, plus who fell out along the way."""

    result: RoundResult
    fingerprint: str
    failures: dict[str, str]  # peer_id -> why it contributed nothing
    notified: list[str]  # peers handed the aggregated adapter


class TrainingCoordinator:
    """Runs rounds against a set of connected peers.

    Participants are the node's ``_peer_connections`` entries (anything
    exposing a ``.protocol``), so a coordinator can be pointed at a subset of
    the fleet — e.g. only peers whose train.status reported can_train_local.
    """

    def __init__(self, peer_id: str) -> None:
        self.peer_id = peer_id

    async def run_round(
        self,
        config: RoundConfig,
        base: Adapter,
        participants: Mapping[str, PeerConnection],
    ) -> RoundOutcome:
        """Announce a round, collect updates, aggregate, publish the result.

        Every participant is asked concurrently and given `deadline_s` to
        answer; a peer that times out, errors, or returns an unusable update is
        recorded in RoundOutcome.failures and simply left out of the average.
        Raises AggregationError if too few usable updates came back to meet
        `min_participants` — the caller can retry the round rather than publish
        a global adapter shaped by one peer.
        """
        round_state = TrainingRound(config=config, base=base)
        announce = build_train_round_payload(config, base)
        failures: dict[str, str] = {}

        collected = await asyncio.gather(
            *(
                self._collect_update(peer_id, conn, announce, base, config.deadline_s)
                for peer_id, conn in participants.items()
            )
        )
        usable = 0
        for peer_id, update, error in collected:
            if update is None:
                failures[peer_id] = error or "no update"
                logger.warning(
                    f"Training round {config.job_id}/{config.round_index}: "
                    f"peer {peer_id[:8]} dropped ({failures[peer_id]})"
                )
                continue
            round_state.submit(update)
            if update.num_samples > 0:
                usable += 1

        if not round_state.has_quorum():
            raise AggregationError(
                f"round {config.job_id}/{config.round_index} got {usable} usable "
                f"update(s), needs {config.min_participants}"
            )

        result = round_state.aggregate()
        for peer_id in result.dropped:
            failures.setdefault(peer_id, "update carried no samples")

        fingerprint = adapter_fingerprint(result.adapter)
        logger.info(
            f"Training round {config.job_id}/{config.round_index} aggregated "
            f"{len(result.contributors)} updates ({result.total_samples} samples) "
            f"→ adapter {fingerprint}"
        )
        notified = await self._publish_result(result, participants, failures)
        return RoundOutcome(
            result=result,
            fingerprint=fingerprint,
            failures=failures,
            notified=notified,
        )

    async def _collect_update(
        self,
        peer_id: str,
        conn: PeerConnection,
        announce: dict,
        base: Adapter,
        timeout: float,
    ) -> tuple[str, ParticipantUpdate | None, str | None]:
        """Ask one peer for its update. Never raises — failures come back as text."""
        msg = train_round(self.peer_id, announce)
        try:
            reply = await conn.protocol.send_and_wait(msg, timeout=timeout, bidirectional=True)
        except (asyncio.TimeoutError, TimeoutError):
            return peer_id, None, f"no update within {timeout:g}s"
        except Exception as e:
            return peer_id, None, f"announce failed: {e}"

        if reply.type == MessageType.ERROR:
            return peer_id, None, reply.payload.get("error_message", "peer error")
        if reply.type != MessageType.TRAIN_UPDATE:
            return peer_id, None, f"unexpected reply {reply.type.value}"

        try:
            update = parse_train_update_payload(reply.payload)
        except Exception as e:
            return peer_id, None, f"malformed update: {e}"

        # A peer may only contribute as itself — otherwise one participant
        # could stuff the average (and later, the credit for it) under another
        # peer's name, and last-write-wins in TrainingRound.submit() would let
        # it overwrite the real contribution.
        if update.peer_id != peer_id:
            return peer_id, None, f"update claims peer {update.peer_id[:8]}"

        try:
            validate_update(base, update)
        except AggregationError as e:
            return peer_id, None, str(e)
        return peer_id, update, None

    async def _publish_result(
        self,
        result: RoundResult,
        participants: Mapping[str, PeerConnection],
        failures: dict[str, str],
    ) -> list[str]:
        """Announce the aggregated adapter to every participant (no reply expected)."""
        payload = build_train_result_payload(result)
        notified: list[str] = []
        for peer_id, conn in participants.items():
            try:
                stream_id = await conn.protocol.send_message(
                    train_result(self.peer_id, payload), bidirectional=True
                )
                # Nothing comes back on this stream; release its receive side so
                # half-open streams don't accumulate against MAX_STREAMS.
                conn.protocol.close_stream(stream_id)
            except Exception as e:
                failures.setdefault(peer_id, f"result not delivered: {e}")
                logger.warning(f"TRAIN_RESULT to {peer_id[:8]} failed: {e}")
                continue
            notified.append(peer_id)
        return notified


class TrainingParticipant:
    """Node-side handler for the training messages a coordinator sends.

    Holds no transport of its own: it replies on whichever stream the request
    arrived on, exactly like the node's fleet-command handler.
    """

    def __init__(self, peer_id: str, trainer: LocalTrainer | None = None) -> None:
        self.peer_id = peer_id
        self.trainer = trainer
        # Newest global adapter this node has been handed, per job.
        self.results: dict[str, RoundResult] = {}
        self._active: set[str] = set()

    async def handle_message(self, protocol, msg: MessageEnvelope, stream_id: int) -> bool:
        """Handle a TRAIN_* message; returns True if it was one of ours.

        Signature matches Node._handle_peer_message so the node can dispatch
        straight into this.
        """
        if msg.type == MessageType.TRAIN_ROUND:
            await self._handle_round(protocol, msg, stream_id)
            return True
        if msg.type == MessageType.TRAIN_RESULT:
            self._handle_result(msg)
            return True
        return False

    def status(self) -> dict:
        """Training state for the fleet-visible train.status command."""
        return {
            "active_jobs": sorted(self._active),
            "known_jobs": {
                job_id: result.round_index for job_id, result in sorted(self.results.items())
            },
        }

    async def _handle_round(self, protocol, msg: MessageEnvelope, stream_id: int) -> None:
        try:
            config, base = parse_train_round_payload(msg.payload)
        except Exception as e:
            await self._reply_error(
                protocol, msg, stream_id, ErrorCode.INVALID_MESSAGE, f"malformed TRAIN_ROUND: {e}"
            )
            return

        if self.trainer is None:
            await self._reply_error(
                protocol,
                msg,
                stream_id,
                ErrorCode.MODEL_UNAVAILABLE,
                "node has no local trainer",
            )
            return

        self._active.add(config.job_id)
        try:
            local = await self.trainer(config, base)
        except Exception as e:
            logger.warning(f"Local training for {config.job_id} failed: {e}")
            await self._reply_error(protocol, msg, stream_id, ErrorCode.BACKEND_ERROR, str(e))
            return
        finally:
            self._active.discard(config.job_id)

        update = ParticipantUpdate(
            peer_id=self.peer_id,
            delta=local.delta,
            num_samples=local.num_samples,
            metrics=local.metrics,
        )
        reply = train_update(self.peer_id, msg.id, build_train_update_payload(update))
        await protocol.reply_on_stream(stream_id, reply)

    def _handle_result(self, msg: MessageEnvelope) -> None:
        try:
            result = parse_train_result_payload(msg.payload)
        except Exception as e:
            logger.warning(f"Dropping malformed TRAIN_RESULT from {msg.from_peer[:8]}: {e}")
            return

        announced = msg.payload.get("fingerprint", "")
        actual = adapter_fingerprint(result.adapter)
        if announced and announced != actual:
            # What arrived is not the adapter the coordinator named — drop it
            # rather than adopt a truncated or substituted global model.
            logger.warning(
                f"Dropping TRAIN_RESULT for {result.job_id}: fingerprint "
                f"{actual} != announced {announced}"
            )
            return

        self.results[result.job_id] = result
        logger.info(
            f"Adopted global adapter {actual} for {result.job_id}/{result.round_index} "
            f"({len(result.contributors)} contributors)"
        )

    async def _reply_error(
        self,
        protocol,
        msg: MessageEnvelope,
        stream_id: int,
        code: ErrorCode,
        text: str,
    ) -> None:
        await protocol.reply_on_stream(stream_id, error_message(self.peer_id, msg.id, code, text))
