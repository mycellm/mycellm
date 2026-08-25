"""Token counts from the MLX vision-language backend's streaming path.

Found live, and the diagnosis is worth keeping because the bug hid behind two
things that both looked fine:

1. **The model did not look like a VLM.** `Qwen3.5-9B` is a text-shaped name,
   but Qwen3.5 is genuinely multimodal — its `config.json` carries
   `vision_config` and `image_token_id` — so `is_mlx_vlm_model_path` correctly
   routed it here rather than to `mlx.py`/`mlx_batched.py`. Every instrument
   pointed at the text backends recorded nothing at all, because the text
   backends were never running.
2. **Non-streaming worked.** `generate()` re-encodes the output when the
   terminal chunk carries no counts, so `/v1/chat/completions` reported a
   confident 13+9 while its streaming twin reported nothing. The fallback
   masked the missing counts on the one path that had them.

The streaming path simply never set `prompt_tokens`/`completion_tokens`, so
`stream_options.include_usage` produced no usage block — and any node relaying
to a VLM-backed model inherited the zeros.

`mlx_vlm` itself is never imported here: the streaming loop's contract is what
is under test, and it is driven with a fake generator, so these run on any
architecture.
"""

import sys
import types

import pytest

from mycellm.inference.base import InferenceRequest
from mycellm.inference.mlx_vlm import MLXVLMBackend


class FakeTokenizer:
    """One token per whitespace-separated word — enough to prove counts are
    real numbers derived from real text, without pulling in a tokenizer."""

    def encode(self, text):
        return text.split()


class FakeProcessor:
    tokenizer = FakeTokenizer()


class Resp:
    def __init__(self, text, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason


def backend_yielding(responses, stop_strings=()):
    """A backend whose mlx-vlm stream yields `responses`."""
    fake = types.ModuleType("mlx_vlm")
    fake.stream_generate = lambda *a, **kw: iter(responses)
    sys.modules["mlx_vlm"] = fake

    b = MLXVLMBackend.__new__(MLXVLMBackend)
    from concurrent.futures import ThreadPoolExecutor

    b._pool = ThreadPoolExecutor(max_workers=1)
    b._resolve_model = lambda name: (None, FakeProcessor(), {})
    b._build = lambda processor, config, request: ("a b c", [])
    b._gen_kwargs = lambda request: {}

    import mycellm.inference.mlx_vlm as mod
    mod.chat_stop_strings = lambda tok, stop: list(stop_strings)
    return b


def req():
    return InferenceRequest(messages=[{"role": "user", "content": "hi"}],
                            model="vlm", max_tokens=50)


async def collect(backend):
    return [c async for c in backend.generate_stream(req())]


# ── the fix ─────────────────────────────────────────────────────────────

async def test_terminal_chunk_carries_counts():
    """The whole bug: this chunk used to have neither field."""
    chunks = await collect(backend_yielding([
        Resp("Hello "), Resp("world"), Resp("", finish_reason="stop"),
    ]))
    final = chunks[-1]
    assert final.finish_reason == "stop"
    assert final.prompt_tokens == 3        # "a b c"
    assert final.completion_tokens == 2    # "Hello world"


async def test_intermediate_chunks_carry_no_counts():
    """Counts belong on the terminal chunk only — the API layer takes the last
    value it sees, and a running total on every chunk would be a different
    contract from every other backend."""
    chunks = await collect(backend_yielding([
        Resp("Hello "), Resp("world"), Resp("", finish_reason="stop"),
    ]))
    for c in chunks[:-1]:
        assert c.prompt_tokens is None
        assert c.completion_tokens is None


async def test_text_is_unchanged_by_the_fix():
    chunks = await collect(backend_yielding([
        Resp("Hello "), Resp("world"), Resp("", finish_reason="stop"),
    ]))
    assert "".join(c.text for c in chunks) == "Hello world"


async def test_counts_appear_even_without_a_finish_reason():
    """⚠️ mlx-vlm DOES NOT GUARANTEE A FINAL CHUNK WITH `finish_reason`.

    When none arrives the counts would be lost entirely, so a trailing
    zero-text chunk carries them — the same shape the other local backends
    use, and one the API layer already handles without emitting an extra SSE
    envelope.
    """
    chunks = await collect(backend_yielding([Resp("Hello "), Resp("world")]))
    assert "".join(c.text for c in chunks) == "Hello world"
    assert chunks[-1].text == ""
    assert chunks[-1].completion_tokens == 2


async def test_no_trailing_chunk_when_finish_already_carried_counts():
    """Do not emit two terminal chunks — the second would double-report."""
    chunks = await collect(backend_yielding([
        Resp("Hello"), Resp("", finish_reason="stop"),
    ]))
    with_counts = [c for c in chunks if c.completion_tokens is not None]
    assert len(with_counts) == 1


async def test_empty_generation_emits_no_phantom_counts():
    """A model that produced nothing must not report a token count for it."""
    chunks = await collect(backend_yielding([]))
    assert chunks == []


# ── stop strings ────────────────────────────────────────────────────────

async def test_stop_string_hit_also_carries_counts():
    """The early-exit path is a separate `yield` and had the same omission."""
    chunks = await collect(backend_yielding(
        [Resp("Hello "), Resp("world <|im_end|>")],
        stop_strings=["<|im_end|>"],
    ))
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].completion_tokens is not None
    assert "<|im_end|>" not in "".join(c.text for c in chunks)


async def test_counts_reflect_truncated_text_not_raw():
    """Counting the raw stream would bill the caller for the stop marker the
    truncation just removed."""
    chunks = await collect(backend_yielding(
        [Resp("one two <|im_end|>")], stop_strings=["<|im_end|>"],
    ))
    emitted = "".join(c.text for c in chunks)
    assert chunks[-1].completion_tokens == len(emitted.split())


# ── the contract the API layer relies on ────────────────────────────────

async def test_counts_are_ints_not_none_on_the_last_chunk():
    """`_emit_chunk` records only non-None values; None here is exactly how the
    usage block went missing in production."""
    chunks = await collect(backend_yielding([
        Resp("a"), Resp("", finish_reason="stop")]))
    assert isinstance(chunks[-1].prompt_tokens, int)
    assert isinstance(chunks[-1].completion_tokens, int)


@pytest.mark.parametrize("responses", [
    [Resp("x", finish_reason="stop")],                       # single chunk
    [Resp("x"), Resp("y", finish_reason="length")],          # length limit
    [Resp("x"), Resp("y")],                                  # no finish at all
])
async def test_every_termination_path_reports_usage(responses):
    """Whatever ends the stream, a caller that asked for usage gets some."""
    chunks = await collect(backend_yielding(responses))
    assert any(c.completion_tokens is not None for c in chunks)
