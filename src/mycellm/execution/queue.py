"""The async job queue — work that waits for a device instead of failing.

Mycellm's fleet is made of personal machines, and personal machines are
intermittent by nature: phones sleep, laptops close, iPads charge overnight.
Through 0.7 every one of those was a failed request, and 0.8 spent a lot of
effort making the failure *legible* — device telemetry, refusal reasons,
degradation reporting. This module takes the next step and stops treating
intermittency as a fault at all.

The rules that matter, and why each one is not negotiable:

- **A queued job outlives the process.** It is in SQLite before the caller is
  told it was accepted. A queue that loses work on restart is worse than no
  queue, because the caller stopped holding the request.
- **Priority is boring on purpose.** stake + waiting time + whose hardware it
  is. Mechanism design is where projects like this die; there is no auction,
  no market clearing price, and no second-price anything.
- **Waiting is never unexplained.** Every job that could not start records why
  in the user's words. A queue that cannot say what it is waiting for is
  indistinguishable from a hang, and we have already paid for that lesson once
  with the streaming timeout.
- **A stake is staked, not spent.** Refunded on expiry or cancellation, so
  bidding for position is never a lottery ticket.

The scheduler that consumes this lives in `scheduler.py`; everything here is
storage plus pure functions, so priority and eligibility can be tested without
a fleet, an event loop, or a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from mycellm.execution.models import Job

#: Tier ranking, matching `router.model_resolver.TIER_THRESHOLDS`.
TIER_RANK = {"frontier": 4, "capable": 3, "fast": 2, "tiny": 1}

#: Priority gained per hour spent waiting.
#:
#: ⚠️ ANTI-STARVATION IS NOT A REFINEMENT, IT IS WHAT KEEPS BIDDING HONEST.
#: With any bid and no age term, a queue converges on pay-to-play: unpaid work
#: never reaches the front, the people contributing hardware stop seeing their
#: own jobs run, and they leave. This term is why a patient job always beats a
#: rich one eventually.
AGE_BONUS_PER_HOUR = 10.0

#: Default lifetime for a queued job. Long enough to survive an overnight
#: charge cycle, which is the canonical case this whole module exists for.
DEFAULT_TTL_S = 24 * 3600


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    #: Deadline passed before any device could take it.
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (JobState.DONE, JobState.FAILED,
                        JobState.EXPIRED, JobState.CANCELLED)


@dataclass
class QueuedJob:
    """A job and everything the scheduler needs to decide when to run it."""

    job_id: str
    messages: list[dict]
    owner_id: str = ""
    model: str = ""
    min_tier: str = ""
    trust: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    token_budget: int = 0
    fanout: int = 0
    state: JobState = JobState.QUEUED
    stake: float = 0.0
    waiting_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    expires_at: float = 0.0
    attempts: int = 0
    result_text: str = ""
    error: str = ""
    served_by: str = ""
    served_model: str = ""
    meta: dict = field(default_factory=dict)

    def age_s(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.created_at)

    def priority(self, now: float | None = None) -> float:
        """Score used to order jobs *within* an ownership class.

        Deliberately not the whole ordering: see `sort_key`, where owning the
        hardware is applied as a hard rule rather than folded in here as a very
        large number. Encoding a hard rule as a weight is how it stops being
        one.
        """
        return self.stake + AGE_BONUS_PER_HOUR * (self.age_s(now) / 3600.0)

    def to_job(self, network_ids: list[str] | None = None) -> Job:
        """Convert to the fabric's `Job`, for planning and execution."""
        return Job(
            job_id=self.job_id,
            model=self.model,
            messages=list(self.messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            network_ids=network_ids,
            token_budget=self.token_budget,
            trust=self.trust,
            fanout=self.fanout,
        )


def sort_key(job: QueuedJob, owner_id: str, now: float | None = None) -> tuple:
    """Ordering for the run queue. Lower sorts first.

    ⚠️ OWNER AFFINITY IS THE FIRST TERM, AND THAT IS THE OWNERSHIP CLAIM MADE
    EXECUTABLE. On hardware you own, your work outranks a stranger's at any
    bid — not "usually", not "weighted heavily", always. A marketplace cannot
    make that promise, because its entire supply depends on strangers' jobs
    getting served; being able to make it is the difference between a fleet and
    a marketplace, so it must not be expressible as a number someone can
    outbid.

    The cost is real and worth stating plainly: while the owner has queued
    work, other people's jobs wait regardless of age. That is the intended
    trade for a *personal* fleet. A node that wants to serve others fairly
    should run them on separate hardware, which is what serving groups are for.
    """
    own = 0 if (owner_id and job.owner_id == owner_id) or not job.owner_id else 1
    return (own, -job.priority(now), job.created_at)


def tier_ok(min_tier: str, params_b: float) -> bool:
    """Does a model of this size satisfy the floor?

    Mirrors `model_resolver._apply_constraints` so the queue and the resolver
    never disagree about what "capable" admits.
    """
    floor = TIER_RANK.get(min_tier, 0)
    if not floor:
        return True
    from mycellm.router.model_resolver import derive_tier

    return TIER_RANK.get(derive_tier(params_b), 0) >= floor


def filter_targets(targets: list, min_tier: str) -> list:
    """Keep only targets meeting the floor.

    ⚠️ A TARGET OF UNKNOWN SIZE IS EXCLUDED BY ANY FLOOR, NOT ASSUMED INTO ONE.
    `estimate_param_count` defaults an unparseable name to 7B, which would make
    a `fast` floor silently admit models nobody sized — the same silent
    downgrade the floor exists to prevent, arriving through the back door.
    `parse_param_count` returns None instead, and None never qualifies.
    """
    if not min_tier:
        return list(targets)
    from mycellm.router.model_resolver import parse_param_count

    kept = []
    for t in targets:
        params = getattr(t, "params_b", 0.0) or parse_param_count(getattr(t, "model", ""))
        if params and tier_ok(min_tier, params):
            kept.append(t)
    return kept


def waiting_reason_for(job: QueuedJob, targets: list, all_targets: list) -> str:
    """Say why this job is not running, specifically enough to act on.

    "No capable-tier model is reachable (3 smaller models online)" tells
    someone to wake the Mac Studio. "Waiting" tells them nothing, and after a
    few minutes it reads as a bug.
    """
    if not all_targets:
        return "No node is serving any model right now."
    if job.min_tier and not targets:
        return (
            f"No {job.min_tier}-tier model is reachable "
            f"({len(all_targets)} model(s) online below that tier)."
        )
    if job.model and not targets:
        from mycellm.execution.planner import SWARM_MODEL

        if job.model == SWARM_MODEL:
            return "Not enough distinct models are online to form a swarm."
        return f"No node is currently serving '{job.model}'."
    if not targets:
        return "No target matched this job's constraints."
    return ""


class JobQueue:
    """Persistent queue over `QueuedJobRow`.

    Thin by design: the interesting decisions are the pure functions above, so
    this class is only responsible for durability and for claiming a job
    atomically enough that two schedulers cannot run the same work twice.
    """

    def __init__(self, owner_id: str = "") -> None:
        self._owner_id = owner_id

    # ── writes ──────────────────────────────────────────────────────────

    async def submit(
        self,
        messages: list[dict],
        *,
        owner_id: str = "",
        model: str = "",
        min_tier: str = "",
        trust: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        token_budget: int = 0,
        fanout: int = 0,
        stake: float = 0.0,
        ttl_s: float = DEFAULT_TTL_S,
    ) -> QueuedJob:
        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        job = QueuedJob(
            job_id=Job.new_id("q"),
            messages=messages,
            owner_id=owner_id or self._owner_id,
            model=model,
            min_tier=min_tier,
            trust=trust,
            temperature=temperature,
            max_tokens=max_tokens,
            token_budget=token_budget,
            fanout=fanout,
            stake=max(0.0, stake),
            created_at=now,
            updated_at=now,
            expires_at=(now + ttl_s) if ttl_s > 0 else 0.0,
        )
        async with get_session() as session:
            session.add(QueuedJobRow(
                job_id=job.job_id,
                owner_id=job.owner_id,
                model=job.model,
                messages=job.messages,
                min_tier=job.min_tier,
                trust=job.trust,
                temperature=job.temperature,
                max_tokens=job.max_tokens,
                token_budget=job.token_budget,
                fanout=job.fanout,
                state=JobState.QUEUED.value,
                stake=job.stake,
                created_at=job.created_at,
                updated_at=job.updated_at,
                expires_at=job.expires_at,
            ))
            await session.commit()
        return job

    async def claim(self, job_id: str) -> bool:
        """Move a job QUEUED → RUNNING. False if someone else got there first.

        The state check is inside the UPDATE rather than a read-then-write, so
        two schedulers racing on the same row cannot both win. Not a
        distributed lock — one node, possibly several workers — which is the
        only contention that exists today.
        """
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        async with get_session() as session:
            result = await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.job_id == job_id,
                       QueuedJobRow.state == JobState.QUEUED.value)
                .values(state=JobState.RUNNING.value, started_at=now, updated_at=now,
                        attempts=QueuedJobRow.attempts + 1, waiting_reason="")
            )
            await session.commit()
            return bool(result.rowcount)

    async def finish(
        self,
        job_id: str,
        *,
        state: JobState,
        text: str = "",
        error: str = "",
        served_by: str = "",
        served_model: str = "",
        meta: dict | None = None,
    ) -> None:
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        async with get_session() as session:
            await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.job_id == job_id)
                .values(state=state.value, finished_at=now, updated_at=now,
                        result_text=text, error=error, served_by=served_by,
                        served_model=served_model, meta=meta or {})
            )
            await session.commit()

    async def requeue(self, job_id: str, reason: str = "") -> None:
        """Put a claimed job back. Used when execution could not start.

        Distinct from `finish(FAILED)`: nothing was attempted, so the job keeps
        its place and its stake rather than consuming a real attempt.
        """
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        async with get_session() as session:
            await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.job_id == job_id,
                       QueuedJobRow.state == JobState.RUNNING.value)
                .values(state=JobState.QUEUED.value, updated_at=now,
                        started_at=0.0, waiting_reason=reason,
                        attempts=QueuedJobRow.attempts - 1)
            )
            await session.commit()

    async def note_waiting(self, job_id: str, reason: str) -> None:
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        async with get_session() as session:
            await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.job_id == job_id,
                       QueuedJobRow.state == JobState.QUEUED.value)
                .values(waiting_reason=reason, updated_at=time.time())
            )
            await session.commit()

    async def cancel(self, job_id: str) -> bool:
        """Cancel a job that has not finished. Returns False if already done.

        A RUNNING job is cancellable too — the coordinator's own budget
        cancellation handles the in-flight units; this marks the intent so a
        result that arrives afterwards is not written over it.
        """
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        async with get_session() as session:
            result = await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.job_id == job_id,
                       QueuedJobRow.state.in_(
                           [JobState.QUEUED.value, JobState.RUNNING.value]))
                .values(state=JobState.CANCELLED.value, finished_at=now, updated_at=now)
            )
            await session.commit()
            return bool(result.rowcount)

    async def expire_due(self, now: float | None = None) -> list[str]:
        """Expire jobs past their deadline. Returns the ids expired.

        Returned rather than merely counted because each one owes its owner a
        stake refund, and the caller is what holds the ledger.
        """
        from sqlalchemy import select, update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        ts = now if now is not None else time.time()
        async with get_session() as session:
            rows = (await session.execute(
                select(QueuedJobRow.job_id).where(
                    QueuedJobRow.state == JobState.QUEUED.value,
                    QueuedJobRow.expires_at > 0,
                    QueuedJobRow.expires_at <= ts,
                )
            )).scalars().all()
            if rows:
                await session.execute(
                    update(QueuedJobRow)
                    .where(QueuedJobRow.job_id.in_(list(rows)))
                    .values(state=JobState.EXPIRED.value, finished_at=ts, updated_at=ts,
                            error="Deadline passed before any device could run it.")
                )
                await session.commit()
            return list(rows)

    async def reclaim_stale_running(self, older_than_s: float = 3600.0) -> int:
        """Return jobs stuck in RUNNING to the queue.

        A node killed mid-job leaves rows claimed forever; without this they
        are invisible to the scheduler and never finish. Recovery on startup is
        the only way a persistent queue survives an unclean shutdown.
        """
        from sqlalchemy import update

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        now = time.time()
        async with get_session() as session:
            result = await session.execute(
                update(QueuedJobRow)
                .where(QueuedJobRow.state == JobState.RUNNING.value,
                       QueuedJobRow.started_at > 0,
                       QueuedJobRow.started_at < now - older_than_s)
                .values(state=JobState.QUEUED.value, started_at=0.0, updated_at=now,
                        waiting_reason="Requeued after an interrupted run.")
            )
            await session.commit()
            return int(result.rowcount or 0)

    # ── reads ───────────────────────────────────────────────────────────

    async def get(self, job_id: str) -> QueuedJob | None:
        from sqlalchemy import select

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        async with get_session() as session:
            row = (await session.execute(
                select(QueuedJobRow).where(QueuedJobRow.job_id == job_id)
            )).scalar_one_or_none()
            return _from_row(row) if row else None

    async def pending(self, limit: int = 200) -> list[QueuedJob]:
        """Queued jobs, best-first by `sort_key`."""
        from sqlalchemy import select

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        async with get_session() as session:
            rows = (await session.execute(
                select(QueuedJobRow)
                .where(QueuedJobRow.state == JobState.QUEUED.value)
                .order_by(QueuedJobRow.created_at)
                .limit(limit)
            )).scalars().all()
        jobs = [_from_row(r) for r in rows]
        now = time.time()
        jobs.sort(key=lambda j: sort_key(j, self._owner_id, now))
        return jobs

    async def list(
        self, *, state: str = "", owner_id: str = "", limit: int = 50
    ) -> list[QueuedJob]:
        from sqlalchemy import desc, select

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        stmt = select(QueuedJobRow)
        if state:
            stmt = stmt.where(QueuedJobRow.state == state)
        if owner_id:
            stmt = stmt.where(QueuedJobRow.owner_id == owner_id)
        stmt = stmt.order_by(desc(QueuedJobRow.created_at)).limit(limit)
        async with get_session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_from_row(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        from sqlalchemy import func, select

        from mycellm.storage.engine import get_session
        from mycellm.storage.models import QueuedJobRow

        async with get_session() as session:
            rows = (await session.execute(
                select(QueuedJobRow.state, func.count()).group_by(QueuedJobRow.state)
            )).all()
        return {state: int(n) for state, n in rows}

    async def position(self, job_id: str) -> int:
        """1-based position in the run queue, or 0 if not queued.

        Computed from the same ordering the scheduler uses, so the number shown
        to a user is the number that will actually be honoured.
        """
        for i, job in enumerate(await self.pending(), start=1):
            if job.job_id == job_id:
                return i
        return 0


def _from_row(row) -> QueuedJob:
    return QueuedJob(
        job_id=row.job_id,
        messages=list(row.messages or []),
        owner_id=row.owner_id,
        model=row.model,
        min_tier=row.min_tier,
        trust=row.trust,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        token_budget=row.token_budget,
        fanout=row.fanout,
        state=JobState(row.state),
        stake=row.stake,
        waiting_reason=row.waiting_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        expires_at=row.expires_at,
        attempts=row.attempts,
        result_text=row.result_text,
        error=row.error,
        served_by=row.served_by,
        served_model=row.served_model,
        meta=dict(row.meta or {}),
    )
