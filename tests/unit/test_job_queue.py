"""The async job queue: ordering, persistence, eligibility, refunds.

Two halves, tested differently on purpose.

The ordering and the tier filter are pure functions, so they get exhaustive
table-style tests with no database anywhere near them — that is the whole
reason they were written as pure functions. The queue and the scheduler need
real persistence (a queue that loses work on restart is worse than no queue),
so those run against a real SQLite file.

Nothing here needs inference, a network, or a model.
"""

import asyncio

import pytest

from mycellm.execution.models import Target
from mycellm.execution.queue import (
    AGE_BONUS_PER_HOUR,
    JobQueue,
    JobState,
    QueuedJob,
    filter_targets,
    sort_key,
    tier_ok,
    waiting_reason_for,
)
from mycellm.execution.scheduler import JobScheduler
from mycellm.storage import close_database, init_database


@pytest.fixture
async def db(tmp_path):
    engine = await init_database(db_path=str(tmp_path / "queue.db"))
    yield engine
    await close_database()


def qjob(job_id="j1", owner="alice", stake=0.0, age_h=0.0, **kw) -> QueuedJob:
    return QueuedJob(
        job_id=job_id, messages=[{"role": "user", "content": "hi"}],
        owner_id=owner, stake=stake, created_at=1_000_000.0 - age_h * 3600.0, **kw
    )


NOW = 1_000_000.0


# ── priority ────────────────────────────────────────────────────────────

def test_stake_raises_priority():
    assert qjob(stake=50).priority(NOW) > qjob(stake=0).priority(NOW)


def test_waiting_raises_priority():
    """Anti-starvation: an hour of waiting is worth AGE_BONUS_PER_HOUR."""
    assert qjob(age_h=1).priority(NOW) == pytest.approx(AGE_BONUS_PER_HOUR)
    assert qjob(age_h=3).priority(NOW) == pytest.approx(3 * AGE_BONUS_PER_HOUR)


def test_patience_eventually_beats_money():
    """The property that keeps bidding from becoming pay-to-play.

    A rich job jumps the queue *now*; it must not be able to hold the front
    forever, or unpaid work never runs and the people donating hardware leave.
    """
    rich = qjob("rich", stake=100, age_h=0)
    patient = qjob("patient", stake=0, age_h=100 / AGE_BONUS_PER_HOUR + 1)
    assert patient.priority(NOW) > rich.priority(NOW)


def test_owner_affinity_beats_any_bid():
    """Owner affinity is a RULE, not a weight — the ownership claim, executable.

    A stranger with an enormous stake still waits behind the owner's work on
    the owner's hardware. If this ever becomes outbiddable, mycellm is a
    marketplace and its one structural advantage is gone.
    """
    mine = qjob("mine", owner="me", stake=0)
    theirs = qjob("theirs", owner="stranger", stake=1_000_000)
    ordered = sorted([theirs, mine], key=lambda j: sort_key(j, "me", NOW))
    assert [j.job_id for j in ordered] == ["mine", "theirs"]


def test_age_still_orders_within_a_class():
    """Affinity separates classes; age and stake order inside one."""
    a = qjob("a", owner="stranger", age_h=5)
    b = qjob("b", owner="stranger", age_h=1)
    ordered = sorted([b, a], key=lambda j: sort_key(j, "me", NOW))
    assert [j.job_id for j in ordered] == ["a", "b"]


def test_unowned_jobs_count_as_local():
    """A job with no owner came from this machine's own CLI or dashboard."""
    local = qjob("local", owner="")
    remote = qjob("remote", owner="stranger", stake=999)
    ordered = sorted([remote, local], key=lambda j: sort_key(j, "me", NOW))
    assert [j.job_id for j in ordered] == ["local", "remote"]


# ── tier floor ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("floor,params,expected", [
    ("", 0.5, True),            # no floor admits anything
    ("tiny", 0.5, True),
    ("fast", 0.5, False),
    ("fast", 7.0, True),
    ("capable", 7.0, False),
    ("capable", 35.0, True),
    ("frontier", 35.0, False),
    ("frontier", 70.0, True),
])
def test_tier_ok(floor, params, expected):
    assert tier_ok(floor, params) is expected


def test_filter_targets_drops_below_floor():
    targets = [
        Target(model="tiny-1b", params_b=1.0),
        Target(model="mid-9b", params_b=9.0),
        Target(model="big-70b", params_b=70.0),
    ]
    kept = [t.model for t in filter_targets(targets, "capable")]
    assert kept == ["big-70b"]


def test_filter_targets_falls_back_to_the_name():
    """No advertised param count is normal on fleet entries; use the name."""
    kept = filter_targets([Target(model="qwen3-70b", params_b=0.0)], "frontier")
    assert len(kept) == 1


def test_unknown_size_never_counts_upward():
    """A model we cannot size must not satisfy a frontier floor.

    Guessing upward would serve a 1B model to someone who explicitly asked for
    frontier — the exact silent downgrade the floor exists to prevent.
    """
    assert filter_targets([Target(model="mystery", params_b=0.0)], "frontier") == []


# ── waiting reasons ─────────────────────────────────────────────────────

def test_reason_names_the_missing_tier():
    job = qjob(min_tier="frontier")
    small = [Target(model="a-1b", params_b=1.0)]
    reason = waiting_reason_for(job, [], small)
    assert "frontier" in reason and "1 model" in reason


def test_reason_when_nothing_is_online():
    assert "any model" in waiting_reason_for(qjob(), [], [])


def test_no_reason_when_runnable():
    targets = [Target(model="a-9b", params_b=9.0)]
    assert waiting_reason_for(qjob(), targets, targets) == ""


def test_swarm_gets_its_own_reason():
    """`mycellm/swarm` is a strategy, not a model — say so, don't say no node
    is serving a model nothing will ever serve."""
    job = qjob(model="mycellm/swarm")
    reason = waiting_reason_for(job, [], [Target(model="a-9b", params_b=9.0)])
    assert "swarm" in reason.lower()


# ── persistence ─────────────────────────────────────────────────────────

async def test_submit_and_read_back(db):
    q = JobQueue(owner_id="me")
    job = await q.submit([{"role": "user", "content": "hello"}], owner_id="me")
    stored = await q.get(job.job_id)
    assert stored is not None
    assert stored.state is JobState.QUEUED
    assert stored.messages == [{"role": "user", "content": "hello"}]


async def test_claim_is_exclusive(db):
    """Two workers racing the same row: exactly one wins.

    The state check is inside the UPDATE for this reason; a read-then-write
    would let both see QUEUED and both run the job.
    """
    q = JobQueue(owner_id="me")
    job = await q.submit([{"role": "user", "content": "x"}])
    first, second = await asyncio.gather(q.claim(job.job_id), q.claim(job.job_id))
    assert [first, second].count(True) == 1


async def test_pending_is_ordered_by_the_same_rule(db):
    q = JobQueue(owner_id="me")
    await q.submit([{"role": "user", "content": "a"}], owner_id="stranger", stake=100)
    mine = await q.submit([{"role": "user", "content": "b"}], owner_id="me")
    pending = await q.pending()
    assert pending[0].job_id == mine.job_id
    assert await q.position(mine.job_id) == 1


async def test_expiry_marks_only_overdue_jobs(db):
    q = JobQueue(owner_id="me")
    doomed = await q.submit([{"role": "user", "content": "x"}], ttl_s=1)
    kept = await q.submit([{"role": "user", "content": "y"}], ttl_s=10_000)
    expired = await q.expire_due(now=doomed.expires_at + 1)
    assert expired == [doomed.job_id]
    assert (await q.get(kept.job_id)).state is JobState.QUEUED
    assert (await q.get(doomed.job_id)).state is JobState.EXPIRED


async def test_never_expires_when_ttl_is_zero(db):
    q = JobQueue(owner_id="me")
    job = await q.submit([{"role": "user", "content": "x"}], ttl_s=0)
    assert await q.expire_due(now=9e12) == []
    assert (await q.get(job.job_id)).state is JobState.QUEUED


async def test_stale_running_jobs_are_reclaimed(db):
    """A node killed mid-job leaves a row claimed forever.

    Without recovery those rows are invisible to the scheduler and the work is
    silently lost — which is the failure that separates "persistent" from
    "persistent until the first crash".
    """
    q = JobQueue(owner_id="me")
    job = await q.submit([{"role": "user", "content": "x"}])
    await q.claim(job.job_id)
    assert await q.reclaim_stale_running(older_than_s=-1) == 1
    assert (await q.get(job.job_id)).state is JobState.QUEUED


async def test_cancel_then_cancel_again(db):
    q = JobQueue(owner_id="me")
    job = await q.submit([{"role": "user", "content": "x"}])
    assert await q.cancel(job.job_id) is True
    assert await q.cancel(job.job_id) is False


# ── scheduler ───────────────────────────────────────────────────────────

class FakeHardware:
    def __init__(self, power=False, thermal=False):
        self.power_constrained = power
        self.thermal_constrained = thermal


class FakeCaps:
    def __init__(self, hardware):
        self.hardware = hardware


class FakeNode:
    """Three attributes and a coroutine — the whole scheduler contract."""

    peer_id = "node-1"
    ledger = None

    def __init__(self, targets=None, hardware=None, result=None, raises=None):
        self._targets = targets or []
        self.capabilities = FakeCaps(hardware or FakeHardware())
        self._result = result or {"text": "done", "meta": {}}
        self._raises = raises
        self.calls = []

    def execution_targets(self, model=""):
        return list(self._targets)

    async def execute_job(self, job, override_privacy=False, targets=None):
        self.calls.append((job.job_id, [t.model for t in (targets or [])]))
        if self._raises:
            raise self._raises
        return self._result


async def _drain(scheduler):
    """Run one tick and let the spawned job task finish."""
    await scheduler.tick()
    for _ in range(50):
        if not scheduler._running:
            return
        await asyncio.sleep(0.01)


async def test_scheduler_runs_a_queued_job(db):
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)])
    job = await q.submit([{"role": "user", "content": "hi"}], owner_id="node-1")

    await _drain(JobScheduler(q, node))

    assert node.calls and node.calls[0][0] == job.job_id
    assert (await q.get(job.job_id)).state is JobState.DONE


async def test_low_power_defers_work(db):
    """The telemetry 0.8 added starts governing what runs.

    A phone at 5% must not take discretionary work — and the user must be able
    to see that is why, not just that nothing happened.
    """
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)],
                    hardware=FakeHardware(power=True))
    job = await q.submit([{"role": "user", "content": "hi"}])

    scheduler = JobScheduler(q, node)
    assert await scheduler.tick() is False
    assert not node.calls
    stored = await q.get(job.job_id)
    assert stored.state is JobState.QUEUED
    assert "Low Power" in stored.waiting_reason


async def test_thermal_throttle_defers_work(db):
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)],
                    hardware=FakeHardware(thermal=True))
    await q.submit([{"role": "user", "content": "hi"}])
    assert await JobScheduler(q, node).tick() is False


async def test_job_waits_for_a_tier_that_is_not_online(db):
    """The canonical case: "frontier or nothing" while only a 9B is awake."""
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)])
    job = await q.submit([{"role": "user", "content": "hi"}], min_tier="frontier")

    assert await JobScheduler(q, node).tick() is False
    stored = await q.get(job.job_id)
    assert stored.state is JobState.QUEUED
    assert "frontier" in stored.waiting_reason
    assert not node.calls, "must not silently downgrade to the 9B"


async def test_job_runs_once_a_big_enough_model_appears(db):
    """Same job, same fleet, one node woken up — it should just go."""
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)])
    job = await q.submit([{"role": "user", "content": "hi"}], min_tier="frontier")
    scheduler = JobScheduler(q, node)
    await scheduler.tick()

    node._targets.append(Target(model="big-70b", params_b=70.0))
    await _drain(scheduler)

    assert (await q.get(job.job_id)).state is JobState.DONE
    assert node.calls[0][1] == ["big-70b"], "only the qualifying target is planned"


async def test_execution_failure_is_recorded_not_raised(db):
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)],
                    raises=RuntimeError("backend exploded"))
    job = await q.submit([{"role": "user", "content": "hi"}])

    await _drain(JobScheduler(q, node))

    stored = await q.get(job.job_id)
    assert stored.state is JobState.FAILED
    assert "backend exploded" in stored.error


async def test_empty_queue_is_a_no_op(db):
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)])
    assert await JobScheduler(JobQueue(owner_id="node-1"), node).tick() is False
    assert not node.calls


async def test_concurrency_limit_is_respected(db):
    """A personal device that starts three jobs because three were waiting is
    a device someone force-quits."""
    q = JobQueue(owner_id="node-1")
    node = FakeNode(targets=[Target(model="a-9b", params_b=9.0)])
    for _ in range(3):
        await q.submit([{"role": "user", "content": "hi"}])

    scheduler = JobScheduler(q, node, max_concurrent=1)
    await scheduler.tick()
    assert len(scheduler._running) <= 1
    await _drain(scheduler)


# ── unknown model sizes ─────────────────────────────────────────────────
#
# `estimate_param_count` defaults an unparseable name to 7B, which is fine for
# ranking (every unknown scores alike) and wrong for a floor. Caught live: a
# relayed model named `swarm-a` was reported as `tier: "fast"` on /v1/models —
# a guess wearing the clothes of a measurement.

@pytest.mark.parametrize("floor", ["tiny", "fast", "capable", "frontier"])
def test_unsized_target_is_excluded_by_every_floor(floor):
    """Not just the high ones. A `fast` floor admitting an unsized model is
    the same silent downgrade as a `frontier` floor doing it, and it is the
    case the 7B default actually caused."""
    assert filter_targets([Target(model="relay-model", params_b=0.0)], floor) == []


def test_a_named_size_still_qualifies():
    """The fix must not throw away real information along with the guess."""
    kept = filter_targets([Target(model="qwen3-70b", params_b=0.0)], "frontier")
    assert len(kept) == 1


def test_advertised_count_beats_an_unparseable_name():
    """A relay that reports its size is believed even when the name says
    nothing — that is a measurement, not a guess."""
    kept = filter_targets([Target(model="relay-model", params_b=70.0)], "frontier")
    assert len(kept) == 1
