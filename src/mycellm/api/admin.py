"""Admin API endpoints — node registry, approval, fleet management."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request

logger = logging.getLogger("mycellm.admin")

router = APIRouter()


def _registry_path(node) -> Path:
    """Path to the persisted registry file."""
    return node._settings.data_dir / "node_registry.json"


def _save_registry(node) -> None:
    """Persist the registry to disk."""
    try:
        path = _registry_path(node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(node.node_registry, indent=2))
    except Exception as e:
        logger.debug(f"Failed to save registry: {e}")


def _load_registry(node) -> None:
    """Load persisted registry from disk into node.node_registry."""
    try:
        path = _registry_path(node)
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                node.node_registry.update(data)
                logger.info(f"Loaded {len(data)} node(s) from registry")
    except Exception as e:
        logger.debug(f"Failed to load registry: {e}")


@router.post("/nodes/announce")
async def announce_node(request: Request):
    """Accept a node announcement. Called by seeder nodes on startup."""
    node = request.app.state.node
    body = await request.json()
    peer_id = body.get("peer_id", "")
    if not peer_id:
        return {"error": "peer_id required"}

    # Detect the announcing node's actual IP from the request
    client_ip = request.client.host if request.client else "unknown"
    api_addr = body.get("api_addr", "")
    # If api_addr has 0.0.0.0, replace with actual IP
    if api_addr.startswith("0.0.0.0:"):
        port = api_addr.split(":")[1]
        api_addr = f"{client_ip}:{port}"

    existing = node.node_registry.get(peer_id, {})
    status = existing.get("status", "pending")

    node.node_registry[peer_id] = {
        "peer_id": peer_id,
        "node_name": body.get("node_name", ""),
        "api_addr": api_addr,
        "role": body.get("role", "seeder"),
        "capabilities": body.get("capabilities", {}),
        "system": body.get("system", {}),
        "status": status,  # preserve approval status
        "last_seen": time.time(),
        "first_seen": existing.get("first_seen", time.time()),
        "ip": client_ip,
    }
    _save_registry(node)

    return {"status": "ok", "node_status": status}


@router.get("/nodes")
async def list_nodes(request: Request):
    """List all registered nodes."""
    node = request.app.state.node
    nodes = []
    for entry in node.node_registry.values():
        # Check if node is reachable (seen in last 120s)
        age = time.time() - entry.get("last_seen", 0)
        entry["online"] = age < 120
        nodes.append(entry)
    return {"nodes": nodes}


@router.post("/nodes/{peer_id}/approve")
async def approve_node(peer_id: str, request: Request):
    """Approve a pending node."""
    node = request.app.state.node
    entry = node.node_registry.get(peer_id)
    if not entry:
        return {"error": "node not found"}
    entry["status"] = "approved"
    _save_registry(node)
    return {"status": "approved", "peer_id": peer_id}


@router.post("/nodes/{peer_id}/remove")
async def remove_node(peer_id: str, request: Request):
    """Remove a node from the registry."""
    node = request.app.state.node
    removed = node.node_registry.pop(peer_id, None)
    if not removed:
        return {"error": "node not found"}
    _save_registry(node)
    return {"status": "removed", "peer_id": peer_id}
