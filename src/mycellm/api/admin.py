"""Admin API endpoints — node registry, approval, fleet management."""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Request

logger = logging.getLogger("mycellm.admin")

router = APIRouter()

# Rate limit: track announce timestamps per IP for auto-approve
_announce_timestamps: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMIT_MAX = 10  # max new nodes per IP per window


async def _save_to_db(node, peer_id: str, data: dict) -> None:
    """Persist a single registry entry to the database."""
    if hasattr(node, "node_registry_repo") and node.node_registry_repo:
        try:
            await node.node_registry_repo.upsert(peer_id, data)
        except Exception as e:
            logger.debug(f"Failed to save node {peer_id[:16]} to DB: {e}")


async def _remove_from_db(node, peer_id: str) -> None:
    """Remove a registry entry from the database."""
    if hasattr(node, "node_registry_repo") and node.node_registry_repo:
        try:
            await node.node_registry_repo.remove(peer_id)
        except Exception as e:
            logger.debug(f"Failed to remove node {peer_id[:16]} from DB: {e}")


@router.post("/nodes/announce")
async def announce_node(request: Request):
    """Accept a node announcement. Called by seeder nodes on startup."""
    node = request.app.state.node
    body = await request.json()
    peer_id = body.get("peer_id", "")
    if not peer_id:
        return {"error": "peer_id required"}

    # Validate required fields
    capabilities = body.get("capabilities", {})
    if not capabilities:
        return {"error": "capabilities required"}

    # Detect the announcing node's actual IP from the request
    client_ip = request.client.host if request.client else "unknown"
    api_addr = body.get("api_addr", "")
    # If api_addr has 0.0.0.0, replace with actual IP
    if api_addr.startswith("0.0.0.0:"):
        port = api_addr.split(":")[1]
        api_addr = f"{client_ip}:{port}"

    # Use external_host from body if provided
    external_host = body.get("external_host", "")
    if external_host and api_addr:
        port = api_addr.split(":")[-1]
        api_addr = f"{external_host}:{port}"

    existing = node.node_registry.get(peer_id, {})
    status = existing.get("status", "pending")

    # Auto-approve for public networks
    is_new = peer_id not in node.node_registry
    if status == "pending" and _is_public_network(node):
        if is_new and not _check_rate_limit(client_ip):
            return {"error": "rate_limited", "message": "Too many new nodes from this IP"}
        status = "approved"
        if is_new:
            logger.info(f"Auto-approved node {peer_id[:16]}... (public network)")

    entry = {
        "peer_id": peer_id,
        "node_name": body.get("node_name", ""),
        "api_addr": api_addr,
        "role": body.get("role", "seeder"),
        "capabilities": capabilities,
        "system": body.get("system", {}),
        "status": status,
        "last_seen": time.time(),
        "first_seen": existing.get("first_seen", time.time()),
        "ip": client_ip,
    }

    # Store telemetry if provided (opt-in by announcing node)
    telemetry = body.get("telemetry")
    if telemetry and isinstance(telemetry, dict):
        entry["telemetry"] = telemetry

    node.node_registry[peer_id] = entry
    await _save_to_db(node, peer_id, entry)

    return {"status": "ok", "node_status": status}


def _is_public_network(node) -> bool:
    """Check if this node is running a public network."""
    if hasattr(node, "federation") and node.federation and node.federation.identity:
        return node.federation.identity.public
    return False


def _check_rate_limit(client_ip: str) -> bool:
    """Check if an IP is within the new-node rate limit. Returns True if allowed."""
    now = time.time()
    timestamps = _announce_timestamps[client_ip]
    # Prune old entries
    _announce_timestamps[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(_announce_timestamps[client_ip]) >= _RATE_LIMIT_MAX:
        return False
    _announce_timestamps[client_ip].append(now)
    return True


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
    await _save_to_db(node, peer_id, entry)
    return {"status": "approved", "peer_id": peer_id}


@router.post("/nodes/{peer_id}/remove")
async def remove_node(peer_id: str, request: Request):
    """Remove a node from the registry."""
    node = request.app.state.node
    removed = node.node_registry.pop(peer_id, None)
    if not removed:
        return {"error": "node not found"}
    await _remove_from_db(node, peer_id)
    return {"status": "removed", "peer_id": peer_id}
