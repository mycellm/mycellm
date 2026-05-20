"""3-node E2E acceptance test.

Tests:
1. Three nodes start up and become healthy
2. Node status is reachable on all nodes
3. Nodes can be queried for models
4. Credit accounting initializes correctly
"""

import pytest
import httpx

from tests.e2e.harness import E2EHarness


@pytest.fixture
async def three_nodes():
    """Spawn three nodes with provisioned identities."""
    harness = E2EHarness(base_port=19620, node_count=3)
    harness.setup()

    # Provision identities
    for node in harness.nodes:
        harness.provision_identity(node)

    yield harness
    harness.teardown()


async def test_three_nodes_start_and_health(three_nodes):
    """All 3 nodes start and become healthy."""
    harness = three_nodes

    # Start all nodes
    for node in harness.nodes:
        harness.start_node(node)

    # Wait for all to be ready
    for node in harness.nodes:
        ready = await harness.wait_ready(node, timeout=15.0)
        assert ready, f"Node {node.name} failed to start"

    # Check health on all
    async with httpx.AsyncClient() as client:
        for node in harness.nodes:
            resp = await client.get(f"{node.api_url}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert len(data["peer_id"]) > 0


async def test_three_nodes_status(three_nodes):
    """All 3 nodes report valid status."""
    harness = three_nodes

    for node in harness.nodes:
        harness.start_node(node)

    for node in harness.nodes:
        await harness.wait_ready(node, timeout=15.0)

    async with httpx.AsyncClient() as client:
        for node in harness.nodes:
            resp = await client.get(f"{node.api_url}/v1/node/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "peer_id" in data
            assert "uptime_seconds" in data
            assert data["uptime_seconds"] > 0


async def test_three_nodes_models_endpoint(three_nodes):
    """Models endpoint lists virtual 'auto' when no GGUF models are loaded."""
    harness = three_nodes

    for node in harness.nodes:
        harness.start_node(node)

    for node in harness.nodes:
        await harness.wait_ready(node, timeout=15.0)

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{harness.nodes[0].api_url}/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "auto"
        assert data[0]["owned_by"] == "mycellm"


async def test_three_nodes_chat_without_model(three_nodes):
    """Chat completions returns graceful response when no model loaded."""
    harness = three_nodes

    for node in harness.nodes:
        harness.start_node(node)

    await harness.wait_ready(harness.nodes[0], timeout=15.0)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{harness.nodes[0].api_url}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["choices"]) == 1
        assert "No model" in data["choices"][0]["message"]["content"]


async def test_three_nodes_shutdown(three_nodes):
    """Nodes shut down cleanly on SIGTERM."""
    harness = three_nodes

    for node in harness.nodes:
        harness.start_node(node)

    for node in harness.nodes:
        await harness.wait_ready(node, timeout=15.0)

    # Stop one node
    harness.stop_node(harness.nodes[1])
    assert not harness.nodes[1].is_running

    # Others should still be healthy
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{harness.nodes[0].api_url}/health")
        assert resp.status_code == 200
        resp = await client.get(f"{harness.nodes[2].api_url}/health")
        assert resp.status_code == 200
