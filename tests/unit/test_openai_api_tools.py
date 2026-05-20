"""Tests for OpenAI tools/function_calling support in the chat completions API."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycellm.api.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatMessage,
)
from mycellm.inference.base import InferenceRequest, InferenceResult, InferenceChunk


_REPLY_TOOL = {
    "type": "function",
    "function": {
        "name": "reply",
        "description": "Send a reply to the user",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
}

_TOOL_CALLS = [
    {
        "id": "call_abc123",
        "type": "function",
        "function": {"name": "reply", "arguments": '{"message": "Hello!"}'},
    }
]


# ── Model field tests ──


class TestChatMessage:
    def test_plain_content(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.content == "hello"
        assert msg.tool_calls is None
        assert msg.tool_call_id is None

    def test_assistant_with_tool_calls(self):
        msg = ChatMessage(role="assistant", content=None, tool_calls=_TOOL_CALLS)
        d = msg.model_dump(exclude_none=True)
        assert "content" not in d
        assert d["tool_calls"] == _TOOL_CALLS

    def test_tool_role_message(self):
        msg = ChatMessage(
            role="tool",
            content='{"result": "ok"}',
            tool_call_id="call_abc123",
            name="reply",
        )
        d = msg.model_dump(exclude_none=True)
        assert d["tool_call_id"] == "call_abc123"
        assert d["name"] == "reply"
        assert d["content"] == '{"result": "ok"}'


class TestChatCompletionRequest:
    def test_no_tools(self):
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "hi"}],
        )
        assert req.tools is None
        assert req.tool_choice is None

    def test_with_tools(self):
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_REPLY_TOOL],
            tool_choice="auto",
        )
        assert len(req.tools) == 1
        assert req.tool_choice == "auto"

    def test_tool_choice_specific(self):
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_REPLY_TOOL],
            tool_choice={"type": "function", "function": {"name": "reply"}},
        )
        assert req.tool_choice["function"]["name"] == "reply"


class TestInferenceRequest:
    def test_tools_fields(self):
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_REPLY_TOOL],
            tool_choice="auto",
        )
        assert req.tools == [_REPLY_TOOL]
        assert req.tool_choice == "auto"

    def test_defaults_none(self):
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.tools is None
        assert req.tool_choice is None


class TestInferenceResult:
    def test_tool_calls_field(self):
        result = InferenceResult(
            text="",
            finish_reason="tool_calls",
            tool_calls=_TOOL_CALLS,
        )
        assert result.tool_calls == _TOOL_CALLS
        assert result.finish_reason == "tool_calls"


# ── Message-building preserves tool fields ──


class TestMessageBuilding:
    """Ensure ChatMessage fields are preserved when building the messages list."""

    def test_content_only(self):
        req = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="hi")],
        )
        msg = req.messages[0]
        assert msg.content == "hi"
        assert msg.tool_calls is None

    def test_tool_calls_preserved(self):
        req = ChatCompletionRequest(
            messages=[
                ChatMessage(role="assistant", content=None, tool_calls=_TOOL_CALLS),
            ],
        )
        msg = req.messages[0]
        assert msg.tool_calls == _TOOL_CALLS
        assert msg.content is None

    def test_tool_result_message(self):
        req = ChatCompletionRequest(
            messages=[
                ChatMessage(
                    role="tool",
                    content='{"result": "ok"}',
                    tool_call_id="call_abc123",
                    name="reply",
                ),
            ],
        )
        msg = req.messages[0]
        assert msg.tool_call_id == "call_abc123"
        assert msg.name == "reply"


# ── openai_compat backend: tools forwarded in HTTP payload ──


class TestOpenAICompatToolsForwarding:
    @pytest.mark.asyncio
    async def test_generate_forwards_tools(self):
        """tools and tool_choice appear in the HTTP payload to the remote."""
        from mycellm.inference.openai_compat import OpenAICompatibleBackend

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": _TOOL_CALLS,
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
        }

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.aclose = AsyncMock()
            MockClient.return_value = mock_client

            backend = OpenAICompatibleBackend()
            await backend.load_model(
                "",
                name="test-model",
                api_base="https://api.example.com/v1",
                api_model="llama-3.1-8b",
            )

            req = InferenceRequest(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
                tools=[_REPLY_TOOL],
                tool_choice={"type": "function", "function": {"name": "reply"}},
            )
            result = await backend.generate(req)

            # Payload sent to remote must include tools
            call_kwargs = mock_client.post.call_args[1]["json"]
            assert "tools" in call_kwargs
            assert call_kwargs["tools"] == [_REPLY_TOOL]
            assert call_kwargs["tool_choice"] == {"type": "function", "function": {"name": "reply"}}

            # Result must carry tool_calls back
            assert result.tool_calls == _TOOL_CALLS
            assert result.finish_reason == "tool_calls"
            assert result.text == ""

    @pytest.mark.asyncio
    async def test_generate_no_tools_not_forwarded(self):
        """Without tools, no tools/tool_choice key in the payload."""
        from mycellm.inference.openai_compat import OpenAICompatibleBackend

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.aclose = AsyncMock()
            MockClient.return_value = mock_client

            backend = OpenAICompatibleBackend()
            await backend.load_model(
                "", name="test-model",
                api_base="https://api.example.com/v1", api_model="llama",
            )

            req = InferenceRequest(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
            )
            result = await backend.generate(req)
            call_kwargs = mock_client.post.call_args[1]["json"]
            assert "tools" not in call_kwargs
            assert "tool_choice" not in call_kwargs
            assert result.tool_calls is None
            assert result.text == "hi"
