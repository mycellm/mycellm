"""Streaming usage accuracy (stream_options.include_usage) and context_length
exposure on /v1/models.

Backends report exact token counts on the terminal InferenceChunk; the API
forwards them in a trailing empty-choices usage chunk per the OpenAI spec —
and never fabricates a usage block when no real counts arrived. /v1/models
exposes each local model's effective (post-preflight-clamp) context window so
agents can size auto-compaction deterministically.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.testclient import TestClient

from mycellm.api.openai import router
from mycellm.inference.base import InferenceChunk
from mycellm.router.model_resolver import ResolvedModel


def _make_app():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    node = MagicMock()
    node.inference.loaded_models = []
    node.registry.connected_peers.return_value = []
    node.node_registry = {}
    app.state.node = node
    return app, node


def _wire_local_stream(node, model: str, chunks: list):
    node.model_resolver.resolve.return_value = [
        ResolvedModel(model_name=model, peer_id="", source="local")
    ]
    node.inference.resolve_model_name = lambda requested: model

    async def _agen():
        for c in chunks:
            yield c

    node.inference.generate_stream = lambda req: _agen()


def _post_stream(app, **overrides) -> list[dict]:
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    body.update(overrides)
    resp = TestClient(app).post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line[6:] != "[DONE]":
            events.append(json.loads(line[6:]))
    return events


TERMINAL = InferenceChunk(text="", finish_reason="stop", prompt_tokens=12, completion_tokens=5)


class TestStreamUsage:
    def test_include_usage_emits_exact_counts(self):
        app, node = _make_app()
        _wire_local_stream(node, "m1", [InferenceChunk(text="Hello"), TERMINAL])
        events = _post_stream(app, stream_options={"include_usage": True})
        usage_events = [e for e in events if e.get("usage")]
        assert len(usage_events) == 1
        assert usage_events[-1]["choices"] == []
        assert usage_events[-1]["usage"] == {
            "prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17,
        }

    def test_no_stream_options_no_usage_chunk(self):
        app, node = _make_app()
        _wire_local_stream(node, "m1", [InferenceChunk(text="Hello"), TERMINAL])
        events = _post_stream(app)
        assert not [e for e in events if e.get("usage")]

    def test_no_backend_counts_means_no_usage_block(self):
        # Never fabricate: a backend that reported nothing yields no usage chunk.
        app, node = _make_app()
        _wire_local_stream(
            node, "m1",
            [InferenceChunk(text="Hello"), InferenceChunk(text="", finish_reason="stop")],
        )
        events = _post_stream(app, stream_options={"include_usage": True})
        assert not [e for e in events if e.get("usage")]


class TestModelsContextLength:
    def test_local_models_expose_context_length(self):
        app, node = _make_app()
        node.inference.loaded_models = [SimpleNamespace(name="qwen-test", ctx_len=8192)]
        resp = TestClient(app).get("/v1/models")
        assert resp.status_code == 200
        entries = {m["id"]: m for m in resp.json()["data"]}
        assert entries["qwen-test"]["context_length"] == 8192
        assert "context_length" not in entries["auto"]
