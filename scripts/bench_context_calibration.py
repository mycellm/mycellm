#!/usr/bin/env python3
"""Empirical context-memory calibration for the KV-aware preflight.

Sweeps context lengths on a real model on THIS hardware, measures the actual
Metal peak (mx.get_peak_memory) during prefill+decode, and compares it with
memory_estimate's analytical prediction. With --apply, writes the worst-case
measured/predicted KV ratio to data_dir/preflight_calibration.json, which
estimate() picks up on every subsequent preflight (see load_calibration()).

Inspired by oMLX's context benchmark (measure the real max prefillable
context instead of trusting the formula). Uses natural-ish varied text, not
repeated padding — repetition compresses attention memory and flatters the
numbers.

Run on an otherwise-idle node with a small model first, e.g.:
  python scripts/bench_context_calibration.py \
      --model mlx-community/Qwen3-1.7B-4bit --levels 2048,4096,8192 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_WORDS = (
    "system latency cache tensor spore relay ledger quorum stream batch "
    "gradient socket beacon fabric kernel mesh cipher token router lattice"
).split()


def synthetic_prompt(tokenizer, n_tokens: int) -> list[int]:
    """~n_tokens of varied (non-repeating-window) text, then hard-truncated."""
    words, i = [], 0
    while len(words) < n_tokens:  # ≥1 token per word, so this overshoots
        words.append(_WORDS[i % len(_WORDS)] + str(i % 97))
        i += 1
    ids = tokenizer.encode(" ".join(words))
    return ids[:n_tokens]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-1.7B-4bit")
    ap.add_argument("--levels", default="2048,4096,8192")
    ap.add_argument("--gen-tokens", type=int, default=16)
    ap.add_argument("--apply", action="store_true",
                    help="write worst-case ratio to data_dir/preflight_calibration.json")
    args = ap.parse_args()

    import mlx.core as mx
    from mlx_lm import load, stream_generate

    from mycellm.inference import memory_estimate as me

    print(f"Loading {args.model} ...", flush=True)
    model, tokenizer = load(args.model)
    mx.eval(model.parameters())
    weights_active = mx.get_active_memory()
    print(f"weights active: {weights_active / 2**30:.2f}GB")

    levels = [int(x) for x in args.levels.split(",")]
    worst = 0.0
    rows = []
    for ctx in levels:
        prompt = synthetic_prompt(tokenizer, ctx)
        mx.clear_cache()
        mx.reset_peak_memory()
        t0 = time.time()
        for _ in stream_generate(model, tokenizer, prompt=mx.array(prompt),
                                 max_tokens=args.gen_tokens):
            pass
        elapsed = time.time() - t0
        peak = mx.get_peak_memory()
        # Analytical prediction for the same shape (1 slot, no calibration,
        # no safety margin — we want the raw model, not the padded budget).
        est = me.estimate(args.model, ctx, 1, weights_bytes=weights_active,
                          overhead_bytes=0, ceiling_bytes=1 << 62, kv_factor=1.0)
        predicted_kv = est["kv_bytes"] if est else 0
        measured_kv = max(0, peak - weights_active)
        ratio = (measured_kv / predicted_kv) if predicted_kv else 0.0
        worst = max(worst, ratio)
        rows.append((ctx, peak, predicted_kv, measured_kv, ratio, elapsed))
        print(f"ctx {ctx:>6}: peak {peak/2**30:5.2f}GB  "
              f"kv predicted {predicted_kv/2**30:5.2f}GB  "
              f"measured {measured_kv/2**30:5.2f}GB  ratio {ratio:4.2f}  "
              f"({elapsed:.1f}s)", flush=True)

    # Linear fit measured_kv ≈ intercept + slope·predicted_kv. The slope is the
    # true per-token calibration; the intercept is a constant transient
    # (prefill activations + runtime buffers) that belongs to the flat
    # preflight_overhead_gb reserve, NOT to a multiplicative factor — using the
    # worst-case small-ctx ratio here would over-clamp large contexts badly.
    xs = [r[2] for r in rows]
    ys = [r[3] for r in rows]
    n = len(rows)
    mx_, my_ = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx_) ** 2 for x in xs) or 1
    slope = sum((x - mx_) * (y - my_) for x, y in zip(xs, ys)) / denom
    intercept = my_ - slope * mx_

    print(f"\nlinear fit: measured_kv ≈ {intercept / 2**30:.2f}GB + {slope:.2f} × predicted_kv")
    print(f"(worst-case raw ratio {worst:.2f} — small-ctx values are dominated "
          f"by the constant transient; do not use as a factor)")
    kv_factor = round(max(1.0, slope), 3)  # never loosen below analytical
    try:
        from mycellm.config import get_settings as _gs
        reserve = _gs().preflight_overhead_gb * 2**30
        if intercept > reserve:
            print(f"WARNING: measured constant transient {intercept / 2**30:.2f}GB exceeds "
                  f"preflight_overhead_gb reserve {reserve / 2**30:.2f}GB — raise "
                  f"MYCELLM_PREFLIGHT_OVERHEAD_GB to at least {intercept / 2**30:.1f}")
        else:
            print(f"constant transient {intercept / 2**30:.2f}GB is covered by the "
                  f"{reserve / 2**30:.1f}GB overhead reserve")
    except Exception:
        pass
    print(f"kv_factor to apply: {kv_factor}")

    if not args.apply:
        print("(dry run — pass --apply to write the calibration file)")
        return 0

    from mycellm.config import get_settings
    out = get_settings().data_dir / "preflight_calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kv_factor": kv_factor,
        "fit_intercept_bytes": int(intercept),
        "model": args.model,
        "levels": levels,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out}: kv_factor={payload['kv_factor']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
