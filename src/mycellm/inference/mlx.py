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
from concurrent.futures import ThreadPoolExecutor
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

# Chat-template end markers that model configs sometimes fail to register as
# eos_token_id (e.g. mlx-community/Qwen2.5-Coder-*-4bit ships
# eos=<|endoftext|> while its chat template terminates turns with <|im_end|>).
# mlx_lm only stops on eos_token_ids, so an unregistered terminator
# detokenizes straight into the output text. Treated as implicit stop strings.
# Deliberately excludes "</s>" (legitimate in generated HTML); </s>-style eos
# tokens are picked up from tokenizer.eos_token instead.
CHAT_END_MARKERS = (
    "<|im_end|>",  # ChatML / Qwen
    "<|eot_id|>",  # Llama 3
    "<|end|>",  # Phi
    "<|endoftext|>",
)


def chat_stop_strings(tokenizer, extra: list[str] | None = None) -> list[str]:
    """Request stop strings plus implicit chat-template terminators."""
    stops = list(extra or [])
    for marker in CHAT_END_MARKERS:
        if marker not in stops:
            stops.append(marker)
    eos = getattr(tokenizer, "eos_token", None)
    if isinstance(eos, str) and eos and eos not in stops:
        stops.append(eos)
    return stops


def truncate_at_stops(text: str, stops: list[str]) -> tuple[str, str | None]:
    """Truncate at the earliest stop-string occurrence.

    Returns (truncated_text, hit) where hit is the matched stop string or
    None if no stop occurs in the text.
    """
    cut, hit = len(text), None
    for s in stops:
        i = text.find(s)
        if i != -1 and i < cut:
            cut, hit = i, s
    return text[:cut], hit


def stop_holdback_len(text: str, stops: list[str]) -> int:
    """Length of the longest suffix of text that is a proper prefix of a stop.

    Streaming must withhold such a suffix: the next token(s) may complete the
    stop string, and once text is emitted to the client it cannot be recalled.
    Returns 0 when the tail cannot begin any stop string.
    """
    hold = 0
    for s in stops:
        for k in range(min(len(s) - 1, len(text)), hold, -1):
            if text.endswith(s[:k]):
                hold = k
                break
    return hold


def prefill_kwargs() -> dict:
    """mlx-lm generation kwargs for the configured prefill chunk size.

    Empty when unset (mlx-lm's 2048 default applies). The prefill transient
    scales with the chunk size, so memory-tight nodes shrink it via
    MYCELLM_MLX_PREFILL_STEP_SIZE.
    """
    try:
        from mycellm.config import get_settings

        step = int(get_settings().mlx_prefill_step_size)
    except Exception:
        step = 0
    return {"prefill_step_size": step} if step > 0 else {}


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
        # Single persistent MLX worker — see MLXVLMBackend.__init__: a thread
        # that touched MLX aborts the process when it exits (~CompilerCache in
        # TLS cleanup), so all MLX work runs on one long-lived thread.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")

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
            loop = asyncio.get_running_loop()
            model, tokenizer = await loop.run_in_executor(self._pool, _do_load)
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
        # Text backend: drop any multimodal parts down to their text. (A VLM
        # request should have routed to the mlx-vlm backend; this is defensive
        # so a text model that receives image content degrades gracefully.)
        messages = flatten_message_content(request.messages)
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
                return tokenizer.apply_chat_template(messages, **template_kwargs)
            except Exception as e:
                # Retry shedding optional kwargs one at a time — some chat
                # templates raise on unknown kwargs.
                for shed_key in ("enable_thinking", "tools"):
                    if shed_key in template_kwargs:
                        logger.warning(f"apply_chat_template with {shed_key} failed ({e}), retrying without")
                        template_kwargs.pop(shed_key, None)
                        try:
                            return tokenizer.apply_chat_template(messages, **template_kwargs)
                        except Exception as e_retry:
                            e = e_retry
                            continue
                logger.warning(f"apply_chat_template failed, falling back to concat: {e}")
        return "\n".join(
            f"{m.get('role','user')}: {m.get('content','')}" for m in messages
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
                **prefill_kwargs(),
            )

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(self._pool, _do_generate)

        prompt_tokens = len(tokenizer.encode(prompt)) if hasattr(tokenizer, "encode") else 0

        finish = "stop"
        text, _ = truncate_at_stops(text, chat_stop_strings(tokenizer, request.stop))
        # Count what the caller receives — after stop truncation, not before.
        completion_tokens = len(tokenizer.encode(text)) if hasattr(tokenizer, "encode") else 0

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
        stop_strings = chat_stop_strings(tokenizer, request.stop)
        seen_text = ""
        sent_len = 0  # chars of seen_text already yielded (stop-holdback)
        n_tokens = 0  # decode steps seen — one GenerationResponse per token
        prompt_token_count: int | None = None

        def _run_stream():
            try:
                for resp in stream_generate(
                    model, tokenizer,
                    prompt=prompt,
                    max_tokens=request.max_tokens,
                    sampler=sampler,
                    **prefill_kwargs(),
                ):
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, resp)
            except Exception as e:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, _SENTINEL)

        # Persistent MLX worker, not a per-request thread (see __init__).
        self._pool.submit(_run_stream)

        while True:
            item = await chunk_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item

            text = getattr(item, "text", "") or ""
            finish = getattr(item, "finish_reason", None)
            n_tokens += 1
            prompt_token_count = getattr(item, "prompt_tokens", None) or prompt_token_count

            if stop_strings:
                seen_text += text
                cut, hit = truncate_at_stops(seen_text, stop_strings)
                if hit:
                    # Emit only what precedes the stop and wasn't sent yet.
                    tail = cut[sent_len:]
                    yield InferenceChunk(
                        text=tail, finish_reason="stop",
                        prompt_tokens=prompt_token_count, completion_tokens=n_tokens,
                    )
                    break
                # Withhold a tail that could still become a stop string; if
                # generation ends without completing it, flush it below.
                send_to = len(seen_text) - stop_holdback_len(seen_text, stop_strings)
                delta = seen_text[sent_len:send_to] if send_to > sent_len else ""
                if finish is not None and send_to < len(seen_text):
                    delta = seen_text[sent_len:]
                    send_to = len(seen_text)
                sent_len = max(sent_len, send_to)
                if delta or finish:
                    yield InferenceChunk(
                        text=delta, finish_reason=finish,
                        prompt_tokens=prompt_token_count if finish else None,
                        completion_tokens=n_tokens if finish else None,
                    )
                continue

            if text or finish:
                yield InferenceChunk(
                    text=text, finish_reason=finish,
                    prompt_tokens=prompt_token_count if finish else None,
                    completion_tokens=n_tokens if finish else None,
                )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise EmbeddingsNotSupportedError(
            "Embeddings are not supported on the MLX backend. Use a llama.cpp "
            'GGUF embedding model (loaded with "embedding": true) or an '
            "OpenAI-compatible remote."
        )

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
