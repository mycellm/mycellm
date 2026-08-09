#!/usr/bin/env python3
"""mycellm benchmark matrix — numbers you can publish and defend.

⚠️ WHY THIS EXISTS RATHER THAN `bench_mlx_batching.py`. That script answers one
question well (does continuous batching help on this box) and its answer went
onto a public site as "4.29× at concurrency 32" with no model, no hardware and
no workload attached. The figure was true — M1 16GB, Qwen3-1.7B 4-bit, greedy,
80 tokens, 45→193 tok/s — and useless, because a reader could not tell it was
the better of two runs on the smallest model. It was also measured beside a live
node, and that caveat never travelled with it.

So this harness records the CONDITIONS as first-class output and refuses to
report a single number without them. Every run emits JSON: host, chip, RAM,
macOS, mlx and mlx-lm versions, model and quantisation, sampler, token budget,
concurrency, load average and thermal pressure at start and finish, repeat count,
and per-repeat results — median reported, spread shown. If the machine was busy,
the JSON says so and the number is a floor rather than a result.

⚠️ THE JUNE FIGURE IS NOT COMPARABLE TO ANYTHING MEASURED NOW. 167 commits and a
0.6.3 release stand between them — speculative decoding, an MLX embeddings
backend, bounded KV, prefill chunking, context calibration. Re-running the same
model on the same host is the only way to say what changed, which is why
`--model mlx-community/Qwen3-1.7B-4bit` on hokulea is the anchor case.

THREE MODES
  batch    aggregate throughput vs concurrency, serial baseline vs continuous
           batching. The refresh of the published claim.
  solo     single-stream latency and time-to-first-token. This is what protects
           the claim that batching costs no individual user anything — the half
           of the story that matters and the half that was missing.
  network  the number mycellm has never had: N heterogeneous nodes pooled behind
           the gateway, serving concurrent requests. "One Mac batches better" is
           a systems result; "these uneven machines together serve X" is the
           project's actual thesis.

⚠️ THE iPAD CAN BE MEASURED DIRECTLY — I had this wrong. mycellm nodes dial out
to the gateway, so I assumed they never listen and that an iPad figure had to be
a network figure. They do listen: Discovery (M4 iPad, 10.1.1.150) serves the
OpenAI-compatible API on **8420**, mycellm 0.6.3, platform "ios". The earlier
probe found nothing because it tried 8000/8080/11434 — the project's own
CLAUDE.md gives the default as 8420. Read the project's conventions before
concluding a machine is closed.

So the iPad is measured with `--mode network` pointed straight at the device:

    --gateway http://10.1.1.150:8420 --model qwen2.5-7b-instruct-uncensored-q4_k_m.gguf

⚠️ That path includes LAN round-trip (~30-55ms to Discovery over WiFi) and the
node runs a GGUF through llama.cpp rather than MLX, so it is NOT comparable with
the mlx `batch`/`solo` numbers from the Macs. It is comparable with itself over
time, and with other nodes measured the same way — which is the honest use.

USAGE
    # anchor case, comparable to the June bench
    python scripts/bench_matrix.py --mode batch \
        --model mlx-community/Qwen3-1.7B-4bit --levels 1,4,8,16,32 --repeats 3

    # single-stream cost
    python scripts/bench_matrix.py --mode solo \
        --model mlx-community/Qwen3-1.7B-4bit --repeats 5

    # the pooled network
    python scripts/bench_matrix.py --mode network \
        --gateway https://GATEWAY --api-key "$MYCELLM_KEY" \
        --model qwen3-1.7b --levels 1,4,8,16 --repeats 3

Results land in `bench-results/<host>-<mode>-<model>-<utc>.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# 32 distinct prompts so batched sequences never share a decode path — identical
# prompts would let the batch collapse into one trajectory and flatter the result.
PROMPTS = [
    "Explain how a bicycle stays upright while moving.",
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
    "What problem does a message queue solve?",
    "Explain why HTTPS matters on a public network.",
    "How does a heat pump move heat against a gradient?",
    "What is the difference between latency and bandwidth?",
    "Explain garbage collection to someone who writes SQL.",
    "Why do aeroplanes need pressurised cabins?",
    "Describe what a database index does and its cost.",
]


# ── conditions ───────────────────────────────────────────────────────────────
def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return ""


def thermal_pressure() -> str:
    """⚠️ A throttled Mac reports a slower model, not a slower machine. Recorded
    at both ends of a run so a number measured during throttling is visible as
    such instead of being averaged into the median silently."""
    out = _sh(["pmset", "-g", "therm"])
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    lim = m.group(1) if m else None
    return f"cpu_speed_limit={lim}" if lim else (out.splitlines()[-1][:60] if out else "unknown")


def load_avg() -> list[float]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except Exception:
        return []


def conditions(extra: dict) -> dict:
    ram = _sh(["sysctl", "-n", "hw.memsize"])
    versions = {}
    try:
        import mlx.core as mx
        import mlx_lm
        versions = {"mlx": mx.__version__, "mlx_lm": mlx_lm.__version__}
    except Exception as e:                                  # network mode needs neither
        versions = {"note": f"mlx not imported ({e.__class__.__name__})"}
    return {
        "host": platform.node().split(".")[0],
        "chip": _sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor(),
        "ram_gb": round(int(ram) / 1024**3) if ram.isdigit() else None,
        "os": f"{platform.system()} {_sh(['sw_vers', '-productVersion']) or platform.release()}",
        "python": platform.python_version(),
        **versions,
        "load_at_start": load_avg(),
        "thermal_at_start": thermal_pressure(),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }


def busy(cond: dict) -> bool:
    """⚠️ An honest run says whether it was alone on the machine. Both candidate
    hosts sat at load 4.9 and 17.4 when this harness was written; a benchmark
    taken then is a floor, and the JSON has to admit it rather than let a reader
    assume a quiet box."""
    la = cond.get("load_at_start") or [0]
    return la[0] > 2.0


# ── mode: batch ──────────────────────────────────────────────────────────────
def mode_batch(args) -> dict:
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.generate import BatchGenerator
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(args.model)

    def tok(text: str) -> list[int]:
        msgs = [{"role": "user", "content": text}]
        if hasattr(tokenizer, "apply_chat_template"):
            return tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True)
        return tokenizer.encode(text)

    def serial(prompts):
        sampler = make_sampler(temp=0.0)                    # greedy, so batch is a fair peer
        n, t0 = 0, time.perf_counter()
        for text in prompts:
            for _ in stream_generate(model, tokenizer, tok(text),
                                     max_tokens=args.max_tokens, sampler=sampler):
                n += 1
        return n, time.perf_counter() - t0

    def batched(prompts):
        ids = [tok(t) for t in prompts]
        gen = BatchGenerator(model, stop_tokens=[[t] for t in tokenizer.eos_token_ids],
                             sampler=lambda x: mx.argmax(x, axis=-1),
                             max_tokens=args.max_tokens)
        # ⚠️ `next_generated()`, and `finish_reason == "stop"` is NOT a token.
        # The stop token is emitted like any other but is not part of the
        # completion — counting it inflates every batched figure by one token
        # per sequence, which at concurrency 32 and 80 tokens is a free 1.25%.
        # This loop is lifted from bench_mlx_batching.py deliberately, so the
        # refreshed numbers stay comparable to the ones they replace.
        counts: dict[int, int] = {}
        t0 = time.perf_counter()
        for uid in gen.insert(ids, [args.max_tokens] * len(ids)):
            counts[uid] = 0
        while responses := gen.next_generated():
            for r in responses:
                if r.finish_reason != "stop":
                    counts[r.uid] += 1
        dt = time.perf_counter() - t0
        gen.close()
        return sum(counts.values()), dt

    levels = [int(x) for x in args.levels.split(",")]
    out = []
    for c in levels:
        prompts = [PROMPTS[i % len(PROMPTS)] for i in range(c)]
        runs = []
        for r in range(args.repeats):
            sn, sdt = serial(prompts)
            bn, bdt = batched(prompts)
            runs.append({
                "repeat": r + 1,
                "serial_tok_s": round(sn / sdt, 1),
                "batched_tok_s": round(bn / bdt, 1),
                "speedup": round((bn / bdt) / (sn / sdt), 2),
            })
        med = lambda k: round(statistics.median(x[k] for x in runs), 2)
        out.append({
            "concurrency": c,
            "serial_tok_s_median": med("serial_tok_s"),
            "batched_tok_s_median": med("batched_tok_s"),
            "speedup_median": med("speedup"),
            # ⚠️ Spread published alongside the median. A 4.3× that ranged
            # 2.1–4.3 across repeats is a different claim from one that landed
            # within a whisker every time, and only one of them is quotable.
            "speedup_range": [min(x["speedup"] for x in runs), max(x["speedup"] for x in runs)],
            "runs": runs,
        })
    return {"levels": out}


# ── mode: solo ───────────────────────────────────────────────────────────────
def mode_solo(args) -> dict:
    """⚠️ THE CLAIM THIS PROTECTS. Batching is only worth publishing if no single
    user pays for it, so time-to-first-token and single-stream tok/s are measured
    on their own — not inferred from the concurrency-1 row of a batch sweep,
    which shares its warm cache and its process."""
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(args.model)
    sampler = make_sampler(temp=0.0)
    runs = []
    for r in range(args.repeats):
        text = PROMPTS[r % len(PROMPTS)]
        msgs = [{"role": "user", "content": text}]
        ids = (tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True)
               if hasattr(tokenizer, "apply_chat_template") else tokenizer.encode(text))
        t0 = time.perf_counter()
        ttft, n = None, 0
        for _ in stream_generate(model, tokenizer, ids, max_tokens=args.max_tokens, sampler=sampler):
            if ttft is None:
                ttft = time.perf_counter() - t0
            n += 1
        dt = time.perf_counter() - t0
        runs.append({"repeat": r + 1, "ttft_s": round(ttft or 0, 3),
                     "tok_s": round(n / dt, 1), "tokens": n})
    return {
        "ttft_s_median": round(statistics.median(x["ttft_s"] for x in runs), 3),
        "tok_s_median": round(statistics.median(x["tok_s"] for x in runs), 1),
        "runs": runs,
    }


# ── mode: network ────────────────────────────────────────────────────────────
def mode_network(args) -> dict:
    """The pooled figure. Drives the GATEWAY, not a node — which is the only way
    an iPad can appear in these numbers at all, since nodes dial out and never
    listen. Records which node served each request where the gateway reports it,
    so a run can say WHICH machines carried the load rather than just how fast
    the total was."""
    if not args.gateway:
        raise SystemExit("--gateway is required for --mode network")

    def one(prompt: str) -> dict:
        body = json.dumps({
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            args.gateway.rstrip("/") + "/v1/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {args.api_key}"} if args.api_key else {})},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            payload = json.loads(resp.read())
        dt = time.perf_counter() - t0
        usage = payload.get("usage") or {}
        return {
            "seconds": round(dt, 3),
            "completion_tokens": usage.get("completion_tokens"),
            # Whatever the gateway is willing to say about provenance. Left as
            # raw keys rather than normalised: better an unfamiliar field in the
            # JSON than a silently dropped one.
            "served_by": payload.get("served_by") or payload.get("node") or usage.get("node"),
        }

    levels = [int(x) for x in args.levels.split(",")]
    out = []
    for c in levels:
        runs = []
        for r in range(args.repeats):
            prompts = [PROMPTS[i % len(PROMPTS)] for i in range(c)]
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=c) as ex:
                res = list(ex.map(one, prompts))
            wall = time.perf_counter() - t0
            toks = sum(x["completion_tokens"] or 0 for x in res)
            runs.append({
                "repeat": r + 1,
                "wall_s": round(wall, 2),
                "aggregate_tok_s": round(toks / wall, 1) if wall else None,
                "slowest_request_s": round(max(x["seconds"] for x in res), 3),
                "nodes": sorted({str(x["served_by"]) for x in res if x["served_by"]}),
            })
        out.append({
            "concurrency": c,
            "aggregate_tok_s_median": round(statistics.median(
                x["aggregate_tok_s"] for x in runs if x["aggregate_tok_s"]), 1),
            "slowest_request_s_median": round(statistics.median(
                x["slowest_request_s"] for x in runs), 3),
            "nodes_seen": sorted({n for x in runs for n in x["nodes"]}),
            "runs": runs,
        })
    return {"levels": out}


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("batch", "solo", "network"), required=True)
    ap.add_argument("--model", required=True,
                    help="mlx repo id for batch/solo; gateway model name for network")
    ap.add_argument("--levels", default="1,4,8,16,32")
    ap.add_argument("--max-tokens", type=int, default=80,
                    help="80 matches the June bench — keep it to stay comparable")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--gateway", default=os.environ.get("MYCELLM_GATEWAY"))
    ap.add_argument("--api-key", default=os.environ.get("MYCELLM_API_KEY"))
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--note", default="", help="anything the JSON should remember")
    ap.add_argument("--out-dir", default="bench-results")
    args = ap.parse_args()

    cond = conditions({"mode": args.mode, "model": args.model,
                       "max_tokens": args.max_tokens, "repeats": args.repeats,
                       "sampler": "greedy (temp=0)", "note": args.note})
    if busy(cond):
        print(f"⚠️  load is {cond['load_at_start']} — this run is a FLOOR, not a result.",
              file=sys.stderr)

    result = {"batch": mode_batch, "solo": mode_solo, "network": mode_network}[args.mode](args)

    cond["load_at_end"] = load_avg()
    cond["thermal_at_end"] = thermal_pressure()
    cond["machine_was_busy"] = busy(cond)
    doc = {"conditions": cond, "result": result}

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", args.model.lower()).strip("-")
    path = out / f"{cond['host']}-{args.mode}-{slug}-{stamp}.json"
    path.write_text(json.dumps(doc, indent=2) + "\n")

    print(json.dumps(doc["result"], indent=2))
    print(f"\n  {cond['host']} · {cond['chip']} · {cond['ram_gb']}GB · "
          f"mlx {cond.get('mlx', '—')} · load {cond['load_at_start']}"
          f"{'  ⚠️ BUSY' if cond['machine_was_busy'] else ''}")
    print(f"  → {path}")


if __name__ == "__main__":
    main()
