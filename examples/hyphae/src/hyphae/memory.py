"""Structural memory: a queryable symbol graph of the workspace (doc §7, lite).

v0.1 indexes Python files with the stdlib `ast` module into SQLite —
definitions, imports, and call names — and serves deterministic queries the
executor uses for context assembly. Tree-sitter/LSP enrichment can slot in
behind the same interface later.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from .workspace import Workspace

_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE TABLE IF NOT EXISTS imports (
    file TEXT NOT NULL,
    module TEXT NOT NULL,
    name TEXT
);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module);
"""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({ast.unparse(node.args)})"


class StructuralMemory:
    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db = sqlite3.connect(str(db_path))
        self._db.executescript(_SCHEMA)

    def index_workspace(self, workspace: Workspace) -> int:
        """(Re)index all Python files. Returns the number of files indexed."""
        self._db.execute("DELETE FROM symbols")
        self._db.execute("DELETE FROM imports")
        count = 0
        for rel in workspace.list_files("**/*.py"):
            try:
                self.index_file(rel, workspace.read(rel))
                count += 1
            except SyntaxError:
                continue
        self._db.commit()
        return count

    def index_file(self, rel_path: str, content: str) -> None:
        self._db.execute("DELETE FROM symbols WHERE file = ?", (rel_path,))
        self._db.execute("DELETE FROM imports WHERE file = ?", (rel_path,))
        tree = ast.parse(content, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_symbol(node.name, "function", rel_path, node, _signature(node))
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                self._add_symbol(node.name, "class", rel_path, node,
                                 f"class {node.name}({bases})")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self._db.execute("INSERT INTO imports VALUES (?, ?, ?)",
                                     (rel_path, alias.name, None))
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self._db.execute("INSERT INTO imports VALUES (?, ?, ?)",
                                     (rel_path, node.module, alias.name))
        self._db.commit()

    def _add_symbol(self, name: str, kind: str, file: str, node: ast.stmt,
                    signature: str) -> None:
        self._db.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?)",
            (name, kind, file, node.lineno, node.end_lineno or node.lineno, signature),
        )

    def lookup(self, name: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT name, kind, file, line, signature FROM symbols WHERE name = ?",
            (name,),
        ).fetchall()
        keys = ("name", "kind", "file", "line", "signature")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def file_outline(self, rel_path: str) -> list[str]:
        rows = self._db.execute(
            "SELECT signature, line FROM symbols WHERE file = ? ORDER BY line",
            (rel_path,),
        ).fetchall()
        return [sig for sig, _ in rows]

    def dependents(self, module: str) -> list[str]:
        """Files that import the given module (exact or submodule prefix)."""
        rows = self._db.execute(
            "SELECT DISTINCT file FROM imports WHERE module = ? OR module LIKE ?",
            (module, f"{module}.%"),
        ).fetchall()
        return sorted(r[0] for r in rows)

    def context_for(self, files: list[str]) -> str:
        """Compact outline of the given files, for prompt context assembly."""
        sections = []
        for rel in files:
            outline = self.file_outline(rel)
            if outline:
                sections.append(f"## {rel}\n" + "\n".join(f"- {s}" for s in outline))
        return "\n\n".join(sections)
