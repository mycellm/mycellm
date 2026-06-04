#!/usr/bin/env python3
"""Benchmark: current serialized MLX path vs continuous batching.

mycellm's MLX backend today serves ONE request per model at a time (per-model
asyncio.Lock — MLX isn't parallel-safe on a Metal queue), so concurrent chats
queue. This measures the upside of continuous batching via mlx-lm's
``BatchGenerator`` (token-level dynamic batching), which is what
``inference/mlx_batched.py`` drives.

Self-contained: depends only on mlx_lm so it can run on a seeder without
importing or modifying the installed mycellm tree.

Continuous-batching approach is modeled on mlx-lm's own ``batch_generate``
consumption loop and informed by jundot/omlx (Apache-2.0) engine/batched.py.

Usage:
    python bench_mlx_batching.py --model mlx-community/Qwen3-1.7B-4bit \
        --max-tokens 80 --levels 1,2,4,8,16,32
"""

import argparse
import time

import mlx.core as mx
from mlx_lm import load, stream_generate
from mlx_lm.generate import BatchGenerator
from mlx_lm.sample_utils import make_sampler


# 32 distinct prompts so batched sequences don't share an identical decode path.
_PROMPTS = [
    "Explain how a bicycle stays upright while moving.",
    "Write a haiku about the ocean at dawn.",
    "What is the capital of Australia and why isn't it Sydney?",
    "Give three tips for writing clean Python code.",
    "Summarize the plot of Romeo and Juliet in two sentences.",
    "How does a heat pump move heat against a temperature gradient?",
    "List four common causes of slow database queries.",
    "Describe the taste of a ripe mango to someone who's never had one.",
    "What's the difference between TCP and UDP?",
    "Suggest a name for a cozy neighborhood coffee shop and explain it.",
    "Explain recursion using a real-world analogy.",
    "Why is the sky blue during the day and red at sunset?",
    "Write a short motivational note for someone starting a new job.",
    "What are the trade-offs between SSDs and hard drives?",
    "Explain what a hash function is and one place it's used.",
    "Give a beginner-friendly definition of machine learning.",
    "How do noise-cancelling headphones work?",
    "What makes sourdough bread rise without commercial yeast?",
    "Describe the water cycle in four steps.",
    "What's the purpose of a load balancer in a web system?",
    "Explain the difference between weather and climate.",
    "Write a one-line elevator pitch for a note-taking app.",
    "Why do leaves change color in autumn?",
    "What is a race condition in concurrent programming?",
    "Give two reasons unit tests are worth writing.",
    "Explain compound interest with a simple example.",
    "How does GPS determine your location?",
    "What is the role of mitochondria in a cell?",
    "Suggest three uses for a Raspberry Pi at home.",
    "Explain what an API is to a non-programmer.",
    "Why does bread go stale and how does freezing help?",
    "Describe how a transistor acts as a switch.",
]


def _tokenize(tokenizer, text: str) -> list[int]:
    msgs = [{"role": "user", "content": text}]
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True
        )
    return tokenizer.encode(text)


def _pick(n: int) -> list[str]:
    return [_PROMPTS[i % len(_PROMPTS)] for i in range(n)]


def run_sequential(model, tokenizer, prompts: list[str], max_tokens: int):
    """Baseline: one request at a time (what the node does today)."""
    sampler = make_sampler(temp=0.0)  # greedy → deterministic, fair vs batch
    total_gen = 0
    t0 = time.perf_counter()
    for text in prompts:
        ids = _tokenize(tokenizer, text)
        n = 0
        for _ in stream_generate(
            model, tokenizer, ids, max_tokens=max_tokens, sampler=sampler
        ):
            n += 1
        total_gen += n
    dt = time.perf_counter() - t0
    return total_gen, dt


def run_batched(model, tokenizer, prompts: list[str], max_tokens: int):
    """Continuous batching: all sequences decode together via BatchGenerator."""
    prompt_ids = [_tokenize(tokenizer, t) for t in prompts]
    gen = BatchGenerator(
        model,
        stop_tokens=[[t] for t in tokenizer.eos_token_ids],
        sampler=lambda x: mx.argmax(x, axis=-1),  # greedy, matches baseline
        max_tokens=max_tokens,
    )
    results: dict[int, int] = {}
    t0 = time.perf_counter()
    uids = gen.insert(prompt_ids, [max_tokens] * len(prompt_ids))
    for uid in uids:
        results[uid] = 0
    while responses := gen.next_generated():
        for r in responses:
            # finish_reason == "stop" => the emitted token is the stop token
            # itself (not part of the completion); everything else counts.
            if r.finish_reason != "stop":
                results[r.uid] += 1
    dt = time.perf_counter() - t0
    gen.close()
    return sum(results.values()), dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-1.7B-4bit")
    ap.add_argument("--max-tokens", type=int, default=80)
    ap.add_argument("--levels", default="1,2,4,8,16,32")
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",")]

    print(f"Loading {args.model} ...", flush=True)
    model, tokenizer = load(args.model)

    # Warmup (graph build / lazy compile) so it doesn't skew the first level.
    print("Warmup ...", flush=True)
    run_sequential(model, tokenizer, _pick(1), 8)
    run_batched(model, tokenizer, _pick(2), 8)

    print(f"\nmodel={args.model}  max_tokens={args.max_tokens}  device={mx.default_device()}")
    print("=" * 78)
    print(f"{'conc':>5} | {'seq tok/s':>10} {'seq s':>8} | "
          f"{'batch tok/s':>11} {'batch s':>8} | {'speedup':>7}")
    print("-" * 78)

    for c in levels:
        prompts = _pick(c)
        seq_tok, seq_dt = run_sequential(model, tokenizer, prompts, args.max_tokens)
        bat_tok, bat_dt = run_batched(model, tokenizer, prompts, args.max_tokens)
        seq_tps = seq_tok / seq_dt if seq_dt else 0
        bat_tps = bat_tok / bat_dt if bat_dt else 0
        speedup = seq_dt / bat_dt if bat_dt else 0
        print(f"{c:>5} | {seq_tps:>10.1f} {seq_dt:>8.2f} | "
              f"{bat_tps:>11.1f} {bat_dt:>8.2f} | {speedup:>6.2f}x", flush=True)

    print("=" * 78)
    print("seq = one-request-at-a-time (current node behavior); "
          "batch = mlx-lm BatchGenerator continuous batching")


if __name__ == "__main__":
    main()
