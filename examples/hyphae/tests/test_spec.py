import pytest
from pydantic import ValidationError

from hyphae.spec import RequestSpec, TaskCard, TaskDAG


def make_dag(tasks: list[TaskCard]) -> TaskDAG:
    return TaskDAG(spec=RequestSpec(goal="test"), tasks=tasks)


def card(tid: str, deps: list[str] | None = None, **kw) -> TaskCard:
    return TaskCard(id=tid, description=f"task {tid}", depends_on=deps or [], **kw)


def test_valid_dag_topological_order():
    dag = make_dag([card("a"), card("b", ["a"]), card("c", ["a", "b"])])
    order = dag.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        make_dag([card("a", ["b"]), card("b", ["a"])])


def test_self_dependency_rejected():
    with pytest.raises(ValidationError, match="depends on itself"):
        make_dag([card("a", ["a"])])


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown tasks"):
        make_dag([card("a", ["ghost"])])


def test_duplicate_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        make_dag([card("a"), card("a")])


def test_ready_respects_dependencies():
    dag = make_dag([card("a"), card("b", ["a"]), card("c")])
    assert {t.id for t in dag.ready(set())} == {"a", "c"}
    assert {t.id for t in dag.ready({"a", "c"})} == {"b"}
    assert dag.ready({"a", "b", "c"}) == []


def test_assign_speculative_next_picks_sole_dependent():
    dag = make_dag([card("a"), card("b", ["a"]), card("c", ["a", "b"])])
    dag.assign_speculative_next()
    assert dag.task("a").speculative_next == "b"
    # c depends on a AND b, so its context is not predictable from b alone
    assert dag.task("b").speculative_next is None
    assert dag.task("c").speculative_next is None
