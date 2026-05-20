"""Tests for OpenAI tools/function_calling support in the chat completions API."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mycellm.api.openai import (
    ChatCompletionRequest,
    ChatMessage,
)
from mycellm.inference.base import InferenceRequest, InferenceResult


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


# generate() in openai_compat delegates internally to generate_stream() to avoid
# request timeouts on slow remote models. Tests must mock client.stream(), not
# client.post().


class _MockSSEResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _MockSSEContext:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self) -> _MockSSEResponse:
        return _MockSSEResponse(self._lines)

    async def __aexit__(self, *args) -> None:
        return None


def _sse_text_lines(content: str, finish: str = "stop") -> list[str]:
    return [
        'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
        f'data: {{"choices":[{{"index":0,"delta":{{"content":"{content}"}},"finish_reason":null}}]}}',
        f'data: {{"choices":[{{"index":0,"delta":{{}},"finish_reason":"{finish}"}}]}}',
        'data: [DONE]',
    ]


def _sse_tool_call_lines(tool_calls: list[dict]) -> list[str]:
    lines = ['data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}']
    for i, tc in enumerate(tool_calls):
        delta = {
            "tool_calls": [{
                "index": i,
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }]
        }
        lines.append(f'data: {json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})}')
    lines.append('data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}')
    lines.append('data: [DONE]')
    return lines


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

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client.stream = MagicMock(return_value=_MockSSEContext(_sse_tool_call_lines(_TOOL_CALLS)))
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
            call_kwargs = mock_client.stream.call_args[1]["json"]
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

        with patch("mycellm.inference.openai_compat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client.stream = MagicMock(return_value=_MockSSEContext(_sse_text_lines("hi")))
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
            call_kwargs = mock_client.stream.call_args[1]["json"]
            assert "tools" not in call_kwargs
            assert "tool_choice" not in call_kwargs
            assert result.tool_calls is None
            assert result.text == "hi"
