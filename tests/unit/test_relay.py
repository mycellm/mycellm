"""Tests for relay backend manager."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mycellm.inference.relay import RelayManager, RelayEndpoint, parse_relay_backends, _label_from_url


# ── parse_relay_backends ──


def test_parse_empty():
    assert parse_relay_backends("") == []


def test_parse_single():
    assert parse_relay_backends("http://ipad.lan:8080") == ["http://ipad.lan:8080"]


def test_parse_multiple():
    result = parse_relay_backends("http://a:8080,http://b:11434, http://c:1234 ")
    assert result == ["http://a:8080", "http://b:11434", "http://c:1234"]


def test_parse_strips_whitespace():
    result = parse_relay_backends("  http://x:80 , http://y:80  ")
    assert result == ["http://x:80", "http://y:80"]


# ── _label_from_url ──


def test_label_localhost():
    assert _label_from_url("http://localhost:8080") == "localhost:8080"


def test_label_ip():
    assert _label_from_url("http://127.0.0.1:11434") == "localhost:11434"


def test_label_hostname():
    assert _label_from_url("http://ipad.lan:8080") == "ipad"


def test_label_fqdn():
    assert _label_from_url("http://my-ollama.home.arpa:11434") == "my-ollama"


# ── RelayManager ──


@pytest.fixture
def mock_inference():
    mgr = MagicMock()
    mgr.loaded_models = []
    mgr.load_model = AsyncMock()
    mgr.unload_model = AsyncMock()
    return mgr


@pytest.fixture
def relay_manager(mock_inference):
    return RelayManager(mock_inference)


def _mock_models_response(models: list[dict]):
    """Create a mock httpx response for /v1/models."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": models}
    return resp


@pytest.mark.asyncio
async def test_add_discovers_models(relay_manager, mock_inference):
    models = [{"id": "llama3.2:3b"}, {"id": "phi-4"}]

    with patch("mycellm.inference.relay.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=_mock_models_response(models))
        MockClient.return_value = client

        relay = await relay_manager.add("http://ipad.lan:8080")

    assert relay.online
    assert relay.url == "http://ipad.lan:8080"
    assert relay.name == "ipad"
    assert len(relay.models) == 2
    assert mock_inference.load_model.call_count == 2

    # Check registered model names
    calls = mock_inference.load_model.call_args_list
    assert calls[0].kwargs["name"] == "relay:llama3.2:3b"
    assert calls[1].kwargs["name"] == "relay:phi-4"
    assert calls[0].kwargs["api_base"] == "http://ipad.lan:8080/v1"


@pytest.mark.asyncio
async def test_add_strips_v1_suffix(relay_manager, mock_inference):
    with patch("mycellm.inference.relay.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=_mock_models_response([{"id": "test"}]))
        MockClient.return_value = client

        relay = await relay_manager.add("http://ollama.lan:11434/v1")

    assert relay.url == "http://ollama.lan:11434"


@pytest.mark.asyncio
async def test_add_offline_relay(relay_manager, mock_inference):
    with patch("mycellm.inference.relay.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = client

        relay = await relay_manager.add("http://offline:8080")

    assert not relay.online
    assert "refused" in relay.error
    assert mock_inference.load_model.call_count == 0


@pytest.mark.asyncio
async def test_add_auth_failure(relay_manager, mock_inference):
    resp = MagicMock()
    resp.status_code = 401

    with patch("mycellm.inference.relay.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp)
        MockClient.return_value = client

        relay = await relay_manager.add("http://secure:8080", api_key="bad-key")

    assert not relay.online
    assert "401" in relay.error


@pytest.mark.asyncio
async def test_remove_unloads_models(relay_manager, mock_inference):
    relay_manager._relays["http://ipad:8080"] = RelayEndpoint(
        url="http://ipad:8080",
        name="ipad",
        models=[{"id": "llama3"}, {"id": "phi-4"}],
        online=True,
    )

    removed = await relay_manager.remove("http://ipad:8080")
    assert removed
    assert mock_inference.unload_model.call_count == 2
    assert len(relay_manager.relays) == 0


@pytest.mark.asyncio
async def test_remove_nonexistent(relay_manager):
    removed = await relay_manager.remove("http://nope:8080")
    assert not removed


@pytest.mark.asyncio
async def test_get_status(relay_manager):
    relay_manager._relays["http://a:8080"] = RelayEndpoint(
        url="http://a:8080", name="ipad", online=True,
        models=[{"id": "llama3"}],
    )
    relay_manager._relays["http://b:11434"] = RelayEndpoint(
        url="http://b:11434", name="ollama", online=False, error="timeout",
        models=[],
    )

    status = relay_manager.get_status()
    assert len(status) == 2
    assert status[0]["online"] is True
    assert status[0]["model_count"] == 1
    assert status[1]["online"] is False
    assert status[1]["error"] == "timeout"


@pytest.mark.asyncio
async def test_duplicate_models_not_reregistered(relay_manager, mock_inference):
    """If a relay model is already loaded, don't load it again."""
    mock_cap = MagicMock()
    mock_cap.name = "relay:llama3"
    mock_inference.loaded_models = [mock_cap]

    with patch("mycellm.inference.relay.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=_mock_models_response([{"id": "llama3"}]))
        MockClient.return_value = client

        await relay_manager.add("http://ipad:8080")

    # Should NOT call load_model since relay:llama3 is already loaded
    assert mock_inference.load_model.call_count == 0
