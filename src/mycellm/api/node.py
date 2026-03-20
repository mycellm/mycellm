"""Node management API endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

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

    try:
        loaded_name = await node.inference.load_model(
            model_path,
            name=name,
            backend_type=backend_type,
            api_base=body.get("api_base", ""),
            api_key=body.get("api_key", ""),
            api_model=body.get("api_model", ""),
            ctx_len=body.get("ctx_len", 4096),
            timeout=body.get("timeout", 120),
        )
        # Update capabilities and announce to peers
        node.capabilities.models = node.inference.loaded_models
        await node.announce_capabilities()
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
    return {"status": "unloaded", "model": model_name}


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
