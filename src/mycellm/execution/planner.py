"""ExecutionPlanner — decides how a job runs, without running it.

Deliberately a pure function over (Job, candidate targets): no network, no
clock, no I/O. That is what makes "why did this request go there" answerable in
a unit test instead of only in production. The coordinator does the executing.

Ordering note: the planner is built alongside the strategies rather than after
a procedural router is adapted, because adapting `ChainBuilder` to carry group
targets and *then* re-layering a planner on top means writing the routing
pipeline twice — the sequencing trap the review panel flagged.
"""

from __future__ import annotations

import logging

from mycellm.execution.models import (
    ExecutionPlan,
    Job,
    Role,
    Strategy,
    Target,
    WorkUnit,
)
from mycellm.execution.policy import EgressPolicy

logger = logging.getLogger("mycellm.execution")

#: Synthetic model names that select a strategy rather than a model.
SWARM_MODEL = "mycellm/swarm"

#: Proposers to use when the caller does not say. Three is the smallest number
#: where a synthesis pass can see disagreement; two only ever shows a tie.
DEFAULT_FANOUT = 3
MAX_FANOUT = 8


def is_swarm_request(model: str) -> bool:
    return (model or "").strip().lower() == SWARM_MODEL


class ExecutionPlanner:
    """Turns a Job plus candidates into an ExecutionPlan."""

    def plan(
        self,
        job: Job,
        candidates: list[Target],
        override_privacy: bool = False,
    ) -> ExecutionPlan:
        policy = EgressPolicy(
            prompt=job.prompt_text(),
            requested_trust=job.trust,
            own_networks=job.network_ids,
            override=override_privacy,
        )

        strategy = Strategy.SWARM if is_swarm_request(job.model) else Strategy.DIRECT
        plan = ExecutionPlan(
            job_id=job.job_id, strategy=strategy, token_budget=job.token_budget
        )

        # ── Eligibility. Egress policy is applied HERE, before anything is
        # dispatched, so a blocked prompt is never partially sent. Filtering
        # after fan-out would mean the first proposer already had it.
        eligible: list[Target] = []
        for t in candidates:
            decision = policy.decide(t)
            if decision.allowed:
                eligible.append(t)
                if "warning" in decision.reason:
                    plan.reasons.append(f"{t}: {decision.reason}")
            else:
                plan.rejected.append((str(t), decision.reason))

        if not eligible:
            plan.reasons.append(
                "no eligible target" if not candidates
                else "every candidate refused by egress policy"
            )
            return plan

        # Prefer local, then faster. Local first is a privacy default as much as
        # a latency one: if the work can be done without leaving the machine,
        # it should be.
        eligible.sort(key=lambda t: (t.is_remote, -t.tok_s))

        if strategy is Strategy.SWARM:
            return self._plan_swarm(job, plan, eligible)
        return self._plan_direct(job, plan, eligible)

    @staticmethod
    def _eligible_for(targets: list[Target], role: Role) -> list[Target]:
        """Targets whose model declares it can perform `role`.

        THE READER for `ModelCapability.execution_roles`. A model that declares
        roles is opting in explicitly; one that declares none keeps 0.7
        semantics (direct only) and is still usable as a proposer, because
        every chat model can generate an answer — that is what a proposer does.
        Declaring `["critic"]` and nothing else, by contrast, means do not ask
        it to answer.
        """
        out = []
        for t in targets:
            if not t.roles:
                # Undeclared: usable for the generative roles, which is what
                # every 0.7 chat model already does.
                if role in (Role.DIRECT, Role.PROPOSER, Role.SYNTHESIZER):
                    out.append(t)
                continue
            if t.can(role.value):
                out.append(t)
        return out

    # ── Strategies ──────────────────────────────────────────────────────

    def _plan_direct(
        self, job: Job, plan: ExecutionPlan, eligible: list[Target]
    ) -> ExecutionPlan:
        target = eligible[0]
        plan.units.append(
            WorkUnit(
                unit_id=f"{job.job_id}-u0",
                role=Role.DIRECT,
                target=target,
                messages=job.messages,
                temperature=job.temperature,
                max_tokens=job.max_tokens,
            )
        )
        plan.reasons.append(f"direct → {target}")
        if not target.is_remote:
            plan.reasons.append("local model available")
        if len(eligible) > 1:
            plan.reasons.append(f"{len(eligible) - 1} fallback target(s) available")
        return plan

    def _plan_swarm(
        self, job: Job, plan: ExecutionPlan, eligible: list[Target]
    ) -> ExecutionPlan:
        want = job.fanout or DEFAULT_FANOUT
        want = max(2, min(want, MAX_FANOUT))

        can_propose = self._eligible_for(eligible, Role.PROPOSER)
        if len(can_propose) < len(eligible):
            plan.reasons.append(
                f"{len(eligible) - len(can_propose)} target(s) do not declare "
                f"the proposer role"
            )
        proposers = self._pick_proposers(can_propose, want)
        if len(proposers) < 2:
            # One target is not a swarm. Degrade to direct and SAY SO, rather
            # than pretending N proposers ran — a swarm that silently becomes a
            # single call while still reporting "swarm" is unfalsifiable.
            plan.strategy = Strategy.DIRECT
            plan.reasons.append(
                f"swarm requested but only {len(proposers)} eligible proposer — "
                f"degraded to direct"
            )
            return self._plan_direct(job, plan, eligible)

        for i, t in enumerate(proposers):
            plan.units.append(
                WorkUnit(
                    unit_id=f"{job.job_id}-p{i}",
                    role=Role.PROPOSER,
                    target=t,
                    messages=job.messages,
                    # Spread temperature so proposers explore differently. With
                    # identical settings a same-model swarm is N copies of one
                    # answer, which is redundancy, not diversity.
                    temperature=min(1.0, job.temperature + 0.15 * i),
                    max_tokens=job.max_tokens,
                )
            )

        synth_pool = self._eligible_for(eligible, Role.SYNTHESIZER) or eligible
        synth = self._pick_synthesizer(synth_pool, proposers)
        plan.units.append(
            WorkUnit(
                unit_id=f"{job.job_id}-syn",
                role=Role.SYNTHESIZER,
                target=synth,
                messages=[],  # filled by the coordinator from proposer output
                temperature=0.2,  # synthesis wants determinism, not exploration
                max_tokens=job.max_tokens,
                depends_on=tuple(u.unit_id for u in plan.proposers),
            )
        )

        distinct = len({t.model for t in proposers})
        plan.reasons.append(
            f"swarm: {len(proposers)} proposers across {distinct} distinct "
            f"model(s) → synthesis on {synth}"
        )
        if distinct == 1:
            # Say it plainly. This arm measures best-of-N sampling, not
            # heterogeneity, and conflating the two is how an unearned quality
            # claim gets made.
            plan.reasons.append(
                "all proposers share one model — this is self-consistency "
                "sampling, not a heterogeneous swarm"
            )
        return plan

    # ── Selection ───────────────────────────────────────────────────────

    @staticmethod
    def _pick_proposers(eligible: list[Target], want: int) -> list[Target]:
        """Prefer distinct models, then fill with duplicates if needed.

        Diversity is the point of a swarm, so one target per model comes first;
        only after every model is represented do we add a second unit on an
        already-used model.
        """
        by_model: dict[str, list[Target]] = {}
        for t in eligible:
            by_model.setdefault(t.model, []).append(t)

        picked: list[Target] = []
        # Round-robin across models so we get breadth before depth.
        while len(picked) < want:
            added = False
            for model in list(by_model):
                if not by_model[model]:
                    continue
                if len(picked) >= want:
                    break
                picked.append(by_model[model].pop(0))
                added = True
            if not added:
                break
        return picked

    @staticmethod
    def _pick_synthesizer(
        eligible: list[Target], proposers: list[Target]
    ) -> Target:
        """Pick who merges the proposals.

        Prefer a local target: synthesis sees every proposal at once, so it is
        the unit with the most context and the most to leak. Otherwise the
        fastest, since it is on the critical path — everything waits for it.
        """
        locals_ = [t for t in eligible if not t.is_remote]
        if locals_:
            return locals_[0]
        return max(eligible, key=lambda t: t.tok_s)
