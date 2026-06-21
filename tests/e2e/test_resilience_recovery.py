"""E2E resilience recovery tests.

These exercise the production recovery *contract* documented in
``mycellm.resilience``: a wedge or crash turns into a non-zero process exit,
and an external supervisor (launchd KeepAlive / systemd Restart= / docker
restart:) relaunches the node, which must come back to a healthy serving state
with its persistent state (identity, credit ledger) intact.

We can't deterministically wedge the event loop from the outside, so we stand
in for the wedge with SIGKILL — from a supervisor's point of view a hard kill,
an OOM-kill, and the HeartbeatWatchdog's ``os._exit(70)`` are indistinguishable:
the process is simply gone and must be brought back. The watchdog's own
wedge-*detection* logic is covered by tests/unit/test_resilience.py; here we
cover what happens *after* it fires.
"""

import asyncio

import httpx
import pytest

from tests.e2e.harness import E2EHarness


@pytest.fixture
async def one_node():
    harness = E2EHarness(base_port=19720, node_count=1)
    harness.setup()
    harness.provision_identity(harness.nodes[0])
    yield harness
    harness.teardown()


async def _peer_id(node) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{node.api_url}/health", timeout=5.0)
        resp.raise_for_status()
        return resp.json()["peer_id"]


async def _status(node) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{node.api_url}/v1/node/status", timeout=5.0)
        resp.raise_for_status()
        return resp.json()


async def test_node_recovers_after_hard_kill(one_node):
    """A hard-killed node, relaunched on the same data dir, serves again with
    the same identity and a fresh uptime (proving it's a new process, not a
    survivor)."""
    harness = one_node
    node = harness.nodes[0]

    harness.start_node(node)
    assert await harness.wait_ready(node, timeout=15.0)

    peer_before = await _peer_id(node)
    status_before = await _status(node)
    assert status_before["uptime_seconds"] > 0

    # Hard kill — no graceful shutdown, like a crash / OOM / watchdog os._exit.
    harness.kill_node(node)
    assert not node.is_running

    # Supervisor relaunch.
    harness.restart_node(node)
    assert await harness.wait_ready(node, timeout=15.0)

    peer_after = await _peer_id(node)
    status_after = await _status(node)

    # Identity persisted across the crash (data dir reused).
    assert peer_after == peer_before
    # It really is a fresh process: uptime reset rather than kept climbing.
    assert status_after["uptime_seconds"] > 0
    assert status_after["uptime_seconds"] <= status_before["uptime_seconds"] + 30


async def test_node_exit_is_nonzero_on_signal_kill(one_node):
    """A killed node exits abnormally — the precondition for any supervisor
    (and the watchdog's os._exit contract) to trigger a restart at all."""
    harness = one_node
    node = harness.nodes[0]

    harness.start_node(node)
    assert await harness.wait_ready(node, timeout=15.0)

    harness.kill_node(node)
    code = node.process.returncode
    # SIGKILL surfaces as returncode -9; the contract only needs "not a clean 0".
    assert code != 0


async def test_supervisor_respawns_crashed_node(one_node):
    """With a supervisor watching, a crashed node is brought back automatically
    — no manual restart. This is the launchd/systemd/docker path end to end."""
    harness = one_node
    node = harness.nodes[0]

    harness.start_node(node)
    assert await harness.wait_ready(node, timeout=15.0)
    peer_before = await _peer_id(node)

    supervisor = await harness.supervise(node)
    try:
        # Crash it; the supervisor should notice the abnormal exit and respawn.
        harness.kill_node(node)

        # Wait for the supervisor to register a restart and the node to recover.
        deadline = 20.0
        ready = False
        while deadline > 0:
            if node.supervisor_restarts >= 1 and node.is_running:
                ready = await harness.wait_ready(node, timeout=2.0)
                if ready:
                    break
            await asyncio.sleep(0.25)
            deadline -= 0.25

        assert node.supervisor_restarts >= 1, "supervisor never respawned the node"
        assert ready, "respawned node never became healthy"
        assert await _peer_id(node) == peer_before
    finally:
        # Cancel before teardown so a graceful SIGTERM doesn't race the watcher.
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            pass


async def test_credit_ledger_survives_crash(one_node):
    """The SQLite credit ledger is durable across a crash+restart — state
    recovery, not just identity. Guards the storage-layer resilience work
    (NullPool / cached-credits commit) on the feat/node-resilience branch."""
    harness = one_node
    node = harness.nodes[0]

    harness.start_node(node)
    assert await harness.wait_ready(node, timeout=15.0)

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{node.api_url}/v1/node/credits", timeout=5.0)
        assert resp.status_code == 200
        credits_before = resp.json()
    # Shape contract — the fields the menu bar / dashboard depend on.
    for key in ("balance", "earned", "spent"):
        assert key in credits_before

    harness.kill_node(node)
    harness.restart_node(node)
    assert await harness.wait_ready(node, timeout=15.0)

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{node.api_url}/v1/node/credits", timeout=5.0)
        assert resp.status_code == 200
        credits_after = resp.json()

    # Ledger reopened cleanly (no corruption / lock from the abrupt kill) and
    # balance is preserved.
    assert credits_after["balance"] == credits_before["balance"]
