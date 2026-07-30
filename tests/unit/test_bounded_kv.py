"""Bounded (rotating) KV cache: preflight math and clamp target.

With per-model max_kv_size set, KV memory is governed by the rotating-cache
bound rather than ctx_len, so preflight must estimate KV at
min(ctx_len, max_kv_size) — and when clamping, tighten the bound instead of
shrinking the model's context window.
"""

from types import SimpleNamespace

import mycellm.inference.memory_estimate as me
from mycellm.inference.manager import _run_kv_preflight


def _settings(action="clamp"):
    return SimpleNamespace(
        default_ctx_len=32768,
        max_concurrent_inferences=2,
        preflight_action=action,
        preflight_safety_fraction=0.9,
        preflight_overhead_gb=1.0,
        preflight_min_ctx_len=2048,
    )


def _patch_estimate(monkeypatch, fits=True, max_ctx=4096, record=None):
    def fake_estimate(model_path, ctx_len, slots, **kw):
        if record is not None:
            record.append(ctx_len)
        return {
            "fits": fits,
            "max_ctx_len": max_ctx,
            "weights_bytes": 1 << 30,
            "kv_bytes": 1 << 28,
            "peak_bytes": 2 << 30,
            "budget_bytes": 4 << 30,
            "ceiling_bytes": 5 << 30,
        }

    monkeypatch.setattr(me, "estimate", fake_estimate)


class TestBoundedKvPreflight:
    def test_kv_estimated_at_bound_not_ctx(self, monkeypatch):
        seen = []
        _patch_estimate(monkeypatch, fits=True, record=seen)
        kwargs = {"ctx_len": 32768, "max_kv_size": 8192}
        assert _run_kv_preflight("m", "/nonexistent", kwargs, _settings(), "mlx")
        assert seen == [8192]

    def test_unbounded_estimates_at_ctx(self, monkeypatch):
        seen = []
        _patch_estimate(monkeypatch, fits=True, record=seen)
        kwargs = {"ctx_len": 32768}
        assert _run_kv_preflight("m", "/nonexistent", kwargs, _settings(), "mlx")
        assert seen == [32768]

    def test_clamp_tightens_bound_and_keeps_ctx(self, monkeypatch):
        _patch_estimate(monkeypatch, fits=False, max_ctx=4096)
        kwargs = {"ctx_len": 32768, "max_kv_size": 16384}
        assert _run_kv_preflight("m", "/nonexistent", kwargs, _settings(), "mlx")
        assert kwargs["max_kv_size"] == 4096
        assert kwargs["ctx_len"] == 32768

    def test_clamp_without_bound_shrinks_ctx(self, monkeypatch):
        _patch_estimate(monkeypatch, fits=False, max_ctx=4096)
        kwargs = {"ctx_len": 32768}
        assert _run_kv_preflight("m", "/nonexistent", kwargs, _settings(), "mlx")
        assert kwargs["ctx_len"] == 4096
        assert "max_kv_size" not in kwargs


class TestPerModelOptionPersistence:
    def test_max_kv_and_draft_survive_save(self, tmp_path):
        import asyncio
        import json
        from mycellm.inference.manager import InferenceManager
        from mycellm.protocol.capabilities import ModelCapability

        m = InferenceManager()
        m._model_info["m1"] = ModelCapability(name="m1", backend="mlx")
        m._model_max_kv["m1"] = 4096
        m._model_draft["m1"] = ("mlx-community/Qwen3-0.6B-4bit", 4)
        asyncio.run(m.save_model_configs(tmp_path))
        cfg = json.loads((tmp_path / "model_configs.json").read_text())[0]
        assert cfg["max_kv_size"] == 4096
        assert cfg["draft_model"] == "mlx-community/Qwen3-0.6B-4bit"
        assert cfg["num_draft_tokens"] == 4
