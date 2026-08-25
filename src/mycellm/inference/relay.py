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


def _slugify(label: str) -> str:
    """A short, stable, name-safe token for a relay label."""
    out = "".join(c if (c.isalnum() or c in "-_") else "-" for c in label.lower())
    return out.strip("-") or "relay"


@dataclass
class RelayEndpoint:
    """A discovered relay backend.

    In 0.8 terms a relay endpoint IS a ServingGroup: a gateway-owned serving
    service that may front one process or a whole distributed cluster. Each
    model it exposes is a Deployment. We do not model those as separate
    registries — the group is this object and the deployments are
    `registered` — because a parallel registry would become a second source
    of truth about what this node can serve.
    """

    url: str
    name: str = ""  # user-friendly label
    api_key: str = ""
    max_concurrent: int = 32  # per-model concurrency limit for this device
    models: list[dict] = field(default_factory=list)
    online: bool = False
    error: str = ""

    #: registered model name -> upstream model id. THE OWNERSHIP RECORD.
    #:
    #: ⚠️ WITHOUT THIS, TWO BUGS WERE UNFIXABLE. Nothing recorded which
    #: registered models belonged to which relay, so (a) when a relay went
    #: offline its models stayed loaded and advertised — the network kept
    #: routing to a dead endpoint — and (b) `remove()` reconstructed
    #: `relay:{id}` from the upstream list, which meant removing relay B
    #: unloaded relay A's identically-named model.
    registered: dict[str, str] = field(default_factory=dict)

    @property
    def group_id(self) -> str:
        """Stable identity for this serving group."""
        return f"grp_{_slugify(self.name or _label_from_url(self.url))}"

    def deployment_id(self, registered_name: str) -> str:
        """Stable identity for one model served by this group."""
        return f"dep_{_slugify(self.group_id)}_{_slugify(registered_name)}"

    @property
    def healthy(self) -> bool:
        """Eligible to serve right now.

        `online` alone was never enough: it was set False on failure while the
        models stayed registered, so "offline" and "not routed to" were
        different things.
        """
        return self.online and bool(self.registered)


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

        # Unload exactly what THIS relay registered. Reconstructing
        # `relay:{id}` from the upstream model list (as this did) unloaded
        # another relay's model whenever two exposed the same name.
        n = await self._deregister(relay, reason="relay removed")
        logger.info(f"Removed relay {url} ({n} models unloaded)")
        return True

    async def _deregister(self, relay: RelayEndpoint, reason: str) -> int:
        """Unload every model this relay registered, and forget them.

        ⚠️ THIS IS THE FIX FOR GHOST MODELS. Every failure path used to set
        `online = False` and return, leaving the models loaded and still
        advertised in capabilities — so the fleet kept routing requests to an
        endpoint that was gone, and each one failed at inference time instead
        of the model simply not being offered.
        """
        if not relay.registered:
            return 0
        names = list(relay.registered)
        for name in names:
            try:
                await self._inference.unload_model(name)
            except Exception as e:  # already gone, or backend refused
                logger.debug(f"Unloading {name} during deregister: {e}")
        relay.registered.clear()
        logger.info(
            f"Relay {relay.url}: withdrew {len(names)} model(s) — {reason}"
        )
        return len(names)

    def _claim_name(self, relay: RelayEndpoint, model_id: str) -> str:
        """Pick the registered name for `model_id`, avoiding collisions.

        Two relays exposing the same upstream model (two Ollama boxes both
        serving `llama3`) both wanted `relay:llama3`. The second was silently
        skipped by the "already loaded" check, so its capacity was invisible
        and, worse, when the first went offline the model vanished although
        the second still served it.

        First claimant keeps the plain name so existing setups see no rename;
        later claimants are qualified by relay label. Order-dependent by
        construction, which is why the deployment id — not the display name —
        is the stable identity.
        """
        plain = f"relay:{model_id}"
        owner = self._owner_of(plain)
        if owner is None or owner is relay:
            return plain
        return f"relay:{_slugify(relay.name or _label_from_url(relay.url))}:{model_id}"

    def _is_ours(self, relay: RelayEndpoint, registered_name: str) -> bool:
        """Is this already-loaded model actually served by THIS relay?

        Checked against the backend's configured `api_base`, not against the
        name — a name match is what we are trying to disambiguate. Falls back to
        the advertised `serving_group_id` when the api_base is unavailable.
        """
        info = getattr(self._inference, "_model_info", {}).get(registered_name)
        if info is not None and getattr(info, "serving_group_id", ""):
            return info.serving_group_id == relay.group_id
        saved = getattr(self._inference, "_saved_configs", {}).get(registered_name, {})
        api_base = (saved.get("api_base") or "").rstrip("/")
        return bool(api_base) and api_base == f"{relay.url}/v1"

    def _owner_of(self, registered_name: str) -> RelayEndpoint | None:
        for r in self._relays.values():
            if registered_name in r.registered:
                return r
        return None

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
                    await self._deregister(relay, reason="auth failed (401)")
                    return 0

                if resp.status_code != 200:
                    relay.online = False
                    relay.error = f"HTTP {resp.status_code}"
                    await self._deregister(relay, reason=f"HTTP {resp.status_code}")
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
            await self._deregister(relay, reason="connection refused")
            return 0
        except Exception as e:
            relay.online = False
            relay.error = str(e)
            logger.warning(f"Relay {relay.url}: {e}")
            await self._deregister(relay, reason=str(e))
            return 0

        # Register each discovered model via the openai-compat backend
        registered = 0
        seen: dict[str, str] = {}  # registered name -> upstream id, this pass
        for model in models:
            model_id = model.get("id", "") if isinstance(model, dict) else str(model)
            if not model_id:
                continue

            # Skip models that are themselves relayed from other peers —
            # only register the remote node's own local models to prevent
            # relay:relay:relay: prefix multiplication across the network.
            if model_id.startswith("relay:"):
                continue

            # Also skip fleet/peer models (owned_by != "local") if metadata available
            if isinstance(model, dict):
                owned_by = model.get("owned_by", "local")
                if owned_by and owned_by != "local" and not owned_by.startswith("system"):
                    continue

            relay_name = self._claim_name(relay, model_id)
            seen[relay_name] = model_id

            # Already ours and still loaded — nothing to do.
            if relay_name in relay.registered:
                continue

            # Loaded but not in our ownership record. Two very different cases.
            if relay_name in {m.name for m in self._inference.loaded_models}:
                if self._is_ours(relay, relay_name):
                    # ADOPTION. The model auto-loaded from its saved config on
                    # startup, before discovery ran, so it is already serving
                    # but unowned. Without this the relay refused to claim it,
                    # `registered` stayed empty, and the group reported
                    # unhealthy with zero deployments while actually serving
                    # traffic — health that contradicts reality.
                    relay.registered[relay_name] = model_id
                    logger.info(
                        f"Relay {relay.url}: adopted already-loaded {relay_name}"
                    )
                    continue
                # A genuine foreign backend (a local model of the same name, or
                # another relay that claimed it first). Do not displace it.
                logger.debug(
                    f"Relay {relay.url}: {relay_name} already served by another "
                    f"backend — not claiming it"
                )
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
                    # Identity for the 0.8 fabric: this model is served by a
                    # group, not by this process. Consumed by
                    # GET /v1/node/groups and carried in the announcement so
                    # peers can tell a grouped deployment from a local model.
                    serving_group_id=relay.group_id,
                    deployment_id=relay.deployment_id(relay_name),
                    parallelism={"type": "external"},
                )
                relay.registered[relay_name] = model_id
                registered += 1
                logger.info(f"Relay model registered: {relay_name} (via {relay.name})")
            except Exception as e:
                logger.warning(f"Failed to register relay model {model_id}: {e}")

        # ── Reconcile removals ──────────────────────────────────────────
        # A refresh only ever ADDED models. A model withdrawn upstream — a
        # different model loaded in LM Studio, an Ollama model deleted —
        # stayed registered and advertised forever, so the network offered
        # something the endpoint would refuse.
        withdrawn = [n for n in relay.registered if n not in seen]
        for name in withdrawn:
            try:
                await self._inference.unload_model(name)
            except Exception as e:
                logger.debug(f"Unloading withdrawn {name}: {e}")
            relay.registered.pop(name, None)
        if withdrawn:
            logger.info(
                f"Relay {relay.url}: withdrew {len(withdrawn)} model(s) no "
                f"longer served upstream ({', '.join(withdrawn)})"
            )

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
                # What this relay is actually *serving* right now, as opposed
                # to what it advertised upstream. These differ whenever a
                # model was withdrawn, collided, or the endpoint is down —
                # and that difference used to be invisible.
                "registered_count": len(r.registered),
                "healthy": r.healthy,
            }
            for r in self._relays.values()
        ]

    def get_groups(self) -> list[dict]:
        """Serving groups and their deployments — the 0.8 view of the same state.

        A relay endpoint is a ServingGroup: a gateway-owned serving service.
        Each model it currently serves is a Deployment with a stable id.

        This is the consumer for the `serving_group_id` / `deployment_id`
        capability fields. They were added in the same change as this endpoint
        deliberately: a field with no reader is the bug this codebase keeps
        shipping, so nothing here is advertised before something reads it.
        """
        return [
            {
                "group_id": r.group_id,
                "name": r.name,
                "endpoint": r.url,
                "runtime": "external",
                "endpoint_mode": "gateway",
                "healthy": r.healthy,
                "online": r.online,
                "error": r.error,
                "deployments": [
                    {
                        "deployment_id": r.deployment_id(registered),
                        "model": registered,
                        "upstream_model": upstream,
                        "parallelism": {"type": "external"},
                    }
                    for registered, upstream in sorted(r.registered.items())
                ],
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
