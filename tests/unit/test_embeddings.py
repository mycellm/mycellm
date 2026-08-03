"""Tests for embeddings: types, backends, manager entry point, and API endpoint."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mycellm.api.openai import router
from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingsNotSupportedError,
    InferenceBackend,
)
from mycellm.inference.llamacpp import LlamaCppBackend
from mycellm.inference.manager import InferenceManager
from mycellm.inference.openai_compat import OpenAICompatibleBackend, _RemoteModel
from mycellm.protocol.capabilities import ModelCapability


# ── Helpers ──


class DummyBackend(InferenceBackend):
    """Backend that doesn't override embed() — inherits the base error."""

    async def load_model(self, *a, **kw): pass
    async def unload_model(self, *a, **kw): pass
    async def generate(self, *a, **kw): pass
    async def generate_stream(self, *a, **kw): yield
    def get_loaded_models(self): return []
    def get_capabilities(self): return {}


class FakeEmbeddingBackend(DummyBackend):
    """Backend returning deterministic vectors: [input_index, len(text)]."""

    def __init__(self):
        self.requests: list[EmbeddingRequest] = []

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.requests.append(request)
        texts = request.input if isinstance(request.input, list) else [request.input]
        embeddings = [[float(i), float(len(t))] for i, t in enumerate(texts)]
        total = sum(len(t.split()) for t in texts)
        return EmbeddingResult(embeddings=embeddings, total_tokens=total)


def _manager_with(backend: InferenceBackend, name: str = "embed-model") -> InferenceManager:
    mgr = InferenceManager()
    mgr._backends[name] = backend
    mgr._model_locks[name] = asyncio.Lock()
    mgr._queue_depth[name] = 0
    return mgr


def _manager_with_multi(*specs: tuple[str, InferenceBackend, str]) -> InferenceManager:
    """specs: (name, backend_instance, backend_type) triples, in load order
    (dict insertion order). backend_type populates ModelCapability so
    capability-aware auto resolution has something to derive tags from."""
    mgr = InferenceManager()
    for name, backend, backend_type in specs:
        mgr._backends[name] = backend
        mgr._model_locks[name] = asyncio.Lock()
        mgr._queue_depth[name] = 0
        mgr._model_info[name] = ModelCapability(name=name, backend=backend_type)
    return mgr


def _make_app(manager: InferenceManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.state.node = SimpleNamespace(inference=manager)
    return app


async def _post_embeddings(manager: InferenceManager, payload: dict):
    transport = ASGITransport(app=_make_app(manager))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/v1/embeddings", json=payload)


# ── Types ──


def test_embedding_request():
    req = EmbeddingRequest(input="hello world", model="test")
    assert req.model == "test"
    assert req.input == "hello world"


def test_embedding_request_list():
    req = EmbeddingRequest(input=["hello", "world"], model="test")
    assert isinstance(req.input, list)
    assert len(req.input) == 2


def test_embedding_result():
    result = EmbeddingResult(embeddings=[[0.1, 0.2], [0.3, 0.4]], total_tokens=10)
    assert len(result.embeddings) == 2
    assert result.total_tokens == 10


def test_base_backend_embed_not_supported():
    """Base backend raises a typed, NotImplementedError-compatible error."""
    backend = DummyBackend()
    with pytest.raises(EmbeddingsNotSupportedError, match="DummyBackend"):
        asyncio.run(backend.embed(EmbeddingRequest(input="test")))
    # Still catchable as NotImplementedError for legacy callers
    with pytest.raises(NotImplementedError):
        asyncio.run(backend.embed(EmbeddingRequest(input="test")))


# ── InferenceManager.embed ──


async def test_manager_embed_resolves_default_model():
    backend = FakeEmbeddingBackend()
    mgr = _manager_with(backend)
    result = await mgr.embed(EmbeddingRequest(input="hello", model=""))
    assert len(result.embeddings) == 1
    assert backend.requests[0].model == "embed-model"


async def test_manager_embed_no_models():
    mgr = InferenceManager()
    with pytest.raises(RuntimeError, match="No models loaded"):
        await mgr.embed(EmbeddingRequest(input="hello", model=""))


async def test_manager_embed_releases_lock():
    backend = FakeEmbeddingBackend()
    mgr = _manager_with(backend)
    await mgr.embed(EmbeddingRequest(input="hello", model="embed-model"))
    lock = mgr._model_locks["embed-model"]
    assert not lock.locked()
    assert mgr.active_count == 0
    assert mgr.queue_status["embed-model"] == 0


async def test_manager_embed_releases_lock_on_error():
    mgr = _manager_with(DummyBackend(), name="text-model")
    with pytest.raises(EmbeddingsNotSupportedError):
        await mgr.embed(EmbeddingRequest(input="hello", model="text-model"))
    assert not mgr._model_locks["text-model"].locked()
    assert mgr.active_count == 0


# ── POST /v1/embeddings ──


async def test_api_embeddings_string_input():
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "embed-model", "input": "hello world"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert data["model"] == "embed-model"
    assert len(data["data"]) == 1
    assert data["data"][0]["object"] == "embedding"
    assert data["data"][0]["index"] == 0
    assert data["data"][0]["embedding"] == [0.0, 11.0]


async def test_api_embeddings_list_input_ordering():
    texts = ["a", "bbb", "cc"]
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "embed-model", "input": texts},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [d["index"] for d in data] == [0, 1, 2]
    # Each vector encodes [input_index, len(text)] — order must match inputs
    for i, text in enumerate(texts):
        assert data[i]["embedding"] == [float(i), float(len(text))]


async def test_api_embeddings_usage_counts():
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "embed-model", "input": ["one two three", "four five"]},
    )
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["prompt_tokens"] == 5
    assert usage["total_tokens"] == 5


async def test_api_embeddings_token_arrays_rejected():
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "embed-model", "input": [[1, 2, 3], [4, 5]]},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert "Token array" in err["message"]
    assert err["code"] == "invalid_input"


async def test_api_embeddings_empty_input_rejected():
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "embed-model", "input": []},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_input"


async def test_api_embeddings_unknown_model():
    resp = await _post_embeddings(
        _manager_with(FakeEmbeddingBackend()),
        {"model": "nope", "input": "hello"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "model_not_found"
    assert "nope" in err["message"]


async def test_api_embeddings_unsupported_backend_maps_to_400():
    resp = await _post_embeddings(
        _manager_with(DummyBackend(), name="chat-model"),
        {"model": "chat-model", "input": "hello"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "embeddings_not_supported"
    assert "not supported" in err["message"]


# ── Capability-aware auto resolution ──
#
# model omitted/"auto" must pick a loaded embedding-capable model even when
# it isn't first in the backend dict; when none is loaded, the response is
# unchanged (falls back to the first-loaded model, which then reports
# embeddings_not_supported/model_not_found as before). "all-MiniLM-L6-v2"
# deliberately has no "embed"/"embedding" substring, so these tests exercise
# the mlx-embeddings backend-type signal, not just the pre-existing name
# heuristic.


async def test_api_embeddings_auto_picks_embedding_capable_model_not_first():
    embed_backend = FakeEmbeddingBackend()
    manager = _manager_with_multi(
        ("qwen2.5-7b", DummyBackend(), "llama.cpp"),
        ("all-MiniLM-L6-v2", embed_backend, "mlx-embeddings"),
    )
    resp = await _post_embeddings(manager, {"input": "hello world"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "all-MiniLM-L6-v2"
    assert len(embed_backend.requests) == 1


async def test_api_embeddings_auto_keyword_picks_embedding_capable_model():
    embed_backend = FakeEmbeddingBackend()
    manager = _manager_with_multi(
        ("qwen2.5-7b", DummyBackend(), "llama.cpp"),
        ("all-MiniLM-L6-v2", embed_backend, "mlx-embeddings"),
    )
    resp = await _post_embeddings(manager, {"model": "auto", "input": "hello world"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "all-MiniLM-L6-v2"


async def test_api_embeddings_auto_unchanged_when_no_embedding_model_loaded():
    """No embedding-capable model loaded -> falls back to the first-loaded
    model (existing default), which then reports embeddings_not_supported."""
    manager = _manager_with_multi(("chat-model", DummyBackend(), "llama.cpp"))
    resp = await _post_embeddings(manager, {"input": "hello world"})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "embeddings_not_supported"
    assert "not supported" in err["message"]


# ── LlamaCppBackend.embed ──


async def test_llamacpp_embed_requires_embedding_flag():
    backend = LlamaCppBackend()
    backend._models["m"] = MagicMock()  # loaded, but without embedding=True
    with pytest.raises(EmbeddingsNotSupportedError, match='"embedding": true'):
        await backend.embed(EmbeddingRequest(input="hello", model="m"))


async def test_llamacpp_embed_model_not_loaded():
    backend = LlamaCppBackend()
    with pytest.raises(RuntimeError, match="not loaded"):
        await backend.embed(EmbeddingRequest(input="hello", model="missing"))


async def test_llamacpp_embed_returns_vectors_sorted_by_index():
    backend = LlamaCppBackend()
    llm = MagicMock()
    llm.create_embedding.return_value = {
        "object": "list",
        "data": [
            {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
        ],
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
    }
    backend._models["m"] = llm
    backend._embedding_models.add("m")

    result = await backend.embed(EmbeddingRequest(input=["a", "b"], model="m"))
    assert result.embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert result.total_tokens == 7
    llm.create_embedding.assert_called_once_with(["a", "b"])


async def test_llamacpp_embed_wraps_string_input():
    backend = LlamaCppBackend()
    llm = MagicMock()
    llm.create_embedding.return_value = {
        "data": [{"index": 0, "embedding": [0.5]}],
        "usage": {"total_tokens": 3},
    }
    backend._models["m"] = llm
    backend._embedding_models.add("m")

    result = await backend.embed(EmbeddingRequest(input="solo", model="m"))
    assert result.embeddings == [[0.5]]
    llm.create_embedding.assert_called_once_with(["solo"])


async def test_llamacpp_unload_clears_embedding_flag():
    backend = LlamaCppBackend()
    backend._models["m"] = MagicMock()
    backend._embedding_models.add("m")
    await backend.unload_model("m")
    assert "m" not in backend._embedding_models


# ── OpenAICompatibleBackend.embed (relay) ──


def _remote_backend(client: MagicMock, name: str = "emb") -> OpenAICompatibleBackend:
    backend = OpenAICompatibleBackend()
    backend._models[name] = _RemoteModel(
        name=name,
        api_model="text-embedding-3-small",
        api_base="https://api.example.com/v1",
        client=client,
    )
    return backend


def _mock_embeddings_response(status_code: int = 200, payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload or {}
    return resp


async def test_openai_compat_embed_relays_request():
    client = MagicMock()
    client.post = AsyncMock(return_value=_mock_embeddings_response(200, {
        "object": "list",
        "data": [
            {"object": "embedding", "index": 1, "embedding": [0.9, 1.0]},
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }))
    backend = _remote_backend(client)

    result = await backend.embed(EmbeddingRequest(input=["x", "y"], model="emb"))

    client.post.assert_called_once_with(
        "/embeddings",
        json={"model": "text-embedding-3-small", "input": ["x", "y"]},
    )
    # Vectors are returned in upstream index order
    assert result.embeddings == [[0.1, 0.2], [0.9, 1.0]]
    assert result.total_tokens == 4


async def test_openai_compat_embed_upstream_rejection():
    client = MagicMock()
    client.post = AsyncMock(return_value=_mock_embeddings_response(404))
    backend = _remote_backend(client)

    with pytest.raises(EmbeddingsNotSupportedError, match="HTTP 404"):
        await backend.embed(EmbeddingRequest(input="x", model="emb"))


async def test_openai_compat_embed_unknown_model():
    backend = OpenAICompatibleBackend()
    with pytest.raises(RuntimeError, match="not configured"):
        await backend.embed(EmbeddingRequest(input="x", model="missing"))
