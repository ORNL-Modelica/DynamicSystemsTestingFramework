"""A UI test's Shell, page tree and Session go with it (the suite's run
time grew with every one kept: each NiceGUI simulation runs a full
`gc.collect()` on entry and exit). `ui_leak_pages.py` opens the Test page
on a fresh Shell in several tests in a row, in a pytest of its own so that
no distribution of this suite splits the sequence, and its last test counts
the Shells still alive."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("nicegui")

TESTS = Path(__file__).parent


def test_a_ui_test_s_shell_is_collected_after_it():
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "COV_CORE_"))
    }
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            str(TESTS / "ui_leak_pages.py"),
        ],
        cwd=TESTS.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
