import pytest

from hyphae.llm import extract_json, parse_file_blocks, strip_thinking


def test_strip_thinking():
    text = "<think>let me reason\nabout this</think>\nThe answer is 4."
    assert strip_thinking(text) == "The answer is 4."


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = 'Here is the plan:\n```json\n{"a": [1, 2]}\n```\nDone.'
    assert extract_json(text) == {"a": [1, 2]}


def test_extract_json_embedded_in_prose():
    text = 'Sure! {"files": [{"path": "x.py", "content": "a = \\"{\\""}]} hope that helps'
    assert extract_json(text) == {"files": [{"path": "x.py", "content": 'a = "{"'}]}


def test_extract_json_after_thinking():
    text = '<think>{"draft": true}</think>{"final": true}'
    assert extract_json(text) == {"final": True}


def test_extract_json_array():
    assert extract_json("the list: [1, 2, 3] ok") == [1, 2, 3]


def test_extract_json_nothing_found():
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("I could not produce output, sorry.")


def test_parse_file_blocks_basic():
    text = (
        "### FILE: src/app.py\n"
        "```python\n"
        "def main():\n"
        "    return 1\n"
        "```\n"
        "\n"
        "### NOTES\n"
        "Implemented main.\n"
    )
    files, notes = parse_file_blocks(text)
    assert files == {"src/app.py": "def main():\n    return 1\n"}
    assert notes == "Implemented main."


def test_parse_file_blocks_multiple_files():
    text = (
        "### FILE: a.py\n```\nx = 1\n```\n"
        "### FILE: b.py\n```\ny = 2\n```\n"
        "### NOTES\ntwo files\n"
    )
    files, notes = parse_file_blocks(text)
    assert files == {"a.py": "x = 1\n", "b.py": "y = 2\n"}
    assert notes == "two files"


def test_parse_file_blocks_content_with_triple_quotes():
    # The exact failure mode that breaks the JSON contract on coder models:
    # multi-line code with Python triple-quoted strings.
    text = (
        "### FILE: doc.py\n"
        "```python\n"
        'def f():\n    """Docstring with ```nothing``` special."""\n    return 1\n'
        "```\n"
        "### NOTES\nok\n"
    )
    files, _ = parse_file_blocks(text)
    assert '"""Docstring' in files["doc.py"]


def test_parse_file_blocks_unfenced_body_tolerated():
    text = "### FILE: a.py\nx = 1\n### NOTES\nforgot the fences\n"
    files, notes = parse_file_blocks(text)
    assert files == {"a.py": "x = 1\n"}
    assert notes == "forgot the fences"


def test_parse_file_blocks_notes_only_analysis():
    files, notes = parse_file_blocks("### NOTES\nThe theme system uses CSS vars.\n")
    assert files == {}
    assert notes == "The theme system uses CSS vars."


def test_parse_file_blocks_strips_thinking():
    text = "<think>### FILE: ghost.py\nnope</think>### NOTES\nclean\n"
    files, notes = parse_file_blocks(text)
    assert files == {}
    assert notes == "clean"


def test_parse_file_blocks_returns_none_without_headers():
    assert parse_file_blocks('{"files": [], "notes": "json contract"}') is None
    assert parse_file_blocks("just prose") is None
