"""Tests for public API gateway — rate limiting, tier restriction, metadata stripping."""

import pytest
import time
from unittest.mock import MagicMock

from mycellm.protocol.capabilities import classify_tier, ModelCapability


# --- Tier classification ---

def test_tier1_small_models():
    assert classify_tier(1.0) == 1
    assert classify_tier(3.0) == 1
    assert classify_tier(7.0) == 1
    assert classify_tier(8.0) == 1


def test_tier2_medium_models():
    assert classify_tier(13.0) == 2
    assert classify_tier(34.0) == 2
    assert classify_tier(70.0) == 2


def test_tier3_large_models():
    assert classify_tier(72.0) == 3
    assert classify_tier(200.0) == 3
    assert classify_tier(405.0) == 3


def test_tier_unknown_defaults_to_1():
    assert classify_tier(0) == 1
    assert classify_tier(-1) == 1


# --- Rate limiting ---

def test_rate_limit_allows_normal_usage():
    from mycellm.api.gateway import _check_rate, _rate_state
    _rate_state.clear()
    allowed, _ = _check_rate("10.0.0.1", 100)
    assert allowed is True


def test_rate_limit_blocks_over_budget():
    from mycellm.api.gateway import _check_rate, _record_usage, _rate_state
    _rate_state.clear()
    _record_usage("10.0.0.2", 4900)
    allowed, reason = _check_rate("10.0.0.2", 200)
    assert allowed is False
    assert "limit" in reason.lower()


def test_rate_limit_per_minute():
    from mycellm.api.gateway import _check_rate, _rate_state
    _rate_state.clear()
    # Set the minute to current so _check_rate sees the same window
    current_minute = int(time.time() / 60)
    for _ in range(10):
        state = _rate_state["10.0.0.3"]
        state["minute"] = current_minute
        state["requests_minute"] += 1
    allowed, reason = _check_rate("10.0.0.3")
    assert allowed is False
    assert "requests" in reason.lower()


def test_rate_limit_per_ip():
    from mycellm.api.gateway import _check_rate, _record_usage, _rate_state
    _rate_state.clear()
    _record_usage("10.0.0.4", 5000)
    allowed_blocked, _ = _check_rate("10.0.0.4", 1)
    allowed_other, _ = _check_rate("10.0.0.5", 1)
    assert allowed_blocked is False
    assert allowed_other is True


# --- Model selection ---

class _FakeRegistry:
    def connected_peers(self):
        return []

def test_select_tier1_model():
    from mycellm.api.gateway import _select_tier1_model

    class FakeNode:
        node_registry = {}
        registry = _FakeRegistry()
        registry = _FakeRegistry()
        class inference:
            loaded_models = [
                ModelCapability(name="llama-70b", param_count_b=70.0),
                ModelCapability(name="qwen-7b", param_count_b=7.0),
                ModelCapability(name="phi-3b", param_count_b=3.0),
            ]
    name, addr = _select_tier1_model(FakeNode())
    assert name in ("qwen-7b", "phi-3b")  # both Tier 1, round-robin picks either
    assert addr is None  # local model


def test_select_tier1_no_models():
    from mycellm.api.gateway import _select_tier1_model

    class FakeNode:
        node_registry = {}
        registry = _FakeRegistry()
        class inference:
            loaded_models = []
    name, addr = _select_tier1_model(FakeNode())
    assert name is None


def test_select_tier1_fallback_unknown_params():
    from mycellm.api.gateway import _select_tier1_model

    class FakeNode:
        node_registry = {}
        registry = _FakeRegistry()
        class inference:
            loaded_models = [
                ModelCapability(name="mystery-model", param_count_b=0),
            ]
    name, addr = _select_tier1_model(FakeNode())
    assert name == "mystery-model"


def test_select_tier1_from_fleet():
    """Falls back to fleet node when no local models."""
    from mycellm.api.gateway import _select_tier1_model

    class FakeNode:
        registry = _FakeRegistry()
        node_registry = {
            "peer1": {
                "status": "approved",
                "last_seen": time.time(),
                "api_addr": "10.0.0.5:8420",
                "capabilities": {"models": [{"name": "qwen-3b", "param_count_b": 3.0}]},
            }
        }
        class inference:
            loaded_models = []
    name, addr = _select_tier1_model(FakeNode())
    assert name == "qwen-3b"
    assert addr == "10.0.0.5:8420"


# --- Gateway endpoint ---

def _make_gateway_node():
    from mycellm.inference.manager import InferenceManager
    from mycellm.router.registry import PeerRegistry
    from mycellm.activity import ActivityTracker
    from mycellm.api.gateway import _rate_state
    _rate_state.clear()

    class FakeNode:
        def __init__(self):
            self.peer_id = "gatewaypeer123"
            self.capabilities = type("C", (), {
                "models": [], "role": "seeder",
                "hardware": type("H", (), {
                    "to_dict": lambda s: {}, "gpu": "CPU", "vram_gb": 0, "backend": "cpu",
                })(),
            })()
            self.inference = InferenceManager()
            self.registry = PeerRegistry()
            self.node_registry = {}
            self.activity = ActivityTracker()
            self.federation = None
            self.peer_manager = type("PM", (), {"get_connections": lambda s: []})()
            self.ledger = None
            self._start_time = time.time()
            self._running = True
            self.secret_store = None
            self.reputation = type("R", (), {})()
            self.device_key = None
            self.device_cert = None
            self.account_key = None
            self._settings = type("S", (), {
                "node_name": "test", "api_key": "", "bootstrap_peers": "",
                "data_dir": MagicMock(), "config_dir": MagicMock(),
                "db_url": "", "hf_token": "", "log_level": "INFO", "telemetry": False,
            })()

        @property
        def uptime(self):
            return time.time() - self._start_time

        def get_status(self):
            return {"node_name": "test", "peer_id": self.peer_id, "uptime_seconds": self.uptime,
                    "role": "seeder", "hardware": {}, "peers": [], "models": [],
                    "inference": {"active": 0, "max_concurrent": 2}}

        def get_system_info(self):
            return {"memory": {"total_gb": 16}}

        async def get_credits(self):
            return {"balance": 100}

    return FakeNode()


@pytest.mark.anyio
async def test_gateway_no_auth_required():
    """Public chat endpoint works without auth even when API key is set."""
    from unittest.mock import patch
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    mock_settings = MagicMock()
    mock_settings.api_key = "secret"
    with patch("mycellm.config.get_settings", return_value=mock_settings):
        node = _make_gateway_node()
        app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Should get 503 (no models loaded) not 401 (unauthorized)
        resp = await client.post("/v1/public/chat/completions", json={
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 503
        assert "No models" in resp.json()["error"]["message"]


@pytest.mark.anyio
async def test_gateway_empty_messages():
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_gateway_node()
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/public/chat/completions", json={"messages": []})
        assert resp.status_code == 400


@pytest.mark.anyio
async def test_gateway_message_too_long():
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_gateway_node()
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/public/chat/completions", json={
            "messages": [{"role": "user", "content": "x" * 3000}],
        })
        assert resp.status_code == 400
        assert "too long" in resp.json()["error"]["message"].lower()


@pytest.mark.anyio
async def test_gateway_response_has_no_sensitive_data():
    """Response should not contain peer IDs, routing info, or credit data."""
    # This test verifies the response structure is clean
    # (actual inference would need a loaded model, so we test the error path
    # and the response format for the 503 case)
    from httpx import ASGITransport, AsyncClient
    from mycellm.api.app import create_app

    node = _make_gateway_node()
    app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/public/chat/completions", json={
            "messages": [{"role": "user", "content": "test"}],
        })
        text = resp.text
        assert "gatewaypeer" not in text
        assert "credit" not in text.lower()
        assert "api_key" not in text


# --- Caller-requested model and tier (the public chat box's selector) ---
#
# The public box defaults to Auto and stays there, but a visitor who picks
# "Frontier" is making a claim about what they want, not a suggestion. These
# tests fix the two halves of honouring that: narrow the field, and when the
# field is empty say so rather than serving whatever happened to be online.

def _node_with(models):
    """A node serving `models` as (name, param_count_b) locally."""
    node = MagicMock()
    node.inference.loaded_models = [
        MagicMock(name=n, param_count_b=p, backend="mlx") for n, p in models
    ]
    # MagicMock's `name` kwarg is reserved, so set it after construction.
    for m, (n, _) in zip(node.inference.loaded_models, models):
        m.name = n
    node.registry.connected_peers.return_value = []
    node.node_registry = {}
    return node


def test_candidates_unfiltered_by_default():
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0), ("mid-9b", 9.0)])
    assert len(_get_candidates(node)) == 2


def test_tier_floor_drops_smaller_models():
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0), ("mid-9b", 9.0), ("big-70b", 70.0)])
    names = [c[0] for c in _get_candidates(node, min_tier="capable")]
    assert names == ["big-70b"]


def test_tier_floor_can_empty_the_field():
    """An empty result is the correct answer, not a bug to paper over.

    Falling back to the whole list here would silently serve a 1B model to
    someone who asked for frontier — which is exactly the behaviour that makes
    a tier control meaningless.
    """
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0)])
    assert _get_candidates(node, min_tier="frontier") == []


def test_named_model_narrows_to_one():
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0), ("mid-9b", 9.0)])
    assert [c[0] for c in _get_candidates(node, want_model="mid-9b")] == ["mid-9b"]


def test_named_model_that_is_not_served_yields_nothing():
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0)])
    assert _get_candidates(node, want_model="not-here-13b") == []


def test_unknown_tier_name_is_ignored_by_the_filter():
    """Validation belongs at the endpoint, which 400s. The collector must not
    silently treat a typo as "no models available"."""
    from mycellm.api.gateway import _get_candidates
    node = _node_with([("tiny-1b", 1.0)])
    assert len(_get_candidates(node, min_tier="enormous")) == 1
