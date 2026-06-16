"""Inference manager — handles model loading, concurrency, and routing to backends."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)
from mycellm.protocol.capabilities import ModelCapability

logger = logging.getLogger("mycellm.inference")

# Backends that load model bytes into local memory (need RAM check, file-based
# progress tracking, single-threaded Lock). Remote backends (openai-compat)
# stream over HTTP and don't need any of that.
LOCAL_BACKENDS = ("llama.cpp", "mlx", "mlx-batched", "mlx-vlm")
# Local backends that manage their own internal concurrency (own the Metal
# queue via a worker thread) and so must NOT be serialized with a per-model
# Lock — they get a Semaphore so requests flow into the batcher concurrently.
SELF_BATCHING_BACKENDS = ("mlx-batched",)


def _model_size_on_disk(model_path: str) -> int:
    """Return total bytes of a model on disk.

    For llama.cpp: a single .gguf file.
    For MLX: a directory containing config.json + one or more .safetensors shards.
    """
    p = Path(model_path)
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    if p.is_dir():
        try:
            return sum(f.stat().st_size for f in p.glob("*.safetensors"))
        except OSError:
            return 0
    return 0


def _get_rss_bytes() -> int:
    """Get current process RSS in bytes. Cross-platform."""
    try:
        import os
        import platform
        if platform.system() == "Linux":
            with open(f"/proc/{os.getpid()}/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        elif platform.system() == "Darwin":
            import resource
            # ru_maxrss is in bytes on macOS
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        pass
    return 0


def _parse_vm_stat(output: str) -> int:
    """Available RAM from macOS `vm_stat` output: free+inactive+purgeable.

    macOS has no MemAvailable equivalent; free + inactive + purgeable pages
    is the standard approximation (inactive/purgeable are reclaimed under
    pressure). Parsing the text output also sidesteps Mach host_statistics
    struct layouts, which shift between macOS releases.
    """
    import re

    page_size = 16384
    match = re.search(r"page size of (\d+) bytes", output)
    if match:
        page_size = int(match.group(1))
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages purgeable"):
        match = re.search(rf"^{key}:\s+(\d+)\.", output, re.MULTILINE)
        if match:
            pages += int(match.group(1))
    return pages * page_size


def _available_ram_bytes() -> int:
    """Best-effort *available* (not total) system RAM in bytes; 0 if unknown."""
    try:
        import platform
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        elif platform.system() == "Darwin":
            import subprocess
            r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                return _parse_vm_stat(r.stdout)
    except Exception:
        pass
    return 0


def _estimate_kv_bytes(model_path: str, ctx_len: int, file_size: int) -> int:
    """Rough peak KV-cache size for the requested context length.

    For MLX model dirs, computes fp16 K+V from config.json attention
    geometry (2 tensors × layers × kv_heads × head_dim × 2 bytes per
    token). Otherwise falls back to a size-proportional heuristic
    (~100KB/token for a 4.5GB model). Preflight accuracy matters in both
    directions: too high refuses loads that would fit, too low OOMs
    mid-prefill (cf. jundot/omlx#1763).
    """
    import json

    try:
        cfg_path = Path(model_path) / "config.json"
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text())
            layers = int(cfg.get("num_hidden_layers") or 0)
            heads = int(cfg.get("num_attention_heads") or 0)
            kv_heads = int(cfg.get("num_key_value_heads") or heads)
            head_dim = int(cfg.get("head_dim") or 0)
            if not head_dim and heads:
                head_dim = int(cfg.get("hidden_size") or 0) // heads
            if layers and kv_heads and head_dim:
                return 2 * layers * kv_heads * head_dim * 2 * ctx_len
    except Exception:
        pass
    return (file_size // 45_000) * ctx_len


# --- model crash-loop guard ---------------------------------------------------
# A model that OOM-kills the process (or otherwise fails) during boot-restore
# would be retried on every restart → an unbreakable crash loop. We persist a
# per-model attempt counter that is bumped *before* each restore attempt (so it
# survives a hard kill) and cleared on success. Once a model exceeds the
# configured ceiling it is quarantined (enabled=false) so restore skips it.

def _attempts_path(data_dir: Path) -> Path:
    return Path(data_dir) / "model_load_attempts.json"


def _read_attempts(data_dir: Path) -> dict[str, int]:
    import json

    try:
        return json.loads(_attempts_path(data_dir).read_text())
    except Exception:
        return {}


def _write_attempts(data_dir: Path, attempts: dict[str, int]) -> None:
    import json

    try:
        path = _attempts_path(data_dir)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(attempts))
        tmp.rename(path)
    except Exception:
        pass


def _bump_attempt(data_dir: Path, name: str) -> int:
    attempts = _read_attempts(data_dir)
    attempts[name] = attempts.get(name, 0) + 1
    _write_attempts(data_dir, attempts)
    return attempts[name]


def _clear_attempt(data_dir: Path, name: str) -> None:
    attempts = _read_attempts(data_dir)
    if attempts.pop(name, None) is not None:
        _write_attempts(data_dir, attempts)


class InferenceManager:
    """Manages loaded models, concurrency limits, and backend routing.

    Concurrency model:
      - Global semaphore limits total active inferences across all models
      - Per-model locks serialize access to llama.cpp backends (NOT thread-safe)
      - OpenAI-compat backends get their own per-model semaphore (concurrent OK)
      - When a model is busy, requests queue with a configurable timeout
    """

    def __init__(self, max_concurrent: int = 2, queue_timeout: float = 120.0):
        self._backends: dict[str, InferenceBackend] = {}
        self._model_info: dict[str, ModelCapability] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._active_count = 0
        self._queue_timeout = queue_timeout
        # Per-model locks: llama.cpp → Lock (serialize), openai → Semaphore(4)
        self._model_locks: dict[str, asyncio.Lock | asyncio.Semaphore] = {}
        # Queue depth tracking per model
        self._queue_depth: dict[str, int] = {}
        # Persistent configs — survives unload so models can be re-loaded
        self._saved_configs: dict[str, dict] = {}  # name -> config dict
        # Models disabled by the crash-loop guard this process lifetime
        self._quarantined: set[str] = set()
        # Load status tracking
        self._load_status: dict[str, dict] = {}  # model_name -> {status, phase, error, ...}
        # Model paths (for llama.cpp models)
        self._model_paths: dict[str, str] = {}  # model_name -> file path
        # Request group tracking for batch cancellation
        self._request_groups: dict[str, set[asyncio.Event]] = {}  # group -> cancel events

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
        """Load a model and return its name.

        If model_path starts with "hf:", auto-downloads from HuggingFace first.
        Format: "hf:org/repo:filename.gguf"
        Example: "hf:TheBloke/Llama-2-7B-GGUF:llama-2-7b.Q4_K_M.gguf"
        """
        import time as _time

        # MLX continuous batching is the default: models configured with
        # backend="mlx" run on the batched engine (mlx-lm BatchGenerator) unless
        # explicitly disabled. model_configs.json stays "mlx" (portable); only
        # the runtime backend object + concurrency policy change.
        runtime_backend_type = backend_type
        if backend_type == "mlx":
            # Vision-language models can't run on the text engines (mlx-lm /
            # BatchGenerator). Detect them from config.json and route to the
            # single-stream mlx-vlm backend. Detection needs the files on disk,
            # so it only applies to already-local dirs — an "hf:" path that
            # resolves to a VLM model should be loaded with an explicit
            # backend_type="mlx-vlm".
            from mycellm.inference.mlx_vlm import is_mlx_vlm_model_path
            if model_path and not model_path.startswith("hf:") and is_mlx_vlm_model_path(model_path):
                runtime_backend_type = "mlx-vlm"
            else:
                from mycellm.config import get_settings as _gs
                if _gs().mlx_continuous_batching:
                    runtime_backend_type = "mlx-batched"

        # Auto-download from HuggingFace if path starts with "hf:"
        if model_path.startswith("hf:"):
            model_path = await self._resolve_hf_path(model_path)

        model_name = name or (Path(model_path).stem if model_path else "remote-model")

        if model_name in self._backends:
            logger.info(f"Model {model_name} already loaded")
            return model_name

        self._load_status[model_name] = {
            "model": model_name,
            "status": "loading",
            "phase": "initializing",
            "backend": backend_type,
            "started_at": _time.time(),
            "progress": 0.0,  # 0.0 - 1.0
            "eta_seconds": None,
            "size_gb": 0.0,
            "error": None,
        }

        try:
            self._load_status[model_name]["phase"] = "creating backend"
            backend = self._create_backend(runtime_backend_type)

            # Memory check for any local backend (llama.cpp or mlx).
            # Compares an estimate (weights + KV cache at the requested
            # context) against *available* RAM on both platforms — the old
            # macOS path used hw.memsize (total physical RAM), which passed
            # a 30GB load on a 64GB box with 50GB already in use.
            if backend_type in LOCAL_BACKENDS and model_path:
                file_size = _model_size_on_disk(model_path)
                if file_size > 0:
                    try:
                        from mycellm.config import get_settings as _gs_mem
                        ctx_len = int(
                            kwargs.get("ctx_len")
                            or kwargs.get("n_ctx")
                            or _gs_mem().default_ctx_len
                        )
                        kv_bytes = _estimate_kv_bytes(model_path, ctx_len, file_size)
                        est_ram_needed = int(file_size * 1.1) + kv_bytes
                        avail_ram = _available_ram_bytes()

                        if avail_ram > 0 and est_ram_needed > avail_ram * 0.85:
                            logger.warning(
                                f"Model {model_name} may exceed available RAM: "
                                f"~{est_ram_needed/1024**3:.1f}GB needed "
                                f"(weights {file_size/1024**3:.1f}GB + KV "
                                f"~{kv_bytes/1024**3:.1f}GB at ctx {ctx_len}) vs "
                                f"{avail_ram/1024**3:.1f}GB available. "
                                "Loading anyway — watch for OOM."
                            )
                            self._load_status[model_name]["phase"] = f"loading {file_size/1024**3:.1f}GB (RAM warning)"
                    except Exception:
                        pass

            if backend_type in LOCAL_BACKENDS:
                self._load_status[model_name]["phase"] = "loading model into memory"
                if model_path:
                    size_gb = _model_size_on_disk(model_path) / (1024**3)
                    if size_gb > 0:
                        self._load_status[model_name]["phase"] = f"loading {size_gb:.1f}GB into memory"
                        self._load_status[model_name]["size_gb"] = round(size_gb, 2)

                # Progress tracking — uses llama.cpp callback if available,
                # falls back to RSS-based estimation for older versions / MLX
                load_start = _time.time()
                file_bytes = _model_size_on_disk(model_path) if model_path else 0
                _has_native_progress = False

                def _progress_cb(progress: float):
                    nonlocal _has_native_progress
                    _has_native_progress = True
                    self._load_status[model_name]["progress"] = round(progress, 3)
                    elapsed = _time.time() - load_start
                    if progress > 0.01:
                        eta = (elapsed / progress) * (1.0 - progress)
                        self._load_status[model_name]["eta_seconds"] = round(eta, 1)
                    pct = int(progress * 100)
                    sz = self._load_status[model_name].get("size_gb", 0)
                    self._load_status[model_name]["phase"] = f"loading {sz:.1f}GB — {pct}%"

                kwargs["progress_callback"] = _progress_cb

                # RSS-based progress monitor (fallback when native callback not available)
                self._load_status[model_name]["_monitor"] = True
                async def _rss_progress_monitor():
                    try:
                        rss_start = _get_rss_bytes()
                        if rss_start <= 0 or file_bytes <= 0:
                            return
                        while self._load_status.get(model_name, {}).get("_monitor") and not _has_native_progress:
                            await asyncio.sleep(1.0)
                            if _has_native_progress:
                                break
                            rss_now = _get_rss_bytes()
                            rss_delta = max(0, rss_now - rss_start)
                            progress = min(0.99, rss_delta / file_bytes)
                            self._load_status[model_name]["progress"] = round(progress, 3)
                            elapsed = _time.time() - load_start
                            if progress > 0.02:
                                eta = (elapsed / progress) * (1.0 - progress)
                                self._load_status[model_name]["eta_seconds"] = round(eta, 1)
                            pct = int(progress * 100)
                            sz = self._load_status[model_name].get("size_gb", 0)
                            self._load_status[model_name]["phase"] = f"loading {sz:.1f}GB — {pct}%"
                    except (asyncio.CancelledError, Exception):
                        pass

                rss_task = asyncio.ensure_future(_rss_progress_monitor())
            else:
                self._load_status[model_name]["phase"] = "connecting to API"

            # Inject inference settings from config if not explicitly set
            if backend_type == "llama.cpp" and "flash_attn" not in kwargs:
                from mycellm.config import get_settings
                s = get_settings()
                kwargs.setdefault("flash_attn", s.flash_attn)
                kwargs.setdefault("kv_cache_quant", s.kv_cache_quant)
                kwargs.setdefault("kv_cache_quant_k", s.kv_cache_quant_k)
                kwargs.setdefault("kv_cache_quant_v", s.kv_cache_quant_v)
                kwargs.setdefault("prompt_lookup", s.prompt_lookup)
                kwargs.setdefault("n_threads", s.n_threads)
                kwargs.setdefault("draft_model_path", s.draft_model_path)
                kwargs.setdefault("draft_pred_tokens", s.draft_pred_tokens)

            await backend.load_model(model_path, name=model_name, **kwargs)

            # Stop RSS monitor if it was running
            if backend_type in LOCAL_BACKENDS:
                self._load_status[model_name]["_monitor"] = False
                rss_task.cancel()

            self._load_status[model_name]["progress"] = 1.0
            self._load_status[model_name]["eta_seconds"] = 0
            self._load_status[model_name]["phase"] = "registering"
            if model_path:
                self._model_paths[model_name] = model_path
            self._backends[model_name] = backend
            # Per-model concurrency control:
            # - llama.cpp / plain mlx: Lock (1 concurrent) — the C context /
            #   single Metal queue are NOT safe for parallel forward passes.
            # - mlx-batched: Semaphore — the backend batches internally on its
            #   own worker thread, so requests must be allowed in concurrently.
            # - Remote/relay: configurable via max_concurrent kwarg, default 32
            #   The remote server handles its own backpressure via HTTP 429/503
            if backend_type in LOCAL_BACKENDS and runtime_backend_type not in SELF_BATCHING_BACKENDS:
                # Local model objects (llama.cpp Llama / MLX model+tokenizer)
                # are not safe for parallel forward passes — serialize.
                self._model_locks[model_name] = asyncio.Lock()
            else:
                max_c = kwargs.get("max_concurrent", 32)
                self._model_locks[model_name] = asyncio.Semaphore(max_c)
            self._queue_depth[model_name] = 0
            from mycellm.config import get_settings as _gs
            _default_ctx = _gs().default_ctx_len
            self._model_info[model_name] = ModelCapability(
                name=model_name,
                quant=kwargs.get("quant", ""),
                ctx_len=kwargs.get("ctx_len", kwargs.get("n_ctx", _default_ctx)),
                backend=backend_type,
                loaded_bytes=_model_size_on_disk(model_path) if model_path else 0,
            )

            elapsed = _time.time() - self._load_status[model_name]["started_at"]
            self._load_status[model_name].update({"status": "ready", "phase": "loaded", "elapsed": round(elapsed, 1)})
            logger.info(f"Model {model_name} loaded via {backend_type} ({elapsed:.1f}s)")

            # Auto-save config
            try:
                from mycellm.config import get_settings
                _data_dir = get_settings().data_dir
                await self.save_model_configs(_data_dir)
                # A successful load clears the crash-loop attempt counter (covers
                # both boot-restore and manual re-loads of a fixed/quarantined model).
                _clear_attempt(_data_dir, model_name)
                self._quarantined.discard(model_name)
            except Exception:
                pass

            return model_name

        except Exception as e:
            # Clean up RSS monitor on failure
            if model_name in self._load_status:
                self._load_status[model_name]["_monitor"] = False
            try:
                rss_task.cancel()
            except (NameError, Exception):
                pass
            self._load_status[model_name].update({"status": "failed", "phase": "error", "error": str(e)})
            logger.error(f"Failed to load {model_name}: {e}")
            raise

    async def unload_model(self, model_name: str) -> None:
        backend = self._backends.pop(model_name, None)
        if backend:
            await backend.unload_model(model_name)
            self._model_info.pop(model_name, None)
            self._model_locks.pop(model_name, None)
            self._queue_depth.pop(model_name, None)
            logger.info(f"Model {model_name} unloaded")

            # Mark as disabled in saved configs (don't delete — allows re-enable)
            if model_name in self._saved_configs:
                self._saved_configs[model_name]["enabled"] = False

            # Auto-save config
            try:
                from mycellm.config import get_settings
                await self.save_model_configs(get_settings().data_dir)
            except Exception:
                pass

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

    @property
    def queue_status(self) -> dict[str, int]:
        """Get current queue depth per model."""
        return dict(self._queue_depth)

    async def _acquire_model(self, model_name: str) -> None:
        """Acquire the per-model lock with timeout. Raises if queue full or timeout."""
        lock = self._model_locks.get(model_name)
        if not lock:
            return  # no lock (shouldn't happen)

        self._queue_depth[model_name] = self._queue_depth.get(model_name, 0) + 1
        try:
            if isinstance(lock, asyncio.Lock):
                # Local backend (llama.cpp / mlx): serialize. If locked, we're queued.
                if lock.locked():
                    logger.info(f"Model {model_name} busy — request queued (depth={self._queue_depth[model_name]})")
                await asyncio.wait_for(lock.acquire(), timeout=self._queue_timeout)
            else:
                # Semaphore for remote backends
                await asyncio.wait_for(lock.acquire(), timeout=self._queue_timeout)
        except asyncio.TimeoutError:
            self._queue_depth[model_name] = max(0, self._queue_depth.get(model_name, 1) - 1)
            raise RuntimeError(f"Model {model_name} is busy. Request timed out after {self._queue_timeout}s in queue.")

    def _release_model(self, model_name: str) -> None:
        """Release the per-model lock."""
        lock = self._model_locks.get(model_name)
        if lock:
            lock.release()
        self._queue_depth[model_name] = max(0, self._queue_depth.get(model_name, 1) - 1)

    async def cancel_group(self, group: str) -> int:
        """Cancel all pending/active requests in a group. Returns count cancelled."""
        events = self._request_groups.pop(group, set())
        for event in events:
            event.set()
        return len(events)

    def _register_request_group(self, request: InferenceRequest) -> asyncio.Event | None:
        """Register a request's cancel event in its group. Returns the event."""
        if not request.request_group:
            return None
        cancel_event = asyncio.Event()
        self._request_groups.setdefault(request.request_group, set()).add(cancel_event)
        return cancel_event

    def _unregister_request_group(self, request: InferenceRequest, event: asyncio.Event | None):
        """Remove a request's cancel event from its group."""
        if event and request.request_group and request.request_group in self._request_groups:
            self._request_groups[request.request_group].discard(event)
            if not self._request_groups[request.request_group]:
                del self._request_groups[request.request_group]

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        """Run inference with per-model locking and global concurrency control."""
        model_name = self.resolve_model_name(request.model)
        if not model_name:
            raise RuntimeError("No models loaded")

        request.model = model_name
        backend = self._backends[model_name]
        cancel_event = self._register_request_group(request)

        # Speculative requests use shorter timeout to avoid blocking real work
        orig_timeout = self._queue_timeout
        if request.priority == "speculative":
            self._queue_timeout = min(10.0, orig_timeout)

        try:
            await self._acquire_model(model_name)
        except RuntimeError:
            self._unregister_request_group(request, cancel_event)
            raise
        finally:
            self._queue_timeout = orig_timeout

        self._active_count += 1
        try:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Request cancelled (group cancelled)")
            return await backend.generate(request)
        finally:
            self._active_count -= 1
            self._release_model(model_name)
            self._unregister_request_group(request, cancel_event)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Run embeddings with per-model locking and global concurrency control.

        Resolves the model the same way generate() does and queues on the same
        per-model Lock/Semaphore — embeddings share the backend's context with
        generation, so they must not run a forward pass concurrently with it.
        Backends that can't embed raise EmbeddingsNotSupportedError (the API
        layer maps it to a 400).
        """
        model_name = self.resolve_model_name(request.model)
        if not model_name:
            raise RuntimeError("No models loaded")

        request.model = model_name
        backend = self._backends[model_name]

        await self._acquire_model(model_name)
        self._active_count += 1
        try:
            return await backend.embed(request)
        finally:
            self._active_count -= 1
            self._release_model(model_name)

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        """Run streaming inference with per-model locking and global concurrency control."""
        model_name = self.resolve_model_name(request.model)
        if not model_name:
            raise RuntimeError("No models loaded")

        request.model = model_name
        backend = self._backends[model_name]

        await self._acquire_model(model_name)
        self._active_count += 1
        try:
            async for chunk in backend.generate_stream(request):
                yield chunk
        finally:
            self._active_count -= 1
            self._release_model(model_name)

    async def save_model_configs(self, data_dir: Path) -> None:
        """Save model configs to disk. Preserves unloaded API model configs."""
        import json

        # Update saved configs from currently loaded models
        for name, info in self._model_info.items():
            backend = self._backends.get(name)
            config = {
                "name": name,
                "backend": info.backend,
                "ctx_len": info.ctx_len,
                "quant": info.quant,
                "tags": getattr(info, 'tags', []),
                "tier": getattr(info, 'tier', ''),
                "param_count_b": getattr(info, 'param_count_b', 0.0),
                "enabled": True,
            }
            # Store model path for llama.cpp restore
            if name in self._model_paths:
                config["model_path"] = self._model_paths[name]
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            if isinstance(backend, OpenAICompatibleBackend):
                remote = backend._models.get(name)
                if remote:
                    config["api_base"] = remote.api_base
                    config["api_model"] = remote.api_model
                    auth = remote.client.headers.get("authorization", "")
                    if auth.startswith("Bearer "):
                        config["api_key"] = auth[7:]
            self._saved_configs[name] = config

        configs = list(self._saved_configs.values())
        config_path = data_dir / "model_configs.json"
        tmp = config_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(configs, indent=2))
        tmp.rename(config_path)  # atomic on POSIX
        logger.debug(f"Saved {len(configs)} model configs to {config_path}")

    async def restore_models(self, data_dir: Path) -> int:
        """Restore models from saved config. Returns count of restored models."""
        import json
        config_path = data_dir / "model_configs.json"
        if not config_path.exists():
            return 0

        try:
            configs = json.loads(config_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to read model configs: {e}")
            return 0

        # Load all configs into saved_configs (including disabled)
        for config in configs:
            name = config.get("name", "")
            if name:
                self._saved_configs[name] = config

        # Only auto-load enabled configs
        from mycellm.config import get_settings as _gs
        _settings = _gs()
        _default_ctx = _settings.default_ctx_len
        max_attempts = int(getattr(_settings, "model_max_restore_attempts", 3) or 0)
        restored = 0
        for config in configs:
            name = config.get("name", "")
            if not config.get("enabled", True):
                logger.debug(f"Skipping disabled model '{name}'")
                continue

            # Crash-loop guard. The attempt counter is bumped *before* each load
            # (so an OOM that hard-kills the process still counts) and cleared on
            # success inside load_model(). Once a model exceeds the ceiling it is
            # quarantined (enabled=false) so restore stops reloading it.
            if max_attempts > 0:
                if _read_attempts(data_dir).get(name, 0) >= max_attempts:
                    logger.error(
                        f"Quarantining model '{name}' after >={max_attempts} failed "
                        f"load attempts — disabling so it stops crash-looping. "
                        f"Re-enable by loading it again once the cause is fixed."
                    )
                    config["enabled"] = False
                    self._saved_configs[name] = config
                    try:
                        await self.save_model_configs(data_dir)
                    except Exception:
                        pass
                    _clear_attempt(data_dir, name)
                    self._quarantined.add(name)
                    continue
                _bump_attempt(data_dir, name)

            backend_type = config.get("backend", "llama.cpp")
            try:
                if backend_type in ("openai", "openai-compatible"):
                    await self.load_model(
                        "",
                        name=name,
                        backend_type=backend_type,
                        api_base=config.get("api_base", ""),
                        api_key=config.get("api_key", ""),
                        api_model=config.get("api_model", ""),
                        ctx_len=config.get("ctx_len", _default_ctx),
                    )
                elif config.get("model_path"):
                    model_path = config["model_path"]
                    # MLX (text + vision) accepts an HF repo id (e.g.
                    # "mlx-community/foo") which won't exist as a local path —
                    # let the backend resolve/download it. llama.cpp models must
                    # be a real local file.
                    if backend_type in ("mlx", "mlx-vlm") or Path(model_path).exists():
                        await self.load_model(
                            model_path,
                            name=name,
                            backend_type=backend_type,
                            ctx_len=config.get("ctx_len", _default_ctx),
                            quant=config.get("quant", ""),
                        )
                    else:
                        logger.warning(f"Model file missing for '{name}': {model_path}")
                        continue
                else:
                    logger.debug(f"Skipping local model restore for '{name}' (path not stored)")
                    continue
                restored += 1
                logger.info(f"Restored model '{name}' ({backend_type})")
            except Exception as e:
                logger.warning(f"Failed to restore model '{name}': {e}")

        return restored

    def get_saved_configs(self) -> list[dict]:
        """Get all saved model configs (loaded + unloaded)."""
        return list(self._saved_configs.values())

    async def remove_saved_config(self, model_name: str, data_dir: Path) -> None:
        """Permanently remove a saved config (on delete)."""
        self._saved_configs.pop(model_name, None)
        await self.save_model_configs(data_dir)

    async def _resolve_hf_path(self, hf_spec: str) -> str:
        """Resolve an hf: path to a local file, downloading if needed.

        Format: "hf:org/repo:filename.gguf"
        Returns the local file path after download.
        """
        import httpx

        spec = hf_spec[3:]  # strip "hf:"
        parts = spec.split(":", 1)
        if len(parts) != 2 or "/" not in parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid hf: path format. Expected 'hf:org/repo:filename.gguf', got '{hf_spec}'"
            )

        repo_id = parts[0]
        filename = parts[1]

        from mycellm.config import get_settings
        settings = get_settings()
        model_dir = settings.model_dir or settings.data_dir / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        dest_path = model_dir / filename

        if dest_path.exists():
            logger.info(f"HF model already downloaded: {dest_path}")
            return str(dest_path)

        logger.info(f"Downloading {filename} from HuggingFace ({repo_id})...")
        url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
        headers = {}
        if settings.hf_token:
            headers["Authorization"] = f"Bearer {settings.hf_token}"

        tmp_path = dest_path.with_suffix(".tmp")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, read=3600.0),
                follow_redirects=True,
                headers=headers,
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    last_log = 0.0
                    import time as _t

                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = _t.time()
                            if now - last_log >= 5.0:
                                pct = (downloaded / total * 100) if total > 0 else 0
                                logger.info(
                                    f"Downloading {filename}: {downloaded / 1024**3:.1f}GB"
                                    f" / {total / 1024**3:.1f}GB ({pct:.0f}%)"
                                )
                                last_log = now

            tmp_path.rename(dest_path)
            logger.info(f"Downloaded {filename} ({downloaded / 1024**3:.1f}GB) to {dest_path}")
            return str(dest_path)

        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _create_backend(self, backend_type: str) -> InferenceBackend:
        if backend_type == "llama.cpp":
            from mycellm.inference.llamacpp import LlamaCppBackend
            return LlamaCppBackend()
        if backend_type == "mlx":
            from mycellm.inference.mlx import MLXBackend
            return MLXBackend()
        if backend_type == "mlx-batched":
            from mycellm.inference.mlx_batched import BatchedMLXBackend
            return BatchedMLXBackend()
        if backend_type == "mlx-vlm":
            from mycellm.inference.mlx_vlm import MLXVLMBackend
            return MLXVLMBackend()
        if backend_type in ("openai", "openai-compatible"):
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            return OpenAICompatibleBackend()
        raise ValueError(f"Unknown backend type: {backend_type}")
