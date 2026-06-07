#!/usr/bin/env python3
"""Benchmark the BatchedMLXBackend solo (batch-of-1) fast path.

Measures decode throughput of N *sequential* single requests — the common
low-concurrency case — with the single-stream fast path ON vs OFF:

  OFF  -> every request, even solo, steps mlx-lm's BatchGenerator at batch=1
  ON   -> a solo request decodes on mlx_lm.stream_generate (simple KV cache)

This isolates the regression oMLX reported (single-row decode falling into the
slower batched cache path). Both paths run on the backend's worker thread, so
the only difference is the decode engine.

Run each config in its own process to avoid Metal-cache cross-contamination,
and interleave (0 1 0 1) to control for thermal drift:

    for fp in 0 1 0 1; do
      PYTHONPATH=src python scripts/bench_solo_fastpath.py \
        --model mlx-community/Qwen3-1.7B-4bit --fast-path $fp --n 8 --max-tokens 200
    done
"""
from __future__ import annotations

import argparse
import asyncio
import time

from mycellm.inference.base import InferenceRequest
from mycellm.inference.mlx_batched import BatchedMLXBackend

PROMPT = (
    "Explain in detail how a bicycle stays upright while moving, covering "
    "gyroscopic effects, trail, and how the rider steers to maintain balance."
)


def _mkreq(max_tokens: int) -> InferenceRequest:
    # temperature=0 → greedy, so both configs decode the identical token
    # sequence and tok/s is a clean apples-to-apples comparison.
    return InferenceRequest(
        messages=[{"role": "user", "content": PROMPT}],
        model="bench",
        max_tokens=max_tokens,
        temperature=0.0,
        top_p=1.0,
    )


async def run(model: str, fast_path: bool, n: int, max_tokens: int):
    be = BatchedMLXBackend(solo_fast_path=fast_path)
    await be.load_model(model, name="bench")
    await be.generate(_mkreq(32))  # warmup (prefill + JIT)

    total = 0
    t0 = time.perf_counter()
    for _ in range(n):
        res = await be.generate(_mkreq(max_tokens))
        total += res.completion_tokens
    dt = time.perf_counter() - t0

    await be.unload_model("bench")
    return total, dt


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-1.7B-4bit")
    ap.add_argument("--fast-path", type=int, choices=[0, 1], required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=200)
    args = ap.parse_args()

    total, dt = await run(args.model, bool(args.fast_path), args.n, args.max_tokens)
    label = "ON  (stream_generate)" if args.fast_path else "OFF (BatchGenerator b=1)"
    tps = total / dt if dt > 0 else 0.0
    print(f"fast_path={label:26s} n={args.n} tokens={total:5d} time={dt:6.2f}s -> {tps:7.1f} tok/s")


if __name__ == "__main__":
    asyncio.run(main())
