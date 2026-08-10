"""Multi-step tool-calling reliability harness for mycellm nodes.

Runs eval scenarios from a sibling checkout of antoinezambelli/forge against
any OpenAI-compatible mycellm endpoint. Subclasses forge's LlamafileClient to
skip its /props probe (mycellm's SPA catch-all returns HTML there) and
reuses forge's WorkflowRunner + Guardrails for the eval loop.

This is a maintainer-only development tool, not part of hyphae's shipped
runtime. Requires a separate checkout of forge:

    git clone https://github.com/antoinezambelli/forge

forge is MIT-licensed:
    https://github.com/antoinezambelli/forge/blob/main/LICENSE

Usage:
    python benchmark/tool_call_eval.py [--scenario basic_2step] [--runs 5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import os
from pathlib import Path

# External eval-scenario repo (cloned separately, MIT licensed).
# Point this at your forge checkout. Kept as an env var rather than a fixed
# path so the script works wherever you cloned it.
_EXTERNAL_EVAL_REPO = Path(os.environ.get("FORGE_REPO", "../forge")).expanduser()
sys.path.insert(0, str(_EXTERNAL_EVAL_REPO))

from forge.clients.llamafile import LlamafileClient
from forge.context.manager import ContextManager
from forge.context.strategies import TieredCompact
from forge.core.runner import WorkflowRunner
from tests.eval.eval_runner import _build_workflow_with_capture
from tests.eval.scenarios import ALL_SCENARIOS


class MycellmClient(LlamafileClient):
    """LlamafileClient subclass that skips /props (mycellm's web UI catch-all
    returns the HTML index for unknown paths)."""

    async def get_context_length(self):
        return 32768  # qwen2.5-coder-1.5b-instruct-q8_0 reports 32768


async def run_one(scenario, client, runs: int, verbose: bool = False):
    results = []
    for i in range(runs):
        workflow, capture, validate_state_fn = _build_workflow_with_capture(scenario, ablation=None)
        ctx = ContextManager(strategy=TieredCompact(), budget_tokens=scenario.budget_tokens or 8192)
        runner = WorkflowRunner(
            client=client,
            context_manager=ctx,
            max_iterations=scenario.max_iterations,
            max_retries_per_step=scenario.max_retries_per_step,
            max_tool_errors=scenario.max_tool_errors,
        )

        start = time.monotonic()
        record = {"run": i + 1}
        try:
            await runner.run(workflow, scenario.user_message)
            elapsed = time.monotonic() - start
            record["completeness"] = True
            record["elapsed_s"] = round(elapsed, 1)
            record["terminal_args"] = capture.get("args")
            if scenario.validate and capture.get("args") is not None:
                try:
                    accuracy = scenario.validate(capture["args"])
                    record["accuracy"] = bool(accuracy) if accuracy is not None else True
                except Exception as exc:
                    record["accuracy"] = False
                    record["validate_error"] = f"{type(exc).__name__}: {exc}"
            mark = "PASS" if record.get("accuracy") is not False else "FAIL"
            print(f"  [{i+1}/{runs}] {mark} ({elapsed:.1f}s) accuracy={record.get('accuracy')}")
        except Exception as e:
            elapsed = time.monotonic() - start
            record["completeness"] = False
            record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            record["elapsed_s"] = round(elapsed, 1)
            print(f"  [{i+1}/{runs}] FAIL ({elapsed:.1f}s) {record['error']}")
        results.append(record)
    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="basic_2step")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default="http://localhost:8420/v1")
    parser.add_argument("--model", default="qwen2.5-coder-1.5b-instruct-q8_0")
    parser.add_argument("--mode", default="auto", choices=["native", "prompt", "auto"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    scenarios_by_name = {s.name: s for s in ALL_SCENARIOS}
    if args.scenario not in scenarios_by_name:
        print(f"Unknown scenario '{args.scenario}'. Available:")
        for n in sorted(scenarios_by_name):
            print(f"  {n}")
        sys.exit(1)

    scenario = scenarios_by_name[args.scenario]
    client = MycellmClient(
        gguf_path=Path(args.model),
        base_url=args.base_url,
        mode=args.mode,
        recommended_sampling=False,
    )

    print(f"Forge eval against mycellm")
    print(f"  base_url: {args.base_url}")
    print(f"  model:    {args.model}")
    print(f"  scenario: {args.scenario} (budget={scenario.budget_tokens}, max_iters={scenario.max_iterations})")
    print(f"  runs:     {args.runs}")
    print("=" * 60)
    results = await run_one(scenario, client, args.runs, verbose=args.verbose)

    complete = [r for r in results if r.get("completeness")]
    accurate = [r for r in complete if r.get("accuracy") is True]
    out = {
        "scenario": args.scenario,
        "model": args.model,
        "base_url": args.base_url,
        "runs": results,
        "summary": {
            "completeness_rate": round(len(complete) / len(results), 3),
            "accuracy_rate": round(len(accurate) / len(results), 3),
            "avg_elapsed_s": round(sum(r["elapsed_s"] for r in results) / len(results), 1),
        },
    }
    out_path = Path(__file__).parent / f"tool-call-eval-{args.scenario}-{int(time.time())}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n{'=' * 60}")
    print(f"Report: {out_path}")
    print(f"Summary: completeness {out['summary']['completeness_rate']:.0%}  accuracy {out['summary']['accuracy_rate']:.0%}  avg {out['summary']['avg_elapsed_s']}s")

    await client._http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
