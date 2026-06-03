"""Cross-model routing failover.

When a request targets "auto"/"" the resolver returns a ranked candidate list.
If the best model's only seeder fails (error, timeout, or dead session), routing
must fall through to the next-best *model* instead of failing the request. These
tests exercise ``MycellmNode.route_inference`` / ``route_inference_stream``
against a real registry + chain builder + resolver, mocking only the peer
connection.
"""

import types

import pytest

from mycellm.node import MycellmNode
from mycellm.router.registry import PeerRegistry
from mycellm.router.chain import ChainBuilder
from mycellm.router.model_resolver import ModelResolver
from mycellm.protocol.capabilities import Capabilities, ModelCapability, HardwareInfo
from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.transport.connection import PeerState


def _caps(model: str, tok_s: float = 50.0) -> Capabilities:
    return Capabilities(
        models=[ModelCapability(name=model)],
        hardware=HardwareInfo(gpu="test", backend="cpu"),
        est_tok_s=tok_s,
        role="seeder",
    )


class _FakeConn:
    """Stands in for an open PeerConnection. `responder` produces the reply
    (a MessageEnvelope for request(), or an async-gen for request_stream())."""

    def __init__(self, responder, stream_responder=None):
        self.protocol = types.SimpleNamespace(_is_closed=False)
        self.state = PeerState.ROUTABLE
        self.is_overloaded = False
        self._responder = responder
        self._stream_responder = stream_responder

    async def request(self, msg, timeout: float = 120.0):
        return self._responder(msg)

    async def request_stream(self, msg, timeout: float = 120.0):
        async for ev in self._stream_responder(msg):
            yield ev


def _node(registry: PeerRegistry):
    n = types.SimpleNamespace()
    n.peer_id = "self0000000000000000000000000000"
    n.registry = registry
    n.node_registry = {}
    n.ledger = None
    n.chain_builder = ChainBuilder(registry)
    n.model_resolver = ModelResolver(registry)
    inf = types.SimpleNamespace()
    inf.loaded_models = []
    inf.resolve_model_name = lambda m: ""  # nothing loaded locally (bootstrap)
    n.inference = inf
    # Bind the real helper so route_inference* can call self._candidate_models.
    n._candidate_models = types.MethodType(MycellmNode._candidate_models, n)
    return n


def _err(msg):
    return MessageEnvelope(type=MessageType.ERROR, payload={"message": "seeder down"}, from_peer="x")


def _ok(text):
    def _responder(msg):
        return MessageEnvelope(
            type=MessageType.INFERENCE_RESP,
            payload={"text": text, "completion_tokens": 3},
            from_peer="x",
        )
    return _responder


@pytest.mark.asyncio
async def test_route_inference_fails_over_to_next_model():
    """Best model's seeder errors -> request degrades to the next-best model."""
    reg = PeerRegistry()
    # "big" (30b) outranks "small" (1b) by tier, so it is tried first — and fails.
    reg.register("big", connection=_FakeConn(_err), capabilities=_caps("qwen-30b"))
    reg.register("small", connection=_FakeConn(_ok("hi from small")),
                 capabilities=_caps("qwen-1b"))

    node = _node(reg)
    result = await MycellmNode.route_inference(node, "auto", [{"role": "user", "content": "hi"}])

    assert result is not None
    assert result["text"] == "hi from small"
    # The failed seeder should have been penalized.
    assert reg.get("big").failure_count >= 1


@pytest.mark.asyncio
async def test_route_inference_returns_none_when_all_models_fail():
    reg = PeerRegistry()
    reg.register("big", connection=_FakeConn(_err), capabilities=_caps("qwen-30b"))
    reg.register("small", connection=_FakeConn(_err), capabilities=_caps("qwen-1b"))

    node = _node(reg)
    result = await MycellmNode.route_inference(node, "auto", [{"role": "user", "content": "hi"}])
    assert result is None


@pytest.mark.asyncio
async def test_route_inference_skips_dead_peer_and_uses_live_model():
    """A higher-tier model whose seeder has a dead session is never offered;
    routing uses the healthy lower-tier model directly."""
    reg = PeerRegistry()
    dead = _FakeConn(_ok("should never be called"))
    dead.protocol._is_closed = True  # zombie session
    reg.register("big", connection=dead, capabilities=_caps("qwen-30b"))
    reg.register("small", connection=_FakeConn(_ok("hi from small")),
                 capabilities=_caps("qwen-1b"))

    node = _node(reg)
    result = await MycellmNode.route_inference(node, "auto", [{"role": "user", "content": "hi"}])
    assert result is not None
    assert result["text"] == "hi from small"
    # Dead peer must not have been penalized — it was never tried.
    assert reg.get("big").failure_count == 0


async def _stream_err(msg):
    raise RuntimeError("seeder down")
    yield  # pragma: no cover  (make this an async generator)


def _stream_ok(chunks):
    async def _gen(msg):
        for c in chunks:
            yield MessageEnvelope(
                type=MessageType.INFERENCE_STREAM,
                payload={"text": c, "finish_reason": None},
                from_peer="x",
            )
    return _gen


@pytest.mark.asyncio
async def test_route_inference_stream_fails_over_to_next_model():
    """Streaming 'auto' degrades to the next model when the best one errors
    before producing any chunk."""
    reg = PeerRegistry()
    reg.register("big", connection=_FakeConn(_ok("x"), stream_responder=_stream_err),
                 capabilities=_caps("qwen-30b"))
    reg.register("small", connection=_FakeConn(_ok("x"), stream_responder=_stream_ok(["he", "llo"])),
                 capabilities=_caps("qwen-1b"))

    node = _node(reg)
    chunks = []
    async for piece in MycellmNode.route_inference_stream(node, "auto", [{"role": "user", "content": "hi"}]):
        chunks.append(piece["text"])

    assert "".join(chunks) == "hello"
    assert reg.get("big").failure_count >= 1
