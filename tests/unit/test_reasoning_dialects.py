"""Tests for inference.reasoning_dialects — model-family thinking dialects."""

from __future__ import annotations

import pytest

from mycellm.inference.reasoning_dialects import (
    chat_template_suppress_kwargs,
    dialect_for,
    make_splitter,
    split_reasoning,
    supports_thinking,
)


class TestSupportsThinking:
    @pytest.mark.parametrize(
        "name,expected",
        [
            # Qwen3 hybrid thinking models — yes
            ("Qwen3-1.7B-mlx-4bit", True),
            ("Qwen3-30B-A3B", True),
            # Qwen3 non-thinking variants — no, explicit suppression list
            ("Qwen3-Coder-30B-A3B-Instruct-MLX-4bit", False),
            ("Qwen3-Instruct-2507-7B", False),
            ("Qwen3-VL-Instruct-7B", False),
            # Older Qwen — non-thinking
            ("Qwen2.5-Coder-7B-Instruct", False),
            ("Qwen2.5-7B-Instruct-Q4_K_M", False),
            # DeepSeek-R1 — always thinks
            ("deepseek-r1-distill-llama-8b", True),
            ("DeepSeek-R1-7B", True),
            # GLM thinking
            ("GLM-4.6-Thinking", True),
            # OpenAI o-series
            ("gpt-o1-mini", True),
            ("gpt-o3", True),
            # Non-thinking baselines
            ("llama-3.1-8b-instruct", False),
            ("", False),
            ("mistral-small-24b", False),
        ],
    )
    def test_classification(self, name, expected):
        assert supports_thinking(name) is expected


class TestChatTemplateSuppressKwargs:
    def test_qwen3_hybrid_returns_enable_thinking_false(self):
        assert chat_template_suppress_kwargs("Qwen3-1.7B") == {"enable_thinking": False}

    def test_qwen3_coder_returns_empty(self):
        assert chat_template_suppress_kwargs("Qwen3-Coder-30B") == {}

    def test_deepseek_r1_returns_empty(self):
        # R1 always thinks, no template flag works — strip output instead
        assert chat_template_suppress_kwargs("deepseek-r1-distill-7b") == {}

    def test_non_thinking_returns_empty(self):
        assert chat_template_suppress_kwargs("llama-3.1-8b") == {}

    def test_empty_name_returns_empty(self):
        assert chat_template_suppress_kwargs("") == {}


class TestSplitReasoning:
    def test_basic_split(self):
        text = "<think>Let me consider this</think>The answer is 42."
        content, reasoning = split_reasoning(text, "Qwen3-1.7B")
        assert content == "The answer is 42."
        assert reasoning == "Let me consider this"

    def test_multiple_think_blocks(self):
        text = "<think>step one</think>Some content<think>step two</think>More content."
        content, reasoning = split_reasoning(text, "deepseek-r1-7b")
        assert content == "Some contentMore content."
        assert "step one" in reasoning
        assert "step two" in reasoning

    def test_no_think_block(self):
        text = "Just a plain answer."
        content, reasoning = split_reasoning(text, "Qwen3-1.7B")
        assert content == "Just a plain answer."
        assert reasoning == ""

    def test_unclosed_think(self):
        # Model cut off mid-thinking — treat remainder as reasoning
        text = "<think>I'm still thinking and got cut off"
        content, reasoning = split_reasoning(text, "Qwen3-1.7B")
        assert content == ""
        assert "still thinking" in reasoning

    def test_non_thinking_model_passthrough(self):
        # No output_tag_pair for this dialect — text returns unchanged
        text = "<think>this should NOT be stripped</think>kept verbatim"
        content, reasoning = split_reasoning(text, "Qwen3-Coder-30B")
        assert content == text
        assert reasoning == ""


class TestStreamingThinkSplitter:
    def test_clean_split_across_chunks(self):
        s = make_splitter("Qwen3-1.7B")
        emitted: list[tuple[str, str]] = []
        for chunk in ["<thi", "nk>so I", " should", "</thin", "k>The ", "answer ", "is 42."]:
            emitted.extend(s.feed(chunk))
        emitted.extend(s.flush())
        # Reassemble per kind
        reasoning = "".join(p for k, p in emitted if k == "reasoning")
        content = "".join(p for k, p in emitted if k == "content")
        assert "so I should" in reasoning
        assert "The answer is 42." in content

    def test_unclosed_think_flushes_as_reasoning(self):
        s = make_splitter("Qwen3-1.7B")
        out = s.feed("<think>incomplete reasoning")
        out += s.flush()
        # All of it should be classified as reasoning
        kinds = {k for k, _ in out}
        assert kinds == {"reasoning"}
        assert "incomplete reasoning" in "".join(p for _, p in out)

    def test_non_thinking_model_passes_through_as_content(self):
        s = make_splitter("Qwen3-Coder-30B")
        out = s.feed("<think>literal text</think>more")
        out += s.flush()
        kinds = {k for k, _ in out}
        assert kinds == {"content"}
        assert "".join(p for _, p in out) == "<think>literal text</think>more"

    def test_no_think_block_at_all(self):
        s = make_splitter("Qwen3-1.7B")
        out = s.feed("Just an answer.")
        out += s.flush()
        kinds = {k for k, _ in out}
        assert kinds == {"content"}
        assert "".join(p for _, p in out) == "Just an answer."

    def test_empty_token(self):
        s = make_splitter("Qwen3-1.7B")
        assert s.feed("") == []
        assert s.flush() == []

    def test_multiple_think_blocks_streamed(self):
        s = make_splitter("deepseek-r1-7b")
        text = "<think>a</think>X<think>b</think>Y"
        # Feed character by character
        out: list[tuple[str, str]] = []
        for ch in text:
            out.extend(s.feed(ch))
        out.extend(s.flush())
        reasoning = "".join(p for k, p in out if k == "reasoning")
        content = "".join(p for k, p in out if k == "content")
        assert "a" in reasoning and "b" in reasoning
        assert content == "XY"


class TestDialectFor:
    def test_unknown_model_is_non_thinking(self):
        d = dialect_for("some-future-model-2030")
        assert d.supports is False
        assert d.output_tag_pair is None
        assert d.template_kwarg_suppress is None
