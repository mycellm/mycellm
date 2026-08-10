"""Test-execution verification: run the workspace's test suite (doc §4.4).

LLM review can rubber-stamp wrong code — especially when the Architect is
the same model as the Builder. Actually executing the tests is deterministic
and catches what review misses (wrong semantics, tests that don't import the
code under test, etc.). Failure output feeds back into the next iteration.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import PurePath

from .workspace import Workspace

TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
MAX_OUTPUT_CHARS = 4000


@dataclass
class TestReport:
    ran: bool
    ok: bool
    command: str = ""
    output: str = ""


def has_tests(workspace: Workspace) -> bool:
    return any(
        fnmatch.fnmatch(PurePath(rel).name, pattern)
        for rel in workspace.list_files("**/*.py")
        for pattern in TEST_FILE_PATTERNS
    )


async def run_tests(
    workspace: Workspace,
    command: str | None = None,
    timeout: float = 120.0,
) -> TestReport:
    """Run the workspace test suite.

    With no explicit command, auto-detects pytest-style test files and runs
    `python -m pytest` in the workspace root; if there are none, reports
    ran=False (callers treat that as "nothing to verify").
    """
    if command:
        argv = shlex.split(command)
    elif has_tests(workspace):
        argv = [sys.executable, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider"]
    else:
        return TestReport(ran=False, ok=True)

    cmd_str = " ".join(argv)
    # ⚠️ PYTHONDONTWRITEBYTECODE, AND IT IS A CORRECTNESS FIX, NOT TIDINESS.
    # CPython validates a cached .pyc against the source's mtime AND SIZE. An
    # agent fixing a bug frequently changes neither: `return a - b` becoming
    # `return a + b` is the same number of bytes, and a fix written within the
    # same mtime second is indistinguishable from the original. The stale
    # bytecode is then reused, the workspace's tests run the OLD code, and the
    # loop concludes the fix did not work — so it burns another iteration
    # re-fixing something that was already correct.
    #
    # This surfaced as an intermittently failing test (the scripted responses
    # ran out on the extra iteration), but the flake was the symptom: the same
    # trap silently costs real runs an iteration, and one-character fixes are
    # exactly what a repair loop produces. `-p no:cacheprovider` above disables
    # PYTEST's cache; it does nothing about Python's.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=workspace.root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return TestReport(
            ran=True, ok=False, command=cmd_str,
            output=f"test run timed out after {timeout:.0f}s",
        )
    text = out.decode(errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        text = "…" + text[-MAX_OUTPUT_CHARS:]
    return TestReport(ran=True, ok=proc.returncode == 0, command=cmd_str, output=text)
