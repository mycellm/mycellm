from hyphae.validate import validate_files


def test_valid_python_passes():
    report = validate_files({"a.py": "def f():\n    return 1\n"})
    assert report.ok


def test_python_syntax_error_caught():
    report = validate_files({"a.py": "def broken(:\n    pass\n"})
    assert not report.ok
    assert "a.py" in report.summary()
    assert "syntax error" in report.summary()


def test_invalid_json_caught():
    report = validate_files({"config.json": '{"a": }'})
    assert not report.ok
    assert "invalid JSON" in report.summary()


def test_invalid_toml_caught():
    report = validate_files({"x.toml": "a = = 1"})
    assert not report.ok


def test_empty_file_caught():
    report = validate_files({"a.py": "   \n"})
    assert not report.ok
    assert "empty" in report.summary()


def test_unknown_extension_passes():
    report = validate_files({"notes.md": "# anything goes"})
    assert report.ok
