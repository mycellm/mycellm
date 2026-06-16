"""Tests for the model crash-loop guard in InferenceManager.restore_models().

A model that fails to load (e.g. OOM-kills the process) on boot would be retried
on every restart forever. After model_max_restore_attempts failures it must be
quarantined (enabled=false) so restore stops reloading it.
"""

import json

import pytest

from mycellm.inference import manager as mgr_mod
from mycellm.inference.manager import (
    InferenceManager,
    _bump_attempt,
    _clear_attempt,
    _preflight_local_model,
    _read_attempts,
)


def test_preflight_returns_zeros_for_missing_path(tmp_path):
    # Empty path and a non-existent path both short-circuit to zeros without
    # touching vm_stat / config.json (so the off-loop call is cheap & safe).
    assert _preflight_local_model("", 4096) == (0, 0, 0)
    assert _preflight_local_model(str(tmp_path / "nope"), 4096) == (0, 0, 0)


def test_attempt_helpers_roundtrip(tmp_path):
    assert _read_attempts(tmp_path) == {}
    assert _bump_attempt(tmp_path, "m") == 1
    assert _bump_attempt(tmp_path, "m") == 2
    assert _read_attempts(tmp_path)["m"] == 2
    _clear_attempt(tmp_path, "m")
    assert "m" not in _read_attempts(tmp_path)


@pytest.mark.asyncio
async def test_failing_model_is_quarantined_after_max_attempts(tmp_path, monkeypatch):
    # One enabled local (mlx) model whose load always fails.
    config = [{
        "name": "badmodel",
        "backend": "mlx",
        "model_path": "mlx-community/does-not-matter",
        "ctx_len": 4096,
        "enabled": True,
    }]
    (tmp_path / "model_configs.json").write_text(json.dumps(config))

    mgr = InferenceManager()

    calls = {"n": 0}

    async def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("simulated OOM/load failure")

    monkeypatch.setattr(mgr, "load_model", boom)

    # Default ceiling is 3 (settings.model_max_restore_attempts).
    # 3 attempts fail (load_model called), 4th restore quarantines and skips.
    for _ in range(4):
        restored = await mgr.restore_models(tmp_path)
        assert restored == 0

    assert calls["n"] == 3, "load should be attempted exactly max_attempts times"
    assert "badmodel" in mgr._quarantined

    # Persisted config must now be disabled so future boots skip it.
    saved = json.loads((tmp_path / "model_configs.json").read_text())
    entry = next(c for c in saved if c["name"] == "badmodel")
    assert entry["enabled"] is False


@pytest.mark.asyncio
async def test_successful_load_clears_attempts(tmp_path, monkeypatch):
    config = [{
        "name": "okmodel",
        "backend": "mlx",
        "model_path": "mlx-community/whatever",
        "ctx_len": 4096,
        "enabled": True,
    }]
    (tmp_path / "model_configs.json").write_text(json.dumps(config))
    # Pre-seed an attempt as if a prior boot had failed once.
    _bump_attempt(tmp_path, "okmodel")

    mgr = InferenceManager()

    async def ok(*args, **kwargs):
        # Simulate what real load_model does on success: clear the counter.
        mgr_mod._clear_attempt(tmp_path, "okmodel")
        return "okmodel"

    monkeypatch.setattr(mgr, "load_model", ok)
    restored = await mgr.restore_models(tmp_path)
    assert restored == 1
    assert "okmodel" not in _read_attempts(tmp_path)
