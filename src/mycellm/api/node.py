"""Node management API endpoints."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from mycellm.activity import EventType

router = APIRouter()


@router.get("/version")
async def node_version(request: Request):
    """Get current version and check for updates."""
    from mycellm import __version__
    result = {"current": __version__, "latest": None, "update_available": False}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("https://pypi.org/pypi/mycellm/json")
            if resp.status_code == 200:
                latest = resp.json().get("info", {}).get("version", "")
                if latest:
                    result["latest"] = latest
                    result["update_available"] = _is_newer(latest, __version__)
    except Exception:
        pass  # offline or not published yet
    return result


def _is_newer(latest: str, current: str) -> bool:
    """Check if latest version is newer than current using tuple comparison."""
    try:
        def parse(v):
            return tuple(int(x) for x in v.split(".")[:3])
        return parse(latest) > parse(current)
    except (ValueError, TypeError):
        return False


@router.get("/status")
async def node_status(request: Request):
    """Get comprehensive node status."""
    node = request.app.state.node
    status = node.get_status()
    status["credits"] = await node.get_credits()
    return status


@router.get("/system")
async def system_info(request: Request):
    """Get detailed system hardware and software info."""
    node = request.app.state.node
    return node.get_system_info()


@router.get("/debug/config")
async def debug_config(request: Request):
    """Debug: show relevant runtime config."""
    node = request.app.state.node
    db_backend = "PostgreSQL" if node._settings.db_url and "postgresql" in node._settings.db_url else "SQLite"
    return {
        "node_name": node._settings.node_name,
        "bootstrap_peers": node._settings.bootstrap_peers,
        "bootstrap_parsed": [f"{h}:{p}" for h, p in node._settings.get_bootstrap_list()],
        "api_key_set": bool(node._settings.api_key),
        "hf_token_set": bool(node._settings.hf_token),
        "db_backend": db_backend,
        "log_level": node._settings.log_level,
        "no_log_inference": node._settings.no_log_inference,
        "telemetry": node._settings.telemetry,
        "announce_task_alive": node._announce_task is not None and not node._announce_task.done() if hasattr(node, '_announce_task') else False,
    }


@router.get("/settings/secrets")
async def list_secrets(request: Request):
    """List stored secret names (not values)."""
    node = request.app.state.node
    if not hasattr(node, "secret_store") or not node.secret_store:
        return {"secrets": []}
    return {"secrets": node.secret_store.list_names()}


@router.post("/settings/secrets")
async def set_secret(request: Request):
    """Store an encrypted secret."""
    node = request.app.state.node
    if not hasattr(node, "secret_store") or not node.secret_store:
        return {"error": "Secret store not initialized"}
    body = await request.json()
    name = body.get("name", "")
    value = body.get("value", "")
    if not name or not value:
        return {"error": "name and value required"}
    node.secret_store.set(name, value)
    return {"status": "ok", "name": name}


@router.delete("/settings/secrets")
async def remove_secret(request: Request):
    """Remove a stored secret."""
    node = request.app.state.node
    if not hasattr(node, "secret_store") or not node.secret_store:
        return {"error": "Secret store not initialized"}
    body = await request.json()
    name = body.get("name", "")
    if not name:
        return {"error": "name required"}
    removed = node.secret_store.remove(name)
    return {"status": "removed" if removed else "not_found", "name": name}


@router.get("/peers")
async def node_peers(request: Request):
    """List connected peers."""
    node = request.app.state.node
    return {"peers": node.get_status().get("peers", [])}


@router.get("/credits")
async def node_credits(request: Request):
    """Get credit balance and history."""
    node = request.app.state.node
    return await node.get_credits()


@router.get("/credits/tier")
async def credit_tier(request: Request):
    """Get the consumer's current credit tier and access level."""
    node = request.app.state.node
    balance = 0.0
    if node.ledger:
        balance = await node.ledger.balance(node.peer_id)

    if balance >= 50:
        tier = "power"
        access = "All model tiers"
        label = "Power Seeder"
    elif balance >= 10:
        tier = "contributor"
        access = "Tier 1 + Tier 2 models"
        label = "Contributor"
    else:
        tier = "free"
        access = "Tier 1 models only"
        label = "Free Tier"

    # Receipt stats
    receipts_received = 0
    receipts_verified = 0
    if node.ledger:
        all_receipts = await node.ledger.get_receipts(node.peer_id, limit=1000)
        receipts_received = len(all_receipts)
        receipts_verified = sum(1 for r in all_receipts if r.get("signature") and r["signature"] != "fleet")

    return {
        "balance": round(balance, 2),
        "tier": tier,
        "label": label,
        "access": access,
        "thresholds": {"free": 0, "contributor": 10, "power": 50},
        "receipts": {
            "total": receipts_received,
            "verified": receipts_verified,
            "fleet": receipts_received - receipts_verified,
        },
    }


@router.get("/credits/history")
async def credit_history(request: Request, limit: int = 50):
    """Get credit transaction history."""
    node = request.app.state.node
    if node.ledger:
        return {"transactions": await node.ledger.history(node.peer_id, limit)}
    return {"transactions": []}


@router.post("/models/load")
async def load_model(request: Request):
    """Load a model.

    For local GGUF models (backend=llama.cpp, default):
        {"model_path": "/path/to/model.gguf", "name": "my-model"}

    For remote OpenAI-compatible APIs (backend=openai):
        {"name": "claude-sonnet", "backend": "openai",
         "api_base": "https://openrouter.ai/api/v1",
         "api_key": "sk-or-...", "api_model": "anthropic/claude-sonnet-4"}
    """
    node = request.app.state.node
    body = await request.json()
    backend_type = body.get("backend", "llama.cpp")
    model_path = body.get("model_path", "")
    name = body.get("name")

    # Resolve secret references in api_key (e.g. "secret:openrouter" → actual key)
    api_key = body.get("api_key", "")
    if api_key and hasattr(node, "secret_store") and node.secret_store:
        api_key = node.secret_store.resolve(api_key)

    # Local backends require model_path; remote backends don't
    if backend_type == "llama.cpp" and not model_path:
        return {"error": "model_path required for llama.cpp backend"}

    model_name = name or (model_path.split("/")[-1].replace(".gguf", "") if model_path else "remote-model")

    # For llama.cpp, load async (can take minutes for large models)
    if backend_type == "llama.cpp":
        import asyncio

        async def _bg_load():
            try:
                loaded_name = await node.inference.load_model(
                    model_path, name=name, backend_type=backend_type,
                    ctx_len=body.get("ctx_len", 4096), timeout=body.get("timeout", 120),
                    quant=body.get("quant", ""),
                )
                scope = body.get("scope", "home")
                info = node.inference._model_info.get(loaded_name)
                if info:
                    info.scope = scope
                    info.visible_networks = body.get("visible_networks", [])
                    if body.get("param_count_b"):
                        info.param_count_b = body["param_count_b"]
                node.capabilities.models = node.inference.loaded_models
                await node.announce_capabilities()
                node.activity.record(EventType.MODEL_LOADED, model=loaded_name, backend=backend_type)
            except Exception:
                pass  # error captured in _load_status

        asyncio.ensure_future(_bg_load())
        return {"status": "loading", "model": model_name, "backend": backend_type}

    # For API backends, load synchronously (fast — just a connectivity check)
    try:
        loaded_name = await node.inference.load_model(
            model_path, name=name, backend_type=backend_type,
            api_base=body.get("api_base", ""), api_key=api_key,
            api_model=body.get("api_model", ""), ctx_len=body.get("ctx_len", 4096),
            timeout=body.get("timeout", 120),
        )
        scope = body.get("scope", "home")
        info = node.inference._model_info.get(loaded_name)
        if info:
            info.scope = scope
            info.visible_networks = body.get("visible_networks", [])

        node.capabilities.models = node.inference.loaded_models
        await node.announce_capabilities()
        node.activity.record(EventType.MODEL_LOADED, model=loaded_name, backend=backend_type)
        return {"status": "loaded", "model": loaded_name, "backend": backend_type}
    except Exception as e:
        return {"error": str(e)}


@router.post("/models/unload")
async def unload_model(request: Request):
    """Unload a model."""
    node = request.app.state.node
    body = await request.json()
    model_name = body.get("model", "")
    if not model_name:
        return {"error": "model name required"}

    await node.inference.unload_model(model_name)
    node.capabilities.models = node.inference.loaded_models
    await node.announce_capabilities()
    node.activity.record(EventType.MODEL_UNLOADED, model=model_name)
    return {"status": "unloaded", "model": model_name}


@router.get("/models/load-status")
async def model_load_status(request: Request):
    """Get status of model loading operations (in-progress + recent)."""
    import time
    node = request.app.state.node
    statuses = []
    for name, s in node.inference._load_status.items():
        entry = {**s}
        if s.get("status") == "loading":
            entry["elapsed"] = round(time.time() - s.get("started_at", 0), 1)
        statuses.append(entry)
    return {"statuses": statuses}


@router.get("/models/saved")
async def list_saved_configs(request: Request):
    """List all saved model configs (loaded + unloaded API models)."""
    node = request.app.state.node
    loaded_names = {m.name for m in node.inference.loaded_models}
    configs = []
    for c in node.inference.get_saved_configs():
        configs.append({
            **c,
            "loaded": c.get("name", "") in loaded_names,
            "api_key": "***" if c.get("api_key") else "",  # mask key
        })
    return {"configs": configs}


@router.post("/models/update")
async def update_model(request: Request):
    """Update a model's config (unload + reload with new settings).

    Merges provided fields with saved config. Omitted fields keep current values.
    """
    node = request.app.state.node
    body = await request.json()
    model_name = body.get("model", "")
    if not model_name:
        return {"error": "model name required"}

    # Get existing config (from saved or running)
    config = dict(node.inference._saved_configs.get(model_name, {}))
    if not config:
        return {"error": f"No config for '{model_name}'"}

    # Merge overrides — only update fields that were provided
    if body.get("api_base"): config["api_base"] = body["api_base"]
    if body.get("api_model"): config["api_model"] = body["api_model"]
    if body.get("api_key"):
        new_key = body["api_key"]
        if hasattr(node, "secret_store") and node.secret_store:
            new_key = node.secret_store.resolve(new_key)
        config["api_key"] = new_key
    if body.get("ctx_len"): config["ctx_len"] = body["ctx_len"]

    # Unload if currently loaded
    if model_name in {m.name for m in node.inference.loaded_models}:
        await node.inference.unload_model(model_name)

    backend_type = config.get("backend", "openai")
    try:
        await node.inference.load_model(
            config.get("model_path", ""),
            name=model_name,
            backend_type=backend_type,
            api_base=config.get("api_base", ""),
            api_key=config.get("api_key", ""),
            api_model=config.get("api_model", ""),
            ctx_len=config.get("ctx_len", 4096),
        )
        node.capabilities.models = node.inference.loaded_models
        await node.announce_capabilities()
        node.activity.record(EventType.MODEL_LOADED, model=model_name, backend=backend_type)
        return {"status": "updated", "model": model_name}
    except Exception as e:
        return {"error": str(e)}


@router.post("/models/reload")
async def reload_model(request: Request):
    """Re-load a previously saved (unloaded) model config."""
    node = request.app.state.node
    body = await request.json()
    model_name = body.get("model", "")
    if not model_name:
        return {"error": "model name required"}

    # Find in saved configs
    config = node.inference._saved_configs.get(model_name)
    if not config:
        return {"error": f"No saved config for '{model_name}'"}

    backend_type = config.get("backend", "llama.cpp")
    try:
        await node.inference.load_model(
            config.get("model_path", ""),
            name=model_name,
            backend_type=backend_type,
            api_base=config.get("api_base", ""),
            api_key=config.get("api_key", ""),
            api_model=config.get("api_model", ""),
            ctx_len=config.get("ctx_len", 4096),
        )
        node.capabilities.models = node.inference.loaded_models
        await node.announce_capabilities()
        node.activity.record(EventType.MODEL_LOADED, model=model_name, backend=backend_type)
        return {"status": "loaded", "model": model_name}
    except Exception as e:
        return {"error": str(e)}


@router.post("/models/remove-config")
async def remove_saved_config(request: Request):
    """Permanently remove a saved model config."""
    node = request.app.state.node
    body = await request.json()
    model_name = body.get("model", "")
    if not model_name:
        return {"error": "model name required"}

    # Unload if loaded
    if model_name in {m.name for m in node.inference.loaded_models}:
        await node.inference.unload_model(model_name)
        node.capabilities.models = node.inference.loaded_models

    from mycellm.config import get_settings
    await node.inference.remove_saved_config(model_name, get_settings().data_dir)
    return {"status": "removed", "model": model_name}


@router.get("/models/{model_name}/config")
async def model_config(model_name: str, request: Request):
    """Get a loaded model's config (for edit). API key is masked."""
    node = request.app.state.node
    backend = node.inference.get_backend(model_name)
    if not backend:
        return {"error": "model not found"}

    info = node.inference._model_info.get(model_name)
    result = {
        "name": model_name,
        "backend": info.backend if info else "unknown",
        "ctx_len": info.ctx_len if info else 4096,
    }

    # For openai backends, include api_base and api_model
    from mycellm.inference.openai_compat import OpenAICompatibleBackend
    if isinstance(backend, OpenAICompatibleBackend):
        remote = backend._models.get(model_name)
        if remote:
            result["api_base"] = remote.api_base
            result["api_model"] = remote.api_model
            # Mask API key — show last 4 chars only
            auth = remote.client.headers.get("authorization", "")
            if auth.startswith("Bearer ") and len(auth) > 15:
                result["api_key_hint"] = f"...{auth[-4:]}"
            else:
                result["api_key_hint"] = ""

    return result


@router.get("/connections")
async def node_connections(request: Request):
    """Diagnostic endpoint showing per-peer connection state."""
    node = request.app.state.node
    return {"connections": node.peer_manager.get_connections()}


@router.get("/logs")
async def get_logs(request: Request, limit: int = 100):
    """Get recent log entries."""
    node = request.app.state.node
    entries = node.log_broadcaster.recent[-limit:]
    return {"logs": entries}


@router.get("/logs/stream")
async def stream_logs(request: Request):
    """Stream log entries via SSE."""
    node = request.app.state.node
    q = node.log_broadcaster.subscribe()

    async def generate():
        try:
            while True:
                entry = await q.get()
                yield json.dumps(entry)
        except asyncio.CancelledError:
            pass
        finally:
            node.log_broadcaster.unsubscribe(q)

    return EventSourceResponse(generate())


@router.get("/federation")
async def federation_info(request: Request):
    """Get network federation info."""
    node = request.app.state.node
    if not hasattr(node, 'federation') or not node.federation:
        return {"network_id": "", "network_name": "", "federation": False}
    identity = node.federation.identity
    return {
        "federation": True,
        "network_id": identity.network_id if identity else "",
        "network_name": identity.network_name if identity else "",
        "public": identity.public if identity else False,
        "bootstrap_addrs": identity.bootstrap_addrs if identity else [],
        "tokens": len(node.federation.list_tokens()),
    }


@router.post("/federation/invite")
async def create_invite(request: Request):
    """Create a federation invite token."""
    node = request.app.state.node
    if not hasattr(node, 'federation') or not node.federation:
        return {"error": "Federation not initialized"}
    body = await request.json()
    token = node.federation.create_invite(
        node.device_key,
        roles=body.get("roles", ["seeder"]),
        max_uses=body.get("max_uses", 0),
        expires_hours=body.get("expires_hours", 0),
    )
    return {
        "token_id": token.token_id,
        "portable": token.to_portable(),
        "expires_at": token.expires_at,
        "max_uses": token.max_uses,
    }


@router.post("/federation/join")
async def join_network(request: Request):
    """Join a network using an invite token or network details."""
    node = request.app.state.node
    if not node.federation:
        return {"error": "Federation not initialized"}
    body = await request.json()

    # Join via direct network details
    network_id = body.get("network_id", "")
    if not network_id:
        # Try to extract from invite token
        portable = body.get("invite_token", "")
        if portable:
            from mycellm.federation import InviteToken
            try:
                token = InviteToken.from_portable(portable)
                network_id = token.network_id
            except Exception as e:
                return {"error": f"Invalid invite token: {e}"}

    if not network_id:
        return {"error": "network_id or invite_token required"}

    membership = node.federation.join_network(
        network_id=network_id,
        network_name=body.get("network_name", ""),
        role=body.get("role", "seeder"),
        bootstrap_addrs=body.get("bootstrap_addrs", []),
        models=body.get("models", []),
        quota=body.get("quota", {}),
    )
    return {"status": "joined", "membership": membership.to_dict()}


@router.post("/federation/leave")
async def leave_network(request: Request):
    """Leave a joined network."""
    node = request.app.state.node
    if not node.federation:
        return {"error": "Federation not initialized"}
    body = await request.json()
    network_id = body.get("network_id", "")
    if not network_id:
        return {"error": "network_id required"}
    ok = node.federation.leave_network(network_id)
    return {"status": "left" if ok else "failed"}


@router.get("/federation/memberships")
async def list_memberships(request: Request):
    """List all network memberships."""
    node = request.app.state.node
    if not node.federation:
        return {"memberships": [], "home_network": ""}
    return {
        "home_network": node.federation.network_id,
        "memberships": [m.to_dict() for m in node.federation.memberships],
        "network_ids": node.federation.network_ids,
    }


@router.get("/federation/tokens")
async def list_tokens(request: Request):
    """List all invite tokens."""
    node = request.app.state.node
    if not hasattr(node, 'federation') or not node.federation:
        return {"tokens": []}
    return {"tokens": node.federation.list_tokens()}


@router.get("/public/dashboard")
async def public_dashboard(request: Request):
    """Public read-only network dashboard data.

    Exposes only non-sensitive aggregate stats for public networks.
    """
    node = request.app.state.node

    # Check if public mode is enabled
    if hasattr(node, 'federation') and node.federation and node.federation.identity:
        if not node.federation.identity.public:
            return {"error": "Public dashboard not enabled for this network"}

    peers = node.registry.connected_peers()
    fleet_count = len([n for n in node.node_registry.values() if n.get("status") == "approved"])

    # Aggregate model info (no API keys or addresses)
    models = set()
    for m in node.inference.loaded_models:
        models.add(m.name)
    for entry in peers:
        for m in entry.capabilities.models:
            models.add(m.name)
    for entry in node.node_registry.values():
        if entry.get("status") == "approved":
            for m in entry.get("capabilities", {}).get("models", []):
                name = m.get("name", m) if isinstance(m, dict) else m
                models.add(name)

    stats = node.activity.stats() if hasattr(node, 'activity') else {}

    return {
        "network": {
            "name": node.federation.identity.network_name if node.federation and node.federation.identity else "mycellm",
            "id": node.federation.network_id[:12] if node.federation else "",
            "public": True,
        },
        "nodes": {
            "total": 1 + len(peers) + fleet_count,
            "peers": len(peers),
            "fleet": fleet_count,
        },
        "models": sorted(models),
        "stats": {
            "total_requests": stats.get("total_requests", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "requests_per_min": stats.get("requests_per_min", 0),
        },
        "uptime_seconds": node.uptime,
    }


@router.get("/fleet/hardware")
async def fleet_hardware(request: Request):
    """Aggregate hardware stats across fleet nodes."""
    node = request.app.state.node

    nodes = []

    # Self
    sys_info = node.get_system_info()
    stats = node.activity.stats() if hasattr(node, 'activity') else {}
    tps = node.activity.tps if hasattr(node, 'activity') else 0
    load = stats.get("load", {})
    nodes.append({
        "name": node._settings.node_name,
        "peer_id": node.peer_id,
        "type": "self",
        "gpu": node.capabilities.hardware.gpu,
        "vram_gb": node.capabilities.hardware.vram_gb,
        "backend": node.capabilities.hardware.backend,
        "ram_gb": sys_info.get("memory", {}).get("total_gb", 0),
        "ram_used_pct": sys_info.get("memory", {}).get("used_pct", 0),
        "models": [m.name for m in node.inference.loaded_models],
        "tps": tps,
        "total_requests": stats.get("total_requests", 0),
        "total_tokens": stats.get("total_tokens", 0),
        "load": load,
        "online": True,
        "uptime_seconds": node.uptime,
    })

    # Fleet nodes
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        sys = entry.get("system", {})
        hw = sys.get("gpu", entry.get("capabilities", {}).get("hardware", {}))
        mem = sys.get("memory", {})
        caps = entry.get("capabilities", {})
        models = caps.get("models", [])

        telemetry = entry.get("telemetry", {})
        nodes.append({
            "name": entry.get("node_name", ""),
            "peer_id": entry.get("peer_id", ""),
            "type": "fleet",
            "gpu": hw.get("gpu", "CPU"),
            "vram_gb": hw.get("vram_gb", 0),
            "backend": hw.get("backend", "cpu"),
            "ram_gb": mem.get("total_gb", 0),
            "ram_used_pct": mem.get("used_pct", 0),
            "models": [m.get("name", m) if isinstance(m, dict) else m for m in models],
            "tps": telemetry.get("tps", 0),
            "total_requests": telemetry.get("requests_total", 0),
            "total_tokens": telemetry.get("tokens_total", 0),
            "online": entry.get("online", False),
            "uptime_seconds": telemetry.get("uptime_seconds", 0),
        })

    # Aggregate
    total_tps = sum(n["tps"] for n in nodes)
    total_vram = sum(n["vram_gb"] for n in nodes)
    total_ram = sum(n["ram_gb"] for n in nodes)
    total_models = len(set(m for n in nodes for m in n["models"]))
    total_requests = sum(n.get("total_requests", 0) for n in nodes)
    total_tokens = sum(n.get("total_tokens", 0) for n in nodes)
    online_count = sum(1 for n in nodes if n["online"])

    return {
        "nodes": nodes,
        "aggregate": {
            "total_nodes": len(nodes),
            "online_nodes": online_count,
            "total_tps": round(total_tps, 1),
            "total_vram_gb": round(total_vram, 1),
            "total_ram_gb": round(total_ram, 1),
            "total_models": total_models,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
        },
    }


@router.get("/settings/telemetry")
async def get_telemetry(request: Request):
    """Get telemetry opt-in status."""
    node = request.app.state.node
    return {
        "enabled": node._settings.telemetry,
        "description": "Anonymous usage stats (request/token counts, TPS, model names, uptime). No prompts, IPs, or user data.",
    }


@router.post("/settings/telemetry")
async def set_telemetry(request: Request):
    """Toggle telemetry opt-in. Persists to .env file."""
    import logging
    node = request.app.state.node
    body = await request.json()
    enabled = bool(body.get("enabled", False))

    # Update runtime setting (Pydantic v2 supports attribute assignment)
    try:
        object.__setattr__(node._settings, "telemetry", enabled)
    except Exception as e:
        logging.getLogger("mycellm.api").warning(f"Failed to set telemetry: {e}")
        return {"error": str(e)}

    # Persist to .env
    try:
        env_path = node._settings.config_dir / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_lines = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env_lines[key.strip()] = val.strip()
        env_lines["MYCELLM_TELEMETRY"] = str(enabled).lower()
        env_path.write_text("\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n")
    except Exception as e:
        logging.getLogger("mycellm.api").warning(f"Failed to persist telemetry to .env: {e}")

    return {"enabled": enabled}


@router.get("/public/stats")
async def public_stats(request: Request):
    """Public network stats — no auth required.

    Returns aggregate network information suitable for a public stats page.
    No IPs, keys, or sensitive data exposed.
    """
    node = request.app.state.node

    # Network info
    network_name = "mycellm"
    is_public = False
    if hasattr(node, "federation") and node.federation and node.federation.identity:
        network_name = node.federation.identity.network_name
        is_public = node.federation.identity.public

    # Node counts
    approved_nodes = [n for n in node.node_registry.values() if n.get("status") == "approved"]
    online_nodes = [n for n in approved_nodes if time.time() - n.get("last_seen", 0) < 120]
    seeding_nodes = [n for n in online_nodes if n.get("role") == "seeder"]

    # Compute aggregates
    total_vram_gb = 0.0
    total_ram_gb = 0.0
    for entry in approved_nodes:
        sys = entry.get("system", {})
        hw = sys.get("gpu", entry.get("capabilities", {}).get("hardware", {}))
        mem = sys.get("memory", {})
        total_vram_gb += hw.get("vram_gb", 0)
        total_ram_gb += mem.get("total_gb", 0)

    # Add self
    sys_info = node.get_system_info()
    total_vram_gb += node.capabilities.hardware.vram_gb
    total_ram_gb += sys_info.get("memory", {}).get("total_gb", 0)
    total_tps = node.activity.tps if hasattr(node, "activity") else 0

    # Models (no sensitive info) — with tier classification
    from mycellm.protocol.capabilities import classify_tier, TIER_NAMES
    model_names = set()
    models_by_tier: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    seen_models = set()

    for m in node.inference.loaded_models:
        model_names.add(m.name)
        if m.name not in seen_models:
            tier = classify_tier(m.param_count_b)
            models_by_tier[tier].append({
                "name": m.name, "tier": tier, "param_b": m.param_count_b,
            })
            seen_models.add(m.name)

    for entry in approved_nodes:
        for m in entry.get("capabilities", {}).get("models", []):
            name = m.get("name", m) if isinstance(m, dict) else m
            model_names.add(name)
            if name not in seen_models:
                param_b = m.get("param_count_b", 0) if isinstance(m, dict) else 0
                tier = classify_tier(param_b)
                models_by_tier[tier].append({"name": name, "tier": tier, "param_b": param_b})
                seen_models.add(name)

    # Activity stats — combine local stats with telemetry from announcing nodes
    local_stats = node.activity.stats() if hasattr(node, "activity") else {}
    network_requests = local_stats.get("total_requests", 0)
    network_tokens = local_stats.get("total_tokens", 0)
    network_tps = total_tps  # already includes local tps

    for entry in online_nodes:
        t = entry.get("telemetry", {})
        if t:
            network_requests += t.get("requests_total", 0)
            network_tokens += t.get("tokens_total", 0)
            network_tps += t.get("tps", 0)

    # Top contributors (by node name only — no IPs or peer IDs)
    contributors = []
    for entry in online_nodes:
        t = entry.get("telemetry", {})
        contributors.append({
            "name": entry.get("node_name", "anonymous"),
            "tps": t.get("tps", 0),
            "models": len(t.get("models_loaded", entry.get("capabilities", {}).get("models", []))),
            "requests": t.get("requests_total", 0),
        })
    contributors.sort(key=lambda c: c["tps"], reverse=True)

    # Growth data if available
    growth = {}
    if hasattr(node, "_growth_snapshots"):
        growth = node._growth_snapshots

    return {
        "network_name": network_name,
        "nodes": {
            "total": 1 + len(approved_nodes),
            "online": 1 + len(online_nodes),
            "seeding": 1 + len(seeding_nodes),
        },
        "compute": {
            "total_tps": round(network_tps, 1),
            "total_vram_gb": round(total_vram_gb, 1),
            "total_ram_gb": round(total_ram_gb, 1),
        },
        "models": {
            "total_loaded": len(node.inference.loaded_models) + sum(
                len(n.get("capabilities", {}).get("models", [])) for n in online_nodes
            ),
            "unique": len(model_names),
            "names": sorted(model_names),
            "by_tier": {
                "tier1": [m["name"] for m in models_by_tier[1]],
                "tier2": [m["name"] for m in models_by_tier[2]],
                "tier3": [m["name"] for m in models_by_tier[3]],
            },
        },
        "activity": {
            "total_requests": network_requests,
            "total_tokens": network_tokens,
            "requests_per_min": local_stats.get("requests_per_min", 0),
        },
        "top_contributors": contributors[:10],
        "growth": growth,
        "uptime_seconds": round(node.uptime),
    }


@router.get("/activity")
async def node_activity(request: Request, limit: int = 50, type: str = None):
    """Get recent activity events and rolling stats."""
    node = request.app.state.node
    return {
        "events": node.activity.recent(limit=limit, event_type=type),
        "stats": node.activity.stats(),
        "sparklines": {
            "requests": node.activity.sparkline("requests", 30),
            "tokens": node.activity.sparkline("tokens", 30),
            "credits_earned": node.activity.sparkline("credits_earned", 30),
            "credits_spent": node.activity.sparkline("credits_spent", 30),
        },
    }


@router.get("/activity/stream")
async def stream_activity(request: Request):
    """Stream activity events via SSE."""
    node = request.app.state.node
    q = node.activity.subscribe()

    async def generate():
        try:
            while True:
                event = await q.get()
                yield json.dumps(event.to_dict())
        except asyncio.CancelledError:
            pass
        finally:
            node.activity.unsubscribe(q)

    return EventSourceResponse(generate())
