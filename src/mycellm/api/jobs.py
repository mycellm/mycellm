"""Job queue API — submit work that waits for a device.

Mounted at `/v1/jobs`. Deliberately NOT part of the OpenAI-compatible surface:
an OpenAI client expects a completion in the response, and the entire point
here is that there may not be one for hours. Pretending otherwise — accepting a
chat request and blocking until an iPad wakes up — would break every client
that has a timeout, which is all of them.

The queue's contract is instead: submit, get an id and a position, poll or come
back later. Nothing streams, nothing blocks, and a job that cannot run says
why.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mycellm.execution.queue import TIER_RANK, DEFAULT_TTL_S, JobState

logger = logging.getLogger("mycellm.api.jobs")

router = APIRouter(tags=["jobs"])


class SubmitJobRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    #: '' or 'auto' = the scheduler resolves; else a model or `mycellm/swarm`.
    model: str = ""
    #: Quality floor. Only meaningful while `model` is empty — the same
    #: mutual exclusion the chat surfaces enforce, rejected here rather than
    #: silently dropped so a caller cannot believe it applied.
    min_tier: str = ""
    trust: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    token_budget: int = 0
    fanout: int = 0
    #: Credits staked for position. Staked, not spent — refunded if the job
    #: expires or is cancelled without running.
    stake: float = 0.0
    #: Seconds until the job gives up waiting. 0 = never.
    ttl_s: float = DEFAULT_TTL_S


def _queue(request: Request):
    return getattr(request.app.state.node, "job_queue", None)


def _scheduler(request: Request):
    return getattr(request.app.state.node, "job_scheduler", None)


def _unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": {
        "message": "The job queue is not enabled on this node.",
        "type": "queue_disabled",
        "hint": "Set MYCELLM_QUEUE_ENABLED=true (requires a database).",
    }})


@router.post("")
async def submit_job(request: Request, body: SubmitJobRequest):
    """Queue a job. Returns immediately with an id and a queue position."""
    queue = _queue(request)
    if queue is None:
        return _unavailable()

    if not body.messages:
        return JSONResponse(status_code=400, content={
            "error": {"message": "messages required"}})

    min_tier = (body.min_tier or "").strip().lower()
    if min_tier and min_tier not in TIER_RANK:
        return JSONResponse(status_code=400, content={"error": {
            "message": f"Unknown tier '{min_tier}'.",
            "hint": f"Valid tiers: {', '.join(TIER_RANK)}.",
        }})

    model = (body.model or "").strip()
    if model.lower() in ("auto", "default"):
        model = ""
    if model and min_tier:
        # ⚠️ REFUSED, NOT SILENTLY DROPPED. A tier floor constrains resolution;
        # once a model is named there is nothing left to resolve, so honouring
        # both is impossible and honouring one quietly is a lie about which.
        # 0.8's rule throughout: reject a request that cannot mean what it says.
        return JSONResponse(status_code=400, content={"error": {
            "message": "min_tier applies only when model is empty.",
            "hint": "Ask for a tier OR a model — a floor cannot constrain a "
                    "model you already chose.",
        }})

    stake = max(0.0, float(body.stake or 0.0))
    owner_id = getattr(request.app.state.node, "peer_id", "")

    # A stake must be affordable at submit time, or position could be bought
    # with credits that do not exist and the refund path would mint them.
    if stake > 0:
        ledger = getattr(request.app.state.node, "ledger", None)
        if ledger is not None:
            try:
                balance = await ledger.balance(owner_id)
            except Exception:  # noqa: BLE001 — ledger trouble must not block work
                balance = None
            if balance is not None and balance < stake:
                return JSONResponse(status_code=402, content={"error": {
                    "message": f"Stake of {stake} exceeds balance of {balance}.",
                    "type": "insufficient_credits",
                }})
            try:
                await ledger.debit(owner_id, stake, "queue_stake")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"stake debit failed: {e}")
                stake = 0.0

    job = await queue.submit(
        body.messages,
        owner_id=owner_id,
        model=model,
        min_tier=min_tier,
        trust=body.trust,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        token_budget=body.token_budget,
        fanout=body.fanout,
        stake=stake,
        ttl_s=body.ttl_s,
    )

    scheduler = _scheduler(request)
    if scheduler is not None:
        # Nudge rather than wait: a job submitted while a device is idle should
        # start now, not at the next poll, but the caller must never block on
        # the scheduler's decision.
        import asyncio

        asyncio.create_task(_nudge(scheduler))

    return {
        "job_id": job.job_id,
        "state": job.state.value,
        "position": await queue.position(job.job_id),
        "expires_at": job.expires_at,
        "stake": job.stake,
    }


async def _nudge(scheduler) -> None:
    try:
        await scheduler.tick()
    except Exception as e:  # noqa: BLE001 — the submit already succeeded
        logger.debug(f"scheduler nudge failed: {e}")


@router.get("")
async def list_jobs(request: Request, state: str = "", limit: int = 50):
    """List jobs, newest first, plus why the queue is or is not moving."""
    queue = _queue(request)
    if queue is None:
        return _unavailable()

    if state and state not in {s.value for s in JobState}:
        return JSONResponse(status_code=400, content={"error": {
            "message": f"Unknown state '{state}'.",
            "hint": f"Valid states: {', '.join(s.value for s in JobState)}.",
        }})

    jobs = await queue.list(state=state, limit=max(1, min(limit, 200)))
    scheduler = _scheduler(request)
    return {
        "jobs": [j.__dict__ | {"state": j.state.value} for j in jobs],
        "counts": await queue.counts(),
        # The device-level answer to "why is nothing running", which is
        # otherwise invisible: a fleet can be perfectly healthy and still
        # decline work because this laptop is on battery.
        "scheduler": {
            "running": len(getattr(scheduler, "_running", {})) if scheduler else 0,
            "reason": getattr(scheduler, "last_reason", "") if scheduler else "not running",
        },
    }


@router.get("/{job_id}")
async def get_job(request: Request, job_id: str):
    queue = _queue(request)
    if queue is None:
        return _unavailable()
    job = await queue.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": {"message": "not found"}})
    return job.__dict__ | {
        "state": job.state.value,
        "position": await queue.position(job_id),
    }


@router.delete("/{job_id}")
async def cancel_job(request: Request, job_id: str):
    """Cancel a job and refund its stake if it never ran."""
    queue = _queue(request)
    if queue is None:
        return _unavailable()

    job = await queue.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": {"message": "not found"}})
    if job.state.terminal:
        return JSONResponse(status_code=409, content={"error": {
            "message": f"Job already {job.state.value}.",
        }})

    cancelled = await queue.cancel(job_id)
    refunded = 0.0
    # Refund only work that never started. A cancelled RUNNING job consumed
    # real capacity on somebody's device, and refunding that would make
    # cancellation a way to get free inference.
    if cancelled and job.stake > 0 and job.state == JobState.QUEUED:
        ledger = getattr(request.app.state.node, "ledger", None)
        if ledger is not None:
            try:
                await ledger.credit(job.owner_id, job.stake, "queue_stake_refund:cancelled")
                refunded = job.stake
            except Exception as e:  # noqa: BLE001
                logger.warning(f"refund failed for {job_id}: {e}")

    return {"job_id": job_id, "cancelled": cancelled, "refunded": refunded}
