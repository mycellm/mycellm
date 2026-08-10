"""Fast local validation: catch broken output before it costs an iteration.

This is the v0.1 equivalent of the architecture doc's "Phase 3" stack
(tree-sitter / LSP). It uses only the standard library: `ast` for Python,
`json`/`tomllib` for config files. Diagnostics feed straight back into the
model as micro-iteration context.
"""

from __future__ import annotations

import ast
import json
import tomllib
from dataclasses import dataclass, field


@dataclass
class Diagnostic:
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"


@dataclass
class ValidationReport:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def summary(self) -> str:
        return "\n".join(str(d) for d in self.diagnostics)


def validate_files(files: dict[str, str]) -> ValidationReport:
    report = ValidationReport()
    for path, content in files.items():
        if not content.strip():
            report.diagnostics.append(Diagnostic(path, "file is empty"))
            continue
        if path.endswith(".py"):
            _check_python(path, content, report)
        elif path.endswith(".json"):
            _check_json(path, content, report)
        elif path.endswith(".toml"):
            _check_toml(path, content, report)
    return report


def _check_python(path: str, content: str, report: ValidationReport) -> None:
    try:
        ast.parse(content, filename=path)
    except SyntaxError as exc:
        report.diagnostics.append(
            Diagnostic(path, f"syntax error at line {exc.lineno}: {exc.msg}")
        )


def _check_json(path: str, content: str, report: ValidationReport) -> None:
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        report.diagnostics.append(Diagnostic(path, f"invalid JSON: {exc}"))


def _check_toml(path: str, content: str, report: ValidationReport) -> None:
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        report.diagnostics.append(Diagnostic(path, f"invalid TOML: {exc}"))
