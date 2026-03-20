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

        # Auto-save config
        try:
            from mycellm.config import get_settings
            await self.save_model_configs(get_settings().data_dir)
        except Exception:
            pass  # don't block load on save failure

        return model_name

    async def unload_model(self, model_name: str) -> None:
        backend = self._backends.pop(model_name, None)
        if backend:
            await backend.unload_model(model_name)
            self._model_info.pop(model_name, None)
            logger.info(f"Model {model_name} unloaded")

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
        """Save current model configs to disk for persistence across restarts."""
        import json
        configs = []
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
            }
            # Save backend-specific config
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            if isinstance(backend, OpenAICompatibleBackend):
                remote = backend._models.get(name)
                if remote:
                    config["api_base"] = remote.api_base
                    config["api_model"] = remote.api_model
                    # Extract API key from client headers
                    auth = remote.client.headers.get("authorization", "")
                    if auth.startswith("Bearer "):
                        config["api_key"] = auth[7:]
            configs.append(config)

        config_path = data_dir / "model_configs.json"
        config_path.write_text(json.dumps(configs, indent=2))
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

        restored = 0
        for config in configs:
            name = config.get("name", "")
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
                else:
                    # For local models, we'd need the path — skip if not available
                    logger.info(f"Skipping local model restore for '{name}' (path not stored)")
                    continue
                restored += 1
                logger.info(f"Restored model '{name}' ({backend_type})")
            except Exception as e:
                logger.warning(f"Failed to restore model '{name}': {e}")

        return restored

    def _create_backend(self, backend_type: str) -> InferenceBackend:
        if backend_type == "llama.cpp":
            from mycellm.inference.llamacpp import LlamaCppBackend
            return LlamaCppBackend()
        if backend_type in ("openai", "openai-compatible"):
            from mycellm.inference.openai_compat import OpenAICompatibleBackend
            return OpenAICompatibleBackend()
        raise ValueError(f"Unknown backend type: {backend_type}")
