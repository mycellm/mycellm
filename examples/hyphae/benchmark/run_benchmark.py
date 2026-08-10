#!/usr/bin/env python3
"""Hyphae quality benchmark — runs standard test cases and scores outputs.

Usage:
    python3 benchmark/run_benchmark.py [--mycellm http://localhost:8420]

Scores each test on:
- Syntax validity (does it parse without errors?)
- Import correctness (do cross-file imports resolve?)
- Completeness (are all requested files created?)
- Functional correctness (does the code match the spec?)
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hyphae.inference.client import MycellmClient
from hyphae.inference.roles import classify_roles
from hyphae.executor.loop import Executor, ExecutionContext
from hyphae.executor.validate import validate_project
from hyphae.tools.registry import ToolRegistry
from hyphae.inference.roles import InferenceRole


@dataclass
class TestCase:
    name: str
    request: str
    expected_files: list[str]
    checks: list[str] = field(default_factory=list)  # code strings that should appear


@dataclass
class TestResult:
    name: str
    passed: bool = False
    time_s: float = 0
    files_created: list[str] = field(default_factory=list)
    syntax_score: float = 0  # 0-1
    import_score: float = 0  # 0-1
    completeness_score: float = 0  # 0-1
    check_score: float = 0  # 0-1
    total_score: float = 0  # 0-1
    errors: list[str] = field(default_factory=list)


TEST_CASES = [
    TestCase(
        name="hello-fastapi",
        request="Create main.py: a FastAPI app with GET /hello returning {'message': 'hello world'}",
        expected_files=["main.py"],
        checks=["FastAPI", "hello", "@app.get"],
    ),
    TestCase(
        name="pydantic-model",
        request="Create models.py with Pydantic BaseModel classes: User(id:int, name:str, email:str) and UserCreate(name:str, email:str)",
        expected_files=["models.py"],
        checks=["BaseModel", "class User", "class UserCreate", "id: int", "email: str"],
    ),
    TestCase(
        name="dataclass-store",
        request="Create store.py with a class ItemStore that stores items in a dict with auto-incrementing int IDs. Methods: add(data:dict)->dict, get(id:int)->dict|None, list_all()->list[dict], delete(id:int)->bool",
        expected_files=["store.py"],
        checks=["class ItemStore", "def add", "def get", "def list_all", "def delete"],
    ),
    TestCase(
        name="cli-argparse",
        request="Create cli.py: a Python CLI using argparse with subcommands 'greet' (takes --name, prints hello) and 'count' (takes --n, prints numbers 1 to n)",
        expected_files=["cli.py"],
        checks=["argparse", "add_parser", "greet", "count"],
    ),
    TestCase(
        name="flask-blueprint",
        request="Create two files: 1) routes.py with a Flask Blueprint 'api' having GET /status returning {'status':'ok'} and POST /echo that returns the JSON body 2) app.py that imports and registers the Blueprint, runs on port 5000",
        expected_files=["routes.py", "app.py"],
        checks=["Blueprint", "register_blueprint", "status", "echo"],
    ),
]


async def run_test(
    tc: TestCase, client: MycellmClient, roles, mycellm_url: str
) -> TestResult:
    result = TestResult(name=tc.name)
    workdir = Path(tempfile.mkdtemp(prefix=f"hyphae-bench-{tc.name}-"))

    try:
        tools = ToolRegistry(sandbox=workdir)
        executor = Executor(client, roles, tools)

        ctx = ExecutionContext(
            task_description=tc.request,
            workdir=workdir,
            role=InferenceRole.GENERATE,
            max_iterations=3,
        )

        start = time.time()
        exec_result = await executor.execute(ctx)
        result.time_s = round(time.time() - start, 1)
        result.files_created = exec_result.files_created

        # Score: Completeness
        created_basenames = {Path(f).name for f in exec_result.files_created}
        expected_basenames = {Path(f).name for f in tc.expected_files}
        if expected_basenames:
            result.completeness_score = len(created_basenames & expected_basenames) / len(expected_basenames)

        # Score: Syntax
        py_files = [f for f in workdir.rglob("*.py")]
        if py_files:
            valid = 0
            for f in py_files:
                try:
                    ast.parse(f.read_text())
                    valid += 1
                except SyntaxError:
                    result.errors.append(f"Syntax error in {f.name}")
            result.syntax_score = valid / len(py_files)

        # Score: Import correctness
        all_files = [str(f.relative_to(workdir)) for f in py_files]
        if len(all_files) > 1:
            val_result = validate_project(workdir, files=all_files)
            import_errors = len([e for e in val_result.errors if "import" in e.message.lower()])
            import_warnings = len([w for w in val_result.warnings if "import" in w.message.lower()])
            total_imports = import_errors + import_warnings
            result.import_score = 1.0 if total_imports == 0 else max(0, 1.0 - total_imports * 0.25)
        else:
            result.import_score = 1.0  # single file, no cross-imports to check

        # Score: Functional checks
        if tc.checks:
            all_content = ""
            for f in py_files:
                all_content += f.read_text()
            found = sum(1 for check in tc.checks if check in all_content)
            result.check_score = found / len(tc.checks)

        # Total
        result.total_score = (
            result.completeness_score * 0.25
            + result.syntax_score * 0.25
            + result.import_score * 0.25
            + result.check_score * 0.25
        )
        result.passed = result.total_score >= 0.75

    except Exception as e:
        result.errors.append(str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return result


async def main(mycellm_url: str):
    client = MycellmClient(base_url=mycellm_url)

    try:
        await client.health()
    except Exception as e:
        print(f"Cannot connect to mycellm at {mycellm_url}: {e}")
        return

    models = await client.get_capabilities()
    roles = classify_roles(models)

    print(f"Hyphae Quality Benchmark")
    print(f"mycellm: {mycellm_url}")
    print(f"GENERATE: {roles.best_model_id(InferenceRole.GENERATE)}")
    print(f"{'=' * 70}")

    results = []
    total_time = 0

    for tc in TEST_CASES:
        print(f"\n[{tc.name}] Running...", end="", flush=True)
        r = await run_test(tc, client, roles, mycellm_url)
        results.append(r)
        total_time += r.time_s

        icon = "PASS" if r.passed else "FAIL"
        print(f" {icon} ({r.time_s}s) score={r.total_score:.2f}")
        print(f"  syntax={r.syntax_score:.2f} imports={r.import_score:.2f} "
              f"complete={r.completeness_score:.2f} checks={r.check_score:.2f}")
        if r.errors:
            for e in r.errors[:3]:
                print(f"  ERROR: {e}")

    # Summary
    print(f"\n{'=' * 70}")
    passed = sum(1 for r in results if r.passed)
    avg_score = sum(r.total_score for r in results) / len(results) if results else 0
    print(f"Results: {passed}/{len(results)} passed, avg score={avg_score:.2f}")
    print(f"Total time: {total_time:.0f}s")

    # Write JSON report
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mycellm_url": mycellm_url,
        "model": roles.best_model_id(InferenceRole.GENERATE),
        "passed": passed,
        "total": len(results),
        "avg_score": round(avg_score, 3),
        "total_time_s": round(total_time, 1),
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "time_s": r.time_s,
                "total_score": round(r.total_score, 3),
                "syntax": round(r.syntax_score, 3),
                "imports": round(r.import_score, 3),
                "completeness": round(r.completeness_score, 3),
                "checks": round(r.check_score, 3),
                "errors": r.errors,
            }
            for r in results
        ],
    }
    report_path = Path(__file__).parent / "results.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport: {report_path}")

    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mycellm", default="http://localhost:8420")
    args = parser.parse_args()
    asyncio.run(main(args.mycellm))
