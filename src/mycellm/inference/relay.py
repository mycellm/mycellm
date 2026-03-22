"""Relay manager — auto-discovers and registers models from OpenAI-compatible endpoints.

A relay is an external device or service running an OpenAI-compatible API
(Ollama, LM Studio, vLLM, llama.cpp server, PocketPal, etc.). mycellm
discovers its models, registers them as local backends, and announces
them to the network. The relay device provides the compute; mycellm
provides the routing and network presence.

Usage:
    # Via config (.env or env var)
    MYCELLM_RELAY_BACKENDS=http://ipad.lan:8080,http://ollama.lan:11434

    # Via CLI
    mycellm serve --relay http://ipad.lan:8080

    # Via API
    POST /v1/node/relay/add {"url": "http://ipad.lan:8080"}

    # Via chat REPL
    /relay add http://ipad.lan:8080
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("mycellm.relay")


@dataclass
class RelayEndpoint:
    """A discovered relay backend."""

    url: str
    name: str = ""  # user-friendly label
    api_key: str = ""
    max_concurrent: int = 32  # per-model concurrency limit for this device
    models: list[dict] = field(default_factory=list)
    online: bool = False
    error: str = ""


class RelayManager:
    """Manages relay backend discovery and lifecycle."""

    def __init__(self, inference_manager):
        self._inference = inference_manager
        self._relays: dict[str, RelayEndpoint] = {}  # url -> RelayEndpoint
        self._poll_task: asyncio.Task | None = None
        self._poll_interval: int = 60  # seconds

    @property
    def relays(self) -> list[RelayEndpoint]:
        return list(self._relays.values())

    async def add(self, url: str, api_key: str = "", name: str = "", max_concurrent: int = 32) -> RelayEndpoint:
        """Add a relay backend and discover its models."""
        url = url.rstrip("/")

        # Normalize — strip /v1 if present (we'll add it)
        if url.endswith("/v1"):
            url = url[:-3]

        relay = RelayEndpoint(url=url, api_key=api_key, name=name or _label_from_url(url), max_concurrent=max_concurrent)
        self._relays[url] = relay

        await self._discover_models(relay)
        return relay

    async def remove(self, url: str) -> bool:
        """Remove a relay and unload its models."""
        url = url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]

        relay = self._relays.pop(url, None)
        if not relay:
            return False

        # Unload all models from this relay
        for model_info in relay.models:
            model_name = model_info.get("id", "")
            if model_name:
                relay_name = f"relay:{model_name}"
                try:
                    await self._inference.unload_model(relay_name)
                except Exception:
                    pass

        logger.info(f"Removed relay {url} ({len(relay.models)} models)")
        return True

    async def refresh(self, url: str | None = None) -> int:
        """Re-discover models from one or all relays. Returns total models found."""
        total = 0
        targets = [self._relays[url]] if url and url in self._relays else self._relays.values()
        for relay in targets:
            total += await self._discover_models(relay)
        return total

    async def refresh_all(self) -> int:
        """Re-discover models from all relays."""
        return await self.refresh()

    def start_polling(self, interval: int = 60) -> None:
        """Start background model discovery polling."""
        self._poll_interval = interval
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    def stop_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    async def _poll_loop(self) -> None:
        """Periodically refresh relay models."""
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                await self.refresh_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Relay poll error: {e}")

    async def _discover_models(self, relay: RelayEndpoint) -> int:
        """Query a relay's /v1/models and register discovered models."""
        headers = {"Content-Type": "application/json"}
        if relay.api_key:
            headers["Authorization"] = f"Bearer {relay.api_key}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{relay.url}/v1/models", headers=headers)
                if resp.status_code == 401:
                    relay.online = False
                    relay.error = "Authentication failed (401)"
                    logger.warning(f"Relay {relay.url}: auth failed")
                    return 0

                if resp.status_code != 200:
                    relay.online = False
                    relay.error = f"HTTP {resp.status_code}"
                    return 0

                data = resp.json()
                models = data.get("data", data.get("models", []))

                relay.online = True
                relay.error = ""
                relay.models = models

        except httpx.ConnectError as e:
            relay.online = False
            relay.error = f"Connection refused: {e}"
            logger.warning(f"Relay {relay.url}: connection refused")
            return 0
        except Exception as e:
            relay.online = False
            relay.error = str(e)
            logger.warning(f"Relay {relay.url}: {e}")
            return 0

        # Register each discovered model via the openai-compat backend
        registered = 0
        for model in models:
            model_id = model.get("id", "") if isinstance(model, dict) else str(model)
            if not model_id:
                continue

            # Prefix with relay: to avoid name collisions
            relay_name = f"relay:{model_id}"

            # Skip if already loaded
            if relay_name in {m.name for m in self._inference.loaded_models}:
                continue

            try:
                await self._inference.load_model(
                    "",
                    name=relay_name,
                    backend_type="openai",
                    api_base=f"{relay.url}/v1",
                    api_key=relay.api_key,
                    api_model=model_id,
                    ctx_len=model.get("context_length", 4096) if isinstance(model, dict) else 4096,
                    max_concurrent=relay.max_concurrent,
                )
                registered += 1
                logger.info(f"Relay model registered: {relay_name} (via {relay.name})")
            except Exception as e:
                logger.warning(f"Failed to register relay model {model_id}: {e}")

        if registered:
            logger.info(f"Relay {relay.url}: {registered} new model(s) registered")
        return registered

    def get_status(self) -> list[dict]:
        """Get status of all relay backends."""
        return [
            {
                "url": r.url,
                "name": r.name,
                "online": r.online,
                "error": r.error,
                "models": [
                    m.get("id", m) if isinstance(m, dict) else m
                    for m in r.models
                ],
                "model_count": len(r.models),
            }
            for r in self._relays.values()
        ]


def _label_from_url(url: str) -> str:
    """Generate a friendly label from a URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or "relay"
    # Use hostname minus common suffixes
    if host in ("localhost", "127.0.0.1"):
        return f"localhost:{parsed.port or 80}"
    return host.split(".")[0]


def parse_relay_backends(relay_str: str) -> list[str]:
    """Parse comma-separated relay URLs from config."""
    if not relay_str:
        return []
    return [url.strip() for url in relay_str.split(",") if url.strip()]
