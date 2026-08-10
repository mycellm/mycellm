from conftest import MockLLM, gen_response, make_registry, review_response
from hyphae.config import EngineConfig
from hyphae.executor import HyphaeEngine, TaskStatus
from hyphae.spec import Complexity, RequestSpec, TaskCard, TaskDAG, TaskType

VALID_A = "def alpha():\n    return 1\n"
VALID_B = "def beta():\n    return 2\n"
BROKEN = "def broken(:\n    pass\n"


def two_task_dag() -> TaskDAG:
    dag = TaskDAG(
        spec=RequestSpec(goal="build feature"),
        tasks=[
            TaskCard(id="task-001", description="create alpha module",
                     outputs=["alpha.py"], acceptance_criteria=["exports alpha"]),
            TaskCard(id="task-002", description="create beta module",
                     depends_on=["task-001"], outputs=["beta.py"],
                     acceptance_criteria=["exports beta"]),
        ],
    )
    dag.assign_speculative_next()
    return dag


def builder_router(messages):
    user = messages[1]["content"]
    if "task-001" in user:
        return gen_response({"alpha.py": VALID_A})
    return gen_response({"beta.py": VALID_B})


async def test_end_to_end_with_speculative_warm_start(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(router=builder_router)
    scout = MockLLM(router=lambda m: gen_response({"beta.py": "def beta():\n    pass\n"},
                                                  notes="assumed alpha exists"))
    engine = HyphaeEngine(make_registry(architect, builder, scout), workspace)

    report = await engine.execute("build feature", two_task_dag())

    assert report.success
    assert report.files_written == ["alpha.py", "beta.py"]
    assert workspace.read("alpha.py") == VALID_A
    assert workspace.read("beta.py") == VALID_B

    # the scout speculated task-002 while task-001 was in review...
    assert len(scout.calls) == 1
    assert "create beta module" in scout.calls[0][1]["content"]
    # ...and its draft reached the builder as a warm start
    assert report.results["task-002"].warm_start_used
    task2_prompt = builder.calls[1][1]["content"]
    assert "Scout draft (warm start)" in task2_prompt
    assert not report.results["task-001"].warm_start_used


async def test_micro_iteration_fixes_validation_without_consuming_iteration(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(
        responses=[gen_response({"alpha.py": BROKEN}), gen_response({"alpha.py": VALID_A})]
    )
    scout = MockLLM()
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, scout), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.iterations == 1
    assert result.micro_iterations == 1
    # the micro-iteration prompt carried the diagnostics back to the model
    retry_prompt = builder.calls[1][-1]["content"]
    assert "syntax error" in retry_prompt


async def test_review_rejection_consumes_full_iteration(workspace):
    reviews = [review_response(False, "alpha must return 2"), review_response(True)]
    architect = MockLLM(router=lambda m: reviews.pop(0))
    builder = MockLLM(
        responses=[gen_response({"alpha.py": VALID_A}), gen_response({"alpha.py": VALID_B})]
    )
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.iterations == 2
    feedback_prompt = builder.calls[1][-1]["content"]
    assert "alpha must return 2" in feedback_prompt


async def test_exhausted_iterations_fails_and_blocks_dependents(workspace):
    architect = MockLLM(router=lambda m: review_response(False, "wrong"))
    builder = MockLLM(router=lambda m: gen_response({"alpha.py": VALID_A}))
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM(router=lambda m: "{}")),
                          workspace)

    report = await engine.execute("g", two_task_dag())

    assert not report.success
    assert report.results["task-001"].status is TaskStatus.FAILED
    assert "task-002" not in report.results  # never dispatched
    assert not workspace.exists("alpha.py")


async def test_simple_tasks_skip_architect_review(workspace):
    architect = MockLLM()  # would raise IndexError if consulted
    builder = MockLLM(responses=[gen_response({"alpha.py": VALID_A})])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"],
                        complexity=Complexity.SIMPLE)],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace)

    report = await engine.execute("g", dag)

    assert report.success
    assert architect.calls == []


async def test_analysis_task_routes_to_scout_and_writes_nothing(workspace):
    scout = MockLLM(responses=[gen_response({}, notes="theme system uses CSS vars")])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="analyze theme system",
                        type=TaskType.ANALYSIS)],
    )
    engine = HyphaeEngine(make_registry(MockLLM(), MockLLM(), scout), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.device == "ipad"
    assert result.notes == "theme system uses CSS vars"
    assert result.files == {}
    assert workspace.list_files() == []


async def test_critical_task_uses_structural_consensus(workspace):
    loop_impl = ("def charge(amount):\n    if amount <= 0:\n        raise ValueError\n"
                 "    return amount * 100\n")
    same_shape = ("def charge(value):\n    if value <= 0:\n        raise ValueError\n"
                  "    return value * 100\n")
    different = "def charge(amount):\n    return amount * 100\n"

    architect = MockLLM(router=lambda m: gen_response({"billing.py": loop_impl}))
    builder = MockLLM(router=lambda m: gen_response({"billing.py": same_shape}))
    scout = MockLLM(router=lambda m: gen_response({"billing.py": different}))
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="charge fn", outputs=["billing.py"],
                        complexity=Complexity.CRITICAL)],
    )
    engine = HyphaeEngine(make_registry(architect, builder, scout), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.consensus_votes == 2
    # architect is in the agreeing pair, so its details win
    assert workspace.read("billing.py") == loop_impl


async def test_unparseable_generation_retries_then_fails(workspace):
    builder = MockLLM(router=lambda m: "I refuse to emit JSON")
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"],
                        max_iterations=2)],
    )
    engine = HyphaeEngine(
        make_registry(MockLLM(router=lambda m: review_response(True)), builder, MockLLM()),
        workspace,
    )

    report = await engine.execute("g", dag)

    assert report.results["task-001"].status is TaskStatus.FAILED
    assert len(builder.calls) == 2


async def test_fenced_file_format_accepted(workspace):
    fenced = (
        "### FILE: alpha.py\n```python\n" + VALID_A + "```\n"
        "### NOTES\nfenced response\n"
    )
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(responses=[fenced])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.notes == "fenced response"
    assert workspace.read("alpha.py") == VALID_A


def calc_response(body: str) -> str:
    return (
        "### FILE: calc.py\n```\n" + body + "```\n"
        "### FILE: test_calc.py\n```\n"
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
        "```\n### NOTES\ndone\n"
    )


async def test_failing_tests_consume_iteration_and_roll_back(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(responses=[
        calc_response("def add(a, b):\n    return a - b\n"),  # wrong: tests fail
        calc_response("def add(a, b):\n    return a + b\n"),
    ])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="calc",
                        outputs=["calc.py", "test_calc.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace)

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.iterations == 2
    assert result.tests_passed is True
    assert "return a + b" in workspace.read("calc.py")
    retry_prompt = builder.calls[1][-1]["content"]
    assert "test suite failed" in retry_prompt
    assert "test_add" in retry_prompt  # pytest output fed back


async def test_run_tests_disabled_by_config(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(responses=[calc_response("def add(a, b):\n    return a - b\n")])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="calc",
                        outputs=["calc.py", "test_calc.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace,
                          config=EngineConfig(run_tests=False))

    report = await engine.execute("g", dag)

    result = report.results["task-001"]
    assert result.status is TaskStatus.DONE
    assert result.tests_passed is None  # phase skipped, wrong code kept


async def test_no_test_files_leaves_tests_unset(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(responses=[gen_response({"alpha.py": VALID_A})])
    dag = TaskDAG(
        spec=RequestSpec(goal="g"),
        tasks=[TaskCard(id="task-001", description="alpha", outputs=["alpha.py"])],
    )
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM()), workspace)

    report = await engine.execute("g", dag)

    assert report.results["task-001"].tests_passed is None


async def test_memory_updated_after_each_task(workspace):
    architect = MockLLM(router=lambda m: review_response(True))
    builder = MockLLM(router=builder_router)
    engine = HyphaeEngine(make_registry(architect, builder, MockLLM(router=lambda m: "{}")),
                          workspace)

    await engine.execute("g", two_task_dag())

    assert engine.memory.lookup("alpha")[0]["file"] == "alpha.py"
    assert engine.memory.lookup("beta")[0]["file"] == "beta.py"
