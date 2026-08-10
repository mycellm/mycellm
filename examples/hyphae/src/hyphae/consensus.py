"""Structural consensus verification (architecture doc §6).

For critical tasks, all three devices generate independently. We compare the
*structural skeletons* of the candidates — function/class signatures plus
control-flow shape — and accept a structure when at least two candidates agree.
Different-sized models make different kinds of errors; their agreements are
disproportionately reliable.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .spec import Role

# When candidates agree structurally, take implementation details from the
# strongest source in the agreeing set.
_DETAIL_PRIORITY = [Role.ARCHITECT, Role.BUILDER, Role.SCOUT]

# Control-flow node types that define a skeleton (style differences ignored).
_FLOW_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith,
               ast.Return, ast.Raise, ast.Match)


@dataclass
class ConsensusResult:
    agreed: bool
    winner: Role
    votes: int  # candidates sharing the winning skeleton (1 = no agreement)
    skeletons: dict[Role, tuple]


def python_skeleton(code: str) -> tuple:
    """Structural fingerprint of a Python module.

    Captures definitions (kind, name, arity) and nested control-flow shape;
    ignores naming of locals, literals, comments, and formatting.
    """
    tree = ast.parse(code)
    return _skeleton_of(tree.body)


def _skeleton_of(body: list[ast.stmt]) -> tuple:
    out: list[tuple] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arity = len(node.args.args) + len(node.args.kwonlyargs)
            out.append(("def", node.name, arity, _skeleton_of(node.body)))
        elif isinstance(node, ast.ClassDef):
            out.append(("class", node.name, _skeleton_of(node.body)))
        elif isinstance(node, _FLOW_NODES):
            children = getattr(node, "body", [])
            out.append((type(node).__name__.lower(), _skeleton_of(children)))
    return tuple(out)


def structural_consensus(candidates: dict[Role, str]) -> ConsensusResult:
    """Pick the consensus candidate among per-role Python outputs.

    Candidates that fail to parse are excluded from voting. If no two
    skeletons agree, the highest-priority parseable candidate wins with
    ``agreed=False`` — callers should flag that output for closer review.
    """
    skeletons: dict[Role, tuple] = {}
    for role, code in candidates.items():
        try:
            skeletons[role] = python_skeleton(code)
        except SyntaxError:
            continue
    if not skeletons:
        raise ValueError("no candidate parsed successfully")

    groups: dict[tuple, list[Role]] = {}
    for role, skel in skeletons.items():
        groups.setdefault(skel, []).append(role)
    best_skel, members = max(groups.items(), key=lambda kv: len(kv[1]))

    if len(members) >= 2:
        winner = next(r for r in _DETAIL_PRIORITY if r in members)
        return ConsensusResult(agreed=True, winner=winner, votes=len(members),
                               skeletons=skeletons)

    winner = next(r for r in _DETAIL_PRIORITY if r in skeletons)
    return ConsensusResult(agreed=False, winner=winner, votes=1, skeletons=skeletons)
