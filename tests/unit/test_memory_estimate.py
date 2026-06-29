"""Unit tests for the KV-aware memory estimator (inference/memory_estimate.py).

The per-token KV constants below were validated against measured
mx.get_active_memory() on real hardware (ratio ~1.00 at ctx >= 2k):
  Qwen2.5-3B  -> 36864 B/tok   Qwen2.5-Coder-7B -> 57344 B/tok
  Qwen3-1.7B  -> 114688 B/tok
"""
import json

import pytest

from mycellm.inference import memory_estimate as me

GB = 1024 ** 3


@pytest.mark.parametrize("dims,expected", [
    ({"layers": 36, "n_kv_heads": 2, "head_dim": 128}, 36864),   # Qwen2.5-3B
    ({"layers": 28, "n_kv_heads": 4, "head_dim": 128}, 57344),   # Qwen2.5-Coder-7B
    ({"layers": 28, "n_kv_heads": 8, "head_dim": 128}, 114688),  # Qwen3-1.7B
])
def test_kv_bytes_per_token(dims, expected):
    assert me.kv_bytes_per_token(dims) == expected


def _write_config(tmp_path, **cfg):
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    return str(tmp_path)


def test_read_model_dims_derives_head_dim(tmp_path):
    p = _write_config(tmp_path, num_hidden_layers=28, num_attention_heads=16,
                      num_key_value_heads=8, hidden_size=2048)
    dims = me.read_model_dims(p)
    assert dims == {"layers": 28, "n_kv_heads": 8, "head_dim": 128, "n_q_heads": 16}


def test_read_model_dims_nested_vlm(tmp_path):
    p = _write_config(tmp_path, text_config={
        "num_hidden_layers": 36, "num_attention_heads": 16,
        "num_key_value_heads": 2, "hidden_size": 2048})
    dims = me.read_model_dims(p)
    assert dims["layers"] == 36 and dims["n_kv_heads"] == 2 and dims["head_dim"] == 128


def test_estimate_rejects_historical_oom(tmp_path):
    # Qwen3-1.7B @ ctx 32768 x 4 slots on a 12.71GB-ceiling box = the real OOM.
    p = _write_config(tmp_path, num_hidden_layers=28, num_attention_heads=16,
                      num_key_value_heads=8, hidden_size=2048)
    e = me.estimate(p, 32768, batch_slots=4,
                    weights_bytes=int(0.97 * GB),
                    ceiling_bytes=int(12.71 * GB))
    assert e["fits"] is False
    assert e["kv_bytes"] == 114688 * 32768 * 4
    assert e["max_ctx_len"] < 32768  # must clamp below the requested ctx


def test_estimate_allows_small_model_high_ctx(tmp_path):
    # Qwen2.5-3B fits far past the legacy ctx<=4096 cap.
    p = _write_config(tmp_path, num_hidden_layers=36, num_attention_heads=16,
                      num_key_value_heads=2, hidden_size=2048)
    e = me.estimate(p, 32768, batch_slots=2,
                    weights_bytes=int(1.74 * GB),
                    ceiling_bytes=int(12.71 * GB))
    assert e["fits"] is True
    assert e["max_ctx_len"] > 100_000


def test_estimate_none_without_config(tmp_path):
    assert me.estimate(str(tmp_path), 4096, ceiling_bytes=GB) is None


def test_estimate_none_without_ceiling(tmp_path):
    p = _write_config(tmp_path, num_hidden_layers=36, num_attention_heads=16,
                      num_key_value_heads=2, hidden_size=2048)
    assert me.estimate(p, 4096, ceiling_bytes=0) is None
