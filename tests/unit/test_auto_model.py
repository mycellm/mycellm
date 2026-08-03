"""Tests for the virtual 'auto' model in the OpenAI-compatible API."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from starlette.testclient import TestClient
from fastapi import FastAPI

from mycellm.api.openai import router
from mycellm.inference.manager import InferenceManager
from mycellm.protocol.capabilities import ModelCapability
from mycellm.router.model_resolver import ModelResolver, derive_capability_tags
from mycellm.router.registry import PeerRegistry


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


def test_list_models_includes_auto():
    """GET /v1/models should include 'auto' as the first model."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    model_ids = [m["id"] for m in data["data"]]
    assert "auto" in model_ids
    # auto should be first
    assert model_ids[0] == "auto"
    # owned_by should be "mycellm"
    auto_model = data["data"][0]
    assert auto_model["owned_by"] == "mycellm"


def test_retrieve_model_auto():
    """GET /v1/models/auto should return the virtual auto model."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models/auto")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "auto"
    assert data["object"] == "model"
    assert data["owned_by"] == "mycellm"


def test_retrieve_model_not_found():
    """GET /v1/models/<nonexistent> should return 404."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models/nonexistent-model-xyz")
    assert resp.status_code == 404


def test_retrieve_model_local():
    """GET /v1/models/<local_model> should return the local model."""
    app, node = _make_app()
    mock_model = MagicMock()
    mock_model.name = "qwen2.5-7b"
    node.inference.loaded_models = [mock_model]

    client = TestClient(app)
    resp = client.get("/v1/models/qwen2.5-7b")
    assert resp.status_code == 200
    assert resp.json()["id"] == "qwen2.5-7b"
    assert resp.json()["owned_by"] == "local"


# ── Capability-aware auto resolution ──
#
# "auto"/empty requests must never resolve to an embedding-only model for
# chat (it can't generate — MLXEmbeddingsBackend raises), while embeddings
# and chat requests that explicitly name a model or its "embedding" tag must
# keep working exactly as before. Model names deliberately avoid the
# "embed"/"embedding" substring (e.g. a MiniLM/BERT-family name) so these
# tests actually exercise the backend-type capability signal, not just the
# pre-existing name heuristic.

_CHAT_MODEL = "qwen2.5-7b"
_EMBED_MODEL = "all-MiniLM-L6-v2"


def test_derive_capability_tags_mlx_embeddings_backend_overrides_name():
    """mlx-embeddings is a dedicated embeddings backend (MLXEmbeddingsBackend)
    — it's tagged "embedding" even when the name has no "embed" substring."""
    assert derive_capability_tags(_EMBED_MODEL, backend="mlx-embeddings") == ["embedding"]


def test_derive_capability_tags_falls_back_to_name_heuristic():
    """Without a dedicated embeddings backend, the existing name heuristic
    (derive_tags) still governs."""
    assert derive_capability_tags(_CHAT_MODEL, backend="llama.cpp") == ["chat"]
    assert derive_capability_tags("nomic-embed-text", backend="llama.cpp") == ["embedding"]


def test_resolver_auto_excludes_embedding_only_candidate():
    """An empty/"auto" request must never resolve to an embedding-only model."""
    resolver = ModelResolver(PeerRegistry())
    local_models = [ModelCapability(name=_EMBED_MODEL, backend="mlx-embeddings")]
    assert resolver.resolve("", local_models) == []


def test_resolver_auto_picks_chat_model_over_embedding_model():
    """Both loaded, embedding model listed first in local_models — auto must
    still pick the chat-capable one, not dict/list order."""
    resolver = ModelResolver(PeerRegistry())
    local_models = [
        ModelCapability(name=_EMBED_MODEL, backend="mlx-embeddings"),
        ModelCapability(name=_CHAT_MODEL, backend="llama.cpp"),
    ]
    resolved = resolver.resolve("", local_models)
    assert [c.model_name for c in resolved] == [_CHAT_MODEL]


def test_resolver_explicit_name_still_resolves_embedding_model():
    """Exact-match branch of _filter_candidates is unaffected by the auto
    exclusion — naming the embeddings model directly still works."""
    resolver = ModelResolver(PeerRegistry())
    local_models = [
        ModelCapability(name=_EMBED_MODEL, backend="mlx-embeddings"),
        ModelCapability(name=_CHAT_MODEL, backend="llama.cpp"),
    ]
    resolved = resolver.resolve(_EMBED_MODEL, local_models)
    assert [c.model_name for c in resolved] == [_EMBED_MODEL]


def test_resolver_embedding_tag_request_still_resolves():
    """Tag-match branch of _filter_candidates is unaffected by the auto
    exclusion — requesting the "embedding" tag still works."""
    resolver = ModelResolver(PeerRegistry())
    local_models = [
        ModelCapability(name=_EMBED_MODEL, backend="mlx-embeddings"),
        ModelCapability(name=_CHAT_MODEL, backend="llama.cpp"),
    ]
    resolved = resolver.resolve("embedding", local_models)
    assert [c.model_name for c in resolved] == [_EMBED_MODEL]


def _manager_with_models(*specs: tuple[str, str]) -> InferenceManager:
    """specs: (name, backend) pairs, in load order (dict insertion order)."""
    mgr = InferenceManager()
    for name, backend in specs:
        mgr._backends[name] = MagicMock()
        mgr._model_locks[name] = asyncio.Lock()
        mgr._queue_depth[name] = 0
        mgr._model_info[name] = ModelCapability(name=name, backend=backend)
    return mgr


def test_resolve_model_name_prefer_tag_finds_non_first_match():
    """prefer_tag picks the embedding-capable model even when it isn't first
    in the backend dict (dict insertion order == load order)."""
    mgr = _manager_with_models((_CHAT_MODEL, "llama.cpp"), (_EMBED_MODEL, "mlx-embeddings"))
    assert mgr.resolve_model_name("", prefer_tag="embedding") == _EMBED_MODEL


def test_resolve_model_name_prefer_tag_falls_back_when_none_match():
    """No embedding-capable model loaded -> unchanged default (first-in-dict)."""
    mgr = _manager_with_models((_CHAT_MODEL, "llama.cpp"))
    assert mgr.resolve_model_name("", prefer_tag="embedding") == _CHAT_MODEL


def test_resolve_model_name_exclude_tag_skips_embedding_model():
    mgr = _manager_with_models((_EMBED_MODEL, "mlx-embeddings"), (_CHAT_MODEL, "llama.cpp"))
    assert mgr.resolve_model_name("", exclude_tag="embedding") == _CHAT_MODEL


def test_resolve_model_name_exclude_tag_returns_empty_when_all_excluded():
    mgr = _manager_with_models((_EMBED_MODEL, "mlx-embeddings"))
    assert mgr.resolve_model_name("", exclude_tag="embedding") == ""


def test_resolve_model_name_explicit_name_unaffected_by_exclude_tag():
    mgr = _manager_with_models((_EMBED_MODEL, "mlx-embeddings"))
    assert mgr.resolve_model_name(_EMBED_MODEL, exclude_tag="embedding") == _EMBED_MODEL


class _ChatGuardBackend:
    """Stands in for MLXEmbeddingsBackend: raises like the real chat guard if
    ever asked to generate — proves a request never reached it."""

    def __init__(self):
        self.generate_calls = 0

    async def generate(self, request):
        self.generate_calls += 1
        raise RuntimeError(f"'{request.model}' is an embedding model — it cannot chat.")


def _embed_only_manager() -> tuple[InferenceManager, _ChatGuardBackend]:
    mgr = InferenceManager()
    backend = _ChatGuardBackend()
    mgr._backends[_EMBED_MODEL] = backend
    mgr._model_locks[_EMBED_MODEL] = asyncio.Lock()
    mgr._queue_depth[_EMBED_MODEL] = 0
    mgr._model_info[_EMBED_MODEL] = ModelCapability(name=_EMBED_MODEL, backend="mlx-embeddings")
    return mgr, backend


def test_chat_auto_falls_through_to_no_model_available_with_only_embedding_loaded():
    """With only an embedding model loaded, an "auto" chat request must hit
    the "no model available" error path rather than the backend's chat
    guard (which would otherwise raise mid-request)."""
    app, node = _make_app()
    node.ledger = None
    manager, backend = _embed_only_manager()
    node.inference = manager
    node.model_resolver = ModelResolver(PeerRegistry())
    node.route_inference = AsyncMock(return_value=None)

    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "hi"}],
    })

    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    assert "Load a model with POST /v1/node/models/load or connect to peers." in content
    assert backend.generate_calls == 0
