"""Tests for model config persistence."""

import json
import pytest
from unittest.mock import AsyncMock

from mycellm.inference.manager import InferenceManager


@pytest.fixture
def tmp_data_dir(tmp_path):
    return tmp_path


@pytest.mark.asyncio
async def test_save_model_configs(tmp_data_dir):
    mgr = InferenceManager()
    # Manually add a model info entry
    from mycellm.protocol.capabilities import ModelCapability
    mgr._model_info["test-model"] = ModelCapability(
        name="test-model", backend="openai", ctx_len=8192
    )
    mgr._backends["test-model"] = AsyncMock()  # mock backend

    await mgr.save_model_configs(tmp_data_dir)

    config_path = tmp_data_dir / "model_configs.json"
    assert config_path.exists()
    configs = json.loads(config_path.read_text())
    assert len(configs) == 1
    assert configs[0]["name"] == "test-model"


@pytest.mark.asyncio
async def test_restore_models_no_file(tmp_data_dir):
    mgr = InferenceManager()
    count = await mgr.restore_models(tmp_data_dir)
    assert count == 0


@pytest.mark.asyncio
async def test_restore_models_from_config(tmp_data_dir):
    config = [
        {
            "name": "remote-model",
            "backend": "openai",
            "api_base": "https://api.example.com/v1",
            "api_key": "sk-test",
            "api_model": "gpt-4",
            "ctx_len": 4096,
        }
    ]
    (tmp_data_dir / "model_configs.json").write_text(json.dumps(config))

    mgr = InferenceManager()

    # Mock load_model to avoid actual HTTP calls
    mgr.load_model = AsyncMock(return_value="remote-model")

    count = await mgr.restore_models(tmp_data_dir)
    assert count == 1
    mgr.load_model.assert_called_once()
