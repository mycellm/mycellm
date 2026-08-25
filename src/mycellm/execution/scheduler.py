"""JobScheduler — runs queued work when a device is actually fit to take it.

The queue decides *order*; this decides *when*. They are separate because the
ordering is a pure function worth testing exhaustively, while the timing is an
event loop that is tedious to test and boring once the ordering is right.

Eligibility is not a new mechanism. 0.8 already advertises everything needed to
answer "should this device be given discretionary work right now" —
`power_constrained`, `thermal_constrained`, and the foreground rule iOS applies
to itself. Until now that telemetry only *described* a device; here it starts
governing what runs, which is what turns a fleet of intermittent personal
machines from a reliability problem into a scheduling substrate.
"""

from __future__ import annotations

import asyncio
import logging
import time

from mycellm.execution.planner import SWARM_MODEL
from mycellm.execution.queue import (
    JobQueue,
    JobState,
    QueuedJob,
    filter_targets,
    waiting_reason_for,
)

logger = logging.getLogger("mycellm.scheduler")

#: How often to look for runnable work when the queue is non-empty.
POLL_INTERVAL_S = 5.0

#: How often to look when the queue is empty. Longer, because an idle laptop
#: waking up to check an empty table every five seconds is exactly the kind of
#: background drain that gets a node uninstalled.
IDLE_POLL_INTERVAL_S = 30.0


class JobScheduler:
    """Drains a `JobQueue` into `node.execute_job`, when the device allows.

    The node is injected rather than imported so the whole scheduler — polling,
    eligibility, claiming, refunds — is testable against a stub with three
    attributes and no inference, network, or model anywhere near it.
    """

    def __init__(
        self,
        queue: JobQueue,
        node,
        *,
        poll_interval: float = POLL_INTERVAL_S,
        max_concurrent: int = 1,
    ) -> None:
        self._queue = queue
        self._node = node
        self._poll = poll_interval
        self._max_concurrent = max(1, max_concurrent)
        self._running: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        #: Last eligibility answer, surfaced on /v1/jobs so a user can see why
        #: nothing is moving without reading logs.
        self.last_reason: str = ""

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        # Anything left RUNNING belongs to a process that is gone. Recovering
        # it here is the difference between a persistent queue and a queue that
        # merely persists until the first unclean shutdown.
        try:
            reclaimed = await self._queue.reclaim_stale_running()
            if reclaimed:
                logger.info(f"requeued {reclaimed} job(s) from an interrupted run")
        except Exception as e:  # noqa: BLE001 — never block startup on the queue
            logger.warning(f"queue recovery skipped: {e}")
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="job-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a bad tick must not kill the loop
                logger.warning(f"scheduler tick failed: {e}")
                worked = False
            delay = self._poll if worked else IDLE_POLL_INTERVAL_S
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    # ── eligibility ─────────────────────────────────────────────────────

    def eligible(self) -> tuple[bool, str]:
        """Is this device in a state to take discretionary work?

        Returns (ok, reason). The reason is written into the jobs it declines
        to start, so "nothing is happening" always has an answer attached.
        """
        if len(self._running) >= self._max_concurrent:
            return False, f"Busy — {len(self._running)} job(s) already running here."

        # `node.capabilities` is the advertised Capabilities object, refreshed
        # on announce — the same telemetry every other node sees, so the
        # scheduler cannot decide this device is fine while telling the fleet
        # it is throttled.
        try:
            hardware = getattr(getattr(self._node, "capabilities", None), "hardware", None)
        except Exception:  # noqa: BLE001 — telemetry is advisory, not a gate
            hardware = None

        if hardware is not None:
            if getattr(hardware, "power_constrained", False):
                return False, "Waiting — this device is in Low Power Mode or low on battery."
            if getattr(hardware, "thermal_constrained", False):
                return False, "Waiting — this device is thermally throttled."

        return True, ""

    # ── one step ────────────────────────────────────────────────────────

    async def tick(self) -> bool:
        """Advance the queue once. Returns True if anything was started.

        Split out from the loop so tests drive the scheduler a step at a time
        instead of racing a background task.
        """
        expired = await self._queue.expire_due()
        for job_id in expired:
            await self._refund(job_id, "expired")

        pending = await self._queue.pending()
        if not pending:
            self.last_reason = ""
            return False

        ok, reason = self.eligible()
        self.last_reason = reason
        if not ok:
            # Record the device-level reason on the head of the queue only.
            # Writing it to every pending row would be N updates a poll to say
            # the same thing, and the head is the job whose turn it actually is.
            await self._queue.note_waiting(pending[0].job_id, reason)
            return False

        all_targets = self._targets()
        started = False
        for job in pending:
            if len(self._running) >= self._max_concurrent:
                break
            targets = filter_targets(all_targets, job.min_tier)
            # A strategy model is not a target's model. `mycellm/swarm` names
            # how to execute, not what to execute on, so filtering targets by
            # it would empty the field and queue the job forever waiting for a
            # node to "serve mycellm/swarm" — which nothing ever will.
            if job.model and job.model != SWARM_MODEL:
                targets = [t for t in targets if getattr(t, "model", "") == job.model]
            wait = waiting_reason_for(job, targets, all_targets)
            if wait:
                await self._queue.note_waiting(job.job_id, wait)
                continue
            if not await self._queue.claim(job.job_id):
                continue  # another worker took it
            self._spawn(job, targets)
            started = True
        return started

    def _targets(self) -> list:
        try:
            return list(self._node.execution_targets())
        except Exception as e:  # noqa: BLE001
            logger.debug(f"no execution targets: {e}")
            return []

    def _spawn(self, job: QueuedJob, targets: list) -> None:
        task = asyncio.create_task(self._run(job, targets), name=f"job-{job.job_id}")
        self._running[job.job_id] = task
        task.add_done_callback(lambda _t: self._running.pop(job.job_id, None))

    async def _run(self, job: QueuedJob, targets: list) -> None:
        try:
            result = await self._node.execute_job(job.to_job(), targets=targets)
        except asyncio.CancelledError:
            await self._queue.requeue(job.job_id, "Interrupted — will retry.")
            raise
        except Exception as e:  # noqa: BLE001 — a failed job is data, not a crash
            logger.warning(f"job {job.job_id} failed: {e}")
            await self._queue.finish(job.job_id, state=JobState.FAILED, error=str(e))
            await self._refund(job.job_id, "failed")
            return

        text = (result or {}).get("text", "")
        error = (result or {}).get("error", "")
        meta = (result or {}).get("meta", {}) or {}
        if error and not text:
            await self._queue.finish(job.job_id, state=JobState.FAILED,
                                     error=error, meta=meta)
            await self._refund(job.job_id, "failed")
            return

        # Which node and model actually answered. Recording it is the same
        # requirement as on-device chat: a queued job may run hours later on a
        # different machine than the one that would have taken it at submit
        # time, so "who answered" is not derivable after the fact.
        served_by, served_model = _attribution(meta, targets)
        await self._queue.finish(
            job.job_id, state=JobState.DONE, text=text,
            served_by=served_by, served_model=served_model, meta=meta,
        )

    async def _refund(self, job_id: str, why: str) -> None:
        """Return a stake for work that never ran.

        A stake buys *position*, not a ticket. Keeping it after the job expired
        unrun would make bidding a gamble on the fleet's availability, and
        nobody would bid twice.
        """
        ledger = getattr(self._node, "ledger", None)
        if ledger is None:
            return
        try:
            job = await self._queue.get(job_id)
            if not job or job.stake <= 0 or not job.owner_id:
                return
            await ledger.credit(
                job.owner_id, job.stake, f"queue_stake_refund:{why}",
                counterparty_id=getattr(self._node, "peer_id", ""),
            )
            logger.info(f"refunded {job.stake} credits for {why} job {job_id}")
        except Exception as e:  # noqa: BLE001 — a refund failure must not lose the job
            logger.warning(f"stake refund failed for {job_id}: {e}")


def _attribution(meta: dict, targets: list) -> tuple[str, str]:
    """Best available answer to "who served this", from the plan's own record.

    The plan records target strings like `peer:1a2b3c4d:qwen3-9b`; parsing that
    back is uglier than threading attribution through the coordinator, but it
    is also the only source that reflects what *actually ran* rather than what
    was planned to run before failover.
    """
    units = meta.get("units") or []
    for unit in units:
        if unit.get("role") in ("direct", "synthesizer"):
            target = str(unit.get("target", ""))
            model = str(unit.get("model", ""))
            if target.startswith("peer:"):
                return target.split(":")[1], model
            if target.startswith("group:"):
                return target.split(":")[1], model
            return "local", model
    if targets:
        return "local", getattr(targets[0], "model", "")
    return "", ""


def now_s() -> float:
    return time.time()
