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
