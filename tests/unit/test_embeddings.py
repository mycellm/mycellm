"""Tests for embeddings types."""

import pytest
from mycellm.inference.base import EmbeddingRequest, EmbeddingResult, InferenceBackend


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


def test_base_backend_embed_not_implemented():
    """Base backend raises NotImplementedError for embed."""
    import asyncio

    class DummyBackend(InferenceBackend):
        async def load_model(self, *a, **kw): pass
        async def unload_model(self, *a, **kw): pass
        async def generate(self, *a, **kw): pass
        async def generate_stream(self, *a, **kw): yield
        def get_loaded_models(self): return []
        def get_capabilities(self): return []

    backend = DummyBackend()
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(
            backend.embed(EmbeddingRequest(input="test"))
        )
