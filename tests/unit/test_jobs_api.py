"""`/v1/jobs` — the contract a caller can actually rely on.

The queue is deliberately not part of the OpenAI-compatible surface, so these
tests fix the shape of the alternative: submit returns immediately with an id
and a position, contradictory requests are refused rather than silently
reinterpreted, and a stake is real money that comes back when the work does
not happen.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from mycellm.api.jobs import router
from mycellm.execution.queue import JobQueue, JobState
from mycellm.storage import close_database, init_database


class FakeLedger:
    def __init__(self, balance=100.0):
        self._balance = balance
        self.entries = []

    async def balance(self, peer_id, network_id=""):
        return self._balance

    async def debit(self, peer_id, amount, reason, **kw):
        self._balance -= amount
        self.entries.append(("debit", amount, reason))

    async def credit(self, peer_id, amount, reason, **kw):
        self._balance += amount
        self.entries.append(("credit", amount, reason))


class FakeNode:
    peer_id = "node-1"

    def __init__(self, queue, ledger=None):
        self.job_queue = queue
        self.job_scheduler = None      # no background work during these tests
        self.ledger = ledger


@pytest.fixture
async def client(tmp_path):
    await init_database(db_path=str(tmp_path / "jobs.db"))
    app = FastAPI()
    app.include_router(router, prefix="/v1/jobs")
    queue = JobQueue(owner_id="node-1")
    ledger = FakeLedger()
    app.state.node = FakeNode(queue, ledger)
    yield TestClient(app), queue, ledger
    await close_database()


MSG = [{"role": "user", "content": "hello"}]


def test_submit_returns_immediately_with_a_position(client):
    http, _, _ = client
    resp = http.post("/v1/jobs", json={"messages": MSG})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "queued"
    assert body["position"] == 1
    assert body["job_id"].startswith("q_")


def test_messages_are_required(client):
    http, _, _ = client
    assert http.post("/v1/jobs", json={"messages": []}).status_code == 400


def test_unknown_tier_is_refused(client):
    http, _, _ = client
    resp = http.post("/v1/jobs", json={"messages": MSG, "min_tier": "enormous"})
    assert resp.status_code == 400
    assert "enormous" in resp.json()["error"]["message"]


def test_model_plus_tier_is_refused_not_reinterpreted(client):
    """The request cannot mean what it says, so it is rejected.

    Accepting it and dropping one half would leave the caller believing a floor
    applied that never did — the failure mode the unified selector exists to
    remove on the UI side, enforced here for API callers who have no selector.
    """
    http, _, _ = client
    resp = http.post("/v1/jobs", json={
        "messages": MSG, "model": "qwen3-9b", "min_tier": "frontier"})
    assert resp.status_code == 400
    assert "min_tier" in resp.json()["error"]["message"]


def test_auto_is_not_a_model_name(client):
    """"auto" means "you choose", so it must not conflict with a tier floor."""
    http, _, _ = client
    resp = http.post("/v1/jobs", json={
        "messages": MSG, "model": "auto", "min_tier": "capable"})
    assert resp.status_code == 200


def test_stake_is_debited_at_submit(client):
    http, _, ledger = client
    http.post("/v1/jobs", json={"messages": MSG, "stake": 25})
    assert ledger.entries == [("debit", 25, "queue_stake")]
    assert ledger._balance == 75


def test_stake_beyond_balance_is_refused(client):
    """Position cannot be bought with credits that do not exist — the refund
    path would otherwise mint them."""
    http, _, ledger = client
    resp = http.post("/v1/jobs", json={"messages": MSG, "stake": 500})
    assert resp.status_code == 402
    assert ledger.entries == []


def test_cancel_refunds_a_job_that_never_ran(client):
    http, _, ledger = client
    job_id = http.post("/v1/jobs", json={"messages": MSG, "stake": 30}).json()["job_id"]
    resp = http.delete(f"/v1/jobs/{job_id}")
    assert resp.json() == {"job_id": job_id, "cancelled": True, "refunded": 30.0}
    assert ledger._balance == 100


def test_cancelling_twice_conflicts(client):
    http, _, _ = client
    job_id = http.post("/v1/jobs", json={"messages": MSG}).json()["job_id"]
    assert http.delete(f"/v1/jobs/{job_id}").status_code == 200
    assert http.delete(f"/v1/jobs/{job_id}").status_code == 409


def test_missing_job_is_404(client):
    http, _, _ = client
    assert http.get("/v1/jobs/q_nope").status_code == 404
    assert http.delete("/v1/jobs/q_nope").status_code == 404


def test_list_reports_counts_and_why_nothing_is_running(client):
    http, _, _ = client
    http.post("/v1/jobs", json={"messages": MSG})
    body = http.get("/v1/jobs").json()
    assert body["counts"]["queued"] == 1
    # No scheduler wired in this harness — the answer must still be a sentence,
    # because "nothing is happening" with no reason is the one thing the queue
    # must never render.
    assert body["scheduler"]["reason"] == "not running"


def test_list_rejects_an_unknown_state(client):
    http, _, _ = client
    assert http.get("/v1/jobs", params={"state": "sideways"}).status_code == 400


async def test_get_reports_the_waiting_reason(client):
    http, queue, _ = client
    job_id = http.post("/v1/jobs", json={"messages": MSG}).json()["job_id"]
    await queue.note_waiting(job_id, "No frontier-tier model is reachable.")
    body = http.get(f"/v1/jobs/{job_id}").json()
    assert body["waiting_reason"] == "No frontier-tier model is reachable."
    assert body["state"] == JobState.QUEUED.value


def test_queue_disabled_says_so(tmp_path):
    """503 with a reason, not 404 — "not enabled here" and "no such endpoint"
    send a client down completely different paths."""
    app = FastAPI()
    app.include_router(router, prefix="/v1/jobs")

    class NoQueue:
        peer_id = "n"
        job_queue = None
        job_scheduler = None
        ledger = None

    app.state.node = NoQueue()
    resp = TestClient(app).post("/v1/jobs", json={"messages": MSG})
    assert resp.status_code == 503
    assert resp.json()["error"]["type"] == "queue_disabled"
