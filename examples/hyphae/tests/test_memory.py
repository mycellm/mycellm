from hyphae.memory import StructuralMemory

SAMPLE = '''
import os
from app.theme import ThemeContext, use_theme

class SettingsScreen:
    def render(self):
        return None

def toggle_theme(current):
    if current == "dark":
        return "light"
    return "dark"
'''


def test_index_and_lookup(workspace):
    workspace.write("screens/settings.py", SAMPLE)
    mem = StructuralMemory()
    assert mem.index_workspace(workspace) == 1

    hits = mem.lookup("toggle_theme")
    assert len(hits) == 1
    assert hits[0]["kind"] == "function"
    assert hits[0]["file"] == "screens/settings.py"

    classes = mem.lookup("SettingsScreen")
    assert classes[0]["kind"] == "class"


def test_dependents(workspace):
    workspace.write("screens/settings.py", SAMPLE)
    workspace.write("unrelated.py", "x = 1\n")
    mem = StructuralMemory()
    mem.index_workspace(workspace)
    assert mem.dependents("app.theme") == ["screens/settings.py"]
    assert mem.dependents("app") == ["screens/settings.py"]
    assert mem.dependents("missing") == []


def test_file_outline_and_context(workspace):
    workspace.write("screens/settings.py", SAMPLE)
    mem = StructuralMemory()
    mem.index_workspace(workspace)
    outline = mem.file_outline("screens/settings.py")
    assert any("toggle_theme" in s for s in outline)
    context = mem.context_for(["screens/settings.py"])
    assert "## screens/settings.py" in context


def test_reindex_replaces_old_symbols(workspace):
    mem = StructuralMemory()
    mem.index_file("a.py", "def old():\n    pass\n")
    mem.index_file("a.py", "def new():\n    pass\n")
    assert mem.lookup("old") == []
    assert len(mem.lookup("new")) == 1


def test_syntax_error_files_skipped(workspace):
    workspace.write("good.py", "def ok():\n    pass\n")
    workspace.write("bad.py", "def broken(:\n")
    mem = StructuralMemory()
    assert mem.index_workspace(workspace) == 1
