"""Inference manager — handles model loading, concurrency, and routing to backends."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from mycellm.inference.base import (
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)
from mycellm.protocol.capabilities import ModelCapability

logger = logging.getLogger("mycellm.inference")


class InferenceManager:
    """Manages loaded models, concurrency limits, and backend routing."""

    def __init__(self, max_concurrent: int = 2):
        self._backends: dict[str, InferenceBackend] = {}
        self._model_info: dict[str, ModelCapability] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._active_count = 0

    @property
    def loaded_models(self) -> list[ModelCapability]:
        return list(self._model_info.values())

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def is_overloaded(self) -> bool:
        return self._active_count >= self._max_concurrent

    async def load_model(
        self,
        model_path: str,
        name: str | None = None,
        backend_type: str = "llama.cpp",
        **kwargs,
    ) -> str:
        """Load a model and return its name."""
        model_name = name or (Path(model_path).stem if model_path else "remote-model")

        if model_name in self._backends:
            logger.info(f"Model {model_name} already loaded")
            return model_name

        backend = self._create_backend(backend_type)
        await backend.load_model(model_path, name=model_name, **kwargs)

        self._backends[model_name] = backend
        self._model_info[model_name] = ModelCapability(
            name=model_name,
            quant=kwargs.get("quant", ""),
            ctx_len=kwargs.get("ctx_len", kwargs.get("n_ctx", 4096)),
            backend=backend_type,
        )

        logger.info(f"Model {model_name} loaded via {backend_type}")
        return model_name

    async def unload_model(self, model_name: str) -> None:
        backend = self._backends.pop(model_name, None)
        if backend:
            await backend.unload_model(model_name)
            self._model_info.pop(model_name, None)
            logger.info(f"Model {model_name} unloaded")

    def get_backend(self, model_name: str) -> InferenceBackend | None:
        """Get backend for a specific model, or the first available."""
        if model_name and model_name in self._backends:
            return self._backends[model_name]
        if not model_name and self._backends:
            return next(iter(self._backends.values()))
        return None

    def resolve_model_name(self, requested: str) -> str:
        """Resolve a model name to a loaded model.

        Returns exact match if found. Falls back to first available only
        when no specific model is requested (empty string).
        """
        if requested and requested in self._backends:
            return requested
        if not requested and self._backends:
            return next(iter(self._backends))
        return ""

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        """Run inference with concurrency control."""
        model_name = self.resolve_model_name(request.model)
        if not model_name:
            raise RuntimeError("No models loaded")

        request.model = model_name
        backend = self._backends[model_name]

        async with self._semaphore:
            self._active_count += 1
            try:
                return await backend.generate(request)
            finally:
                self._active_count -= 1

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        """Run streaming inference with concurrency control."""
        model_name = self.resolve_model_name(request.model)
        if not model_name:
            raise RuntimeError("No models loaded")

        request.model = model_name
        backend = self._backends[model_name]

        async with self._semaphore:
            self._active_count += 1
            try:
                async for chunk in backend.generate_stream(request):
                    yield chunk
            finally:
                self._active_count -= 1

    def _create_backend(self, backend_type: str) -> InferenceBackend:
        if backend_type == "llama.cpp":
            from mycellm.inference.llamacpp import LlamaCppBackend
            return LlamaCppBackend()
        if backend_type in ("openai", "openai-compatible"):
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            return OpenAICompatibleBackend()
        raise ValueError(f"Unknown backend type: {backend_type}")
