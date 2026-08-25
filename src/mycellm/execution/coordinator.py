"""ExecutionCoordinator — runs an ExecutionPlan.

The planner decides; this executes. Keeping them apart means the interesting
decision (where does this prompt go) is testable without a fleet, and the
interesting failure (a proposer dies mid-job) is testable without a planner.

Execution rules that are not negotiable:

- **A proposer failure is not a job failure.** Swarms exist because
  heterogeneous peers are unreliable. If one of three proposers dies, the job
  synthesises from two and says so.
- **Synthesis failure degrades to the best proposal**, rather than losing work
  that already succeeded and was already paid for.
- **The token budget is a ceiling, not a hint.** Once spent, remaining units are
  cancelled — otherwise a swarm's cost is unbounded by construction.
- **Nothing reports success it did not achieve.** A degraded job says it
  degraded, in `meta`, every time.
"""

from __future__ import annotations

import asyncio
import logging
import time

from mycellm.execution.models import (
    ExecutionPlan,
    Job,
    Role,
    Strategy,
    WorkUnit,
    WorkUnitResult,
)

logger = logging.getLogger("mycellm.execution")

SYNTHESIS_SYSTEM = (
    "You are given several independent answers to the same question, produced "
    "by different models. Some may be wrong. Produce one best answer.\n"
    "- Prefer claims that several answers agree on.\n"
    "- Where they conflict, decide which is better supported and use it.\n"
    "- Do not mention the answers, the models, or that you are merging.\n"
    "- Answer the original question directly."
)


class ExecutionCoordinator:
    """Executes plans by dispatching WorkUnits through a runner callable.

    The runner is injected rather than reaching into the node, so the whole
    coordinator — fan-out, cancellation, budget, degradation — is testable
    without inference, a network, or a model.

    runner(unit) -> WorkUnitResult
    """

    def __init__(self, runner, activity=None, stream_runner=None):
        self._run_unit = runner
        self._activity = activity
        # Optional: stream_runner(unit) -> async iterator of text fragments.
        # Only synthesis uses it. Proposers deliberately do not stream — several
        # models emitting at once is noise, not progress, and the caller cannot
        # use half of three different answers.
        self._stream_unit = stream_runner

    async def execute(self, job: Job, plan: ExecutionPlan) -> dict:
        started = time.monotonic()

        if not plan.units:
            return {
                "text": "",
                "error": "; ".join(plan.reasons) or "no executable target",
                "meta": plan.to_dict(),
            }

        if plan.strategy is Strategy.SWARM:
            result = await self._run_swarm(job, plan)
        else:
            result = await self._run_direct(job, plan)

        result["meta"] = {
            **plan.to_dict(),
            **result.get("meta", {}),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
        return result

    # ── Direct ──────────────────────────────────────────────────────────

    async def _run_direct(self, job: Job, plan: ExecutionPlan) -> dict:
        unit = plan.units[0]
        res = await self._safe_run(unit)
        if not res.ok:
            return {
                "text": "",
                "error": res.error or "inference produced no output",
                "meta": {"units_ok": 0, "units_failed": 1},
            }
        return {
            "text": res.text,
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "meta": {"units_ok": 1, "units_failed": 0, "served_by": str(res.target)},
        }

    # ── Swarm ───────────────────────────────────────────────────────────

    async def _gather_proposals(
        self, plan: ExecutionPlan, on_result=None
    ) -> tuple[list[WorkUnitResult], list[WorkUnitResult], dict, int]:
        """Run the proposer fan-out. Returns (good, failed, meta, spent).

        ⚠️ SHARED BY THE STREAMING AND NON-STREAMING PATHS ON PURPOSE. The
        budget ceiling, the cancellation settle, and the degradation bookkeeping
        below are subtle enough that a second copy would drift — and the way it
        would drift is a streaming swarm quietly spending past a budget that the
        non-streaming one enforces.

        `on_result` is called as each proposer lands, so a caller can report
        progress without learning how the fan-out works.
        """
        proposers = plan.proposers
        budget = plan.token_budget

        tasks = {
            asyncio.ensure_future(self._safe_run(u)): u for u in proposers
        }
        results: list[WorkUnitResult] = []
        spent = 0
        cancelled_for_budget = 0

        try:
            for fut in asyncio.as_completed(list(tasks)):
                res = await fut
                results.append(res)
                spent += res.completion_tokens
                if on_result is not None:
                    on_result(res, len(results), len(proposers))
                # Budget is enforced as results land, which is the only point
                # actual spend is known. Checking beforehand would need an
                # estimate, and an estimate is not a ceiling.
                if budget and spent >= budget:
                    for f, u in tasks.items():
                        if not f.done():
                            f.cancel()
                            cancelled_for_budget += 1
                    break
        finally:
            for f in tasks:
                if not f.done():
                    f.cancel()
            # Let the cancellations settle so no task outlives the job.
            await asyncio.gather(*tasks, return_exceptions=True)

        good = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok and not r.cancelled]

        meta = {
            "units_ok": len(good),
            "units_failed": len(failed),
            "proposers_planned": len(proposers),
            "completion_tokens_spent": spent,
        }
        if cancelled_for_budget:
            meta["cancelled_for_budget"] = cancelled_for_budget
            meta["degraded"] = True
        if failed:
            meta["failures"] = [
                {"target": str(r.target), "error": r.error[:200]} for r in failed
            ]
            meta["degraded"] = True

        return good, failed, meta, spent

    async def _run_swarm(self, job: Job, plan: ExecutionPlan) -> dict:
        budget = plan.token_budget
        good, failed, meta, spent = await self._gather_proposals(plan)

        if not good:
            return {
                "text": "",
                "error": "every proposer failed: "
                         + "; ".join(r.error[:120] for r in failed) if failed
                         else "no proposer produced output",
                "meta": meta,
            }

        # One survivor is not a swarm — return it rather than paying for a
        # synthesis pass that has nothing to merge.
        if len(good) == 1:
            meta["degraded"] = True
            meta["degradation"] = "one proposer survived; returned directly"
            r = good[0]
            return {
                "text": r.text,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "meta": meta,
            }

        synth_unit = plan.synthesizer
        if synth_unit is None:
            meta["degradation"] = "no synthesizer planned; returned longest proposal"
            meta["degraded"] = True
            best = max(good, key=lambda r: len(r.text))
            return {"text": best.text, "completion_tokens": spent, "meta": meta}

        if budget and spent >= budget:
            meta["degradation"] = "budget exhausted before synthesis"
            best = max(good, key=lambda r: len(r.text))
            return {"text": best.text, "completion_tokens": spent, "meta": meta}

        synth = await self._safe_run(
            self._synthesis_unit(synth_unit, job, good)
        )
        if not synth.ok:
            # Do not throw away work that succeeded and was paid for.
            meta["degraded"] = True
            meta["degradation"] = f"synthesis failed ({synth.error[:120]}); " \
                                  f"returned best proposal"
            best = max(good, key=lambda r: len(r.text))
            return {"text": best.text, "completion_tokens": spent, "meta": meta}

        meta["synthesized_by"] = str(synth.target)
        meta["completion_tokens_spent"] = spent + synth.completion_tokens
        return {
            "text": synth.text,
            "prompt_tokens": synth.prompt_tokens,
            "completion_tokens": spent + synth.completion_tokens,
            "meta": meta,
        }

    # ── Streaming ───────────────────────────────────────────────────────

    async def execute_stream(self, job: Job, plan: ExecutionPlan):
        """Run a plan, yielding events as they happen.

        ⚠️ THIS EXISTS BECAUSE `mycellm/swarm` WAS ADVERTISED AND UNSTREAMABLE.
        `/v1/models` listed it, most clients stream by default, and a streaming
        swarm request fell through to ordinary model resolution and answered
        "No model available" — the advertised-but-unreachable shape 0.8 exists
        to remove, reintroduced by the one strategy 0.8 added.

        The shape of a swarm decides what can stream. Proposers run in parallel
        and their output is an *input* to synthesis, not an answer — streaming
        three at once would interleave three different replies into nonsense.
        So the proposer phase reports progress and the synthesis phase streams
        text, which happens to match what a user wants to see: who is working,
        then the answer arriving.

        Events:
          {"type": "progress", "phase": ..., ...}   never user-facing text
          {"type": "text", "text": ...}             real content
          {"type": "done", "meta": {...}}
          {"type": "error", "error": ..., "meta": {...}}
        """
        if not plan.units:
            yield {"type": "error",
                   "error": "; ".join(plan.reasons) or "no executable target",
                   "meta": plan.to_dict()}
            return

        started = time.monotonic()

        if plan.strategy is not Strategy.SWARM:
            # Direct plans have nothing to orchestrate; run and emit once.
            # Real token-by-token streaming for a single model already exists
            # on `route_inference_stream` and the API uses it directly.
            result = await self._run_direct(job, plan)
            if result.get("error"):
                yield {"type": "error", "error": result["error"],
                       "meta": {**plan.to_dict(), **result.get("meta", {})}}
                return
            yield {"type": "text", "text": result.get("text", "")}
            yield {"type": "done",
                   "meta": {**plan.to_dict(), **result.get("meta", {}),
                            "elapsed_s": round(time.monotonic() - started, 3)}}
            return

        budget = plan.token_budget
        proposals: list[dict] = []

        def _note(res: WorkUnitResult, done: int, total: int) -> None:
            proposals.append({
                "target": str(res.target),
                "ok": res.ok,
                "elapsed_s": res.elapsed_s,
                "done": done,
                "total": total,
            })

        yield {"type": "progress", "phase": "proposing",
               "planned": len(plan.proposers),
               "targets": [str(u.target) for u in plan.proposers]}

        good, failed, meta, spent = await self._gather_proposals(plan, on_result=_note)
        meta["proposals"] = proposals

        def _finish(text: str, extra: dict | None = None):
            return {"type": "done",
                    "meta": {**plan.to_dict(), **meta, **(extra or {}),
                             "elapsed_s": round(time.monotonic() - started, 3)}}

        if not good:
            yield {"type": "error",
                   "error": ("every proposer failed: "
                             + "; ".join(r.error[:120] for r in failed)) if failed
                            else "no proposer produced output",
                   "meta": {**plan.to_dict(), **meta}}
            return

        # Every degradation path below returns finished text rather than a
        # stream. Emitting it as a single chunk keeps the wire shape identical
        # — a client must not have to care whether the answer was synthesised
        # or salvaged, only that the metadata says which.
        if len(good) == 1:
            meta["degraded"] = True
            meta["degradation"] = "one proposer survived; returned directly"
            yield {"type": "text", "text": good[0].text}
            yield _finish(good[0].text)
            return

        synth_unit = plan.synthesizer
        if synth_unit is None:
            meta["degraded"] = True
            meta["degradation"] = "no synthesizer planned; returned longest proposal"
            best = max(good, key=lambda r: len(r.text))
            yield {"type": "text", "text": best.text}
            yield _finish(best.text)
            return

        if budget and spent >= budget:
            meta["degraded"] = True
            meta["degradation"] = "budget exhausted before synthesis"
            best = max(good, key=lambda r: len(r.text))
            yield {"type": "text", "text": best.text}
            yield _finish(best.text)
            return

        unit = self._synthesis_unit(synth_unit, job, good)
        yield {"type": "progress", "phase": "synthesizing",
               "target": str(unit.target), "from_proposals": len(good)}

        # No streaming runner: synthesise normally and emit once. Degrading to
        # a single chunk is correct — the answer is still right, it simply
        # arrives all at once, which is what happened before this method
        # existed.
        if self._stream_unit is None:
            synth = await self._safe_run(unit)
            if not synth.ok:
                meta["degraded"] = True
                meta["degradation"] = (f"synthesis failed ({synth.error[:120]}); "
                                       f"returned best proposal")
                best = max(good, key=lambda r: len(r.text))
                yield {"type": "text", "text": best.text}
                yield _finish(best.text)
                return
            meta["synthesized_by"] = str(synth.target)
            meta["completion_tokens_spent"] = spent + synth.completion_tokens
            yield {"type": "text", "text": synth.text}
            yield _finish(synth.text)
            return

        emitted = ""
        try:
            async for fragment in self._stream_unit(unit):
                if not fragment:
                    continue
                emitted += fragment
                yield {"type": "text", "text": fragment}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a synthesiser may fail any way
            logger.debug(f"synthesis stream failed: {e}")
            # ⚠️ FALLING BACK IS ONLY SAFE BEFORE THE FIRST FRAGMENT. Once text
            # has reached the client, emitting a proposal too would append a
            # second answer to a half-finished one. Past that point the honest
            # move is to stop and say the stream broke.
            if emitted:
                meta["degraded"] = True
                meta["degradation"] = f"synthesis stream broke after partial output ({e})"
                yield _finish(emitted)
                return
            meta["degraded"] = True
            meta["degradation"] = f"synthesis failed ({str(e)[:120]}); returned best proposal"
            best = max(good, key=lambda r: len(r.text))
            yield {"type": "text", "text": best.text}
            yield _finish(best.text)
            return

        if not emitted:
            # A stream that yields nothing is indistinguishable from a clean
            # empty answer, and neither is worth handing to a caller who paid
            # for several proposers.
            meta["degraded"] = True
            meta["degradation"] = "synthesis produced no output; returned best proposal"
            best = max(good, key=lambda r: len(r.text))
            yield {"type": "text", "text": best.text}
            yield _finish(best.text)
            return

        meta["synthesized_by"] = str(unit.target)
        yield _finish(emitted)

    @staticmethod
    def _synthesis_unit(
        template: WorkUnit, job: Job, good: list[WorkUnitResult]
    ) -> WorkUnit:
        """Build the synthesis prompt from proposer output.

        Proposals are labelled but their *models are not named*: naming them
        invites the synthesiser to defer to whichever sounds more authoritative
        rather than to the better-supported answer.
        """
        question = job.prompt_text()
        blocks = "\n\n".join(
            f"[Answer {i + 1}]\n{r.text.strip()}" for i, r in enumerate(good)
        )
        return WorkUnit(
            unit_id=template.unit_id,
            role=Role.SYNTHESIZER,
            target=template.target,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {
                    "role": "user",
                    "content": f"Question:\n{question}\n\n{blocks}\n\n"
                               f"Now give the single best answer.",
                },
            ],
            temperature=template.temperature,
            max_tokens=template.max_tokens,
        )

    # ── Dispatch ────────────────────────────────────────────────────────

    async def _safe_run(self, unit: WorkUnit) -> WorkUnitResult:
        """Run one unit, converting every failure into a result.

        A raised exception from one proposer must not abort the job — that is
        the whole point of fanning out to unreliable peers.
        """
        t0 = time.monotonic()
        try:
            res = await self._run_unit(unit)
            if res is None:
                return WorkUnitResult(
                    unit.unit_id, unit.role, unit.target,
                    error="runner returned nothing",
                    elapsed_s=round(time.monotonic() - t0, 3),
                )
            res.elapsed_s = round(time.monotonic() - t0, 3)
            return res
        except asyncio.CancelledError:
            return WorkUnitResult(
                unit.unit_id, unit.role, unit.target, cancelled=True,
                elapsed_s=round(time.monotonic() - t0, 3),
            )
        except Exception as e:  # noqa: BLE001 — a peer may fail any way it likes
            logger.debug(f"WorkUnit {unit.unit_id} on {unit.target} failed: {e}")
            return WorkUnitResult(
                unit.unit_id, unit.role, unit.target, error=str(e),
                elapsed_s=round(time.monotonic() - t0, 3),
            )
