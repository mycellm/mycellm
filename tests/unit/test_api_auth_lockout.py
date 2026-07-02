"""Regression tests for ApiKeyMiddleware lockout vs valid keys.

The original bug: the per-IP lockout was checked BEFORE key validation, so an
unauthenticated localhost poller (menubar, keyless watchdog probe) could lock
127.0.0.1 and cause the node's own *authenticated* health checks to 429 —
the supervisor then restart-looped a perfectly healthy node.
"""

import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mycellm.api.app import ApiKeyMiddleware

KEY = "test-key-123"


def _app(public: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/node/status")
    async def status():
        return {"ok": True}

    app.add_middleware(ApiKeyMiddleware, api_key=KEY, public=public)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_valid_key_wins_even_while_ip_locked():
    app = _app()
    async with _client(app) as c:
        # Hammer unauthenticated until the IP is locked
        for _ in range(6):
            r = await c.get("/v1/node/status")
        assert r.status_code == 429  # locked

        # A correctly-keyed request from the SAME IP must still succeed
        r = await c.get("/v1/node/status", headers={"x-api-key": KEY})
        assert r.status_code == 200

        r = await c.get("/v1/node/status", headers={"Authorization": f"Bearer {KEY}"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_valid_key_resets_failure_state():
    app = _app()
    async with _client(app) as c:
        for _ in range(3):
            await c.get("/v1/node/status")
        r = await c.get("/v1/node/status", headers={"x-api-key": KEY})
        assert r.status_code == 200
        # Failure counter was reset — three more bad attempts don't lock (5 needed)
        for _ in range(3):
            r = await c.get("/v1/node/status")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_still_locks_out():
    app = _app()
    async with _client(app) as c:
        for _ in range(5):
            await c.get("/v1/node/status")
        r = await c.get("/v1/node/status")
        assert r.status_code == 429
        assert "locked" in r.json()["error"]


@pytest.mark.asyncio
async def test_bare_key_in_authorization_header_rejected():
    app = _app()
    async with _client(app) as c:
        r = await c.get("/v1/node/status", headers={"Authorization": KEY})
        assert r.status_code == 401
