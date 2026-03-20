"""llama-cpp-python inference backend.

All sync llama-cpp-python calls are wrapped in asyncio.to_thread() to avoid
blocking the event loop (critical for QUIC/API handling during inference).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import AsyncIterator

from mycellm.inference.base import (
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)

logger = logging.getLogger("mycellm.inference")


class LlamaCppBackend(InferenceBackend):
    """Inference backend wrapping llama-cpp-python."""

    def __init__(self):
        self._models: dict[str, object] = {}  # name -> Llama instance

    async def load_model(self, model_path: str, **kwargs) -> None:
        """Load a GGUF model (runs in thread to avoid blocking)."""
        from llama_cpp import Llama

        model_name = kwargs.get("name", model_path.split("/")[-1])
        n_ctx = kwargs.get("n_ctx", 4096)
        n_gpu_layers = kwargs.get("n_gpu_layers", -1)  # -1 = auto

        logger.info(f"Loading model {model_name} from {model_path}")
        llm = await asyncio.to_thread(
            Llama,
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self._models[model_name] = llm
        logger.info(f"Model {model_name} loaded")

    async def unload_model(self, model_name: str) -> None:
        model = self._models.pop(model_name, None)
        if model:
            del model
            logger.info(f"Model {model_name} unloaded")

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        model_name = request.model or next(iter(self._models), "")
        if not model_name or model_name not in self._models:
            raise RuntimeError(f"Model '{model_name}' not loaded")

        llm = self._models[model_name]
        extra_kwargs = {}
        if request.stop:
            extra_kwargs["stop"] = request.stop
        if request.frequency_penalty:
            extra_kwargs["frequency_penalty"] = request.frequency_penalty
        if request.presence_penalty:
            extra_kwargs["presence_penalty"] = request.presence_penalty
        if request.seed is not None:
            extra_kwargs["seed"] = request.seed
        if request.response_format:
            extra_kwargs["response_format"] = request.response_format
        response = await asyncio.to_thread(
            llm.create_chat_completion,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            top_p=request.top_p,
            **extra_kwargs,
        )

        choice = response["choices"][0]
        usage = response.get("usage", {})

        return InferenceResult(
            text=choice["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        model_name = request.model or next(iter(self._models), "")
        if not model_name or model_name not in self._models:
            raise RuntimeError(f"Model '{model_name}' not loaded")

        llm = self._models[model_name]

        # Use a thread + asyncio.Queue to bridge sync iterator to async generator
        loop = asyncio.get_running_loop()
        chunk_queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        extra_kwargs = {}
        if request.stop:
            extra_kwargs["stop"] = request.stop
        if request.frequency_penalty:
            extra_kwargs["frequency_penalty"] = request.frequency_penalty
        if request.presence_penalty:
            extra_kwargs["presence_penalty"] = request.presence_penalty
        if request.seed is not None:
            extra_kwargs["seed"] = request.seed
        if request.response_format:
            extra_kwargs["response_format"] = request.response_format

        def _run_stream():
            try:
                stream = llm.create_chat_completion(
                    messages=request.messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    top_p=request.top_p,
                    stream=True,
                    **extra_kwargs,
                )
                for chunk in stream:
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, _SENTINEL)

        thread = threading.Thread(target=_run_stream, daemon=True)
        thread.start()

        while True:
            item = await chunk_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            delta = item["choices"][0].get("delta", {})
            content = delta.get("content", "")
            finish = item["choices"][0].get("finish_reason")
            if content or finish:
                yield InferenceChunk(text=content, finish_reason=finish)

    async def embed(self, request):
        from mycellm.inference.base import EmbeddingResult
        model_name = request.model
        model = self._models.get(model_name)
        if not model:
            model = next(iter(self._models.values()), None)
        if not model:
            raise RuntimeError("No model loaded for embeddings")

        inputs = request.input if isinstance(request.input, list) else [request.input]
        result = await asyncio.to_thread(model.create_embedding, inputs)

        embeddings = [d["embedding"] for d in result["data"]]
        total_tokens = result.get("usage", {}).get("total_tokens", 0)
        return EmbeddingResult(embeddings=embeddings, total_tokens=total_tokens)

    def get_loaded_models(self) -> list[str]:
        return list(self._models.keys())

    def get_capabilities(self) -> dict:
        """Detect hardware capabilities."""
        try:
            from llama_cpp import llama_backend_info
            return {"backend": "llama.cpp", "info": str(llama_backend_info)}
        except Exception:
            return {"backend": "llama.cpp", "info": "unknown"}
