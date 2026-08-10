"""Core data models: request specs, task cards, and the task DAG."""

from __future__ import annotations

import enum
from graphlib import CycleError, TopologicalSorter

from pydantic import BaseModel, Field, model_validator


class Role(enum.StrEnum):
    """Device roles in the Hyphae network (see hyphae-architecture.md §2)."""

    ARCHITECT = "architect"  # reasoning heavyweight: planning, review, hard tasks
    BUILDER = "builder"  # primary code-generation workhorse
    SCOUT = "scout"  # speculative execution + low-stakes analysis


class TaskType(enum.StrEnum):
    ANALYSIS = "analysis"
    CODE_GENERATION = "code_generation"
    TEST_WRITING = "test_writing"


class Complexity(enum.StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    CRITICAL = "critical"


class RequestSpec(BaseModel):
    """A natural-language request rewritten into a structured spec."""

    goal: str
    relevant_files: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskCard(BaseModel):
    """An atomic subtask in the DAG."""

    id: str
    type: TaskType = TaskType.CODE_GENERATION
    description: str
    depends_on: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    complexity: Complexity = Complexity.MEDIUM
    max_iterations: int = 3
    # Task id the Scout should speculatively execute while this task is in
    # review. Filled in by the planner; never required from the model.
    speculative_next: str | None = None


class TaskDAG(BaseModel):
    spec: RequestSpec
    tasks: list[TaskCard]

    @model_validator(mode="after")
    def _check_graph(self) -> TaskDAG:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate task ids: {ids}")
        known = set(ids)
        for task in self.tasks:
            unknown = [d for d in task.depends_on if d not in known]
            if unknown:
                raise ValueError(f"task {task.id!r} depends on unknown tasks: {unknown}")
            if task.id in task.depends_on:
                raise ValueError(f"task {task.id!r} depends on itself")
        try:
            self.topological_order()
        except CycleError as exc:
            raise ValueError(f"task graph contains a cycle: {exc.args[1]}") from exc
        return self

    def task(self, task_id: str) -> TaskCard:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)

    def topological_order(self) -> list[str]:
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for t in self.tasks:
            sorter.add(t.id, *t.depends_on)
        return list(sorter.static_order())

    def ready(self, completed: set[str]) -> list[TaskCard]:
        """Tasks whose dependencies are all complete and that aren't done."""
        return [
            t
            for t in self.tasks
            if t.id not in completed and all(d in completed for d in t.depends_on)
        ]

    def successors(self, task_id: str) -> list[TaskCard]:
        return [t for t in self.tasks if task_id in t.depends_on]

    def assign_speculative_next(self) -> None:
        """Mark, for each task, the successor the Scout should pre-execute.

        Heuristic: the first successor whose *only* dependency is this task —
        its context is fully predictable from this task's spec alone.
        """
        for task in self.tasks:
            for succ in self.successors(task.id):
                if succ.depends_on == [task.id]:
                    task.speculative_next = succ.id
                    break
