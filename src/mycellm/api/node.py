"""Node management API endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from mycellm.activity import EventType

router = APIRouter()


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
    return {
        "node_name": node._settings.node_name,
        "bootstrap_peers": node._settings.bootstrap_peers,
        "bootstrap_parsed": [f"{h}:{p}" for h, p in node._settings.get_bootstrap_list()],
        "api_key_set": bool(node._settings.api_key),
        "announce_task_alive": node._announce_task is not None and not node._announce_task.done() if hasattr(node, '_announce_task') else False,
    }


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
            api_base=body.get("api_base", ""), api_key=body.get("api_key", ""),
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
