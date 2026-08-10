import pytest

from hyphae.workspace import Workspace, WorkspaceError


def test_write_and_read(workspace):
    workspace.write("src/app.py", "x = 1\n")
    assert workspace.read("src/app.py") == "x = 1\n"
    assert workspace.exists("src/app.py")
    assert not workspace.exists("src/other.py")


def test_path_escape_rejected(workspace):
    with pytest.raises(WorkspaceError, match="escapes"):
        workspace.write("../evil.py", "boom")
    with pytest.raises(WorkspaceError, match="escapes"):
        workspace.read("../../etc/passwd")


def test_list_files_skips_ignored_dirs(workspace):
    workspace.write("src/app.py", "x = 1\n")
    (workspace.root / ".git").mkdir()
    (workspace.root / ".git" / "config").write_text("noise")
    (workspace.root / "__pycache__").mkdir()
    (workspace.root / "__pycache__" / "app.pyc").write_text("noise")
    assert workspace.list_files() == ["src/app.py"]


def test_missing_root_rejected(tmp_path):
    with pytest.raises(WorkspaceError, match="does not exist"):
        Workspace(tmp_path / "nope")


def test_write_all_returns_sorted_paths(workspace):
    written = workspace.write_all({"b.py": "x = 1\n", "a.py": "y = 2\n"})
    assert written == ["a.py", "b.py"]
