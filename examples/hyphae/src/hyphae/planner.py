"""The Planner: natural-language request → structured spec → task DAG (doc §3)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .llm import LLMClient, extract_json
from .spec import RequestSpec, TaskCard, TaskDAG
from .workspace import Workspace

PLANNER_SYSTEM = """\
You are the Planner of Hyphae, a distributed coding-agent harness. Decompose
the user's request into a DAG of small, atomic subtasks.

Respond with ONLY a JSON object, no prose, in this exact shape:
{
  "spec": {
    "goal": "<one-sentence goal>",
    "relevant_files": ["<existing files that matter>"],
    "constraints": ["<hard constraints>"],
    "acceptance_criteria": ["<verifiable outcomes>"]
  },
  "tasks": [
    {
      "id": "task-001",
      "type": "analysis" | "code_generation" | "test_writing",
      "description": "<what to do, specific and self-contained>",
      "depends_on": ["<task ids this needs>"],
      "outputs": ["<files this task creates or modifies>"],
      "acceptance_criteria": ["<per-task checks>"],
      "complexity": "simple" | "medium" | "complex" | "critical"
    }
  ]
}

Rules:
- 2 to 8 tasks. Each produces at most 2 files.
- depends_on may only reference earlier task ids; the graph must be acyclic.
- Mark complexity "critical" only for code touching auth, money, or data
  persistence; "complex" for cross-file refactors or concurrency.
- analysis tasks produce no files (outputs: []).
- Prefer ending with a test_writing task covering the new code.
"""

MAX_PLAN_ATTEMPTS = 3


class PlanningError(Exception):
    pass


class Planner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def plan(self, request: str, workspace: Workspace) -> TaskDAG:
        files = workspace.list_files()
        file_listing = "\n".join(files[:200]) or "(empty workspace)"
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Request: {request}\n\nFiles in the workspace:\n{file_listing}"
                ),
            },
        ]
        last_error = ""
        for _ in range(MAX_PLAN_ATTEMPTS):
            raw = await self.client.complete(messages, temperature=0.2)
            try:
                dag = self._parse(raw)
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)
                # Repair loop: feed the validation error back to the model.
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"That plan was invalid: {exc}\n"
                            "Return the corrected JSON object only."
                        ),
                    },
                ]
                continue
            dag.assign_speculative_next()
            return dag
        raise PlanningError(f"no valid plan after {MAX_PLAN_ATTEMPTS} attempts: {last_error}")

    def _parse(self, raw: str) -> TaskDAG:
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        spec = RequestSpec.model_validate(data.get("spec", {}))
        tasks = [TaskCard.model_validate(t) for t in data.get("tasks", [])]
        if not tasks:
            raise ValueError("plan contains no tasks")
        return TaskDAG(spec=spec, tasks=tasks)


def render_dag(dag: TaskDAG) -> str:
    """Human-readable plan summary (used by the CLI)."""
    lines = [f"Goal: {dag.spec.goal}", ""]
    for tid in dag.topological_order():
        task = dag.task(tid)
        deps = f" <- {', '.join(task.depends_on)}" if task.depends_on else ""
        spec_next = f" [speculates: {task.speculative_next}]" if task.speculative_next else ""
        lines.append(
            f"{task.id} ({task.type.value}, {task.complexity.value}){deps}{spec_next}"
        )
        lines.append(f"    {task.description}")
        if task.outputs:
            lines.append(f"    -> {', '.join(task.outputs)}")
    return "\n".join(lines)


def dag_to_json(dag: TaskDAG) -> str:
    return json.dumps(dag.model_dump(mode="json"), indent=2)
