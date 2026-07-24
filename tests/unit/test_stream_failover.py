"""Cross-model / dead-peer failover on the streaming chat path.

A streaming request for "auto" resolves to a ranked candidate list. When the
top candidate can't produce a stream — dead QUIC peer, backend error, or a
stream that yields nothing — the SSE response must fall through to the
next-ranked candidate instead of erroring or handing the client an empty
stream. These tests drive the API through TestClient with a mock node, the
same way tests/unit/test_auto_model.py does.
"""

import json

from unittest.mock import MagicMock
from starlette.testclient import TestClient
from fastapi import FastAPI

from mycellm.api.openai import router
from mycellm.inference.base import InferenceChunk
from mycellm.router.model_resolver import ResolvedModel


def _make_app():
    """Create a minimal FastAPI app with the openai router and mock node."""
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    node = MagicMock()
    node.inference.loaded_models = []
    node.registry.connected_peers.return_value = []
    node.node_registry = {}

    app.state.node = node
    return app, node


def _resolver(node, *resolved: ResolvedModel):
    """Make the node's ModelResolver return this ranked candidate list."""
    node.model_resolver.resolve.return_value = list(resolved)


def _local(node, streams: dict[str, list]):
    """Wire up local inference: `streams` maps model name -> chunk list, or to
    an exception instance the stream raises instead of producing output."""

    def _resolve_model_name(requested: str) -> str:
        if requested in streams:
            return requested
        return "" if requested else next(iter(streams), "")

    def _generate_stream(req):
        return _agen(streams[req.model])

    node.inference.resolve_model_name = _resolve_model_name
    node.inference.generate_stream = _generate_stream


async def _agen(chunks):
    """Async generator over `chunks`; an exception instance raises instead."""
    if isinstance(chunks, BaseException):
        raise chunks
    for c in chunks:
        yield c


def _chunks(*texts) -> list[InferenceChunk]:
    return [InferenceChunk(text=t) for t in texts]


def _sse(resp) -> list[dict]:
    """Parse an SSE response body into its chat.completion.chunk payloads."""
    events = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        events.append(json.loads(data))
    return events


def _content(events: list[dict]) -> str:
    return "".join(e["choices"][0]["delta"].get("content", "") for e in events)


def _post(app, **overrides) -> list[dict]:
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    body.update(overrides)
    resp = TestClient(app).post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    return _sse(resp)


def test_stream_auto_falls_over_when_top_peer_is_dead():
    """Top candidate is a QUIC peer that streams nothing → next-best model."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="big-70b", peer_id="deadpeer", source="quic"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {"small-7b": _chunks("hello ", "world")})
    # Dead peer: the QUIC relay opens but never yields a chunk.
    node.route_inference_stream = lambda *a, **kw: _agen([])

    events = _post(app)

    assert _content(events) == "hello world"
    assert all(e["model"] == "small-7b" for e in events)
    assert events[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_auto_falls_over_when_top_peer_errors():
    """A QUIC peer that raises mid-connect is skipped, not surfaced."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="big-70b", peer_id="deadpeer", source="quic"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {"small-7b": _chunks("fallback")})
    node.route_inference_stream = lambda *a, **kw: _agen(ConnectionError("peer gone"))

    events = _post(app)

    assert _content(events) == "fallback"
    assert all(e["model"] == "small-7b" for e in events)


def test_stream_auto_falls_over_from_failing_local_model():
    """A local candidate whose backend blows up degrades to the next model."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="broken-13b", peer_id="", source="local"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {
        "broken-13b": RuntimeError("backend crashed"),
        "small-7b": _chunks("still here"),
    })

    events = _post(app)

    assert _content(events) == "still here"
    assert all(e["model"] == "small-7b" for e in events)


def test_stream_auto_falls_over_from_empty_local_stream():
    """A local candidate that yields no tokens is a dead candidate."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="silent-13b", peer_id="", source="local"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {"silent-13b": [], "small-7b": _chunks("recovered")})

    events = _post(app)

    assert _content(events) == "recovered"
    # The dead candidate must not have leaked a role delta of its own.
    assert [e["model"] for e in events].count("silent-13b") == 0


def test_stream_auto_uses_peer_when_it_answers():
    """Ranking still wins: a healthy top-ranked peer serves the stream."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="big-70b", peer_id="livepeer", source="quic"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {"small-7b": _chunks("local answer")})
    node.route_inference_stream = lambda *a, **kw: _agen([
        {"text": "peer ", "finish_reason": None, "tool_calls": None},
        {"text": "answer", "finish_reason": None, "tool_calls": None},
    ])

    events = _post(app)

    assert _content(events) == "peer answer"
    assert all(e["model"] == "big-70b" for e in events)
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_auto_reports_when_every_candidate_fails():
    """No candidate produced output → one descriptive chunk, not an empty stream."""
    app, node = _make_app()
    _resolver(
        node,
        ResolvedModel(model_name="big-70b", peer_id="deadpeer", source="quic"),
        ResolvedModel(model_name="small-7b", peer_id="", source="local"),
    )
    _local(node, {"small-7b": []})
    node.route_inference_stream = lambda *a, **kw: _agen([])

    events = _post(app)

    assert len(events) == 1
    assert "No model available" in events[0]["choices"][0]["delta"]["content"]
    assert events[0]["choices"][0]["finish_reason"] == "stop"


def test_stream_concrete_model_does_not_fail_over():
    """A named model is served as-is — no resolver, no cross-model failover."""
    app, node = _make_app()
    _resolver(node, ResolvedModel(model_name="other-7b", peer_id="", source="local"))
    _local(node, {"small-7b": _chunks("named")})

    events = _post(app, model="small-7b")

    assert _content(events) == "named"
    assert all(e["model"] == "small-7b" for e in events)
    node.model_resolver.resolve.assert_not_called()


def test_stream_splits_thinking_into_reasoning_content():
    """Per-stream splitter still routes <think> blocks to reasoning_content."""
    app, node = _make_app()
    _resolver(node, ResolvedModel(model_name="qwen3-32b", peer_id="", source="local"))
    _local(node, {"qwen3-32b": _chunks("<think>", "pondering", "</think>", "answer")})

    events = _post(app, reasoning={"exclude": False})

    assert _content(events) == "answer"
    reasoning = "".join(
        e["choices"][0]["delta"].get("reasoning_content", "") for e in events
    )
    assert reasoning == "pondering"


def test_non_streaming_local_is_unchanged():
    """The non-streaming path keeps its JSON shape and routed-to header."""
    from mycellm.inference.base import InferenceResult

    app, node = _make_app()
    _local(node, {"small-7b": _chunks("unused")})
    node.model_resolver = None

    async def _generate(req):
        return InferenceResult(
            text="non-streamed", finish_reason="stop",
            prompt_tokens=3, completion_tokens=2,
        )

    node.inference.generate = _generate

    resp = TestClient(app).post("/v1/chat/completions", json={
        "model": "small-7b", "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    assert resp.headers["X-Mycellm-Routed-To"] == "local"
    data = resp.json()
    assert data["model"] == "small-7b"
    assert data["choices"][0]["message"]["content"] == "non-streamed"
    assert data["usage"]["total_tokens"] == 5
