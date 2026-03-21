"""Prometheus metrics for mycellm nodes.

Exposes standard Prometheus metrics at /metrics for scraping.
All metrics are prefixed with `mycellm_`.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

# Dedicated registry (avoids default process/platform collectors)
REGISTRY = CollectorRegistry()

# --- Node info ---
node_info = Info(
    "mycellm_node",
    "Node identity and version",
    registry=REGISTRY,
)

# --- Inference ---
inference_requests_total = Counter(
    "mycellm_inference_requests_total",
    "Total inference requests processed",
    ["model", "backend", "status"],
    registry=REGISTRY,
)

inference_tokens_total = Counter(
    "mycellm_inference_tokens_total",
    "Total tokens generated",
    ["model", "direction"],  # direction: prompt | completion
    registry=REGISTRY,
)

inference_latency_seconds = Histogram(
    "mycellm_inference_latency_seconds",
    "Inference request latency in seconds",
    ["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=REGISTRY,
)

inference_active = Gauge(
    "mycellm_inference_active",
    "Currently running inference requests",
    registry=REGISTRY,
)

# --- Models ---
models_loaded = Gauge(
    "mycellm_models_loaded",
    "Number of currently loaded models",
    registry=REGISTRY,
)

# --- Credits ---
credits_balance = Gauge(
    "mycellm_credits_balance",
    "Current credit balance",
    registry=REGISTRY,
)

credits_earned_total = Counter(
    "mycellm_credits_earned_total",
    "Total credits earned from serving inference",
    registry=REGISTRY,
)

credits_spent_total = Counter(
    "mycellm_credits_spent_total",
    "Total credits spent consuming inference",
    registry=REGISTRY,
)

# --- Peers ---
peers_connected = Gauge(
    "mycellm_peers_connected",
    "Number of connected peers",
    registry=REGISTRY,
)

fleet_nodes_total = Gauge(
    "mycellm_fleet_nodes_total",
    "Total registered fleet nodes",
    ["status"],  # approved, pending
    registry=REGISTRY,
)

# --- Network ---
announce_total = Counter(
    "mycellm_announce_total",
    "Bootstrap announce attempts",
    ["result"],  # ok, failed
    registry=REGISTRY,
)

# --- Hardware ---
hardware_vram_gb = Gauge(
    "mycellm_hardware_vram_gb",
    "Available VRAM in GB",
    registry=REGISTRY,
)

hardware_ram_total_gb = Gauge(
    "mycellm_hardware_ram_total_gb",
    "Total system RAM in GB",
    registry=REGISTRY,
)

# --- Uptime ---
uptime_seconds = Gauge(
    "mycellm_uptime_seconds",
    "Node uptime in seconds",
    registry=REGISTRY,
)

# --- HTTP API ---
http_requests_total = Counter(
    "mycellm_http_requests_total",
    "Total HTTP API requests",
    ["method", "path", "status"],
    registry=REGISTRY,
)


def set_node_info(peer_id: str, node_name: str, version: str) -> None:
    """Set static node info labels."""
    node_info.info({
        "peer_id": peer_id,
        "node_name": node_name,
        "version": version,
    })


def collect_from_node(node) -> None:
    """Pull current state from a MycellmNode into gauges.

    Called before each /metrics scrape to ensure gauges reflect
    the latest state without requiring push on every change.
    """
    # Uptime
    uptime_seconds.set(node.uptime)

    # Models
    models_loaded.set(len(node.inference.loaded_models))

    # Peers
    peers_connected.set(len(node.registry.connected_peers()))

    # Fleet
    approved = sum(1 for n in node.node_registry.values() if n.get("status") == "approved")
    pending = sum(1 for n in node.node_registry.values() if n.get("status") == "pending")
    fleet_nodes_total.labels(status="approved").set(approved)
    fleet_nodes_total.labels(status="pending").set(pending)

    # Hardware
    hardware_vram_gb.set(node.capabilities.hardware.vram_gb)
    sys_info = node.get_system_info()
    hardware_ram_total_gb.set(sys_info.get("memory", {}).get("total_gb", 0))

    # Active inference
    inference_active.set(node.inference.active_count if hasattr(node.inference, "active_count") else 0)


def render_metrics() -> bytes:
    """Generate Prometheus text format output."""
    return generate_latest(REGISTRY)
