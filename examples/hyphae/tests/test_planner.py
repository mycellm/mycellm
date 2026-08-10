import json

import pytest

from conftest import MockLLM
from hyphae.planner import Planner, PlanningError

GOOD_PLAN = json.dumps(
    {
        "spec": {
            "goal": "add dark mode",
            "relevant_files": [],
            "constraints": [],
            "acceptance_criteria": ["toggle works"],
        },
        "tasks": [
            {
                "id": "task-001",
                "type": "code_generation",
                "description": "create theme context",
                "depends_on": [],
                "outputs": ["theme.py"],
                "acceptance_criteria": ["exports ThemeContext"],
                "complexity": "medium",
            },
            {
                "id": "task-002",
                "type": "test_writing",
                "description": "test theme context",
                "depends_on": ["task-001"],
                "outputs": ["test_theme.py"],
                "acceptance_criteria": ["tests pass"],
                "complexity": "simple",
            },
        ],
    }
)


async def test_plan_first_try(workspace):
    client = MockLLM([GOOD_PLAN])
    dag = await Planner(client).plan("add dark mode", workspace)
    assert [t.id for t in dag.tasks] == ["task-001", "task-002"]
    assert dag.task("task-001").speculative_next == "task-002"
    assert dag.spec.goal == "add dark mode"


async def test_repair_loop_recovers_from_bad_json(workspace):
    client = MockLLM(["sorry, I cannot do JSON today", GOOD_PLAN])
    dag = await Planner(client).plan("add dark mode", workspace)
    assert len(client.calls) == 2
    assert len(dag.tasks) == 2
    # the repair prompt must include the error and the bad attempt
    repair_messages = client.calls[1]
    assert any("invalid" in m["content"].lower() for m in repair_messages)


async def test_repair_loop_recovers_from_cyclic_plan(workspace):
    cyclic = json.loads(GOOD_PLAN)
    cyclic["tasks"][0]["depends_on"] = ["task-002"]
    client = MockLLM([json.dumps(cyclic), GOOD_PLAN])
    dag = await Planner(client).plan("add dark mode", workspace)
    assert len(client.calls) == 2
    assert len(dag.tasks) == 2


async def test_gives_up_after_max_attempts(workspace):
    client = MockLLM(["nope", "still nope", "never"])
    with pytest.raises(PlanningError, match="no valid plan"):
        await Planner(client).plan("add dark mode", workspace)


async def test_workspace_listing_included_in_prompt(workspace):
    workspace.write("existing.py", "x = 1\n")
    client = MockLLM([GOOD_PLAN])
    await Planner(client).plan("add dark mode", workspace)
    user_message = client.calls[0][1]["content"]
    assert "existing.py" in user_message
