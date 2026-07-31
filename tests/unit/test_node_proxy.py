"""Tests for the dashboard's same-origin fleet proxy — POST /v1/node/proxy.

The dashboard needs to reach other fleet nodes' HTTP APIs for browsing and
loading remote models, but the browser must never fetch a remote node's
origin directly with the local admin session's Authorization header
attached — that would hand a real credential to whatever host the
dashboard's "node address" field names. These tests assert the proxy only
relays to node_addr values already approved in node.node_registry, and that
it builds the outbound request without the local api_key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from mycellm.api.app import create_app


def _make_node(node_registry=None):
    class FakeNode:
        def __init__(self):
            self.peer_id = "proxy_test_peer"
            self.node_registry = node_registry if node_registry is not None else {}

    return FakeNode()


def _settings(api_key="admin_secret_key"):
    settings = MagicMock()
    settings.api_key = api_key
    settings.public = False
    return settings


@pytest.mark.anyio
async def test_proxy_requires_auth():
    """The proxy route stays behind AuthMiddleware like other /v1/node/* routes."""
    with patch("mycellm.config.get_settings", return_value=_settings()):
        node = _make_node()
        app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/node/proxy", json={
            "node_addr": "10.0.0.5:8420", "path": "/v1/node/models/local",
        })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_proxy_rejects_unapproved_target():
    """A pending (not-yet-approved) registry entry is not a valid proxy target."""
    settings = _settings()
    with patch("mycellm.config.get_settings", return_value=settings):
        node = _make_node(node_registry={
            "peer1": {"api_addr": "10.0.0.5:8420", "status": "pending"},
        })
        app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/node/proxy",
            json={"node_addr": "10.0.0.5:8420", "path": "/v1/node/models/local"},
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
    assert resp.status_code == 200
    assert resp.json()["error"] == "node not approved"


@pytest.mark.anyio
async def test_proxy_rejects_unknown_target():
    """An address the browser supplies that isn't in the registry at all is refused."""
    settings = _settings()
    with patch("mycellm.config.get_settings", return_value=settings):
        node = _make_node()  # empty registry
        app = create_app(node)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/node/proxy",
            json={"node_addr": "10.9.9.9:8420", "path": "/v1/node/models/local"},
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
    assert resp.status_code == 200
    assert resp.json()["error"] == "node not approved"


@pytest.mark.anyio
async def test_proxy_forwards_without_local_api_key():
    """The outbound request the proxy builds must never carry the local admin
    api_key, even when relaying to a target already approved in the registry."""
    settings = _settings()
    with patch("mycellm.config.get_settings", return_value=settings):
        node = _make_node(node_registry={
            "peer1": {"api_addr": "10.0.0.5:8420", "status": "approved"},
        })
        app = create_app(node)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"files": []}

    with patch("mycellm.api.node.httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.request = AsyncMock(return_value=mock_response)
        MockClient.return_value = client_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            resp = await http_client.post(
                "/v1/node/proxy",
                json={"node_addr": "10.0.0.5:8420", "path": "/v1/node/models/local"},
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"files": []}

    assert client_instance.request.call_count == 1
    call = client_instance.request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "http://10.0.0.5:8420/v1/node/models/local"
    outbound_headers = call.kwargs.get("headers", {})
    assert "Authorization" not in outbound_headers
    assert settings.api_key not in outbound_headers.values()
