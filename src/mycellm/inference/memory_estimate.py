"""KV-cache-aware memory estimation for model-load preflight (Apple Silicon / MLX).

Our legacy preflight compared on-disk weight size against *total* RAM with a flat
1.2x fudge — it had no notion of context length, batch slots, or the Metal wired
ceiling, so it both missed real OOMs (KV grows with ctx x batch) and would have
mis-sized small models.

This splits memory the way omlx's Memory Guard does (omlx, Apache-2.0,
``omlx/memory_monitor.py``):

    peak ~= weights + KV(ctx, batch) + overhead_reserve

and checks it against the Metal *recommended working-set* ceiling (the real cap
when ``iogpu.wired_limit_mb`` is 0) rather than total system RAM.

KV cache math (validated against measured ``mx.get_active_memory()`` on
Qwen2.5-3B, Qwen2.5-Coder-7B and Qwen3-1.7B — predicted/measured ratio ~1.00 at
ctx >= 2k):

    kv_bytes_per_token = num_hidden_layers * num_key_value_heads * head_dim
                         * KV_DTYPE_BYTES * 2        # *2 = K and V

KV is stored at the activation dtype (fp16/bf16 = 2 bytes) *even for 4-bit
weight-quantized models* — the quant applies to weights, not the cache. The SDPA
prefill transient is O(L) and small on MLX 0.31 (fused for all head_dim), so it
is folded into ``overhead_reserve`` rather than modelled term-by-term.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import platform
import subprocess

logger = logging.getLogger("mycellm.inference")

# KV cache element width. fp16/bf16 regardless of weight quantization.
KV_DTYPE_BYTES = 2
_GB = 1024 ** 3


def _load_config(model_path: str) -> dict | None:
    """Load a model's config.json from a local dir or the HF hub cache."""
    if not model_path:
        return None
    # Local directory (mycellm prefers local snapshot dirs).
    cand = model_path if model_path.endswith(".json") else os.path.join(model_path, "config.json")
    if os.path.exists(cand):
        try:
            return json.load(open(cand))
        except Exception:
            return None
    # Looks like an HF repo id — search the hub cache snapshots.
    if "/" in model_path and not os.path.isabs(model_path):
        base = os.path.expanduser(
            "~/.cache/huggingface/hub/models--" + model_path.replace("/", "--") + "/snapshots"
        )
        for d in sorted(glob.glob(base + "/*"), reverse=True):
            p = os.path.join(d, "config.json")
            if os.path.exists(p):
                try:
                    return json.load(open(p))
                except Exception:
                    continue
    return None


def read_model_dims(model_path: str) -> dict | None:
    """Extract (layers, n_kv_heads, head_dim, n_q_heads) from a model config.

    Handles nested ``text_config``/``language_config`` for VLMs. Returns None if
    the config can't be found or lacks the fields (caller falls back to legacy).
    """
    cfg = _load_config(model_path)
    if not cfg:
        return None
    # VLMs nest the LM config; the KV cache belongs to the text decoder.
    for key in ("text_config", "language_config", "llm_config"):
        if isinstance(cfg.get(key), dict):
            cfg = {**cfg, **cfg[key]}
    n_q = cfg.get("num_attention_heads")
    hidden = cfg.get("hidden_size")
    layers = cfg.get("num_hidden_layers")
    n_kv = cfg.get("num_key_value_heads") or n_q
    head_dim = cfg.get("head_dim") or ((hidden // n_q) if (hidden and n_q) else None)
    if not (layers and n_kv and head_dim):
        return None
    return {
        "layers": int(layers),
        "n_kv_heads": int(n_kv),
        "head_dim": int(head_dim),
        "n_q_heads": int(n_q or n_kv),
    }


def kv_bytes_per_token(dims: dict) -> int:
    return dims["layers"] * dims["n_kv_heads"] * dims["head_dim"] * KV_DTYPE_BYTES * 2


def metal_ceiling_bytes() -> int:
    """Usable GPU memory ceiling in bytes.

    macOS: the Metal *recommended working-set* (what MLX can allocate before the
    system pushes back), unless ``iogpu.wired_limit_mb`` is explicitly raised, in
    which case honour that. Falls back to a fraction of physical RAM, then to 0
    (meaning "unknown — skip the check") on non-macOS / failure.
    """
    if platform.system() != "Darwin":
        return 0
    # Explicit kernel override wins if set (>0).
    try:
        r = subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"],
                           capture_output=True, text=True, timeout=3)
        mb = int((r.stdout or "0").strip() or 0)
        if mb > 0:
            return mb * 1024 * 1024
    except Exception:
        pass
    # Metal recommended working set (the real default cap).
    try:
        import mlx.core as mx
        info = mx.device_info() if hasattr(mx, "device_info") else mx.metal.device_info()
        mrws = int(info.get("max_recommended_working_set_size", 0))
        if mrws > 0:
            return mrws
    except Exception:
        pass
    # Last resort: ~75% of physical RAM (Apple Silicon default wired fraction).
    try:
        r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                           capture_output=True, text=True, timeout=3)
        total = int((r.stdout or "0").strip() or 0)
        if total > 0:
            return int(total * 0.75)
    except Exception:
        pass
    return 0


def estimate(
    model_path: str,
    ctx_len: int,
    batch_slots: int = 1,
    *,
    weights_bytes: int = 0,
    overhead_bytes: int = _GB,
    safety_fraction: float = 0.90,
    ceiling_bytes: int | None = None,
) -> dict | None:
    """Estimate load+serve peak memory and whether it fits the GPU ceiling.

    Returns a dict (see keys below) or None if model dims are unavailable
    (caller should fall back to the legacy size-based check). ``max_ctx_len`` is
    the largest context that fits the budget at ``batch_slots``.
    """
    dims = read_model_dims(model_path)
    if not dims:
        return None
    ceiling = ceiling_bytes if ceiling_bytes is not None else metal_ceiling_bytes()
    if ceiling <= 0:
        return None  # unknown ceiling — let caller fall back

    slots = max(1, int(batch_slots))
    per_tok = kv_bytes_per_token(dims)
    kv = per_tok * max(0, int(ctx_len)) * slots
    peak = int(weights_bytes) + kv + int(overhead_bytes)
    budget = int(ceiling * safety_fraction)

    avail_for_kv = budget - int(weights_bytes) - int(overhead_bytes)
    max_ctx = max(0, avail_for_kv // (per_tok * slots)) if per_tok else 0

    return {
        "dims": dims,
        "kv_bytes_per_token": per_tok,
        "weights_bytes": int(weights_bytes),
        "kv_bytes": kv,
        "overhead_bytes": int(overhead_bytes),
        "peak_bytes": peak,
        "ceiling_bytes": ceiling,
        "budget_bytes": budget,
        "batch_slots": slots,
        "ctx_len": int(ctx_len),
        "fits": peak <= budget,
        "max_ctx_len": int(max_ctx),
    }


def _fmt(b: float) -> str:
    return f"{b / _GB:.2f}GB"


if __name__ == "__main__":  # node validation / manual probe
    import sys
    mp = sys.argv[1]
    ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 32768
    slots = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    wb = 0
    try:
        from mycellm.inference.manager import _model_size_on_disk
        wb = _model_size_on_disk(mp)
    except Exception:
        pass
    e = estimate(mp, ctx, slots, weights_bytes=wb)
    if not e:
        print("no estimate (config/ceiling unavailable)")
        sys.exit(1)
    print(f"model={mp}")
    print(f"dims={e['dims']}  kv/tok={e['kv_bytes_per_token']/1024:.1f}KB")
    print(f"weights={_fmt(e['weights_bytes'])}  kv@{ctx}x{slots}={_fmt(e['kv_bytes'])}  "
          f"overhead={_fmt(e['overhead_bytes'])}")
    print(f"peak={_fmt(e['peak_bytes'])}  budget={_fmt(e['budget_bytes'])} "
          f"(ceiling={_fmt(e['ceiling_bytes'])} x {0.90})")
    print(f"FITS={e['fits']}  max_ctx@{slots}slots={e['max_ctx_len']}")
