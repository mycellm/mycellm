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
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingsNotSupportedError,
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
                ctx_len: context length to advertise (default: settings.default_ctx_len)
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
        # Use streaming internally to avoid httpx read timeouts on slow models.
        # Non-streaming POST blocks until the full response is ready — for a 32B
        # model that takes ~110s to emit a first token, this races the 120s timeout
        # and loses.  Streaming with SSE keepalive pings keeps the connection alive
        # indefinitely, so we collect chunks here and reassemble into an InferenceResult.
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
            # Counts arrive on a trailing zero-text chunk. Reassembling here is
            # what stops the non-streaming path from reporting 0+0 for a
            # request the provider counted perfectly well.
            if chunk.prompt_tokens is not None:
                prompt_tokens = chunk.prompt_tokens
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens

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
            raise RuntimeError(f"Remote model '{model_name}' not configured")

        remote = self._models[model_name]
        body = {
            "model": remote.api_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "stream": True,
            # ⚠️ WITHOUT THIS, A STREAMED RESPONSE CARRIES NO TOKEN COUNTS AT
            # ALL. The OpenAI streaming spec omits `usage` unless it is asked
            # for, so every relayed model reported 0 prompt / 0 completion
            # tokens — which the dashboard rendered as a confident "0+0 tokens"
            # pill and agents used for context accounting. A made-up number
            # would be worse; an unasked-for one is simply absent.
            "stream_options": {"include_usage": True},
        }
        if request.stop:
            body["stop"] = request.stop
        if request.frequency_penalty:
            body["frequency_penalty"] = request.frequency_penalty
        if request.presence_penalty:
            body["presence_penalty"] = request.presence_penalty
        if request.seed is not None:
            body["seed"] = request.seed
        if request.response_format:
            body["response_format"] = request.response_format
        if request.tools:
            body["tools"] = request.tools
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice

        accumulated_tool_calls: dict[int, dict] = {}
        # None (not 0) until upstream tells us: absent counts and a genuine
        # zero are different facts, and only one of them should be reported.
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

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

                # Usage is captured wherever it appears. OpenAI itself sends
                # a dedicated final chunk with an EMPTY `choices` list, but
                # several compatible providers attach it to the last content
                # chunk instead — reading it only from the empty-choices frame
                # would silently keep reporting zeros for those.
                usage = chunk.get("usage") or {}
                if usage:
                    if usage.get("prompt_tokens") is not None:
                        prompt_tokens = usage["prompt_tokens"]
                    if usage.get("completion_tokens") is not None:
                        completion_tokens = usage["completion_tokens"]

                # The dedicated usage chunk carries no choice to index.
                if not chunk.get("choices"):
                    continue

                choice = chunk["choices"][0]
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                finish = choice.get("finish_reason")

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

        if accumulated_tool_calls:
            tool_calls_list = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]
            yield InferenceChunk(text="", finish_reason="tool_calls", tool_calls=tool_calls_list)

        # Trailing zero-text chunk carrying real counts, matching what the
        # local backends do. Emitted only when upstream actually reported
        # them — never fabricated, because agents size their context windows
        # from these numbers.
        if prompt_tokens is not None or completion_tokens is not None:
            yield InferenceChunk(
                text="",
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
            )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Relay an embeddings request to the upstream /embeddings endpoint."""
        model_name = request.model or next(iter(self._models), "")
        if not model_name or model_name not in self._models:
            raise RuntimeError(f"Remote model '{model_name}' not configured")

        remote = self._models[model_name]
        body = {"model": remote.api_model, "input": request.input}
        resp = await remote.client.post("/embeddings", json=body)
        if resp.status_code in (400, 404, 422):
            # Upstream rejected the request — most commonly the configured
            # api_model is a chat model that doesn't serve embeddings.
            raise EmbeddingsNotSupportedError(
                f"Upstream provider rejected embeddings for '{model_name}' "
                f"(HTTP {resp.status_code}). Is '{remote.api_model}' an embedding model?"
            )
        resp.raise_for_status()

        data = resp.json()
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        embeddings = [item["embedding"] for item in items]
        usage = data.get("usage", {}) or {}
        return EmbeddingResult(
            embeddings=embeddings,
            total_tokens=usage.get("total_tokens", usage.get("prompt_tokens", 0)),
        )

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
