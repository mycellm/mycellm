"""MLX vision-language (multimodal) backend for Apple Silicon.

The text MLX backends (``mlx.py`` / ``mlx_batched.py``) wrap ``mlx-lm``, which
is text-only. Vision/audio models use a separate package, ``mlx-vlm``, with a
different load path (it returns a *processor*, not just a tokenizer) and a
generate signature that takes image inputs alongside the prompt.

This backend is deliberately **single-stream** (one request per model at a
time, serialized by the InferenceManager's per-model Lock — same policy as the
plain ``mlx`` backend). ``mlx-vlm`` has no continuous-batching ``BatchGenerator``
equivalent, so there is no batched VLM path to fall back on; this is also why
the single-row decode efficiency in ``mlx_batched.py`` matters for the VLM
roadmap — solo decode *is* the VLM hot path.

Input format: this backend consumes OpenAI-style multimodal chat content —
``content`` may be a plain string or a list of parts::

    {"role": "user", "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ]}

NOTE (API plumbing): ``api/openai.py``'s ``ChatMessage.content`` is currently
typed ``Optional[str]``, so the HTTP layer rejects list content before it
reaches here. Widening that Pydantic model (and the CBOR node wire format) to
accept content-part arrays is the follow-up that makes this backend reachable
end-to-end. The backend is written to the target shape so that widening is the
only remaining step.

Model format: a directory containing ``config.json`` (with a ``vision_config``
or an image-token index) + safetensors — the layout used by
``mlx-community/*-VL-*`` repos. Use :func:`is_mlx_vlm_model_path` to detect.

mlx-vlm API version sensitivity: the ``generate`` / ``stream_generate`` /
``apply_chat_template`` signatures have shifted across mlx-vlm releases. The
call sites below are marked `# mlx-vlm API` and isolated in small adapters so a
version bump touches one place. Pin tested in pyproject: ``mlx-vlm>=0.1.0``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import platform
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator

from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingsNotSupportedError,
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)
from mycellm.inference.mlx import chat_stop_strings, truncate_at_stops

logger = logging.getLogger("mycellm.inference")


def _require_apple_silicon() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError(
            "MLX-VLM backend requires Apple Silicon (macOS arm64). "
            "Use backend='llama.cpp' on this platform."
        )


# config.json signals that a safetensors dir is a vision-language model.
# A top-level/nested `vision_config` is the strongest signal; the image-token
# keys cover models that inline the vision tower config. model_type is a
# fallback for families that don't expose either (list is non-exhaustive —
# extend as new VLM families land in mlx-community).
_VLM_CONFIG_KEYS = ("vision_config", "image_token_index", "image_token_id")
_VLM_MODEL_TYPES = {
    "llava", "llava_next", "qwen2_vl", "qwen2_5_vl", "idefics2", "idefics3",
    "paligemma", "pixtral", "mllama", "phi3_v", "multi_modality",
    "internvl_chat", "smolvlm", "gemma3", "gemma3n",
}


def is_mlx_vlm_model_path(path: str) -> bool:
    """True if ``path`` looks like an MLX vision-language model directory.

    Reads ``config.json`` and looks for a vision tower config / image-token
    index, or a known multimodal ``model_type``. Returns False for plain text
    MLX models (which route to the ``mlx`` / ``mlx-batched`` backends).
    """
    p = Path(path)
    cfg = p / "config.json"
    if not p.is_dir() or not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return False
    if any(k in data for k in _VLM_CONFIG_KEYS):
        return True
    # Some configs nest the vision tower under text/vision sub-configs.
    if isinstance(data.get("text_config"), dict) and "vision_config" in data:
        return True
    return str(data.get("model_type", "")).lower() in _VLM_MODEL_TYPES


def _data_uri_to_image(url: str):
    """Decode a ``data:image/...;base64,<data>`` URI to a PIL Image."""
    from PIL import Image  # mlx-vlm depends on Pillow
    import io

    try:
        header, b64 = url.split(",", 1)
    except ValueError as e:
        raise ValueError("Malformed data URI for image") from e
    if ";base64" not in header:
        raise ValueError("Only base64-encoded data URIs are supported")
    try:
        raw = base64.b64decode(b64, validate=True)
    except binascii.Error as e:
        raise ValueError("Invalid base64 image data") from e
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _resolve_image(spec: Any):
    """Resolve one OpenAI image content part to something mlx-vlm accepts.

    Returns either a URL/path string (http(s):// or local file — mlx-vlm's
    loader fetches/opens these) or a PIL Image (for inlined data: URIs).
    """
    # OpenAI shape: {"type":"image_url","image_url":{"url": "..."}}
    url = None
    if isinstance(spec, dict):
        iu = spec.get("image_url")
        if isinstance(iu, dict):
            url = iu.get("url")
        elif isinstance(iu, str):
            url = iu
        elif isinstance(spec.get("url"), str):
            url = spec["url"]
    elif isinstance(spec, str):
        url = spec
    if not url:
        raise ValueError(f"Unsupported image content part: {spec!r}")
    if url.startswith("data:"):
        return _data_uri_to_image(url)
    # http(s) URL or local path — handed straight to mlx-vlm's image loader.
    return url


def _split_content(messages: list[dict]) -> tuple[list[dict], list[Any]]:
    """Split OpenAI multimodal messages into (text-only messages, images).

    Image parts are pulled out (their positional placeholders are re-inserted
    by the processor's chat template, keyed off image count). Each message's
    text parts are concatenated back into a plain string so the result is the
    ``[{"role","content": str}]`` shape mlx-vlm's ``apply_chat_template`` wants.
    """
    text_messages: list[dict] = []
    images: list[Any] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            text_messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            text_messages.append({"role": role, "content": str(content)})
            continue
        text_parts: list[str] = []
        for part in content:
            ptype = part.get("type") if isinstance(part, dict) else None
            if ptype == "text":
                text_parts.append(part.get("text", ""))
            elif ptype in ("image_url", "image", "input_image"):
                images.append(_resolve_image(part))
            else:
                # Unknown part type (e.g. future audio) — skip for now; log so
                # we notice when a new modality starts arriving.
                logger.debug(f"mlx-vlm: skipping unsupported content part type {ptype!r}")
        text_messages.append({"role": role, "content": "".join(text_parts)})
    return text_messages, images


class MLXVLMBackend(InferenceBackend):
    """Single-stream vision-language backend wrapping ``mlx-vlm``."""

    def __init__(self):
        # name -> (model, processor, config)
        self._models: dict[str, tuple[object, object, dict]] = {}
        self._paths: dict[str, str] = {}
        # All MLX work (load + generate + stream) runs on ONE persistent worker
        # thread. MLX keeps a thread-local CompilerCache whose destructor runs at
        # pthread exit; tearing down a thread that touched MLX aborts the whole
        # process (SIGTRAP in ~CompilerCache during TLS cleanup). A single
        # long-lived thread never exits during operation, so it never fires —
        # this is what stopped the ~hourly crash loop on the VLM node.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-vlm")

    # ---- lifecycle -----------------------------------------------------

    async def load_model(self, model_path: str, **kwargs) -> None:
        _require_apple_silicon()
        from mlx_vlm import load as vlm_load  # mlx-vlm API
        from mlx_vlm.utils import load_config  # mlx-vlm API

        model_name = kwargs.get("name") or Path(model_path).name
        progress_callback = kwargs.get("progress_callback")

        target = model_path
        if Path(model_path).is_dir():
            target = str(Path(model_path).resolve())

        logger.info(f"Loading MLX-VLM model {model_name} from {target}")
        if progress_callback:
            try:
                progress_callback(0.0)
            except Exception:
                pass

        def _do_load():
            model, processor = vlm_load(target)
            try:
                config = load_config(target)
            except Exception:
                config = {}
            return model, processor, config

        try:
            loop = asyncio.get_running_loop()
            model, processor, config = await loop.run_in_executor(self._pool, _do_load)
        except Exception as e:
            err = str(e)
            if "Model type" in err and "not supported" in err:
                raise RuntimeError(
                    f"Failed to load {model_name}: {err}. Try: pip install --upgrade mlx-vlm"
                ) from e
            raise

        self._models[model_name] = (model, processor, config)
        self._paths[model_name] = target

        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass
        logger.info(f"MLX-VLM model {model_name} loaded")

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
            logger.info(f"MLX-VLM model {model_name} unloaded")

    def _resolve_model(self, model_name: str):
        name = model_name or next(iter(self._models), "")
        if not name or name not in self._models:
            raise RuntimeError(f"Model '{name}' not loaded")
        return self._models[name]

    # ---- prompt building ----------------------------------------------

    def _build(self, processor, config: dict, request: InferenceRequest):
        """Return (formatted_prompt, images) for an mlx-vlm generate call."""
        from mlx_vlm.prompt_utils import apply_chat_template  # mlx-vlm API

        text_messages, images = _split_content(request.messages)
        # apply_chat_template inserts the right number of image placeholders
        # for this model family given num_images, and renders the model's own
        # chat template (so we get correct special tokens, unlike the manual
        # ChatML in the iOS backend).
        formatted = apply_chat_template(
            processor, config, text_messages, num_images=len(images)
        )
        return formatted, images

    # ---- generation ----------------------------------------------------

    def _gen_kwargs(self, request: InferenceRequest) -> dict:
        # mlx-vlm's generate takes temperature/top_p as bare kwargs (not an
        # mlx-lm sampler object). Keep this in one place — it's the part most
        # likely to drift across mlx-vlm versions.
        return {
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        model, processor, config = self._resolve_model(request.model)
        prompt, images = self._build(processor, config, request)

        from mlx_vlm import generate as vlm_generate  # mlx-vlm API

        def _do_generate():
            # mlx-vlm API: generate(model, processor, prompt, image=[...], ...).
            # Returns a GenerationResult (.text) on recent versions, or a bare
            # str on older ones — normalize below.
            return vlm_generate(
                model, processor, prompt,
                image=images,
                verbose=False,
                **self._gen_kwargs(request),
            )

        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(self._pool, _do_generate)
        text = getattr(out, "text", None)
        if text is None:
            text = out if isinstance(out, str) else str(out)

        finish = "stop"
        tok = getattr(processor, "tokenizer", None) or processor
        text, _ = truncate_at_stops(text, chat_stop_strings(tok, request.stop))

        prompt_tokens, completion_tokens = self._count_tokens(processor, prompt, text)
        return InferenceResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish,
        )

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        model, processor, config = self._resolve_model(request.model)
        prompt, images = self._build(processor, config, request)

        from mlx_vlm import stream_generate as vlm_stream  # mlx-vlm API

        loop = asyncio.get_running_loop()
        chunk_queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()
        tok = getattr(processor, "tokenizer", None) or processor
        stop_strings = chat_stop_strings(tok, request.stop)
        seen_text = ""

        def _run_stream():
            try:
                for resp in vlm_stream(
                    model, processor, prompt,
                    image=images,
                    **self._gen_kwargs(request),
                ):
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, resp)
            except Exception as e:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, _SENTINEL)

        # Run on the persistent MLX worker, not a fresh per-request thread — a
        # thread exiting after each stream is what aborted the process (see
        # __init__). submit() reuses the single long-lived worker.
        self._pool.submit(_run_stream)

        while True:
            item = await chunk_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item

            # mlx-vlm stream chunks are objects with .text on recent versions,
            # bare strings on older ones.
            text = getattr(item, "text", None)
            if text is None:
                text = item if isinstance(item, str) else ""
            finish = getattr(item, "finish_reason", None)

            if stop_strings:
                seen_text += text
                cut, hit = truncate_at_stops(seen_text, stop_strings)
                if hit:
                    tail = cut[len(seen_text) - len(text):]
                    yield InferenceChunk(text=tail, finish_reason="stop")
                    break

            if text or finish:
                yield InferenceChunk(text=text, finish_reason=finish)

    # ---- introspection -------------------------------------------------

    def _count_tokens(self, processor, prompt: str, text: str) -> tuple[int, int]:
        tok = getattr(processor, "tokenizer", None) or processor
        enc = getattr(tok, "encode", None)
        if not callable(enc):
            return 0, 0
        try:
            return len(enc(prompt)), len(enc(text))
        except Exception:
            return 0, 0

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise EmbeddingsNotSupportedError(
            "Embeddings are not supported on the MLX-VLM backend. Use a "
            'llama.cpp GGUF embedding model (loaded with "embedding": true) or '
            "an OpenAI-compatible remote."
        )

    def get_loaded_models(self) -> list[str]:
        return list(self._models.keys())

    def get_capabilities(self) -> dict:
        info = {"backend": "mlx-vlm", "multimodal": True, "modalities": ["text", "image"]}
        try:
            import mlx.core as mx
            info["device"] = str(mx.default_device())
        except Exception:
            info["info"] = "mlx not importable"
        try:
            import mlx_vlm
            info["mlx_vlm_version"] = getattr(mlx_vlm, "__version__", "unknown")
        except Exception:
            pass
        return info
