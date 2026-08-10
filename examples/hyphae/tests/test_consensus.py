import pytest

from hyphae.consensus import python_skeleton, structural_consensus
from hyphae.spec import Role

IMPL_A = """
def total(items, tax):
    if not items:
        return 0
    result = 0
    for item in items:
        result += item
    return result * tax
"""

# Same structure as IMPL_A, different naming and literals.
IMPL_B = """
def total(values, rate):
    if not values:
        return 0
    acc = 0
    for v in values:
        acc += v
    return acc * rate
"""

# Different control flow: sum() instead of loop, no guard.
IMPL_C = """
def total(items, tax):
    return sum(items) * tax
"""


def test_skeleton_ignores_naming_differences():
    assert python_skeleton(IMPL_A) == python_skeleton(IMPL_B)
    assert python_skeleton(IMPL_A) != python_skeleton(IMPL_C)


def test_two_of_three_agreement():
    result = structural_consensus(
        {Role.ARCHITECT: IMPL_C, Role.BUILDER: IMPL_A, Role.SCOUT: IMPL_B}
    )
    assert result.agreed
    assert result.votes == 2
    # builder and scout agree; builder outranks scout for detail selection
    assert result.winner is Role.BUILDER


def test_winner_prefers_architect_within_agreeing_set():
    result = structural_consensus(
        {Role.ARCHITECT: IMPL_A, Role.BUILDER: IMPL_B, Role.SCOUT: IMPL_C}
    )
    assert result.agreed
    assert result.winner is Role.ARCHITECT


def test_no_agreement_falls_back_to_priority():
    different = "def total(items, tax):\n    while items:\n        items.pop()\n    return tax\n"
    result = structural_consensus(
        {Role.ARCHITECT: IMPL_A, Role.BUILDER: IMPL_C, Role.SCOUT: different}
    )
    assert not result.agreed
    assert result.votes == 1
    assert result.winner is Role.ARCHITECT


def test_unparseable_candidates_excluded():
    result = structural_consensus(
        {Role.ARCHITECT: "def broken(:", Role.BUILDER: IMPL_A, Role.SCOUT: IMPL_B}
    )
    assert result.agreed
    assert result.winner is Role.BUILDER


def test_all_unparseable_raises():
    with pytest.raises(ValueError, match="no candidate parsed"):
        structural_consensus({Role.BUILDER: "def broken(:"})
