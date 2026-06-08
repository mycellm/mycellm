"""Per-model-family reasoning ("thinking") dialect lookup.

Different model families surface reasoning differently:
  * Qwen3 (hybrid)        — chat template flag `enable_thinking=False` suppresses;
                            when enabled, reasoning wraps in <think>...</think>
  * Qwen3-Coder / Instruct-2507 — never emits reasoning
  * DeepSeek-R1 family    — always wraps reasoning in <think>...</think>;
                            no template flag to suppress
  * GLM-4.x-Thinking       — wraps in <think>...</think>
  * Gemini 2.0 Thinking    — wraps in <think>...</think>

This module is the single place to encode that variance. New families add
one line to `DIALECTS`. Backends and the API layer call the three
helpers below and never need to know which family they're dealing with.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ThinkingDialect:
    """How a model family handles reasoning ("thinking")."""

    # Can the model emit reasoning at all? Drives capability advertisement.
    supports: bool = False
    # apply_chat_template kwarg to pass when caller wants reasoning suppressed.
    # Most families don't accept any — set to None.
    template_kwarg_suppress: tuple[str, object] | None = None
    # Tag pair the model emits to wrap reasoning. None means no wrapping
    # (or no reasoning at all). Used for split + strip in API layer.
    output_tag_pair: tuple[str, str] | None = ("<think>", "</think>")
    # If True, the model always emits reasoning regardless of template flag
    # (e.g. DeepSeek-R1). Callers can still strip it via output_tag_pair.
    always_thinks: bool = False


_NON_THINKING = ThinkingDialect(supports=False, output_tag_pair=None)


# Match by lowercased substring against model name. First match wins,
# so list more-specific patterns BEFORE more-general ones.
DIALECTS: list[tuple[str, ThinkingDialect]] = [
    # Qwen3 non-thinking variants — explicit suppression list (matched first)
    ("qwen3-coder", _NON_THINKING),
    ("qwen3-instruct-2507", _NON_THINKING),
    ("qwen3-vl-instruct", _NON_THINKING),

    # Qwen3 hybrid thinking models — template flag works
    ("qwen3", ThinkingDialect(
        supports=True,
        template_kwarg_suppress=("enable_thinking", False),
        output_tag_pair=("<think>", "</think>"),
    )),

    # DeepSeek-R1 family — always thinks, no suppression flag
    ("deepseek-r1", ThinkingDialect(
        supports=True,
        template_kwarg_suppress=None,
        output_tag_pair=("<think>", "</think>"),
        always_thinks=True,
    )),

    # GLM thinking variants
    ("glm-4-thinking", ThinkingDialect(
        supports=True,
        output_tag_pair=("<think>", "</think>"),
    )),
    ("glm-4.6-thinking", ThinkingDialect(
        supports=True,
        output_tag_pair=("<think>", "</think>"),
    )),

    # Gemini thinking (via relay)
    ("gemini-2.0-flash-thinking", ThinkingDialect(
        supports=True,
        output_tag_pair=("<think>", "</think>"),
    )),

    # OpenAI o-series (via relay) — reasoning hidden by upstream API, not <think>-wrapped
    ("gpt-o1", ThinkingDialect(supports=True, output_tag_pair=None)),
    ("gpt-o3", ThinkingDialect(supports=True, output_tag_pair=None)),
    ("gpt-o4", ThinkingDialect(supports=True, output_tag_pair=None)),
]


def dialect_for(model_name: str) -> ThinkingDialect:
    """Return the ThinkingDialect for a model name. Default: non-thinking."""
    if not model_name:
        return _NON_THINKING
    name_lower = model_name.lower()
    for pattern, dialect in DIALECTS:
        if pattern in name_lower:
            return dialect
    return _NON_THINKING


def supports_thinking(model_name: str) -> bool:
    """Does this model family emit reasoning content at all?"""
    return dialect_for(model_name).supports


def chat_template_suppress_kwargs(model_name: str) -> dict:
    """Kwargs to pass to apply_chat_template when caller wants reasoning suppressed.

    Returns {} if the model doesn't accept any suppression flag (or doesn't think).
    Caller must independently strip <think>...</think> from output for models with
    output_tag_pair set, since the template flag may not be honored on all backends.
    """
    d = dialect_for(model_name)
    if d.template_kwarg_suppress is None:
        return {}
    k, v = d.template_kwarg_suppress
    return {k: v}


def split_reasoning(text: str, model_name: str) -> tuple[str, str]:
    """Split raw model output into (content, reasoning_content).

    If the model's dialect declares an output_tag_pair (e.g. <think></think>),
    extract all such blocks into reasoning_content and remove from content.
    Returns (content, reasoning_content). reasoning_content is "" if none.

    Tolerates unbalanced tags: if an opener has no closer, the unclosed remainder
    is treated as reasoning (model was cut off mid-think).
    """
    d = dialect_for(model_name)
    if d.output_tag_pair is None:
        return text, ""

    open_tag, close_tag = d.output_tag_pair
    if open_tag not in text:
        return text, ""

    # Greedy non-overlapping extraction.
    pattern = re.compile(
        re.escape(open_tag) + r"(.*?)(?:" + re.escape(close_tag) + r"|$)",
        re.DOTALL,
    )
    reasoning_parts: list[str] = []

    def _replace(m: re.Match) -> str:
        reasoning_parts.append(m.group(1).strip())
        return ""

    cleaned = pattern.sub(_replace, text).strip()
    reasoning = "\n\n".join(p for p in reasoning_parts if p)
    return cleaned, reasoning


class StreamingThinkSplitter:
    """Streaming state machine for splitting <think>...</think> from a token stream.

    Usage:
        splitter = StreamingThinkSplitter("<think>", "</think>")
        for token in stream:
            for kind, chunk in splitter.feed(token):
                # kind in ("content", "reasoning")
                emit(kind, chunk)
        for kind, chunk in splitter.flush():
            emit(kind, chunk)

    Handles tags that straddle chunk boundaries by buffering up to len(close_tag)-1
    bytes. Tolerates unclosed think blocks (flush emits the remainder as reasoning).
    Inert (always emits "content") when tag pair is None.
    """

    def __init__(self, open_tag: str | None, close_tag: str | None):
        self._open = open_tag
        self._close = close_tag
        self._enabled = bool(open_tag and close_tag)
        self._buf = ""
        self._in_think = False

    def feed(self, token: str) -> list[tuple[str, str]]:
        """Process one streamed token. Returns list of (kind, chunk) tuples to emit."""
        if not self._enabled or not token:
            return [("content", token)] if token else []

        out: list[tuple[str, str]] = []
        self._buf += token

        while self._buf:
            if self._in_think:
                # Look for closing tag.
                idx = self._buf.find(self._close)
                if idx >= 0:
                    if idx > 0:
                        out.append(("reasoning", self._buf[:idx]))
                    self._buf = self._buf[idx + len(self._close):]
                    self._in_think = False
                    continue
                # No close yet — emit all but the trailing close-tag-length bytes
                # (in case the close tag is straddling).
                safe = len(self._buf) - (len(self._close) - 1)
                if safe > 0:
                    out.append(("reasoning", self._buf[:safe]))
                    self._buf = self._buf[safe:]
                break
            else:
                # Look for opening tag.
                idx = self._buf.find(self._open)
                if idx >= 0:
                    if idx > 0:
                        out.append(("content", self._buf[:idx]))
                    self._buf = self._buf[idx + len(self._open):]
                    self._in_think = True
                    continue
                # No open yet — emit all but the trailing open-tag-length bytes.
                safe = len(self._buf) - (len(self._open) - 1)
                if safe > 0:
                    out.append(("content", self._buf[:safe]))
                    self._buf = self._buf[safe:]
                break

        return out

    def flush(self) -> list[tuple[str, str]]:
        """Drain remaining buffer at end of stream."""
        if not self._buf:
            return []
        kind = "reasoning" if self._in_think else "content"
        out = [(kind, self._buf)]
        self._buf = ""
        return out


def make_splitter(model_name: str) -> StreamingThinkSplitter:
    """Build a StreamingThinkSplitter sized to the model's tag pair."""
    d = dialect_for(model_name)
    if d.output_tag_pair is None:
        return StreamingThinkSplitter(None, None)
    return StreamingThinkSplitter(*d.output_tag_pair)
