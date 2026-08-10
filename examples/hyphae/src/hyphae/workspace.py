"""Sandboxed file access for the project being worked on."""

from __future__ import annotations

from pathlib import Path

IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", "dist", "build"}


class WorkspaceError(Exception):
    pass


class Workspace:
    """All reads and writes are confined to the workspace root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"workspace root does not exist: {self.root}")

    def _resolve(self, rel_path: str) -> Path:
        path = (self.root / rel_path).resolve()
        if not path.is_relative_to(self.root):
            raise WorkspaceError(f"path escapes workspace: {rel_path!r}")
        return path

    def exists(self, rel_path: str) -> bool:
        return self._resolve(rel_path).is_file()

    def read(self, rel_path: str) -> str:
        path = self._resolve(rel_path)
        if not path.is_file():
            raise WorkspaceError(f"no such file in workspace: {rel_path!r}")
        return path.read_text()

    def write(self, rel_path: str, content: str) -> Path:
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def write_all(self, files: dict[str, str]) -> list[str]:
        for rel_path, content in files.items():
            self.write(rel_path, content)
        return sorted(files)

    def snapshot(self, rel_paths) -> dict[str, str | None]:
        """Capture current content of the given paths (None = absent)."""
        return {p: (self.read(p) if self.exists(p) else None) for p in rel_paths}

    def restore(self, snap: dict[str, str | None]) -> None:
        """Revert paths to a snapshot, deleting files that didn't exist."""
        for rel_path, content in snap.items():
            if content is None:
                path = self._resolve(rel_path)
                if path.is_file():
                    path.unlink()
            else:
                self.write(rel_path, content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        out = []
        for path in self.root.glob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            out.append(str(rel))
        return sorted(out)
