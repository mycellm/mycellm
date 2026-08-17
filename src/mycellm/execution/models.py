"""Core types for the adaptive inference fabric.

`Request → ExecutionPlan → execution graph`, replacing `Request → node`.

These dataclasses are the load-bearing part of 0.8: strategies, the planner and
the coordinator all speak in terms of them, so getting the shape right matters
more than any single strategy. They are deliberately plain data with no
behaviour that touches the network — the planner is a pure function over them,
which is what makes the routing decision testable without a fleet.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum


class Strategy(str, Enum):
    """How a job is executed.

    `DIRECT` is 0.7 behaviour: pick the best target and go. Everything else
    exists because adding peers should be able to improve the *work*, not just
    add capacity.
    """

    DIRECT = "direct"
    #: Same prompt to N targets; first good answer wins. Redundancy, not diversity.
    REPLICA = "replica"
    #: N proposers, then a synthesis pass. Diversity.
    SWARM = "swarm"


class Role(str, Enum):
    """What a target is being asked to do within a job."""

    DIRECT = "direct"
    PROPOSER = "proposer"
    SYNTHESIZER = "synthesizer"
    CRITIC = "critic"
    VERIFIER = "verifier"
    EMBED = "embed"


@dataclass(frozen=True)
class Target:
    """Somewhere a WorkUnit can run.

    ⚠️ THIS IS NOT A PEER. That distinction is the whole reason this type
    exists. A target may be this process's own backend, a QUIC peer, or a model
    fronted by an external serving group — and `PeerEntry` cannot represent the
    last one, because `peers_for_model` requires an open QUIC connection
    (`registry.py`), so an HTTP-reached gateway would be permanently invisible
    to routing. Planning against peers directly is what made groups
    unrepresentable.
    """

    model: str
    #: "local" | "peer" | "group"
    kind: str = "local"
    peer_id: str = ""
    serving_group_id: str = ""
    #: Networks this target is reachable on; empty = public/legacy.
    network_ids: tuple[str, ...] = ()
    #: Measured throughput, for ordering. 0 = unknown.
    tok_s: float = 0.0
    #: Roles this target's model declares (`ModelCapability.execution_roles`).
    #: Empty = 0.7 semantics: direct serving only.
    roles: tuple[str, ...] = ()

    def can(self, role: str) -> bool:
        """Mirrors `ModelCapability.can` so both sides answer identically."""
        if not self.roles:
            return role == "direct"
        return role in self.roles

    @property
    def is_remote(self) -> bool:
        """True if running here would send the prompt off this machine."""
        return self.kind != "local"

    def __str__(self) -> str:  # for plan reasons and logs
        if self.kind == "local":
            return f"local:{self.model}"
        if self.kind == "group":
            return f"group:{self.serving_group_id}:{self.model}"
        return f"peer:{self.peer_id[:8]}:{self.model}"


@dataclass
class WorkUnit:
    """One unit of work assigned to one target.

    A swarm job is several of these plus a synthesis unit. A direct job is
    exactly one, which is why direct routing is a strategy rather than a
    special case bypassing all of this.
    """

    unit_id: str
    role: Role
    target: Target
    messages: list[dict]
    temperature: float = 0.7
    max_tokens: int = 2048
    #: Units that must finish before this one can start (synthesis waits).
    depends_on: tuple[str, ...] = ()


@dataclass
class WorkUnitResult:
    unit_id: str
    role: Role
    target: Target
    text: str = ""
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error and not self.cancelled


@dataclass
class Job:
    """A request, before any decision about how to execute it has been made."""

    job_id: str
    model: str
    messages: list[dict]
    temperature: float = 0.7
    max_tokens: int = 2048
    #: Requester's networks; None = unrestricted (an un-federated node).
    network_ids: list[str] | None = None
    #: How much the caller is willing to spend in generated tokens across the
    #: whole job. 0 = no ceiling. Enforced by the coordinator, not advisory.
    token_budget: int = 0
    #: Trust ceiling the caller asked for: "local" | "trusted" | "any".
    trust: str = ""
    #: Number of proposers for a swarm. 0 = let the planner decide.
    fanout: int = 0

    @staticmethod
    def new_id(prefix: str = "job") -> str:
        # Content-free id: callers may run identical prompts and must still get
        # distinguishable jobs.
        return f"{prefix}_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]}"

    def prompt_text(self) -> str:
        """All message text, for privacy scanning.

        Scanning must see everything that would leave the machine — including
        system prompts, which is where credentials tend to be pasted.
        """
        parts = []
        for m in self.messages:
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                parts.extend(p.get("text", "") for p in c if isinstance(p, dict))
        return " ".join(p for p in parts if p)


@dataclass
class ExecutionPlan:
    """What the planner decided, and why.

    `reasons` is not decoration. A fabric that silently picks a strategy is
    unauditable — when a request is slow or expensive or went somewhere
    surprising, the reasons are the only way to find out why without a
    reproduction.
    """

    job_id: str
    strategy: Strategy
    units: list[WorkUnit] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    #: Targets considered and refused, with cause. Refusals are as important
    #: as selections — a blocked egress must be visible, not silent.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    token_budget: int = 0

    @property
    def proposers(self) -> list[WorkUnit]:
        return [u for u in self.units if u.role == Role.PROPOSER]

    @property
    def synthesizer(self) -> WorkUnit | None:
        for u in self.units:
            if u.role == Role.SYNTHESIZER:
                return u
        return None

    def to_dict(self) -> dict:
        """Debug shape for `/v1/node/plan` and response metadata."""
        return {
            "job_id": self.job_id,
            "strategy": self.strategy.value,
            "reasons": self.reasons,
            "rejected": [{"target": t, "reason": r} for t, r in self.rejected],
            "token_budget": self.token_budget,
            "units": [
                {
                    "unit_id": u.unit_id,
                    "role": u.role.value,
                    "target": str(u.target),
                    "model": u.target.model,
                    "depends_on": list(u.depends_on),
                }
                for u in self.units
            ],
        }
