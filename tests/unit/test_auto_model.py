"""Tests for the virtual 'auto' model in the OpenAI-compatible API."""

from unittest.mock import MagicMock
from starlette.testclient import TestClient
from fastapi import FastAPI

from mycellm.api.openai import router


def _make_app():
    """Create a minimal FastAPI app with the openai router and mock node."""
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    node = MagicMock()
    node.inference.loaded_models = []
    node.registry.connected_peers.return_value = []
    node.node_registry = {}

    app.state.node = node
    return app, node


def test_list_models_includes_auto():
    """GET /v1/models should include 'auto' as the first model."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    model_ids = [m["id"] for m in data["data"]]
    assert "auto" in model_ids
    # auto should be first
    assert model_ids[0] == "auto"
    # owned_by should be "mycellm"
    auto_model = data["data"][0]
    assert auto_model["owned_by"] == "mycellm"


def test_retrieve_model_auto():
    """GET /v1/models/auto should return the virtual auto model."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models/auto")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "auto"
    assert data["object"] == "model"
    assert data["owned_by"] == "mycellm"


def test_retrieve_model_not_found():
    """GET /v1/models/<nonexistent> should return 404."""
    app, _ = _make_app()
    client = TestClient(app)

    resp = client.get("/v1/models/nonexistent-model-xyz")
    assert resp.status_code == 404


def test_retrieve_model_local():
    """GET /v1/models/<local_model> should return the local model."""
    app, node = _make_app()
    mock_model = MagicMock()
    mock_model.name = "qwen2.5-7b"
    node.inference.loaded_models = [mock_model]

    client = TestClient(app)
    resp = client.get("/v1/models/qwen2.5-7b")
    assert resp.status_code == 200
    assert resp.json()["id"] == "qwen2.5-7b"
    assert resp.json()["owned_by"] == "local"
