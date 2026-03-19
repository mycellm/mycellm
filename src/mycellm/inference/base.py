"""Abstract inference backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class InferenceRequest:
    """A single inference request."""

    messages: list[dict[str, str]]
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 1.0


@dataclass
class InferenceChunk:
    """A single token/chunk from streaming inference."""

    text: str
    finish_reason: str | None = None


@dataclass
class InferenceResult:
    """Complete inference result."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"


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
