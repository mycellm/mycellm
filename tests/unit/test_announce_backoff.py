"""Per-bootstrap backoff for failing HTTP announces.

A bootstrap that keeps refusing the HTTP announce must be skipped for an
exponentially growing (capped) delay, while the reachable bootstraps keep the
normal announce cadence. Backoff clears on a successful announce and on a
forced re-announce (address-change self-heal).
"""

import asyncio
import time
import types

import httpx
import pytest

from mycellm.activity import ActivityTracker, EventType
from mycellm.node import (
    ANNOUNCE_BACKOFF_BASE_S,
    ANNOUNCE_BACKOFF_MAX_S,
    MycellmNode,
)

GOOD = "http://good.example:8420/v1/admin/nodes/announce"
BAD = "http://bad.example:8420/v1/admin/nodes/announce"


def _backoff_node():
    """Minimal node carrying just the announce-backoff state + helpers."""
    node = types.SimpleNamespace()
    node._announce_backoff = {}
    for name in ("_announce_backoff_remaining", "_announce_backoff_bump", "_announce_backoff_reset"):
        setattr(node, name, types.MethodType(getattr(MycellmNode, name), node))
    return node


def test_backoff_grows_exponentially_from_the_base():
    node = _backoff_node()
    delays = [node._announce_backoff_bump(BAD) for _ in range(4)]
    assert delays == [
        ANNOUNCE_BACKOFF_BASE_S,
        ANNOUNCE_BACKOFF_BASE_S * 2,
        ANNOUNCE_BACKOFF_BASE_S * 4,
        ANNOUNCE_BACKOFF_BASE_S * 8,
    ]
    # A host in backoff is not due yet; an untouched host always is.
    assert node._announce_backoff_remaining(BAD) > 0
    assert node._announce_backoff_remaining(GOOD) == 0


def test_backoff_is_capped():
    node = _backoff_node()
    for _ in range(50):
        delay = node._announce_backoff_bump(BAD)
    assert delay == ANNOUNCE_BACKOFF_MAX_S
    assert node._announce_backoff_remaining(BAD) <= ANNOUNCE_BACKOFF_MAX_S


def test_expired_backoff_is_due_again_and_keeps_growing():
    node = _backoff_node()
    node._announce_backoff_bump(BAD)
    # Simulate the delay having elapsed.
    delay, _skip_until = node._announce_backoff[BAD]
    node._announce_backoff[BAD] = (delay, time.monotonic() - 1)
    assert node._announce_backoff_remaining(BAD) == 0
    # Retrying and failing again doubles rather than restarting at the base.
    assert node._announce_backoff_bump(BAD) == ANNOUNCE_BACKOFF_BASE_S * 2


def test_backoff_resets_on_success():
    node = _backoff_node()
    node._announce_backoff_bump(BAD)
    node._announce_backoff_bump(BAD)
    node._announce_backoff_reset(BAD)
    assert node._announce_backoff_remaining(BAD) == 0
    # Next failure starts over at the base delay.
    assert node._announce_backoff_bump(BAD) == ANNOUNCE_BACKOFF_BASE_S


@pytest.mark.asyncio
async def test_force_announce_clears_backoff_for_every_host():
    """Address-change self-heal re-announces everywhere immediately."""
    node = _backoff_node()
    node._force_announce = asyncio.Event()
    node._announce_backoff_bump(BAD)
    node._announce_backoff_bump(GOOD)

    node._force_announce.set()
    await MycellmNode._wait_or_forced(node, 30)

    assert node._announce_backoff == {}
    assert not node._force_announce.is_set()  # request consumed


@pytest.mark.asyncio
async def test_timed_out_wait_leaves_backoff_intact():
    """A normal cycle tick must not clear backoff — only a forced announce does."""
    node = _backoff_node()
    node._force_announce = asyncio.Event()
    node._announce_backoff_bump(BAD)

    await MycellmNode._wait_or_forced(node, 0.01)

    assert BAD in node._announce_backoff


# --- announce loop -----------------------------------------------------------


class _FakeSettings:
    node_name = "test-node"
    api_key = ""
    external_host = ""
    telemetry = False

    def get_bootstrap_list(self):
        return [("good.example", 8420), ("bad.example", 8420)]


class _FakeCapabilities:
    role = "seeder"

    def to_dict(self):
        return {"models": [], "role": self.role}


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _fake_httpx_client(posted):
    """AsyncClient stand-in recording every announce URL; bad.example refuses."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            posted.append(url)
            if "bad.example" in url:
                raise ConnectionError("connection refused")
            return _FakeResponse(200)

    return _FakeClient


def _announce_node(rounds):
    """Fake node that runs the real announce loop for ``rounds`` re-announces."""
    node = types.SimpleNamespace()
    node._settings = _FakeSettings()
    node.peer_id = "test_peer_id_1234"
    node.api_host = "127.0.0.1"
    node.api_port = 8420
    node.capabilities = _FakeCapabilities()
    node.activity = ActivityTracker()
    node.inference = types.SimpleNamespace(loaded_models=[])
    node.uptime = 1.0
    node._running = True
    node._announce_backoff = {}
    node.get_system_info = lambda: {"os": "test"}

    async def _announce_capabilities():
        return None

    node.announce_capabilities = _announce_capabilities

    remaining = {"n": rounds}

    async def _wait_or_forced(timeout):
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            node._running = False

    node._wait_or_forced = _wait_or_forced
    for name in ("_announce_backoff_remaining", "_announce_backoff_bump", "_announce_backoff_reset"):
        setattr(node, name, types.MethodType(getattr(MycellmNode, name), node))
    return node


@pytest.mark.asyncio
async def test_failing_bootstrap_is_skipped_while_healthy_one_keeps_cadence(monkeypatch):
    posted = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx_client(posted))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: None)

    node = _announce_node(rounds=4)
    await MycellmNode._announce_to_bootstrap(node)

    # 1 initial announce + 4 loop rounds; the healthy host is announced to every
    # time, the dead one only on the first attempt (then it is backing off).
    assert posted.count(GOOD) == 5
    assert posted.count(BAD) == 1
    assert node._announce_backoff_remaining(BAD) > 0

    ok = node.activity.recent(50, event_type=EventType.ANNOUNCE_OK.value)
    assert [e["bootstrap"] for e in ok] == [GOOD] * 5

    failures = node.activity.recent(50, event_type=EventType.ANNOUNCE_FAILED.value)
    assert len(failures) == 1
    assert failures[0]["bootstrap"] == BAD
    assert failures[0]["backoff_s"] == ANNOUNCE_BACKOFF_BASE_S


@pytest.mark.asyncio
async def test_successful_announce_clears_a_hosts_backoff(monkeypatch):
    posted = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx_client(posted))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: None)

    node = _announce_node(rounds=1)
    # The healthy host is carrying stale backoff that already expired.
    node._announce_backoff[GOOD] = (ANNOUNCE_BACKOFF_BASE_S, time.monotonic() - 1)

    await MycellmNode._announce_to_bootstrap(node)

    assert GOOD not in node._announce_backoff
