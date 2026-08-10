from hyphae.testing import has_tests, run_tests


def test_has_tests_detection(workspace):
    workspace.write("module.py", "x = 1\n")
    assert not has_tests(workspace)
    workspace.write("test_module.py", "def test_x():\n    assert True\n")
    assert has_tests(workspace)


async def test_no_tests_reports_not_ran(workspace):
    workspace.write("module.py", "x = 1\n")
    report = await run_tests(workspace)
    assert not report.ran
    assert report.ok


async def test_passing_suite(workspace):
    workspace.write("test_ok.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    report = await run_tests(workspace)
    assert report.ran
    assert report.ok
    assert "pytest" in report.command


async def test_failing_suite_captures_output(workspace):
    workspace.write(
        "test_bad.py",
        "def test_bad():\n    assert 1 + 1 == 3, 'arithmetic is broken'\n",
    )
    report = await run_tests(workspace)
    assert report.ran
    assert not report.ok
    assert "arithmetic is broken" in report.output


async def test_import_failure_is_a_test_failure(workspace):
    # The exact defect from the live run: a test that doesn't import the
    # module under test passes; one that imports a missing symbol fails.
    workspace.write("test_imports.py", "from missing_module import nope\n")
    report = await run_tests(workspace)
    assert report.ran
    assert not report.ok


async def test_explicit_command(workspace):
    report = await run_tests(workspace, command="true")
    assert report.ran
    assert report.ok
    report = await run_tests(workspace, command="false")
    assert report.ran
    assert not report.ok


async def test_timeout_kills_run(workspace):
    report = await run_tests(workspace, command="sleep 5", timeout=0.3)
    assert report.ran
    assert not report.ok
    assert "timed out" in report.output


def test_snapshot_and_restore(workspace):
    workspace.write("kept.py", "original\n")
    snap = workspace.snapshot(["kept.py", "new.py"])
    workspace.write("kept.py", "modified\n")
    workspace.write("new.py", "created\n")
    workspace.restore(snap)
    assert workspace.read("kept.py") == "original\n"
    assert not workspace.exists("new.py")
