"""Tests for the OpenAI-compatible inference backend."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycellm.inference.openai_compat import OpenAICompatibleBackend, _build_headers
from mycellm.inference.base import InferenceRequest, InferenceResult
from mycellm.inference.manager import InferenceManager


# ── Helpers ──


def _mock_chat_response(content: str = "Hello!") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# ── Unit tests ──


class TestBuildHeaders:
    def test_with_api_key(self):
        headers = _build_headers("sk-test-123")
        assert headers["Authorization"] == "Bearer sk-test-123"
        assert headers["Content-Type"] == "application/json"

    def test_without_api_key(self):
        headers = _build_headers("")
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"


class TestOpenAICompatibleBackend:
    @pytest.fixture
    def backend(self):
        return OpenAICompatibleBackend()

    @pytest.mark.asyncio
    async def test_load_model_requires_api_base(self, backend):
        with pytest.raises(ValueError, match="api_base is required"):
            await backend.load_model("", name="test", api_base="")

    @pytest.mark.asyncio
    async def test_load_model_bad_url(self, backend):
        with pytest.raises(ValueError, match="Cannot reach"):
            await backend.load_model(
                "", name="test",
                api_base="http://127.0.0.1:1",
                api_key="test",
            )

    @pytest.mark.asyncio
    async def test_generate_not_loaded(self, backend):
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}], model="nope")
        with pytest.raises(RuntimeError, match="not configured"):
            await backend.generate(req)

    @pytest.mark.asyncio
    async def test_generate_stream_not_loaded(self, backend):
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}], model="nope")
        with pytest.raises(RuntimeError, match="not configured"):
            async for _ in backend.generate_stream(req):
                pass

    def test_get_loaded_models_empty(self, backend):
        assert backend.get_loaded_models() == []

    def test_get_capabilities_empty(self, backend):
        caps = backend.get_capabilities()
        assert caps["backend"] == "openai-compatible"
        assert caps["models"] == {}

    @pytest.mark.asyncio
    async def test_load_generate_unload(self, backend):
        """Full lifecycle with mocked httpx client."""
        mock_resp_models = MagicMock()
        mock_resp_models.status_code = 200

        mock_resp_chat = MagicMock()
        mock_resp_chat.status_code = 200
        mock_resp_chat.raise_for_status = MagicMock()
        mock_resp_chat.json.return_value = _mock_chat_response("Test response")

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp_models)
            mock_client.post = AsyncMock(return_value=mock_resp_chat)
            mock_client.aclose = AsyncMock()
            MockClient.return_value = mock_client

            await backend.load_model(
                "", name="test-model",
                api_base="https://api.example.com/v1",
                api_key="sk-test",
                api_model="gpt-4o-mini",
            )

            assert "test-model" in backend.get_loaded_models()
            caps = backend.get_capabilities()
            assert caps["models"]["test-model"]["api_model"] == "gpt-4o-mini"
            assert caps["models"]["test-model"]["api_base"] == "https://api.example.com/v1"

            req = InferenceRequest(
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )
            result = await backend.generate(req)
            assert isinstance(result, InferenceResult)
            assert result.text == "Test response"
            assert result.prompt_tokens == 10
            assert result.completion_tokens == 5

            # Verify the upstream model ID was used, not the local name
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[1]["json"]["model"] == "gpt-4o-mini"

            await backend.unload_model("test-model")
            assert backend.get_loaded_models() == []
            mock_client.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_load_401_rejected(self, backend):
        """API key rejection should raise ValueError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.aclose = AsyncMock()
            MockClient.return_value = mock_client

            with pytest.raises(ValueError, match="API key rejected"):
                await backend.load_model(
                    "", name="test",
                    api_base="https://api.example.com/v1",
                    api_key="bad-key",
                )


class TestManagerBackendRouting:
    def test_create_openai_backend(self):
        manager = InferenceManager()
        backend = manager._create_backend("openai")
        assert isinstance(backend, OpenAICompatibleBackend)

    def test_create_openai_compatible_backend(self):
        manager = InferenceManager()
        backend = manager._create_backend("openai-compatible")
        assert isinstance(backend, OpenAICompatibleBackend)

    def test_unknown_backend_raises(self):
        manager = InferenceManager()
        with pytest.raises(ValueError, match="Unknown backend"):
            manager._create_backend("some-unknown-thing")

    @pytest.mark.asyncio
    async def test_manager_load_remote_model(self):
        """Manager correctly loads an openai backend model."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            # save_model_configs reads client.headers.get synchronously; AsyncMock would
            # make .get an async mock and leave an un-awaited coroutine warning.
            mock_client.headers = MagicMock()
            mock_client.headers.get = MagicMock(return_value="Bearer sk-or-test")
            MockClient.return_value = mock_client

            manager = InferenceManager()
            name = await manager.load_model(
                "",
                name="claude-sonnet",
                backend_type="openai",
                api_base="https://openrouter.ai/api/v1",
                api_key="sk-or-test",
                api_model="anthropic/claude-sonnet-4",
                ctx_len=200000,
            )

            assert name == "claude-sonnet"
            assert manager.resolve_model_name("claude-sonnet") == "claude-sonnet"
            models = manager.loaded_models
            assert len(models) == 1
            assert models[0].name == "claude-sonnet"
            assert models[0].backend == "openai"
            assert models[0].ctx_len == 200000
