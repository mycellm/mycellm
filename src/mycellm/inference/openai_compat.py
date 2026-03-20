"""OpenAI-compatible API backend for remote inference providers.

Supports any provider with an OpenAI-compatible chat completions API:
OpenRouter, OpenAI, Together, Groq, local vLLM/Ollama, etc.

Usage via API:
    POST /v1/node/models/load
    {
        "name": "claude-sonnet",
        "backend": "openai",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "sk-or-...",
        "api_model": "anthropic/claude-sonnet-4"
    }

The model appears on the network as "claude-sonnet" — peers don't know
or care that it's backed by a remote API.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from mycellm.inference.base import (
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)

logger = logging.getLogger("mycellm.inference")


class OpenAICompatibleBackend(InferenceBackend):
    """Inference backend that proxies to any OpenAI-compatible API."""

    def __init__(self):
        self._models: dict[str, _RemoteModel] = {}

    async def load_model(self, model_path: str, **kwargs) -> None:
        """Register a remote model.

        Args:
            model_path: ignored (kept for interface compat) — use kwargs instead
            **kwargs:
                name: local name for this model on the network
                api_base: base URL (e.g. https://openrouter.ai/api/v1)
                api_key: bearer token
                api_model: upstream model ID (e.g. anthropic/claude-sonnet-4)
                ctx_len: context length to advertise (default 4096)
                timeout: request timeout in seconds (default 120)
        """
        model_name = kwargs.get("name", "remote-model")
        api_base = kwargs.get("api_base", "")
        api_key = kwargs.get("api_key", "")
        api_model = kwargs.get("api_model", model_name)
        timeout = kwargs.get("timeout", 120)

        if not api_base:
            raise ValueError("api_base is required for openai backend")

        # Normalize base URL
        api_base = api_base.rstrip("/")

        # Validate connectivity
        client = httpx.AsyncClient(
            base_url=api_base,
            headers=_build_headers(api_key),
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        try:
            resp = await client.get("/models")
            if resp.status_code == 401:
                await client.aclose()
                raise ValueError("API key rejected (401)")
            logger.info(f"Connected to {api_base} (status={resp.status_code})")
        except httpx.ConnectError as e:
            await client.aclose()
            raise ValueError(f"Cannot reach {api_base}: {e}") from e

        self._models[model_name] = _RemoteModel(
            name=model_name,
            api_model=api_model,
            api_base=api_base,
            client=client,
        )
        logger.info(f"Remote model '{model_name}' registered (upstream={api_model} via {api_base})")

    async def unload_model(self, model_name: str) -> None:
        model = self._models.pop(model_name, None)
        if model:
            await model.client.aclose()
            logger.info(f"Remote model '{model_name}' unregistered")

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        model_name = request.model or next(iter(self._models), "")
        if not model_name or model_name not in self._models:
            raise RuntimeError(f"Remote model '{model_name}' not configured")

        remote = self._models[model_name]
        body = {
            "model": remote.api_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "stream": False,
        }

        resp = await remote.client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

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
            raise RuntimeError(f"Remote model '{model_name}' not configured")

        remote = self._models[model_name]
        body = {
            "model": remote.api_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "stream": True,
        }

        async with remote.client.stream("POST", "/chat/completions", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break

                import json
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content", "")
                finish = chunk["choices"][0].get("finish_reason")
                if content or finish:
                    yield InferenceChunk(text=content, finish_reason=finish)

    def get_loaded_models(self) -> list[str]:
        return list(self._models.keys())

    def get_capabilities(self) -> dict:
        models = {}
        for name, remote in self._models.items():
            models[name] = {
                "api_model": remote.api_model,
                "api_base": remote.api_base,
            }
        return {"backend": "openai-compatible", "models": models}


class _RemoteModel:
    """Internal state for a registered remote model."""

    __slots__ = ("name", "api_model", "api_base", "client")

    def __init__(self, name: str, api_model: str, api_base: str, client: httpx.AsyncClient):
        self.name = name
        self.api_model = api_model
        self.api_base = api_base
        self.client = client


def _build_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
