"""``Backend.quiet`` — a run narrates the console only when asked.

The tool runs the pipeline inside a UI process, where the backend's
"Running N tests..." / progress-bar / "Reading N result files..." narration
goes to a console nobody is reading. ``quiet`` silences exactly that; the
CLI's backend leaves it False and prints as before.

Both tests drive the real Python backend over a copy of
``examples/python/PythonTestingLib`` (``ConstantCsv`` only — no scipy
needed, ~0.5 s), so what is asserted is the console of a genuine run.
"""

from __future__ import annotations

import pytest

from conftest import python_example_copy

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"


@pytest.fixture
def constant_csv(tmp_path):
    """(config, [ConstantCsv]) over a temporary copy of the Python example."""
    config, tests = python_example_copy(tmp_path)
    keep = [t for t in tests if t.model_id == CONSTANT_CSV]
    assert keep, f"{CONSTANT_CSV} not discovered in the example library"
    return config, keep


def test_quiet_backend_runs_and_reads_silently(constant_csv, capsys):
    """quiet=True: a full run + read leaves both streams empty."""
    from dstf.backends import get_backend

    config, tests = constant_csv
    backend = get_backend(config)
    backend.quiet = True

    backend.run_tests(tests)
    results = backend.read_last_results(tests)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""

    result = results[CONSTANT_CSV]
    assert result.success, result.error_message
    assert len(result.variables) == 1


def test_loud_backend_narrates_the_same_run(constant_csv, capsys):
    """The default (quiet=False) still prints — so the flag silenced it,
    rather than the prints having moved somewhere else."""
    from dstf.backends import get_backend

    config, tests = constant_csv
    backend = get_backend(config)
    assert backend.quiet is False

    backend.run_tests(tests)
    backend.read_last_results(tests)
    captured = capsys.readouterr()

    assert "Running 1 tests" in captured.err
    assert "Reading 1 result files" in captured.out
