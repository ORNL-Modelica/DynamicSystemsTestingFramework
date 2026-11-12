"""`--shard I/N`: CI's browser job runs a third of the tests on each of three runners.

Each case runs pytest in a subprocess over a small generated test file, with
the shard plugin (`tests/_shard.py`) loaded by `-p`, and reads which tests it
collected or ran.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent

# Ten tests, t0 .. t9; the even ones carry a `slow` mark.
TEN_TESTS = """
import pytest

@pytest.mark.parametrize(
    "i", [pytest.param(i, marks=pytest.mark.slow) if i % 2 == 0 else i for i in range(10)]
)
def test_t(i):
    pass
"""


def _pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    (tmp_path / "test_ten.py").write_text(TEN_TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: a slow test\n")
    env = {**os.environ, "PYTHONPATH": str(TESTS_DIR)}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "_shard",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _collected(tmp_path: Path, *args: str) -> list[int]:
    proc = _pytest(tmp_path, "--collect-only", "-q", *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [
        int(line.split("[")[1].rstrip("]"))
        for line in proc.stdout.splitlines()
        if "::" in line
    ]


def test_the_shards_deal_the_tests_round_robin(tmp_path):
    assert _collected(tmp_path, "--shard", "1/3") == [0, 3, 6, 9]
    assert _collected(tmp_path, "--shard", "2/3") == [1, 4, 7]
    assert _collected(tmp_path, "--shard", "3/3") == [2, 5, 8]


def test_no_shard_runs_every_test(tmp_path):
    assert _collected(tmp_path) == list(range(10))


def test_a_marker_selection_is_applied_before_the_shards(tmp_path):
    # `-m slow` leaves t0 t2 t4 t6 t8; shard 1/2 takes the 1st, 3rd and 5th of those.
    # Sharding the ten first would give t0 t2 t4 t6 t8 again.
    assert _collected(tmp_path, "-m", "slow", "--shard", "1/2") == [0, 4, 8]
    assert _collected(tmp_path, "-m", "slow", "--shard", "2/2") == [2, 6]


def test_every_xdist_worker_runs_the_same_shard(tmp_path):
    proc = _pytest(tmp_path, "-n", "2", "-v", "--shard", "2/3")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    passed = sorted(
        int(m) for m in re.findall(r"PASSED test_ten\.py::test_t\[(\d+)\]", proc.stdout)
    )
    # xdist refuses a run whose workers collected different tests, so the run
    # passing with these three is each worker keeping the same three.
    assert passed == [1, 4, 7]


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "x", "1", "1/3/3", "-1/3"])
def test_a_bad_shard_is_a_usage_error_naming_the_option(tmp_path, value):
    proc = _pytest(tmp_path, "--collect-only", "-q", f"--shard={value}")
    assert proc.returncode == pytest.ExitCode.USAGE_ERROR, proc.stdout + proc.stderr
    assert "--shard" in proc.stderr
    assert value in proc.stderr
