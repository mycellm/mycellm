"""llama-cpp-python inference backend.

All sync llama-cpp-python calls are wrapped in asyncio.to_thread() to avoid
blocking the event loop (critical for QUIC/API handling during inference).
"""

from __future__ import annotations

import asyncio
import logging
import queue
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
        response = await asyncio.to_thread(
            llm.create_chat_completion,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            top_p=request.top_p,
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

        # Use a thread + queue to bridge sync iterator to async generator
        chunk_queue: queue.Queue = queue.Queue()
        _SENTINEL = object()

        def _run_stream():
            try:
                stream = llm.create_chat_completion(
                    messages=request.messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    top_p=request.top_p,
                    stream=True,
                )
                for chunk in stream:
                    chunk_queue.put(chunk)
            except Exception as e:
                chunk_queue.put(e)
            finally:
                chunk_queue.put(_SENTINEL)

        thread = threading.Thread(target=_run_stream, daemon=True)
        thread.start()

        while True:
            # Poll queue without blocking the event loop
            while chunk_queue.empty():
                await asyncio.sleep(0.01)
            item = chunk_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            delta = item["choices"][0].get("delta", {})
            content = delta.get("content", "")
            finish = item["choices"][0].get("finish_reason")
            if content or finish:
                yield InferenceChunk(text=content, finish_reason=finish)

    def get_loaded_models(self) -> list[str]:
        return list(self._models.keys())

    def get_capabilities(self) -> dict:
        """Detect hardware capabilities."""
        try:
            from llama_cpp import llama_backend_info
            return {"backend": "llama.cpp", "info": str(llama_backend_info)}
        except Exception:
            return {"backend": "llama.cpp", "info": "unknown"}
