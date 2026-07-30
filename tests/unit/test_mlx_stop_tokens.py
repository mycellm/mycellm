"""Chat-terminator stop-string handling for the MLX backends.

Regression for the <|im_end|> leak: mlx-community/Qwen2.5-Coder-*-4bit ships
eos_token_id=<|endoftext|> while its chat template ends turns with <|im_end|>,
so mlx_lm never stops on it and the literal marker leaked into completions
served over the public gateway. The MLX backends now treat well-known chat
terminators as implicit stop strings.
"""

from mycellm.inference.mlx import (
    CHAT_END_MARKERS,
    chat_stop_strings,
    truncate_at_stops,
)


class FakeTokenizer:
    def __init__(self, eos_token=None):
        if eos_token is not None:
            self.eos_token = eos_token


class TestTruncateAtStops:
    def test_no_stop_returns_text_unchanged(self):
        text, hit = truncate_at_stops("def f():\n    return 1\n", ["<|im_end|>"])
        assert text == "def f():\n    return 1\n"
        assert hit is None

    def test_trailing_marker_trimmed(self):
        text, hit = truncate_at_stops("```python\ncode\n```<|im_end|>", ["<|im_end|>"])
        assert text == "```python\ncode\n```"
        assert hit == "<|im_end|>"

    def test_earliest_of_multiple_stops_wins(self):
        text, hit = truncate_at_stops(
            "abc<|endoftext|>def<|im_end|>", ["<|im_end|>", "<|endoftext|>"]
        )
        assert text == "abc"
        assert hit == "<|endoftext|>"

    def test_content_after_marker_dropped(self):
        text, hit = truncate_at_stops("answer<|im_end|>garbage tokens", ["<|im_end|>"])
        assert text == "answer"
        assert hit == "<|im_end|>"

    def test_empty_stop_list(self):
        text, hit = truncate_at_stops("anything", [])
        assert text == "anything"
        assert hit is None


class TestChatStopStrings:
    def test_includes_known_chat_terminators(self):
        stops = chat_stop_strings(FakeTokenizer())
        for marker in CHAT_END_MARKERS:
            assert marker in stops

    def test_request_stops_come_first(self):
        stops = chat_stop_strings(FakeTokenizer(), ["STOP"])
        assert stops[0] == "STOP"

    def test_tokenizer_eos_token_appended(self):
        stops = chat_stop_strings(FakeTokenizer(eos_token="</s>"))
        assert "</s>" in stops

    def test_no_duplicates(self):
        stops = chat_stop_strings(
            FakeTokenizer(eos_token="<|im_end|>"), ["<|im_end|>"]
        )
        assert stops.count("<|im_end|>") == 1

    def test_tokenizer_without_eos_token_attribute(self):
        stops = chat_stop_strings(object())
        assert "<|im_end|>" in stops

    def test_non_string_eos_token_ignored(self):
        stops = chat_stop_strings(FakeTokenizer(eos_token=12345))
        assert 12345 not in stops

    def test_html_strikethrough_close_tag_not_implicit(self):
        # "</s>" appears in legitimate HTML output; it must only become a
        # stop string when the tokenizer actually declares it as eos.
        stops = chat_stop_strings(FakeTokenizer())
        assert "</s>" not in stops


class TestQwenCoderRegression:
    def test_observed_public_gateway_leak(self):
        # Exact shape of the leaked completion observed via api.mycellm.dev
        leaked = (
            "```python\nimport re\n\ndef slugify(text):\n    return text\n```"
            "<|im_end|>"
        )
        tok = FakeTokenizer(eos_token="<|endoftext|>")
        text, hit = truncate_at_stops(leaked, chat_stop_strings(tok))
        assert hit == "<|im_end|>"
        assert "<|im_end|>" not in text
        assert text.endswith("```")


class TestStopHoldbackLen:
    def test_no_overlap_returns_zero(self):
        from mycellm.inference.mlx import stop_holdback_len
        assert stop_holdback_len("hello world", ["<|im_end|>"]) == 0

    def test_partial_prefix_held(self):
        from mycellm.inference.mlx import stop_holdback_len
        assert stop_holdback_len("hello <|im", ["<|im_end|>"]) == 4
        assert stop_holdback_len("hello <", ["<|im_end|>"]) == 1
        assert stop_holdback_len("hello <|im_end", ["<|im_end|>"]) == 8

    def test_complete_stop_not_held(self):
        # A full stop match is truncate_at_stops' job, not holdback's;
        # a proper prefix is the longest thing holdback reports.
        from mycellm.inference.mlx import stop_holdback_len
        assert stop_holdback_len("x<|im_end|>", ["<|im_end|>"]) == 0

    def test_longest_prefix_across_stops_wins(self):
        from mycellm.inference.mlx import stop_holdback_len
        assert stop_holdback_len("abc<|end", ["<|end|>", "<|endoftext|>"]) == 5

    def test_empty_stops(self):
        from mycellm.inference.mlx import stop_holdback_len
        assert stop_holdback_len("anything", []) == 0


class _RecordingLoop:
    def call_soon_threadsafe(self, fn, arg):
        fn(arg)


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class _StubGen:
    def remove(self, uids):
        pass


def _make_streaming_job():
    """A _Job + fake BatchedMLXBackend wired to record emitted chunks."""
    from mycellm.inference.mlx_batched import BatchedMLXBackend, _Job

    backend = object.__new__(BatchedMLXBackend)
    q = _RecordingQueue()
    job = _Job(
        tokens=[1], max_tokens=64, sampler=None,
        stop=["<|im_end|>"], loop=_RecordingLoop(), chunks=q,
    )
    return backend, job, q


def _texts(q):
    return [c.text for c in q.items if c is not None and getattr(c, "text", "")]


class TestStreamingStopHoldback:
    """The oMLX-class bug: a stop string arriving split across stream chunks
    must not leak its prefix to the client (sent text cannot be recalled)."""

    def test_split_stop_marker_never_leaks(self):
        backend, job, q = _make_streaming_job()
        gen, uid_map = _StubGen(), {}
        for piece in ["Hello", " world", "<|im", "_end", "|>ignored"]:
            job.emitted_text += piece
            if backend._stream_progress(gen, uid_map, job):
                break
        assert "".join(_texts(q)) == "Hello world"
        assert job.finished
        finals = [c for c in q.items if c is not None and c.finish_reason]
        assert finals and finals[-1].finish_reason == "stop"

    def test_withheld_prefix_flushed_on_eos_finish(self):
        backend, job, q = _make_streaming_job()
        gen, uid_map = _StubGen(), {}
        for piece in ["result: a ", "<|im"]:
            job.emitted_text += piece
            assert not backend._stream_progress(gen, uid_map, job)
        # Held back so far — "<|im" could still become the stop marker.
        assert "".join(_texts(q)) == "result: a "
        # EOS arrived instead: the held tail was real content after all.
        backend._finish(gen, uid_map, job, "stop")
        assert "".join(_texts(q)) == "result: a <|im"

    def test_plain_text_streams_through_unchanged(self):
        backend, job, q = _make_streaming_job()
        gen, uid_map = _StubGen(), {}
        for piece in ["def f():", "\n    return 1"]:
            job.emitted_text += piece
            assert not backend._stream_progress(gen, uid_map, job)
        backend._finish(gen, uid_map, job, "length")
        assert "".join(_texts(q)) == "def f():\n    return 1"


class TestPrefillKwargs:
    def test_default_is_empty(self, monkeypatch):
        from types import SimpleNamespace
        import mycellm.config as cfg
        from mycellm.inference.mlx import prefill_kwargs
        monkeypatch.setattr(cfg, "get_settings",
                            lambda: SimpleNamespace(mlx_prefill_step_size=0))
        assert prefill_kwargs() == {}

    def test_configured_step_passed_through(self, monkeypatch):
        from types import SimpleNamespace
        import mycellm.config as cfg
        from mycellm.inference.mlx import prefill_kwargs
        monkeypatch.setattr(cfg, "get_settings",
                            lambda: SimpleNamespace(mlx_prefill_step_size=512))
        assert prefill_kwargs() == {"prefill_step_size": 512}
