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
        # Persistent configs — survives unload so models can be re-loaded
        self._saved_configs: dict[str, dict] = {}  # name -> config dict
        # Load status tracking
        self._load_status: dict[str, dict] = {}  # model_name -> {status, phase, error, ...}
        # Model paths (for llama.cpp models)
        self._model_paths: dict[str, str] = {}  # model_name -> file path

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
        import time as _time
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
            "error": None,
        }

        try:
            self._load_status[model_name]["phase"] = "creating backend"
            backend = self._create_backend(backend_type)

            # Memory check for llama.cpp models
            if backend_type == "llama.cpp" and model_path:
                file_size = Path(model_path).stat().st_size if Path(model_path).exists() else 0
                if file_size > 0:
                    est_ram_needed = file_size * 1.2  # model + KV cache overhead
                    try:
                        import platform as _platform
                        avail_ram = 0
                        if _platform.system() == "Linux":
                            with open("/proc/meminfo") as f:
                                for line in f:
                                    if line.startswith("MemAvailable:"):
                                        avail_ram = int(line.split()[1]) * 1024
                                        break
                        elif _platform.system() == "Darwin":
                            import subprocess
                            r = subprocess.run(
                                ["sysctl", "-n", "hw.memsize"],
                                capture_output=True, text=True, timeout=3,
                            )
                            if r.returncode == 0:
                                avail_ram = int(r.stdout.strip())

                        if avail_ram > 0 and est_ram_needed > avail_ram * 0.85:
                            logger.warning(
                                f"Model {model_name} ({file_size/1024**3:.1f}GB) may exceed available RAM "
                                f"({avail_ram/1024**3:.1f}GB). Loading anyway — watch for OOM."
                            )
                            self._load_status[model_name]["phase"] = f"loading {file_size/1024**3:.1f}GB (RAM warning)"
                    except Exception:
                        pass

            if backend_type == "llama.cpp":
                self._load_status[model_name]["phase"] = "loading model into memory"
                if model_path:
                    size_gb = Path(model_path).stat().st_size / (1024**3) if Path(model_path).exists() else 0
                    if size_gb > 0:
                        self._load_status[model_name]["phase"] = f"loading {size_gb:.1f}GB into memory"
            else:
                self._load_status[model_name]["phase"] = "connecting to API"

            await backend.load_model(model_path, name=model_name, **kwargs)

            self._load_status[model_name]["phase"] = "registering"
            if model_path:
                self._model_paths[model_name] = model_path
            self._backends[model_name] = backend
            self._model_info[model_name] = ModelCapability(
                name=model_name,
                quant=kwargs.get("quant", ""),
                ctx_len=kwargs.get("ctx_len", kwargs.get("n_ctx", 4096)),
                backend=backend_type,
            )

            elapsed = _time.time() - self._load_status[model_name]["started_at"]
            self._load_status[model_name].update({"status": "ready", "phase": "loaded", "elapsed": round(elapsed, 1)})
            logger.info(f"Model {model_name} loaded via {backend_type} ({elapsed:.1f}s)")

            # Auto-save config
            try:
                from mycellm.config import get_settings
                await self.save_model_configs(get_settings().data_dir)
            except Exception:
                pass

            return model_name

        except Exception as e:
            self._load_status[model_name].update({"status": "failed", "phase": "error", "error": str(e)})
            logger.error(f"Failed to load {model_name}: {e}")
            raise

    async def unload_model(self, model_name: str) -> None:
        backend = self._backends.pop(model_name, None)
        if backend:
            await backend.unload_model(model_name)
            self._model_info.pop(model_name, None)
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
        restored = 0
        for config in configs:
            name = config.get("name", "")
            if not config.get("enabled", True):
                logger.debug(f"Skipping disabled model '{name}'")
                continue
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
                        ctx_len=config.get("ctx_len", 4096),
                    )
                elif config.get("model_path"):
                    model_path = config["model_path"]
                    if Path(model_path).exists():
                        await self.load_model(
                            model_path,
                            name=name,
                            backend_type=backend_type,
                            ctx_len=config.get("ctx_len", 4096),
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

    def _create_backend(self, backend_type: str) -> InferenceBackend:
        if backend_type == "llama.cpp":
            from mycellm.inference.llamacpp import LlamaCppBackend
            return LlamaCppBackend()
        if backend_type in ("openai", "openai-compatible"):
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            return OpenAICompatibleBackend()
        raise ValueError(f"Unknown backend type: {backend_type}")
