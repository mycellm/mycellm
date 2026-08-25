"""Streaming `mycellm/swarm`.

`/v1/models` advertises `mycellm/swarm`, and most clients stream by default —
but the swarm branch used to sit *after* the `body.stream` check, so a
streaming swarm request fell through to ordinary model resolution and answered
"[mycellm] No model available." Advertised and unreachable, which is the exact
shape 0.8 exists to remove.

The shape of a swarm decides what can stream: proposers run in parallel and
their answers are an input to synthesis, so only synthesis produces user-facing
tokens. These tests fix that contract — progress carries no content, content is
only ever the answer, and every degradation path still ends the stream with
something honest.
"""

import json

from fastapi import FastAPI
from starlette.testclient import TestClient

from mycellm.api.openai import router
from mycellm.execution.coordinator import ExecutionCoordinator
from mycellm.execution.models import (
    ExecutionPlan,
    Job,
    Role,
    Strategy,
    Target,
    WorkUnit,
    WorkUnitResult,
)

SWARM = "mycellm/swarm"


def target(model="m", kind="local", peer=""):
    return Target(model=model, kind=kind, peer_id=peer, params_b=9.0)


def swarm_plan(n_proposers=2, synthesizer=True, budget=0):
    units = [
        WorkUnit(unit_id=f"p{i}", role=Role.PROPOSER, target=target(f"m{i}"),
                 messages=[{"role": "user", "content": "hi"}])
        for i in range(n_proposers)
    ]
    if synthesizer:
        units.append(WorkUnit(unit_id="s", role=Role.SYNTHESIZER, target=target("synth"),
                              messages=[{"role": "user", "content": "hi"}],
                              depends_on=tuple(u.unit_id for u in units)))
    return ExecutionPlan(job_id="j1", strategy=Strategy.SWARM, units=units,
                         token_budget=budget)


def job(**kw):
    return Job(job_id="j1", model=SWARM,
               messages=[{"role": "user", "content": "hi"}], **kw)


def runner_for(texts: dict, fail: set = frozenset()):
    """Runner returning canned text per unit id."""
    async def run(unit):
        if unit.unit_id in fail:
            return WorkUnitResult(unit.unit_id, unit.role, unit.target,
                                  error="backend exploded")
        return WorkUnitResult(unit.unit_id, unit.role, unit.target,
                              text=texts.get(unit.unit_id, "answer"),
                              completion_tokens=10)
    return run


def stream_runner_for(fragments: list, raises: Exception | None = None,
                      raise_after: int = 0):
    async def run(unit):
        for i, fragment in enumerate(fragments):
            if raises is not None and i == raise_after:
                raise raises
            yield fragment
        if raises is not None and raise_after >= len(fragments):
            raise raises
    return run


async def collect(coordinator, plan, j=None):
    return [e async for e in coordinator.execute_stream(j or job(), plan)]


# ── coordinator: the streaming contract ─────────────────────────────────

async def test_synthesis_streams_fragment_by_fragment():
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "b"}),
        stream_runner=stream_runner_for(["Hel", "lo ", "world"]),
    )
    events = await collect(coordinator, swarm_plan())
    text = [e["text"] for e in events if e["type"] == "text"]
    assert text == ["Hel", "lo ", "world"], "synthesis must arrive incrementally"
    assert events[-1]["type"] == "done"


async def test_progress_never_carries_user_facing_text():
    """The load-bearing distinction.

    A client that concatenates content must get the answer and nothing else. If
    progress ever leaked into text, every naive client would render the
    execution plan into the reply.
    """
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "b"}),
        stream_runner=stream_runner_for(["answer"]),
    )
    events = await collect(coordinator, swarm_plan())
    progress = [e for e in events if e["type"] == "progress"]
    assert progress, "the proposer phase must report progress"
    assert all("text" not in e for e in progress)
    assert "".join(e["text"] for e in events if e["type"] == "text") == "answer"


async def test_progress_names_the_phases_in_order():
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "b"}),
        stream_runner=stream_runner_for(["x"]),
    )
    events = await collect(coordinator, swarm_plan())
    phases = [e["phase"] for e in events if e["type"] == "progress"]
    assert phases == ["proposing", "synthesizing"]


async def test_done_carries_the_plan_and_attribution():
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "b"}),
        stream_runner=stream_runner_for(["x"]),
    )
    events = await collect(coordinator, swarm_plan())
    meta = events[-1]["meta"]
    assert meta["strategy"] == "swarm"
    assert meta["units_ok"] == 2
    assert "synthesized_by" in meta, "a caller paying for N proposers sees what ran"


# ── degradation: every path must still produce an answer ────────────────

async def test_one_surviving_proposer_is_returned_directly():
    """One survivor is not a swarm — do not pay for a synthesis with nothing
    to merge, and say the job degraded."""
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "only answer"}, fail={"p1"}),
        stream_runner=stream_runner_for(["should not run"]),
    )
    events = await collect(coordinator, swarm_plan())
    assert "".join(e["text"] for e in events if e["type"] == "text") == "only answer"
    assert events[-1]["meta"]["degraded"] is True


async def test_every_proposer_failing_is_an_error_not_an_empty_stream():
    coordinator = ExecutionCoordinator(
        runner_for({}, fail={"p0", "p1"}),
        stream_runner=stream_runner_for(["x"]),
    )
    events = await collect(coordinator, swarm_plan())
    assert events[-1]["type"] == "error"
    assert "proposer" in events[-1]["error"]


async def test_synthesis_failure_falls_back_to_the_best_proposal():
    """Do not throw away work that succeeded and was already paid for."""
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "short", "p1": "a much longer answer"}),
        stream_runner=stream_runner_for([], raises=RuntimeError("synth died")),
    )
    events = await collect(coordinator, swarm_plan())
    assert "".join(e["text"] for e in events if e["type"] == "text") == "a much longer answer"
    assert "synthesis failed" in events[-1]["meta"]["degradation"]


async def test_no_fallback_once_text_has_reached_the_client():
    """⚠️ The rule that keeps a broken stream from producing two answers.

    Falling back after partial output would append a whole proposal to a
    half-finished synthesis. Past the first fragment the honest move is to stop
    and record that the stream broke.
    """
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "proposal one", "p1": "proposal two"}),
        stream_runner=stream_runner_for(["par", "tial"],
                                        raises=RuntimeError("died"), raise_after=2),
    )
    events = await collect(coordinator, swarm_plan())
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert text == "partial"
    assert "proposal" not in text, "a second answer must never be appended"
    assert "partial output" in events[-1]["meta"]["degradation"]


async def test_empty_synthesis_falls_back():
    """A stream yielding nothing is indistinguishable from a clean empty
    answer, and neither is worth handing to someone who paid for proposers."""
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "bb"}),
        stream_runner=stream_runner_for([]),
    )
    events = await collect(coordinator, swarm_plan())
    assert "".join(e["text"] for e in events if e["type"] == "text") == "bb"
    assert "no output" in events[-1]["meta"]["degradation"]


async def test_without_a_stream_runner_synthesis_still_works():
    """Degrading to one chunk is correct — the answer is right, it just
    arrives at once, which is what happened before streaming existed."""
    coordinator = ExecutionCoordinator(runner_for({"p0": "a", "p1": "b", "s": "merged"}))
    events = await collect(coordinator, swarm_plan())
    assert "".join(e["text"] for e in events if e["type"] == "text") == "merged"


async def test_budget_exhaustion_skips_synthesis():
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "longer"}),
        stream_runner=stream_runner_for(["should not run"]),
    )
    events = await collect(coordinator, swarm_plan(budget=5))
    assert "should not run" not in "".join(
        e["text"] for e in events if e["type"] == "text")
    assert events[-1]["meta"]["degraded"] is True


async def test_no_synthesizer_returns_the_longest_proposal():
    coordinator = ExecutionCoordinator(
        runner_for({"p0": "short", "p1": "the longer one"}),
        stream_runner=stream_runner_for(["x"]),
    )
    events = await collect(coordinator, swarm_plan(synthesizer=False))
    assert "".join(e["text"] for e in events if e["type"] == "text") == "the longer one"


async def test_empty_plan_is_an_error_with_reasons():
    coordinator = ExecutionCoordinator(runner_for({}))
    plan = ExecutionPlan(job_id="j1", strategy=Strategy.SWARM, units=[],
                         reasons=["every target refused by egress policy"])
    events = await collect(coordinator, plan)
    assert events[0]["type"] == "error"
    assert "egress" in events[0]["error"]


async def test_budget_is_enforced_identically_on_both_paths():
    """The reason the proposer fan-out is shared code.

    If these ever diverge, a streaming swarm spends past a ceiling the
    non-streaming one enforces — and nobody would notice until a bill.
    """
    plan_a, plan_b = swarm_plan(budget=5), swarm_plan(budget=5)
    streamed = ExecutionCoordinator(runner_for({"p0": "a", "p1": "b"}))
    events = await collect(streamed, plan_a)
    blocking = await ExecutionCoordinator(
        runner_for({"p0": "a", "p1": "b"})).execute(job(), plan_b)
    assert events[-1]["meta"]["completion_tokens_spent"] == \
        blocking["meta"]["completion_tokens_spent"]


# ── API: the SSE wire shape ─────────────────────────────────────────────

class FakeNode:
    """Only what `_stream_swarm` touches."""

    def __init__(self, events):
        self._events = events
        self.activity = _NullActivity()

    async def execute_job_stream(self, job, override_privacy=False, targets=None):
        for event in self._events:
            yield event


class _NullActivity:
    def record(self, *a, **kw):
        pass


def sse(node, body=None):
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.state.node = node
    payload = {"model": SWARM, "messages": [{"role": "user", "content": "hi"}],
               "stream": True}
    payload.update(body or {})
    resp = TestClient(app).post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    return [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"]


def test_sse_content_is_only_the_answer():
    chunks = sse(FakeNode([
        {"type": "progress", "phase": "proposing", "planned": 2},
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world"},
        {"type": "done", "meta": {"strategy": "swarm", "units_ok": 2}},
    ]))
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "Hello world"


def test_sse_progress_chunks_have_empty_content_and_a_mycellm_block():
    chunks = sse(FakeNode([
        {"type": "progress", "phase": "proposing", "planned": 3},
        {"type": "text", "text": "x"},
        {"type": "done", "meta": {}},
    ]))
    progress = chunks[0]
    assert progress["choices"][0]["delta"] == {}
    assert progress["mycellm"]["phase"] == "proposing"
    assert progress["mycellm"]["planned"] == 3


def test_sse_sends_the_role_once_on_the_first_real_content():
    chunks = sse(FakeNode([
        {"type": "progress", "phase": "proposing"},
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
        {"type": "done", "meta": {}},
    ]))
    roles = [c for c in chunks if "role" in c["choices"][0]["delta"]]
    assert len(roles) == 1
    assert roles[0]["choices"][0]["delta"]["content"] == "a"


def test_sse_reports_a_refusal_as_content():
    """⚠️ An SSE stream cannot change its status code — headers are long gone.

    A refusal has to arrive as content or the client sees a clean empty stream
    and reports success. The plan rides along so a blocked egress stays visible.
    """
    chunks = sse(FakeNode([
        {"type": "error", "error": "every target refused by egress policy",
         "meta": {"rejected": [{"target": "peer:abc", "reason": "egress blocked"}]}},
    ]))
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert "egress policy" in content
    assert chunks[-1]["mycellm"]["rejected"][0]["reason"] == "egress blocked"


def test_sse_never_ends_silently():
    """A coordinator that yields nothing must not look like a model with
    nothing to say. Those are opposite facts."""
    chunks = sse(FakeNode([]))
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert "no result" in content


def test_sse_final_chunk_finishes_the_stream():
    chunks = sse(FakeNode([
        {"type": "text", "text": "hi"},
        {"type": "done", "meta": {"strategy": "swarm"}},
    ]))
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["mycellm"]["strategy"] == "swarm"


def test_sse_does_not_double_finish_after_an_error():
    chunks = sse(FakeNode([
        {"type": "error", "error": "boom", "meta": {}},
        {"type": "done", "meta": {}},
    ]))
    finishes = [c for c in chunks if c["choices"][0]["finish_reason"] == "stop"]
    assert len(finishes) == 1
