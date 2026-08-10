"""The execution engine: Generate → Validate → Fix, with speculative task
execution and structural consensus (architecture doc §4–§6)."""

from __future__ import annotations

import asyncio
import contextlib
import enum
from dataclasses import dataclass, field

from .config import DeviceRegistry, EngineConfig
from .consensus import structural_consensus
from .llm import extract_json, parse_file_blocks
from .memory import StructuralMemory
from .planner import Planner
from .spec import Complexity, Role, TaskCard, TaskDAG, TaskType
from .testing import run_tests
from .validate import validate_files
from .workspace import Workspace

GENERATE_SYSTEM = '''\
You are a {role} agent in Hyphae, a distributed coding harness. Complete the
assigned task exactly as specified.

Respond with one block per file you create or modify, in exactly this format:

### FILE: relative/path.py
```
<COMPLETE file content — never fragments or diffs>
```

After all file blocks, finish with a short summary:

### NOTES
<what you did and any caveats>

Rules:
- Only create or modify the files the task asks for.
- Address every acceptance criterion.
- For analysis tasks, emit no FILE blocks — only the NOTES section.
'''

REVIEW_SYSTEM = """\
You are the Architect of Hyphae, reviewing code another agent generated.
Judge whether the output satisfies the task. Be strict about correctness and
acceptance criteria; ignore style preferences.

Respond with ONLY a JSON object:
{"approved": true | false, "feedback": "<if not approved: specific, actionable fixes>"}
"""

SPECULATE_SYSTEM = '''\
You are the Scout of Hyphae. The task below will be executed soon, but its
prerequisite is still in review. Produce a fast DRAFT now: correct file
structure, imports, and signatures matter most; implementation details can be
rough. A stronger model will refine your draft.

Respond with one block per draft file, in exactly this format:

### FILE: relative/path.py
```
<full draft content>
```

### NOTES
<assumptions you made about the prerequisite's output>
'''


class TaskStatus(enum.StrEnum):
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    device: str
    files: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    iterations: int = 1
    micro_iterations: int = 0
    warm_start_used: bool = False
    consensus_votes: int | None = None
    tests_passed: bool | None = None  # None = test phase didn't run
    failure_reason: str = ""


@dataclass
class RunReport:
    request: str
    results: dict[str, TaskResult] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.results) and all(
            r.status is TaskStatus.DONE for r in self.results.values()
        )

    @property
    def files_written(self) -> list[str]:
        out: set[str] = set()
        for r in self.results.values():
            if r.status is TaskStatus.DONE:
                out.update(r.files)
        return sorted(out)


class GenerationParseError(Exception):
    pass


class HyphaeEngine:
    def __init__(
        self,
        registry: DeviceRegistry,
        workspace: Workspace,
        config: EngineConfig | None = None,
        memory: StructuralMemory | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.registry = registry
        self.workspace = workspace
        self.config = config or EngineConfig()
        self.memory = memory or StructuralMemory()
        self.planner = planner or Planner(registry.get(Role.ARCHITECT).client)
        # task id -> scout draft (rendered text) produced speculatively
        self._speculative: dict[str, str] = {}
        self._events: list[str] = []
        # Serializes workspace mutation + test execution across concurrent
        # tasks so one task's pytest run never observes another task's
        # half-applied (and possibly rolled-back) files.
        self._verify_lock = asyncio.Lock()

    async def run(self, request: str) -> RunReport:
        self.memory.index_workspace(self.workspace)
        dag = await self.planner.plan(request, self.workspace)
        self._log(f"plan: {len(dag.tasks)} tasks for goal {dag.spec.goal!r}")
        for tid in dag.topological_order():
            task = dag.task(tid)
            self._log(f"  {task.id} ({task.type}, {task.complexity}): "
                      f"{task.description[:100]}")
        return await self.execute(request, dag)

    async def execute(self, request: str, dag: TaskDAG) -> RunReport:
        report = RunReport(request=request, events=self._events)
        completed: set[str] = set()
        while len(completed) < len(dag.tasks):
            ready = dag.ready(completed)
            if not ready:
                break  # remaining tasks are blocked by a failure
            batch = await asyncio.gather(*[self._execute_task(dag, t) for t in ready])
            failed = False
            for result in batch:
                report.results[result.task_id] = result
                if result.status is TaskStatus.DONE:
                    completed.add(result.task_id)
                else:
                    failed = True
            if failed:
                break
        return report

    # ------------------------------------------------------------------ #
    # Single-task execution                                              #
    # ------------------------------------------------------------------ #

    async def _execute_task(self, dag: TaskDAG, task: TaskCard) -> TaskResult:
        if (
            task.complexity is Complexity.CRITICAL
            and self.config.consensus_for_critical
            and self._distinct_device_count() >= 2
        ):
            return await self._execute_consensus(dag, task)
        return await self._execute_single(dag, task)

    async def _execute_single(self, dag: TaskDAG, task: TaskCard) -> TaskResult:
        device = self.registry.for_task(task)
        warm_start = self._speculative.pop(task.id, None)
        context = self._assemble_context(dag, task, warm_start)
        messages = [
            {"role": "system", "content": GENERATE_SYSTEM.format(role=device.role.value)},
            {"role": "user", "content": context},
        ]
        self._log(f"{task.id}: dispatched to {device.name} ({device.role.value})"
                  + (" with warm start" if warm_start else ""))

        micro_total = 0
        for iteration in range(1, task.max_iterations + 1):
            raw = await device.client.complete(
                messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            try:
                files, notes = self._parse_generation(raw)
            except GenerationParseError as exc:
                self._log(f"{task.id}: unparseable generation (iter {iteration})")
                messages = self._followup(
                    messages, raw,
                    f"Invalid response: {exc}. Respond using the "
                    "'### FILE: <path>' fenced-block format only.",
                )
                continue

            if task.type is TaskType.ANALYSIS:
                # Analysis is low-stakes: no files, no review (doc §10).
                return TaskResult(task.id, TaskStatus.DONE, device.name, notes=notes,
                                  iterations=iteration, micro_iterations=micro_total,
                                  warm_start_used=warm_start is not None)

            # Phase 3: fast local validation with micro-iterations — these
            # never consume a full iteration (doc §4.3).
            files, raw, messages, micros = await self._micro_iterate(
                device, messages, raw, files
            )
            micro_total += micros
            report = validate_files(files)
            if not report.ok:
                self._log(f"{task.id}: validation failed (iter {iteration}): "
                          f"{report.summary()[:120]}")
                messages = self._followup(
                    messages, raw,
                    f"Validation still failing:\n{report.summary()}\nFix and respond "
                    "with the full corrected FILE blocks.",
                )
                continue

            # Phase 4a: test-execution verification (doc §4.4). Apply the
            # files, run the suite; a failure rolls them back, feeds the
            # output to the model, and consumes a full iteration.
            async with self._verify_lock:
                snapshot = self.workspace.snapshot(files)
                self.workspace.write_all(files)
                test_report = None
                if self.config.run_tests:
                    test_report = await run_tests(
                        self.workspace, self.config.test_command,
                        self.config.test_timeout,
                    )
                    if not test_report.ran:
                        test_report = None
                tests_passed = test_report.ok if test_report else None
                if test_report and not test_report.ok:
                    self.workspace.restore(snapshot)
            if test_report and not test_report.ok:
                self._log(f"{task.id}: tests failed (iter {iteration})")
                messages = self._followup(
                    messages, raw,
                    "The test suite failed after applying your files.\n"
                    f"Command: {test_report.command}\nOutput:\n{test_report.output}\n"
                    "Fix the code and respond with the full corrected FILE blocks.",
                )
                continue

            # Phase 4b: architect review, with the Scout speculating on the
            # next task concurrently (doc §5).
            approved, feedback = True, ""
            if self._review_needed(task):
                review_coro = self._review(task, files, notes)
                spec_coro = self._maybe_speculate(dag, task)
                (approved, feedback), _ = await asyncio.gather(review_coro, spec_coro)
            else:
                await self._maybe_speculate(dag, task)

            if approved:
                self._update_memory(files)
                tests_label = {True: "pass", False: "fail", None: "n/a"}[tests_passed]
                self._log(f"{task.id}: done on {device.name} "
                          f"(iter {iteration}, micro {micro_total}, tests {tests_label})")
                return TaskResult(task.id, TaskStatus.DONE, device.name, files=files,
                                  notes=notes, iterations=iteration,
                                  micro_iterations=micro_total,
                                  warm_start_used=warm_start is not None,
                                  tests_passed=tests_passed)

            async with self._verify_lock:
                self.workspace.restore(snapshot)
            self._log(f"{task.id}: review rejected (iter {iteration}): {feedback[:120]}")
            messages = self._followup(
                messages, raw,
                f"Architect review rejected this output:\n{feedback}\n"
                "Address the feedback and respond with the full corrected FILE blocks.",
            )

        return TaskResult(task.id, TaskStatus.FAILED, device.name,
                          iterations=task.max_iterations, micro_iterations=micro_total,
                          failure_reason="exhausted iterations without approval")

    async def _micro_iterate(self, device, messages, raw, files):
        """Re-generate on validation failure without consuming an iteration."""
        micros = 0
        report = validate_files(files)
        while not report.ok and micros < self.config.max_micro_iterations:
            micros += 1
            messages = self._followup(
                messages, raw,
                f"Local validation failed:\n{report.summary()}\n"
                "Fix these issues and respond with the full corrected FILE blocks.",
            )
            raw = await device.client.complete(
                messages, temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            try:
                files, _ = self._parse_generation(raw)
            except GenerationParseError:
                continue
            report = validate_files(files)
        return files, raw, messages, micros

    # ------------------------------------------------------------------ #
    # Consensus path (critical tasks)                                    #
    # ------------------------------------------------------------------ #

    async def _execute_consensus(self, dag: TaskDAG, task: TaskCard) -> TaskResult:
        self._log(f"{task.id}: critical — three-body consensus")
        context = self._assemble_context(dag, task, None)
        roles = [Role.ARCHITECT, Role.BUILDER, Role.SCOUT]
        devices = {role: self.registry.get(role) for role in roles}

        async def generate(role: Role):
            device = devices[role]
            messages = [
                {"role": "system", "content": GENERATE_SYSTEM.format(role=role.value)},
                {"role": "user", "content": context},
            ]
            raw = await device.client.complete(
                messages, temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            try:
                return role, self._parse_generation(raw)
            except GenerationParseError:
                return role, None

        outputs = dict(await asyncio.gather(*[generate(r) for r in roles]))
        parsed = {r: out for r, out in outputs.items() if out is not None}
        if not parsed:
            return TaskResult(task.id, TaskStatus.FAILED, "consensus",
                              failure_reason="no device produced parseable output")

        target = next((p for p in task.outputs if p.endswith(".py")), None)
        candidates = {
            role: files[target]
            for role, (files, _) in parsed.items()
            if target is not None and target in files
        }
        if len(candidates) >= 2:
            result = structural_consensus(candidates)
            winner_role, votes = result.winner, result.votes
        else:
            winner_role = next(r for r in roles if r in parsed)
            votes = 1

        files, notes = parsed[winner_role]
        report = validate_files(files)
        if not report.ok:
            return TaskResult(task.id, TaskStatus.FAILED, devices[winner_role].name,
                              consensus_votes=votes,
                              failure_reason=f"consensus winner failed validation: "
                                             f"{report.summary()}")
        tests_passed: bool | None = None
        async with self._verify_lock:
            snapshot = self.workspace.snapshot(files)
            self.workspace.write_all(files)
            if self.config.run_tests:
                test_report = await run_tests(
                    self.workspace, self.config.test_command, self.config.test_timeout
                )
                if test_report.ran:
                    tests_passed = test_report.ok
                    if not test_report.ok:
                        self.workspace.restore(snapshot)
        if tests_passed is False:
            return TaskResult(task.id, TaskStatus.FAILED, devices[winner_role].name,
                              consensus_votes=votes, tests_passed=False,
                              failure_reason="consensus winner failed the test suite")
        self._update_memory(files)
        self._log(f"{task.id}: consensus winner {winner_role.value} ({votes} votes)")
        return TaskResult(task.id, TaskStatus.DONE, devices[winner_role].name,
                          files=files, notes=notes, consensus_votes=votes,
                          tests_passed=tests_passed)

    # ------------------------------------------------------------------ #
    # Speculative task execution (doc §5)                                #
    # ------------------------------------------------------------------ #

    async def _maybe_speculate(self, dag: TaskDAG, task: TaskCard) -> None:
        next_id = task.speculative_next
        if (
            next_id is None
            or next_id in self._speculative
            or not self.registry.has_dedicated(Role.SCOUT)
        ):
            return
        scout = self.registry.get(Role.SCOUT)
        next_task = dag.task(next_id)
        prompt = (
            f"Upcoming task: {next_task.description}\n"
            f"Expected output files: {', '.join(next_task.outputs) or '(none)'}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"- {c}" for c in next_task.acceptance_criteria)
            + f"\n\nIts prerequisite ({task.id}) is expected to produce: "
            f"{', '.join(task.outputs) or 'analysis notes'} satisfying:\n"
            + "\n".join(f"- {c}" for c in task.acceptance_criteria)
        )
        try:
            raw = await scout.client.complete(
                [{"role": "system", "content": SPECULATE_SYSTEM},
                 {"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.config.max_tokens,
            )
            files, notes = self._parse_generation(raw)
        except Exception as exc:  # noqa: BLE001 — speculation is best-effort
            self._log(f"{next_id}: speculation failed ({exc}); no warm start")
            return
        rendered = "\n\n".join(
            f"--- draft of {path} ---\n{content}" for path, content in files.items()
        )
        if rendered:
            self._speculative[next_id] = f"{rendered}\n\nScout assumptions: {notes}"
            self._log(f"{next_id}: scout warm start ready ({len(files)} draft files)")

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    async def _review(self, task: TaskCard, files: dict[str, str],
                      notes: str) -> tuple[bool, str]:
        architect = self.registry.get(Role.ARCHITECT)
        body = "\n\n".join(f"--- {path} ---\n{content}" for path, content in files.items())
        prompt = (
            f"Task: {task.description}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"- {c}" for c in task.acceptance_criteria)
            + f"\n\nGenerated output:\n{body}\n\nAgent notes: {notes}"
        )
        raw = await architect.client.complete(
            [{"role": "system", "content": REVIEW_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1024,
        )
        try:
            data = extract_json(raw)
            return bool(data.get("approved")), str(data.get("feedback", ""))
        except ValueError:
            # An unparseable review must not block the pipeline.
            return True, ""

    def _review_needed(self, task: TaskCard) -> bool:
        return (
            self.config.architect_review
            and task.complexity is not Complexity.SIMPLE
        )

    def _assemble_context(self, dag: TaskDAG, task: TaskCard,
                          warm_start: str | None) -> str:
        parts = [f"# Task {task.id}: {task.description}"]
        # Always carry the overall goal: planners (especially small models)
        # drop load-bearing details when summarizing tasks, and a builder
        # that only sees the task description drifts from the user's spec.
        parts.append(f"## Overall goal\n{dag.spec.goal}")
        if dag.spec.acceptance_criteria:
            parts.append("## Overall acceptance criteria\n"
                         + "\n".join(f"- {c}" for c in dag.spec.acceptance_criteria))
        if task.acceptance_criteria:
            parts.append("## Acceptance criteria\n"
                         + "\n".join(f"- {c}" for c in task.acceptance_criteria))
        if task.outputs:
            parts.append("## Files to produce\n" + "\n".join(f"- {p}" for p in task.outputs))
        if dag.spec.constraints:
            parts.append("## Project constraints\n"
                         + "\n".join(f"- {c}" for c in dag.spec.constraints))

        # Current contents of files this task modifies or should know about.
        for rel in dict.fromkeys(list(task.outputs) + dag.spec.relevant_files):
            if self.workspace.exists(rel):
                content = self.workspace.read(rel)
                parts.append(f"## Current content of {rel}\n```\n{content}\n```")

        outline = self.memory.context_for(dag.spec.relevant_files)
        if outline:
            parts.append(f"## Codebase outline\n{outline}")

        if warm_start:
            parts.append(
                "## Scout draft (warm start)\n"
                "A fast scout model drafted this while the prerequisite was in "
                "review. Refine it — keep what is correct, fix what is not:\n"
                + warm_start
            )
        return "\n\n".join(parts)

    def _parse_generation(self, raw: str) -> tuple[dict[str, str], str]:
        # Primary contract: fenced FILE/NOTES blocks — natural for coder
        # models, which reliably emit invalid JSON when asked to embed
        # multi-line code in JSON strings (e.g. Python-style """ quoting).
        parsed = parse_file_blocks(raw)
        if parsed is not None:
            return parsed
        # Fallback: the JSON object contract.
        try:
            data = extract_json(raw)
        except ValueError as exc:
            raise GenerationParseError(str(exc)) from exc
        if not isinstance(data, dict):
            raise GenerationParseError("response must be a JSON object")
        notes = str(data.get("notes", ""))
        raw_files = data.get("files", [])
        files: dict[str, str] = {}
        if isinstance(raw_files, dict):
            files = {str(k): str(v) for k, v in raw_files.items()}
        elif isinstance(raw_files, list):
            for entry in raw_files:
                if not isinstance(entry, dict) or "path" not in entry:
                    raise GenerationParseError(f"bad files entry: {entry!r}")
                files[str(entry["path"])] = str(entry.get("content", ""))
        else:
            raise GenerationParseError("'files' must be a list or object")
        return files, notes

    def _update_memory(self, files: dict[str, str]) -> None:
        for path, content in files.items():
            if path.endswith(".py"):
                with contextlib.suppress(SyntaxError):
                    self.memory.index_file(path, content)

    def _distinct_device_count(self) -> int:
        return len({d.name for d in self.registry.devices})

    @staticmethod
    def _followup(messages: list[dict[str, str]], raw: str,
                  instruction: str) -> list[dict[str, str]]:
        return messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": instruction},
        ]

    def _log(self, message: str) -> None:
        self._events.append(message)
