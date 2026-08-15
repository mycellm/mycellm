"""Tests for security defaults and configuration."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mycellm.api.app import ApiKeyMiddleware
from mycellm.config.settings import MycellmSettings

# These assert the in-code defaults, so they must not read the developer's
# ~/.config/mycellm/.env (which commonly sets MYCELLM_QUIC_HOST=0.0.0.0).


def test_default_host_is_localhost():
    """Default bind should be localhost, not 0.0.0.0."""
    settings = MycellmSettings(_env_file=None)
    assert settings.api_host == "127.0.0.1"
    assert settings.quic_host == "127.0.0.1"


def test_default_api_key_empty():
    settings = MycellmSettings(_env_file=None)
    assert settings.api_key == ""


def test_default_initial_credits():
    settings = MycellmSettings(_env_file=None)
    assert settings.initial_credits == 100.0


KEY = "test-key-123"


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/node/status")
    async def status():
        return {"ok": True}

    app.add_middleware(ApiKeyMiddleware, api_key=KEY, public=False)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_authorization_bearer_accepted():
    """Characterises CURRENT behaviour — a future removal of any of these
    three accepted credential forms should show up here as a deliberate,
    visible test change, not a silent regression."""
    app = _app()
    async with _client(app) as c:
        r = await c.get("/v1/node/status", headers={"Authorization": f"Bearer {KEY}"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_x_api_key_header_accepted():
    app = _app()
    async with _client(app) as c:
        r = await c.get("/v1/node/status", headers={"x-api-key": KEY})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_api_key_query_param_accepted():
    app = _app()
    async with _client(app) as c:
        r = await c.get("/v1/node/status", params={"api_key": KEY})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_no_credential_rejected():
    app = _app()
    async with _client(app) as c:
        r = await c.get("/v1/node/status")
        assert r.status_code == 401
