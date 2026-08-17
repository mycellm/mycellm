"""Planner and coordinator: swarm formation, degradation, and failure injection.

Covers the §20.4 failure-injection list that can be exercised without a fleet:
proposer disappears mid-job, synthesizer fails, duplicate/late results, budget
exhausted, everything refused.

The coordinator takes its runner by injection, so all of this is testable with
no inference, no network and no model — which is the point of separating the
plan from the execution.
"""

import asyncio

import pytest

from mycellm.execution import (
    DEFAULT_FANOUT,
    ExecutionCoordinator,
    ExecutionPlanner,
    Job,
    Role,
    Strategy,
    Target,
    WorkUnitResult,
)

SWARM = "mycellm/swarm"


def job(model=SWARM, content="hello", **kw):
    return Job(job_id="j1", model=model,
               messages=[{"role": "user", "content": content}], **kw)


def peer(model, name="p", tok_s=10.0, roles=(), params_b=7.0):
    return Target(model=model, kind="peer", peer_id=name * 32, tok_s=tok_s,
                  roles=roles, params_b=params_b)


def local(model, roles=(), params_b=7.0, tok_s=0.0):
    return Target(model=model, kind="local", roles=roles, params_b=params_b,
                  tok_s=tok_s)


# ── Planning ────────────────────────────────────────────────────────────

class TestSwarmFormation:
    def test_swarm_model_selects_the_swarm_strategy(self):
        plan = ExecutionPlanner().plan(job(), [local("a"), peer("b"), peer("c", "q")])
        assert plan.strategy is Strategy.SWARM
        assert len(plan.proposers) == 3
        assert plan.synthesizer is not None

    def test_an_ordinary_model_stays_direct(self):
        plan = ExecutionPlanner().plan(job(model="llama3"), [local("llama3")])
        assert plan.strategy is Strategy.DIRECT
        assert len(plan.units) == 1

    def test_fanout_is_capped_and_floored(self):
        cands = [peer(f"m{i}", chr(97 + i)) for i in range(12)]
        assert len(ExecutionPlanner().plan(job(fanout=99), cands).proposers) <= 8
        assert len(ExecutionPlanner().plan(job(fanout=1), cands).proposers) >= 2

    def test_default_fanout_when_unspecified(self):
        cands = [peer(f"m{i}", chr(97 + i)) for i in range(6)]
        assert len(ExecutionPlanner().plan(job(), cands).proposers) == DEFAULT_FANOUT

    def test_distinct_models_are_preferred_over_duplicates(self):
        # Diversity is the point; breadth before depth.
        cands = [peer("a", "1"), peer("a", "2"), peer("a", "3"), peer("b", "4")]
        models = [u.target.model for u in
                  ExecutionPlanner().plan(job(fanout=2), cands).proposers]
        assert set(models) == {"a", "b"}

    def test_proposers_get_spread_temperatures(self):
        plan = ExecutionPlanner().plan(job(), [peer("a"), peer("b", "q"), peer("c", "r")])
        temps = [u.temperature for u in plan.proposers]
        assert len(set(temps)) == len(temps), \
            "identical temperatures make a same-model swarm N copies of one answer"

    def test_synthesis_is_deterministic(self):
        plan = ExecutionPlanner().plan(job(), [peer("a"), peer("b", "q")])
        assert plan.synthesizer.temperature < 0.5

    def test_synthesis_stays_local_when_every_proposer_is_local(self):
        # Nothing has left the machine; keep it that way.
        plan = ExecutionPlanner().plan(job(), [local("a"), local("b")])
        assert plan.synthesizer.target.is_remote is False

    def test_unmeasured_throughput_does_not_decide_synthesis(self):
        """tok_s is 0 on most real targets, so it cannot be the ranking key."""
        plan = ExecutionPlanner().plan(
            job(), [local("small", params_b=0.5, tok_s=0.0),
                    peer("large", "q", params_b=70.0, tok_s=0.0)])
        assert plan.synthesizer.target.model == "large"

    def test_synthesis_does_not_insist_on_a_weak_local_model(self):
        """⚠️ REGRESSION from a live run.

        "Always prefer local" looked like the obvious privacy default. In a real
        two-model run a 0.5B local model and a 35B remote one both proposed; the
        35B was correct, the local model produced gibberish, and because it was
        also chosen to synthesise, the whole swarm returned gibberish while
        reporting success.

        Once ANY proposer is remote the prompt has already left the machine, so
        local synthesis buys almost no privacy and can cost all the quality.
        """
        # BOTH report tok_s 0.0 — the common case, since nothing has measured
        # them. The first fix ranked by tok_s and fell back to "prefer local",
        # which picked the 0.5B again in a live run. Parameter count is the
        # signal that actually separates them.
        weak_local = local("qwen2.5-0.5b", params_b=0.5, tok_s=0.0)
        strong_peer = peer("big-35b", "q", tok_s=0.0, params_b=35.0)
        plan = ExecutionPlanner().plan(job(), [weak_local, strong_peer])
        assert plan.strategy is Strategy.SWARM
        assert plan.synthesizer.target.model == "big-35b", \
            "a remote proposer already saw the prompt; pick the stronger synthesiser"

    def test_synthesis_depends_on_every_proposer(self):
        plan = ExecutionPlanner().plan(job(), [peer("a"), peer("b", "q"), peer("c", "r")])
        assert set(plan.synthesizer.depends_on) == {u.unit_id for u in plan.proposers}


class TestHonestDegradation:
    def test_one_target_degrades_to_direct_and_says_so(self):
        plan = ExecutionPlanner().plan(job(), [local("only")])
        assert plan.strategy is Strategy.DIRECT
        assert any("degraded to direct" in r for r in plan.reasons)

    def test_single_model_swarm_is_labelled_as_self_consistency(self):
        """Not a heterogeneous swarm — and the plan must not imply it is.

        This arm measures best-of-N sampling. Conflating it with heterogeneity
        is how an unearned quality claim gets made.
        """
        plan = ExecutionPlanner().plan(job(), [peer("a", "1"), peer("a", "2")])
        assert plan.strategy is Strategy.SWARM
        assert any("self-consistency" in r for r in plan.reasons), plan.reasons

    def test_no_candidates_produces_no_units_and_a_reason(self):
        plan = ExecutionPlanner().plan(job(), [])
        assert plan.units == []
        assert plan.reasons


class TestEgressInPlanning:
    AWS = "AKIAIOSFODNN7EXAMPLE"

    def test_blocked_targets_are_rejected_before_dispatch(self):
        plan = ExecutionPlanner().plan(
            job(content=f"key {self.AWS}"),
            [local("safe"), peer("a"), peer("b", "q")])
        assert [u.target.kind for u in plan.units] == ["local"]
        assert len(plan.rejected) == 2
        assert all("sensitive" in r for _, r in plan.rejected)

    def test_everything_blocked_yields_no_units(self):
        plan = ExecutionPlanner().plan(
            job(content=f"key {self.AWS}"), [peer("a"), peer("b", "q")])
        assert plan.units == []
        assert len(plan.rejected) == 2
        assert any("refused by egress policy" in r for r in plan.reasons)

    def test_trust_local_keeps_a_swarm_off_the_network(self):
        plan = ExecutionPlanner().plan(
            job(trust="local"), [local("a"), peer("b"), peer("c", "q")])
        assert all(not u.target.is_remote for u in plan.units)


class TestRoleFiltering:
    """The reader for `ModelCapability.execution_roles`."""

    def test_undeclared_roles_can_propose(self):
        # Every 0.7 chat model can generate an answer; that IS proposing.
        plan = ExecutionPlanner().plan(job(), [peer("a"), peer("b", "q")])
        assert len(plan.proposers) == 2

    def test_a_critic_only_model_is_not_used_as_a_proposer(self):
        plan = ExecutionPlanner().plan(
            job(), [peer("a"), peer("b", "q"), peer("c", "r", roles=("critic",))])
        models = {u.target.model for u in plan.proposers}
        assert "c" not in models
        assert any("do not declare" in r for r in plan.reasons)

    def test_an_explicit_proposer_is_used(self):
        plan = ExecutionPlanner().plan(
            job(), [peer("a", roles=("proposer",)), peer("b", "q", roles=("proposer",))])
        assert len(plan.proposers) == 2


# ── Execution ───────────────────────────────────────────────────────────

def runner_for(behaviour):
    """behaviour: unit -> str | Exception | None"""
    async def run(unit):
        out = behaviour(unit)
        if isinstance(out, BaseException):
            raise out
        if out is None:
            return None
        return WorkUnitResult(unit.unit_id, unit.role, unit.target,
                              text=out, completion_tokens=len(out.split()))
    return run


@pytest.mark.asyncio
class TestCoordinator:
    async def _plan(self, cands, **kw):
        j = job(**kw)
        return j, ExecutionPlanner().plan(j, cands)

    async def test_happy_path_synthesises(self):
        j, plan = await self._plan([peer("a"), peer("b", "q"), local("c")])
        coord = ExecutionCoordinator(runner_for(
            lambda u: "SYNTH" if u.role is Role.SYNTHESIZER else f"ans-{u.target.model}"))
        out = await coord.execute(j, plan)
        assert out["text"] == "SYNTH"
        assert out["meta"]["units_ok"] == len(plan.proposers)
        assert "synthesized_by" in out["meta"]
        assert not out["meta"].get("degraded")

    async def test_a_proposer_dying_does_not_fail_the_job(self):
        j, plan = await self._plan([peer("a"), peer("b", "q"), peer("c", "r")])
        dead = plan.proposers[0].target.model

        def behave(u):
            if u.role is Role.SYNTHESIZER:
                return "SYNTH"
            if u.target.model == dead:
                return RuntimeError("peer vanished")
            return f"ans-{u.target.model}"

        out = await ExecutionCoordinator(runner_for(behave)).execute(j, plan)
        assert out["text"] == "SYNTH"
        assert out["meta"]["units_failed"] == 1
        assert out["meta"]["degraded"] is True
        assert out["meta"]["failures"][0]["error"]

    async def test_all_proposers_failing_is_an_error_not_a_silent_empty(self):
        j, plan = await self._plan([peer("a"), peer("b", "q")])
        out = await ExecutionCoordinator(
            runner_for(lambda u: RuntimeError("boom"))).execute(j, plan)
        assert out["text"] == ""
        assert out["error"]
        assert out["meta"]["units_ok"] == 0

    async def test_synthesis_failure_returns_the_best_proposal(self):
        """Work that succeeded and was paid for must not be thrown away."""
        j, plan = await self._plan([peer("a"), peer("b", "q")])

        def behave(u):
            if u.role is Role.SYNTHESIZER:
                return RuntimeError("synthesizer died")
            return "a short one" if u.target.model == "a" else "a considerably longer answer here"

        out = await ExecutionCoordinator(runner_for(behave)).execute(j, plan)
        assert out["text"] == "a considerably longer answer here"
        assert out["meta"]["degraded"] is True
        assert "synthesis failed" in out["meta"]["degradation"]

    async def test_one_survivor_is_returned_without_paying_for_synthesis(self):
        j, plan = await self._plan([peer("a"), peer("b", "q"), peer("c", "r")])
        keep = plan.proposers[0].target.model
        synth_calls = []

        def behave(u):
            if u.role is Role.SYNTHESIZER:
                synth_calls.append(1)
                return "SYNTH"
            return "kept" if u.target.model == keep else RuntimeError("down")

        out = await ExecutionCoordinator(runner_for(behave)).execute(j, plan)
        assert out["text"] == "kept"
        assert synth_calls == [], "synthesis has nothing to merge from one answer"
        assert out["meta"]["degraded"] is True

    async def test_runner_returning_nothing_is_a_failure_not_a_crash(self):
        j, plan = await self._plan([peer("a"), peer("b", "q")])
        out = await ExecutionCoordinator(runner_for(lambda u: None)).execute(j, plan)
        assert out["error"]

    async def test_empty_plan_reports_the_planner_reason(self):
        j, plan = await self._plan([])
        out = await ExecutionCoordinator(runner_for(lambda u: "x")).execute(j, plan)
        assert out["text"] == ""
        assert out["error"]

    async def test_token_budget_cancels_remaining_proposers(self):
        j, plan = await self._plan(
            [peer(f"m{i}", chr(97 + i)) for i in range(4)], fanout=4, token_budget=3)

        async def slow(unit):
            # Later units are slower, so the budget trips before they land.
            idx = int(unit.target.model[1:]) if unit.target.model[1:].isdigit() else 0
            await asyncio.sleep(0.01 * idx)
            return WorkUnitResult(unit.unit_id, unit.role, unit.target,
                                  text="word word", completion_tokens=2)

        out = await ExecutionCoordinator(slow).execute(j, plan)
        assert out["meta"]["completion_tokens_spent"] >= 3
        assert out["meta"].get("degraded") is True
        assert out["meta"].get("cancelled_for_budget", 0) >= 1

    async def test_no_task_outlives_the_job(self):
        """A cancelled proposer must not still be running after execute returns."""
        j, plan = await self._plan([peer(f"m{i}", chr(97 + i)) for i in range(4)],
                                   fanout=4, token_budget=1)
        started, finished = [], []

        async def tracked(unit):
            started.append(unit.unit_id)
            try:
                await asyncio.sleep(0.05)
                return WorkUnitResult(unit.unit_id, unit.role, unit.target,
                                      text="w", completion_tokens=1)
            finally:
                finished.append(unit.unit_id)

        await ExecutionCoordinator(tracked).execute(j, plan)
        await asyncio.sleep(0.1)
        assert set(started) == set(finished), "a cancelled unit kept running"

    async def test_plan_is_reported_in_meta(self):
        j, plan = await self._plan([peer("a"), peer("b", "q")])
        out = await ExecutionCoordinator(runner_for(lambda u: "x")).execute(j, plan)
        assert out["meta"]["strategy"] == "swarm"
        assert out["meta"]["reasons"]
        assert "elapsed_s" in out["meta"]

    async def test_synthesis_prompt_does_not_name_the_models(self):
        """Naming them invites deference to whichever sounds authoritative."""
        j, plan = await self._plan([peer("gpt-4-turbo"), peer("tinyllama", "q")])
        seen = {}

        def behave(u):
            if u.role is Role.SYNTHESIZER:
                seen["prompt"] = " ".join(m["content"] for m in u.messages)
                return "SYNTH"
            return f"answer from {u.target.model}"

        await ExecutionCoordinator(runner_for(behave)).execute(j, plan)
        # The proposal TEXT may mention anything; the scaffolding must not
        # attribute answers to named models.
        assert "[Answer 1]" in seen["prompt"]
        assert "peer:" not in seen["prompt"]


@pytest.mark.asyncio
async def test_direct_strategy_still_works_through_the_coordinator():
    j = job(model="llama3")
    plan = ExecutionPlanner().plan(j, [local("llama3")])

    out = await ExecutionCoordinator(runner_for(lambda u: "hello")).execute(j, plan)
    assert out["text"] == "hello"
    assert out["meta"]["strategy"] == "direct"
    assert out["meta"]["units_ok"] == 1
