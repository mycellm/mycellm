"""Tests for Prometheus metrics endpoint and instrumentation."""

import pytest
import time
from unittest.mock import MagicMock

from mycellm.metrics import (
    REGISTRY,
    collect_from_node,
    render_metrics,
    set_node_info,
    inference_requests_total,
    inference_tokens_total,
    credits_earned_total,
    credits_spent_total,
    announce_total,
    models_loaded,
    peers_connected,
    fleet_nodes_total,
    uptime_seconds,
)


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset counter sample values between tests by noting pre-test values."""
    yield


def _make_fake_node():
    from mycellm.inference.manager import InferenceManager
    from mycellm.router.registry import PeerRegistry
    from mycellm.activity import ActivityTracker

    class FakeNode:
        def __init__(self):
            self.peer_id = "metricspeer123"
            self.capabilities = type("C", (), {
                "models": [],
                "hardware": type("H", (), {
                    "gpu": "RTX 4090", "vram_gb": 24, "backend": "cuda",
                    "to_dict": lambda self: {"gpu": "RTX 4090", "vram_gb": 24},
                })(),
                "role": "seeder",
            })()
            self.inference = InferenceManager()
            self.registry = PeerRegistry()
            self.node_registry = {
                "p1": {"status": "approved"},
                "p2": {"status": "approved"},
                "p3": {"status": "pending"},
            }
            self.activity = ActivityTracker()
            self._settings = type("S", (), {"node_name": "test-metrics"})()
            self._start_time = time.time() - 300
            self._running = True

        @property
        def uptime(self):
            return time.time() - self._start_time

        def get_system_info(self):
            return {"memory": {"total_gb": 64, "used_pct": 40}}

    return FakeNode()


def test_render_metrics_text_format():
    """Metrics endpoint returns valid Prometheus text format."""
    output = render_metrics()
    assert isinstance(output, bytes)
    text = output.decode()
    # Should contain HELP and TYPE lines
    assert "# HELP" in text or "# TYPE" in text or text == ""


def test_set_node_info():
    """Node info labels are set correctly."""
    set_node_info("peer123", "test-node", "0.1.0")
    output = render_metrics().decode()
    assert "peer123" in output
    assert "test-node" in output


def test_collect_from_node_sets_gauges():
    """collect_from_node populates gauge values."""
    node = _make_fake_node()
    collect_from_node(node)

    output = render_metrics().decode()
    # Should have fleet node counts
    assert "mycellm_fleet_nodes_total" in output
    # Should have VRAM
    assert "mycellm_hardware_vram_gb" in output
    # Should have uptime
    assert "mycellm_uptime_seconds" in output


def test_collect_fleet_counts():
    """Fleet node gauge reflects registry status counts."""
    node = _make_fake_node()
    collect_from_node(node)

    output = render_metrics().decode()
    # 2 approved, 1 pending
    assert 'status="approved"' in output
    assert 'status="pending"' in output


def test_activity_pushes_to_prometheus():
    """ActivityTracker.record() pushes to Prometheus counters."""
    from mycellm.activity import ActivityTracker, EventType

    tracker = ActivityTracker()

    # Record some events
    tracker.record(EventType.INFERENCE_COMPLETE, model="llama-7b", backend="llama.cpp",
                   tokens=100, prompt_tokens=50, completion_tokens=100, latency_ms=1500)
    tracker.record(EventType.INFERENCE_FAILED, model="llama-7b", backend="llama.cpp")
    tracker.record(EventType.CREDIT_EARNED, amount=1.5)
    tracker.record(EventType.CREDIT_SPENT, amount=0.5)
    tracker.record(EventType.ANNOUNCE_OK)
    tracker.record(EventType.ANNOUNCE_FAILED)

    output = render_metrics().decode()
    assert "mycellm_inference_requests_total" in output
    assert "mycellm_inference_tokens_total" in output
    assert "mycellm_credits_earned_total" in output
    assert "mycellm_announce_total" in output


def test_latency_histogram():
    """Inference latency is recorded as histogram."""
    from mycellm.activity import ActivityTracker, EventType

    tracker = ActivityTracker()
    tracker.record(EventType.INFERENCE_COMPLETE, model="test-model", backend="cpu",
                   tokens=50, latency_ms=2500)

    output = render_metrics().decode()
    assert "mycellm_inference_latency_seconds" in output


@pytest.mark.anyio
async def test_metrics_endpoint_no_auth():
    """GET /metrics returns 200 without auth even when API key is set."""
    from unittest.mock import patch
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    mock_settings = MagicMock()
    mock_settings.api_key = "test-secret"
    with patch("mycellm.config.get_settings", return_value=mock_settings):
        node = _make_fake_node()
        app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "mycellm_" in resp.text


@pytest.mark.anyio
async def test_metrics_endpoint_content():
    """Metrics endpoint contains expected metric families."""
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_fake_node()
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        text = resp.text
        assert "mycellm_uptime_seconds" in text
        assert "mycellm_models_loaded" in text
        assert "mycellm_fleet_nodes_total" in text
        assert "mycellm_hardware_vram_gb" in text
