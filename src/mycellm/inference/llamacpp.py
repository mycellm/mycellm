"""llama-cpp-python inference backend.

All sync llama-cpp-python calls are wrapped in asyncio.to_thread() to avoid
blocking the event loop (critical for QUIC/API handling during inference).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import AsyncIterator

from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingsNotSupportedError,
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
    flatten_message_content,
)

logger = logging.getLogger("mycellm.inference")


class LlamaGGUFDraftModel:
    """Draft model for speculative decoding using a small GGUF model.

    Loads a small model (e.g. 1.5B) and uses it to predict the next N tokens.
    The main model then verifies these predictions in a single batch forward pass.
    When predictions are accepted (typically 50-70% for code), the effective
    throughput of the main model increases 1.5-2x.

    Uses create_completion with prompt=input_ids for token prediction.
    """

    def __init__(self, model_path: str, num_pred_tokens: int = 8, n_ctx: int = 2048):
        from llama_cpp import Llama

        self.num_pred_tokens = num_pred_tokens
        logger.info(f"Loading draft model: {Path(model_path).stem} (pred_tokens={num_pred_tokens})")
        self._llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=-1,
            flash_attn=True,
            n_threads=_detect_optimal_threads() or 4,
            logits_all=True,
            verbose=False,
        )
        logger.info("Draft model loaded")

    def __call__(self, input_ids, /, **kwargs):
        """Predict next tokens given the current sequence."""
        import numpy as np

        if len(input_ids) == 0:
            return np.array([], dtype=np.intc)

        try:
            # Use the model to predict next tokens via create_completion
            # Feed input_ids as the prompt (token-level)
            max_input = min(len(input_ids), self._llm.n_ctx() - self.num_pred_tokens - 1)
            prompt_tokens = input_ids[-max_input:].tolist()

            output = self._llm.create_completion(
                prompt=prompt_tokens,
                max_tokens=self.num_pred_tokens,
                temperature=0.0,  # greedy for max acceptance
                top_k=1,
            )

            # Extract generated token IDs from the output
            text = output.get("choices", [{}])[0].get("text", "")
            if text:
                # Tokenize the output text to get token IDs
                token_ids = self._llm.tokenize(text.encode(), add_bos=False)
                return np.array(token_ids[:self.num_pred_tokens], dtype=np.intc)

        except Exception as e:
            logger.debug(f"Draft model prediction failed: {e}")

        return np.array([], dtype=np.intc)


def _detect_optimal_threads() -> int:
    """Detect optimal thread count based on platform.

    Apple Silicon: use performance cores only (not efficiency cores).
    Linux: use physical cores (not hyperthreaded logical cores).
    """
    import platform
    import subprocess

    try:
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            # Apple Silicon: p-core count via sysctl
            r = subprocess.run(
                ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                cores = int(r.stdout.strip())
                logger.info(f"Apple Silicon detected: {cores} p-cores")
                return cores

        if platform.system() == "Linux":
            import os
            # Physical cores (not hyperthreaded)
            try:
                with open("/proc/cpuinfo") as f:
                    cores = len(set(
                        line.split(":")[1].strip()
                        for line in f if line.startswith("physical id")
                    )) or 1
                logical = os.cpu_count() or 4
                physical = max(1, logical // 2)  # rough estimate
                return physical
            except Exception:
                return max(1, (os.cpu_count() or 4) // 2)
    except Exception:
        pass

    return 0  # let llama.cpp decide


class LlamaCppBackend(InferenceBackend):
    """Inference backend wrapping llama-cpp-python."""

    def __init__(self):
        self._models: dict[str, object] = {}  # name -> Llama instance
        # Models loaded with embedding=True — llama.cpp only produces
        # embeddings when the context was created with that flag.
        self._embedding_models: set[str] = set()

    async def load_model(self, model_path: str, **kwargs) -> None:
        """Load a GGUF model (runs in thread to avoid blocking)."""
        from llama_cpp import Llama
        import inspect

        model_name = kwargs.get("name", model_path.split("/")[-1])
        n_ctx = kwargs.get("ctx_len", kwargs.get("n_ctx", 4096))
        n_gpu_layers = kwargs.get("n_gpu_layers", -1)  # -1 = auto
        progress_callback = kwargs.get("progress_callback")

        flash_attn = kwargs.get("flash_attn", True)
        kv_quant = kwargs.get("kv_cache_quant", "q8_0")
        kv_quant_k = kwargs.get("kv_cache_quant_k", "")
        kv_quant_v = kwargs.get("kv_cache_quant_v", "")
        prompt_lookup = kwargs.get("prompt_lookup", False)
        n_threads = kwargs.get("n_threads", 0)

        logger.info(f"Loading model {model_name} from {model_path}")

        extra_kwargs = {}
        if progress_callback:
            llama_params = inspect.signature(Llama.__init__).parameters
            if "progress_callback" in llama_params:
                def _on_progress(progress: float) -> bool:
                    progress_callback(progress)
                    return True
                extra_kwargs["progress_callback"] = _on_progress

        # Flash attention (Metal/CUDA optimized attention kernel)
        if flash_attn:
            extra_kwargs["flash_attn"] = True

        # Embeddings — llama.cpp only produces embeddings when the context
        # was created with embedding=True (load option: "embedding": true).
        embedding = kwargs.get("embedding", False)
        if embedding:
            extra_kwargs["embedding"] = True

        # Asymmetric KV cache quantization — keys need higher precision than values
        # Default: K=q8_0 (higher precision), V=q4_0 (lower OK) — 59% less KV memory
        try:
            from llama_cpp import GGML_TYPE_Q8_0, GGML_TYPE_Q4_0
            kv_types = {"q8_0": GGML_TYPE_Q8_0, "q4_0": GGML_TYPE_Q4_0}

            effective_k = kv_quant_k or kv_quant or "q8_0"
            effective_v = kv_quant_v or ("q4_0" if kv_quant_k or not kv_quant_v else kv_quant) or "q4_0"

            if effective_k in kv_types:
                extra_kwargs["type_k"] = kv_types[effective_k]
            if effective_v in kv_types:
                extra_kwargs["type_v"] = kv_types[effective_v]
            logger.info(f"KV cache: K={effective_k}, V={effective_v}")
        except ImportError:
            pass

        # Thread count — auto-detect p-cores on Apple Silicon
        if n_threads <= 0:
            n_threads = _detect_optimal_threads()
        if n_threads > 0:
            extra_kwargs["n_threads"] = n_threads
            extra_kwargs["n_threads_batch"] = n_threads
            logger.info(f"Threads: {n_threads}")

        # Speculative decoding — draft model predicts, main model verifies in batch
        draft_model_path = kwargs.get("draft_model_path", "")
        draft_pred_tokens = kwargs.get("draft_pred_tokens", 8)
        if draft_model_path and Path(draft_model_path).exists():
            extra_kwargs["draft_model"] = LlamaGGUFDraftModel(
                model_path=draft_model_path,
                num_pred_tokens=draft_pred_tokens,
                n_ctx=min(n_ctx, 2048),
            )
            logger.info(f"Speculative decoding: draft={Path(draft_model_path).stem}")
        elif prompt_lookup:
            # Fallback: prompt lookup (n-gram based, no extra model)
            try:
                from llama_cpp.llama_speculative import LlamaPromptLookupDecoding
                extra_kwargs["draft_model"] = LlamaPromptLookupDecoding(num_pred_tokens=10)
                logger.info("Prompt lookup decoding enabled")
            except ImportError:
                pass

        try:
            llm = await asyncio.to_thread(
                Llama,
                model_path=model_path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
                **extra_kwargs,
            )
        except Exception as e:
            err_msg = str(e)
            # Detect model load failures — often caused by unsupported architecture
            if "failed to load model" in err_msg.lower():
                model_name_short = model_path.split("/")[-1]
                raise RuntimeError(
                    f"Failed to load {model_name_short}. This may be an unsupported model "
                    f"architecture. Try: pip install --upgrade llama-cpp-python"
                ) from e
            raise

        self._models[model_name] = llm
        if embedding:
            self._embedding_models.add(model_name)
        logger.info(
            f"Model {model_name} loaded "
            f"(flash_attn={flash_attn}, kv_quant={kv_quant}, embedding={embedding})"
        )

    async def unload_model(self, model_name: str) -> None:
        model = self._models.pop(model_name, None)
        self._embedding_models.discard(model_name)
        if model:
            del model
            logger.info(f"Model {model_name} unloaded")

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        # Use streaming internally — the non-streaming create_chat_completion path
        # can crash with llama_decode errors on large models (e.g. llama_decode
        # returned -3 on 32B with KV cache state from a previous sequence).
        # The streaming path via generate_stream() is stable across all model sizes.
        text = ""
        finish_reason = "stop"
        tool_calls: list | None = None
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in self.generate_stream(request):
            if chunk.text:
                text += chunk.text
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.tool_calls is not None:
                tool_calls = chunk.tool_calls

        return InferenceResult(
            text=text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
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
        if request.grammar:
            try:
                from llama_cpp import LlamaGrammar
                extra_kwargs["grammar"] = LlamaGrammar.from_string(request.grammar)
            except (ImportError, Exception) as e:
                logger.warning(f"Grammar constraint ignored: {e}")
        if request.tools:
            extra_kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            extra_kwargs["tool_choice"] = request.tool_choice

        def _run_stream():
            try:
                stream = llm.create_chat_completion(
                    messages=flatten_message_content(request.messages),
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

        # Accumulate tool_call deltas across streaming chunks.
        # llama-cpp-python emits one delta per tool_call argument token;
        # we collect them and emit a single InferenceChunk with the full
        # tool_calls list at finish_reason=="tool_calls".
        accumulated_tool_calls: dict[int, dict] = {}

        while True:
            item = await chunk_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            choice = item["choices"][0]
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            finish = choice.get("finish_reason")

            # Accumulate tool_call deltas
            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": tc_delta.get("id", ""),
                        "type": tc_delta.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    }
                tc = accumulated_tool_calls[idx]
                fn = tc_delta.get("function", {})
                if fn.get("name") and not tc["function"]["name"]:
                    tc["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    tc["function"]["arguments"] += fn["arguments"]
                if tc_delta.get("id") and not tc["id"]:
                    tc["id"] = tc_delta["id"]

            if content or (finish and finish != "tool_calls"):
                yield InferenceChunk(text=content, finish_reason=finish)

        # If we accumulated tool_calls, emit them as a final chunk
        if accumulated_tool_calls:
            tool_calls_list = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]
            yield InferenceChunk(text="", finish_reason="tool_calls", tool_calls=tool_calls_list)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Generate embeddings via llama.cpp's create_embedding.

        Requires the model to have been loaded with embedding=True (llama.cpp
        only fills the embedding tensor when the context was created with the
        flag). The sync call runs in a thread like generation; the
        InferenceManager's per-model Lock serializes access to the C context.
        """
        model_name = request.model or next(iter(self._models), "")
        if not model_name or model_name not in self._models:
            raise RuntimeError(f"Model '{model_name}' not loaded")
        if model_name not in self._embedding_models:
            raise EmbeddingsNotSupportedError(
                f"Model '{model_name}' was not loaded for embeddings. Reload it with "
                'the "embedding": true load option to enable embeddings.'
            )

        llm = self._models[model_name]
        inputs = request.input if isinstance(request.input, list) else [request.input]
        result = await asyncio.to_thread(llm.create_embedding, inputs)

        data = sorted(result.get("data", []), key=lambda d: d.get("index", 0))
        embeddings = [d["embedding"] for d in data]
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
