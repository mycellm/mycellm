"""Abstract inference backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class InferenceRequest:
    """A single inference request."""

    messages: list[dict]
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 1.0
    stop: list[str] | None = None
    frequency_penalty: float = 0
    presence_penalty: float = 0
    seed: int | None = None
    response_format: dict | None = None
    grammar: str | None = None  # GBNF grammar for constrained output
    tools: list[dict] | None = None        # OpenAI tool definitions
    tool_choice: str | dict | None = None  # "auto", "none", "required", or specific tool
    priority: str = "normal"  # "normal", "high", "speculative"
    request_group: str = ""  # for batch cancellation
    # Reasoning ("thinking") control. When True the backend should suppress
    # reasoning where possible (template flag for Qwen3-family, otherwise the
    # API layer strips <think>...</think> from the output side).
    reasoning_exclude: bool = False


@dataclass
class InferenceChunk:
    """A single token/chunk from streaming inference."""

    text: str
    finish_reason: str | None = None
    tool_calls: list[dict] | None = None  # accumulated tool_calls when finish_reason=="tool_calls"
    # When set, this chunk is reasoning text rather than final answer content.
    # API layer (or callers) route it into the streaming `delta.reasoning_content`
    # field so clients can render it in an ephemeral "thinking" panel.
    reasoning_content: str | None = None


@dataclass
class InferenceResult:
    """Complete inference result."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    tool_calls: list[dict] | None = None  # populated when model called a tool
    # Aggregated reasoning emitted by the model, with surrounding <think>
    # tags removed. Empty string when the model didn't think.
    reasoning_content: str = ""


def content_to_text(content) -> str:
    """Collapse an OpenAI message ``content`` to plain text.

    ``content`` may be a string or a list of content parts (the multimodal
    shape: ``[{"type":"text","text":...}, {"type":"image_url",...}]``). Text
    parts are concatenated; non-text parts (image/audio) are dropped. Used by
    text-only backends and by request guards that need the textual payload.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return "" if content is None else str(content)


def flatten_message_content(messages: list[dict]) -> list[dict]:
    """Return ``messages`` with any list-valued ``content`` collapsed to text.

    Multimodal (image/audio) parts are dropped — text-only backends can't
    consume them; multimodal requests should route to a VLM backend instead.
    Messages whose content is already a string pass through unchanged.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m.get("content"), list):
            nm = dict(m)
            nm["content"] = content_to_text(m["content"])
            out.append(nm)
        else:
            out.append(m)
    return out


@dataclass
class EmbeddingRequest:
    input: str | list[str]
    model: str = ""


@dataclass
class EmbeddingResult:
    embeddings: list[list[float]]
    total_tokens: int = 0


class EmbeddingsNotSupportedError(NotImplementedError):
    """Raised when a backend (or the loaded model) can't produce embeddings.

    Subclasses NotImplementedError so existing callers that catch the generic
    error keep working; the API layer maps this to a 400 response with the
    message intact (it's written to be user-actionable).
    """


class InferenceBackend(ABC):
    """Abstract interface for inference backends."""

    @abstractmethod
    async def load_model(self, model_path: str, **kwargs) -> None:
        """Load a model into memory."""

    @abstractmethod
    async def unload_model(self, model_name: str) -> None:
        """Unload a model from memory."""

    @abstractmethod
    async def generate(self, request: InferenceRequest) -> InferenceResult:
        """Generate a complete response."""

    @abstractmethod
    async def generate_stream(self, request: InferenceRequest) -> AsyncIterator[InferenceChunk]:
        """Generate a streaming response."""

    @abstractmethod
    def get_loaded_models(self) -> list[str]:
        """Return list of currently loaded model names."""

    @abstractmethod
    def get_capabilities(self) -> dict:
        """Return hardware/capability information."""

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Generate embeddings. Override in backends that support it."""
        raise EmbeddingsNotSupportedError(
            f"Embeddings are not supported on the {type(self).__name__} backend"
        )
