"""MLX inference backend for Apple Silicon (M-series).

Uses Apple's MLX framework directly via `mlx-lm`. On M-series Macs this is
typically faster than llama.cpp's Metal backend for the same quantization
and uses unified memory more efficiently.

Model format: directory containing config.json + safetensors (the layout
used by `mlx-community/*` repos on Hugging Face). Single-file GGUF is NOT
supported here — use the llama.cpp backend for those.

Concurrency: MLX model objects are not safe for parallel forward passes
on the same Metal queue. The InferenceManager wraps each call in a
per-model Lock — same model as llama.cpp.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
from pathlib import Path
from typing import AsyncIterator

from mycellm.inference.base import (
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)

logger = logging.getLogger("mycellm.inference")


def _require_apple_silicon() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError(
            "MLX backend requires Apple Silicon (macOS arm64). "
            "Use backend='llama.cpp' on this platform."
        )


def is_mlx_model_path(path: str) -> bool:
    """Detect if a path looks like an MLX model directory.

    MLX repos from `mlx-community/*` are directories containing `config.json`
    and at least one `model.safetensors` (or shard `model-NNNNN-of-MMMMM.safetensors`).
    """
    p = Path(path)
    if not p.is_dir():
        return False
    if not (p / "config.json").exists():
        return False
    return any(p.glob("*.safetensors"))


class MLXBackend(InferenceBackend):
    """Inference backend wrapping `mlx-lm`."""

    def __init__(self):
        # name -> (model, tokenizer)
        self._models: dict[str, tuple[object, object]] = {}
        # name -> model_path (for capabilities/debugging)
        self._paths: dict[str, str] = {}

    async def load_model(self, model_path: str, **kwargs) -> None:
        _require_apple_silicon()
        from mlx_lm import load as mlx_load

        model_name = kwargs.get("name") or Path(model_path).name
        progress_callback = kwargs.get("progress_callback")

        # mlx_lm.load can take a local directory or a HuggingFace repo id.
        # Prefer local dirs that already exist; otherwise mlx_lm will pull
        # via huggingface_hub into ~/.cache/huggingface.
        target = model_path
        if Path(model_path).is_dir():
            target = str(Path(model_path).resolve())

        logger.info(f"Loading MLX model {model_name} from {target}")

        if progress_callback:
            # mlx_lm doesn't surface load progress. Mark as in-flight at 0.0
            # and let the manager's RSS monitor (now generalized) report.
            try:
                progress_callback(0.0)
            except Exception:
                pass

        def _do_load():
            return mlx_load(target)

        try:
            model, tokenizer = await asyncio.to_thread(_do_load)
        except Exception as e:
            err = str(e)
            if "Model type" in err and "not supported" in err:
                raise RuntimeError(
                    f"Failed to load {model_name}: {err}. "
                    "Try: pip install --upgrade mlx-lm"
                ) from e
            raise

        self._models[model_name] = (model, tokenizer)
        self._paths[model_name] = target

        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        logger.info(f"MLX model {model_name} loaded")

    async def unload_model(self, model_name: str) -> None:
        entry = self._models.pop(model_name, None)
        self._paths.pop(model_name, None)
        if entry:
            del entry
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:
                pass
            logger.info(f"MLX model {model_name} unloaded")

    def _resolve_model(self, model_name: str) -> tuple[object, object]:
        name = model_name or next(iter(self._models), "")
        if not name or name not in self._models:
            raise RuntimeError(f"Model '{name}' not loaded")
        return self._models[name]

    def _build_prompt(self, tokenizer, request: InferenceRequest) -> str:
        if hasattr(tokenizer, "apply_chat_template"):
            template_kwargs: dict = {
                "tokenize": False,
                "add_generation_prompt": True,
            }
            if request.tools:
                template_kwargs["tools"] = request.tools
            if request.reasoning_exclude:
                from mycellm.inference.reasoning_dialects import chat_template_suppress_kwargs
                template_kwargs.update(chat_template_suppress_kwargs(request.model))
            try:
                return tokenizer.apply_chat_template(request.messages, **template_kwargs)
            except Exception as e:
                # Retry shedding optional kwargs one at a time — some chat
                # templates raise on unknown kwargs.
                for shed_key in ("enable_thinking", "tools"):
                    if shed_key in template_kwargs:
                        logger.warning(f"apply_chat_template with {shed_key} failed ({e}), retrying without")
                        template_kwargs.pop(shed_key, None)
                        try:
                            return tokenizer.apply_chat_template(request.messages, **template_kwargs)
                        except Exception as e_retry:
                            e = e_retry
                            continue
                logger.warning(f"apply_chat_template failed, falling back to concat: {e}")
        return "\n".join(
            f"{m.get('role','user')}: {m.get('content','')}" for m in request.messages
        )

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        model, tokenizer = self._resolve_model(request.model)
        prompt = self._build_prompt(tokenizer, request)

        from mlx_lm import generate as mlx_generate
        from mlx_lm.sample_utils import make_sampler

        sampler = make_sampler(temp=request.temperature, top_p=request.top_p)

        def _do_generate() -> str:
            return mlx_generate(
                model, tokenizer,
                prompt=prompt,
                max_tokens=request.max_tokens,
                sampler=sampler,
                verbose=False,
            )

        text = await asyncio.to_thread(_do_generate)

        prompt_tokens = len(tokenizer.encode(prompt)) if hasattr(tokenizer, "encode") else 0
        completion_tokens = len(tokenizer.encode(text)) if hasattr(tokenizer, "encode") else 0

        finish = "stop"
        if request.stop:
            for s in request.stop:
                if s in text:
                    text = text.split(s)[0]
                    finish = "stop"
                    break

        return InferenceResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish,
        )

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        model, tokenizer = self._resolve_model(request.model)
        prompt = self._build_prompt(tokenizer, request)

        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        sampler = make_sampler(temp=request.temperature, top_p=request.top_p)

        loop = asyncio.get_running_loop()
        chunk_queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()
        stop_strings = list(request.stop or [])
        seen_text = ""

        def _run_stream():
            try:
                for resp in stream_generate(
                    model, tokenizer,
                    prompt=prompt,
                    max_tokens=request.max_tokens,
                    sampler=sampler,
                ):
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, resp)
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

            text = getattr(item, "text", "") or ""
            finish = getattr(item, "finish_reason", None)

            if stop_strings:
                seen_text += text
                hit = next((s for s in stop_strings if s in seen_text), None)
                if hit:
                    cut = seen_text.split(hit)[0]
                    tail = cut[len(seen_text) - len(text):]
                    if tail:
                        yield InferenceChunk(text=tail, finish_reason="stop")
                    else:
                        yield InferenceChunk(text="", finish_reason="stop")
                    break

            if text or finish:
                yield InferenceChunk(text=text, finish_reason=finish)

    def get_loaded_models(self) -> list[str]:
        return list(self._models.keys())

    def get_capabilities(self) -> dict:
        info = {"backend": "mlx"}
        try:
            import mlx.core as mx
            info["device"] = str(mx.default_device())
            try:
                limit = mx.metal.get_cache_memory()
                info["metal_cache_bytes"] = limit
            except Exception:
                pass
        except Exception:
            info["info"] = "mlx not importable"
        try:
            import mlx_lm
            info["mlx_lm_version"] = getattr(mlx_lm, "__version__", "unknown")
        except Exception:
            pass
        return info
