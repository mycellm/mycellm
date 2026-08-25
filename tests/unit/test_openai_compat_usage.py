"""Token counts on relayed (OpenAI-compatible) models.

Every model reached through a relay — Ollama, LM Studio, vLLM, llama.cpp
server, a hosted provider — reported **0 prompt / 0 completion tokens**, and
the dashboard rendered that as a confident "0+0 tokens" pill. Two causes,
stacked:

1. The request never set `stream_options.include_usage`, and the OpenAI
   streaming spec omits `usage` unless it is asked for. So nothing arrived.
2. `generate()` hardcoded zeros, so even a provider that volunteered counts
   had them thrown away on the non-streaming path.

The rule these tests hold: **report real counts or none**. Agents size their
context windows from these numbers, so a fabricated value is worse than an
absent one.
"""

import json

import httpx

from mycellm.inference.base import InferenceRequest
from mycellm.inference.openai_compat import OpenAICompatibleBackend, _RemoteModel


def sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def content_chunk(text: str, finish=None, **extra) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish}], **extra}


def usage_chunk(prompt: int, completion: int) -> dict:
    """OpenAI's dedicated final frame: usage, and an EMPTY choices list."""
    return {"choices": [],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion}}


def backend_serving(payload: bytes, capture: list | None = None):
    """A backend wired to a stubbed upstream.

    Registered directly rather than through `load_model`, which performs a real
    connectivity GET — the point here is the streaming parse, not registration,
    and going through the network would make these tests both slow and flaky.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(json.loads(request.content))
        return httpx.Response(200, content=payload,
                              headers={"content-type": "text/event-stream"})

    backend = OpenAICompatibleBackend()
    backend._models["relayed-9b"] = _RemoteModel(
        name="relayed-9b",
        api_model="upstream-9b",
        api_base="http://stub.local/v1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="http://stub.local/v1"),
    )
    return backend


def request() -> InferenceRequest:
    return InferenceRequest(messages=[{"role": "user", "content": "hi"}],
                            model="relayed-9b")


# ── the request ─────────────────────────────────────────────────────────

async def test_usage_is_requested():
    """The root cause. Without this flag upstream sends no counts at all, and
    every downstream fix is moot."""
    sent: list = []
    backend = backend_serving(sse(content_chunk("hi")), capture=sent)
    async for _ in backend.generate_stream(request()):
        pass
    assert sent[0]["stream_options"] == {"include_usage": True}


# ── streaming ───────────────────────────────────────────────────────────

async def test_counts_arrive_on_a_trailing_chunk():
    backend = backend_serving(sse(
        content_chunk("Hello"), content_chunk(" world", finish="stop"),
        usage_chunk(12, 34),
    ))
    chunks = [c async for c in backend.generate_stream(request())]
    final = chunks[-1]
    assert final.text == "", "counts ride a zero-text chunk, like the local backends"
    assert final.prompt_tokens == 12
    assert final.completion_tokens == 34


async def test_usage_chunk_does_not_emit_phantom_text():
    """The usage frame has an empty `choices` list — indexing [0] would raise,
    and treating it as content would append an empty delta to the answer."""
    backend = backend_serving(sse(content_chunk("Hello"), usage_chunk(1, 2)))
    chunks = [c async for c in backend.generate_stream(request())]
    assert "".join(c.text for c in chunks) == "Hello"


async def test_usage_attached_to_a_content_chunk_is_still_read():
    """Several compatible providers attach usage to the LAST content chunk
    rather than sending a dedicated frame. Reading it only from the
    empty-choices frame would keep reporting zeros for those."""
    backend = backend_serving(sse(
        content_chunk("Hello"),
        content_chunk("", finish="stop",
                      usage={"prompt_tokens": 7, "completion_tokens": 8}),
    ))
    chunks = [c async for c in backend.generate_stream(request())]
    assert chunks[-1].prompt_tokens == 7
    assert chunks[-1].completion_tokens == 8


async def test_no_usage_chunk_when_upstream_reports_nothing():
    """⚠️ ABSENT COUNTS AND A REAL ZERO ARE DIFFERENT FACTS.

    A provider that ignores `stream_options` must produce no usage chunk at
    all, rather than a fabricated 0+0 the UI would render as a measurement.
    """
    backend = backend_serving(sse(content_chunk("Hello", finish="stop")))
    chunks = [c async for c in backend.generate_stream(request())]
    assert all(c.prompt_tokens is None for c in chunks)
    assert all(c.completion_tokens is None for c in chunks)


async def test_streaming_still_yields_text_in_order():
    backend = backend_serving(sse(
        content_chunk("a"), content_chunk("b"), content_chunk("c", finish="stop"),
        usage_chunk(1, 3),
    ))
    chunks = [c async for c in backend.generate_stream(request())]
    assert "".join(c.text for c in chunks) == "abc"


# ── non-streaming ───────────────────────────────────────────────────────

async def test_generate_reports_real_counts():
    """`generate()` used to hardcode zeros, so even a provider that counted
    perfectly well had its numbers discarded."""
    backend = backend_serving(sse(
        content_chunk("Hello"), content_chunk(" world", finish="stop"),
        usage_chunk(11, 22),
    ))
    result = await backend.generate(request())
    assert result.text == "Hello world"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 22


async def test_generate_reports_zero_when_upstream_is_silent():
    """InferenceResult has no "unknown" — 0 is the only representable answer.
    That is acceptable here precisely because the STREAM keeps them None, so
    the API layer can still tell the difference and omit the usage block."""
    backend = backend_serving(sse(content_chunk("Hello", finish="stop")))
    result = await backend.generate(request())
    assert result.text == "Hello"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


async def test_tool_calls_survive_the_usage_frame():
    """Tool-call accumulation and usage parsing both hook the same loop; a
    usage frame must not truncate the tool call."""
    backend = backend_serving(sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city":'}}]},
            "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"Paris"}'}}]},
            "finish_reason": "tool_calls"}]},
        usage_chunk(5, 6),
    ))
    chunks = [c async for c in backend.generate_stream(request())]
    calls = [c for c in chunks if c.tool_calls]
    assert calls, "the tool call must still be emitted"
    assert calls[0].tool_calls[0]["function"]["name"] == "get_weather"
    assert calls[0].tool_calls[0]["function"]["arguments"] == '{"city":"Paris"}'
    assert chunks[-1].prompt_tokens == 5
