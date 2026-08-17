"""Adaptive inference fabric: Request → ExecutionPlan → execution graph.

Public surface:

    Job, ExecutionPlan, WorkUnit, WorkUnitResult, Strategy, Role, Target
    ExecutionPlanner   — pure: (job, candidates) -> plan
    ExecutionCoordinator — executes a plan through an injected runner
    EgressPolicy       — hard privacy/trust gate applied during planning
"""

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
from mycellm.execution.planner import (
    DEFAULT_FANOUT,
    SWARM_MODEL,
    ExecutionPlanner,
    is_swarm_request,
)
from mycellm.execution.policy import EgressDecision, EgressPolicy, trust_for

__all__ = [
    "DEFAULT_FANOUT",
    "SWARM_MODEL",
    "EgressDecision",
    "EgressPolicy",
    "ExecutionCoordinator",
    "ExecutionPlan",
    "ExecutionPlanner",
    "Job",
    "Role",
    "Strategy",
    "Target",
    "WorkUnit",
    "WorkUnitResult",
    "is_swarm_request",
    "trust_for",
]
