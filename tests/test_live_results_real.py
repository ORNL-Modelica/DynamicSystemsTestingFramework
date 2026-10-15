"""Live results against the real simulators: a slow test run through the
REAL persistent backend while its result file is polled once a second.

Nothing here is simulated: the worker's directory wipe, dymosim's lagging
header, OpenModelica's block writes and ``-mat_sync=1``, and the timeout
kill are the tools' own. The model takes wall time by pausing at every
sample (``sleep``), so a run is watchable on any machine.

Skipped without the tool; deselected in CI by the ``dymola`` / ``ompython``
markers. About two and a half minutes in all.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time as clock
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends import select_backend
from dstf.backends.common.live_mat import GONE, RUNNING, LiveMatReader, LiveSnapshot
from dstf.backends.common.mat_reader import (
    _scan_mat4_headers,
    mat_reaches_time,
    mat_shortfall,
    read_result_mat,
)
from dstf.backends.types import Capability, TestRunResult
from dstf.config import Config
from mat4_files import Trace

NAMES = ["x[1]", "y[2]"]

_MODEL = """within ;
package LiveLib
  model Slow "states and algebraics, and a pause at every sample so a run takes wall time"
    parameter Integer n=100;
    Real x[n](each start=1, each fixed=true);
    Real y[n];
    Integer rc(start=0, fixed=true);
  equation
    for i in 1:n loop
      der(x[i]) = -x[i] + sin(time*i);
      y[i] = x[i]^2 + time;
    end for;
    when sample(0, 0.05) then
      rc = Modelica.Utilities.System.command("sleep 0.05");
    end when;
  end Slow;
end LiveLib;
"""


def _dymola_available() -> bool:
    from dstf.backends.dymola.backend import DymolaBackend

    # The executable and its Python interface, as a run finds them.
    return DymolaBackend.locate({}).complete


def _ompython_available() -> bool:
    try:
        import OMPython  # noqa: F401
    except ImportError:
        return False
    return shutil.which("omc") is not None


_needs_sleep = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sleep") is None,
    reason="the slow model pauses with the POSIX sleep command",
)


def real_dymola(fn):
    fn = pytest.mark.skipif(not _dymola_available(), reason="needs Dymola")(fn)
    return pytest.mark.dymola(_needs_sleep(fn))


def real_openmodelica(fn):
    fn = pytest.mark.skipif(not _ompython_available(), reason="needs omc + OMPython")(
        fn
    )
    return pytest.mark.ompython(_needs_sleep(fn))


@dataclass
class Watch:
    """What watching the runs of one test gave."""

    path: Path
    backend: object
    test: object
    trace: Trace = field(default_factory=Trace)
    #: taken while a run was in progress: the snapshot, and the file's size
    #: and the count in its trajectory header read just BEFORE that poll (so
    #: a header seen under a poll short of the last row is never the final
    #: one)
    polls: list[tuple[LiveSnapshot, int | None, int | None]] = field(
        default_factory=list
    )
    runs: list[TestRunResult] = field(default_factory=list)


def _watch(tmp_path, backend_name: str, *, stop=10.0, timeout=300, repeat=1, **config):
    """Run ``LiveLib.Slow`` ``repeat`` times through the real persistent
    backend on a thread, polling its growing result file at 1 Hz with ONE
    reader from here."""
    lib = tmp_path / "LiveLib"
    lib.mkdir()
    (lib / "package.mo").write_text(_MODEL)
    cfg = Config(
        source_path=lib,
        reference_root=tmp_path / "refs",
        work_dir=tmp_path / "work",
        backend_name=backend_name,
        dependencies=["Modelica"] if backend_name == "OpenModelica" else [],
        timeout=timeout,
        **config,
    )
    test = make_test_model(
        "LiveLib.Slow",
        stop_time=stop,
        number_of_intervals=int(stop * 50),
        variable_patterns=NAMES,
    )
    backend = select_backend(cfg, persistent=True)
    assert Capability.LIVE_RESULTS in backend.capabilities
    path = backend.live_result_path(cfg.work_dir / "test_0001")
    watch = Watch(path=path, backend=backend, test=test)
    failure: list[BaseException] = []

    def run() -> None:
        try:
            for _ in range(repeat):
                (manifest,) = backend.run_tests([test])
                watch.runs.extend(manifest.results)
        except BaseException as exc:  # reported on the test's thread
            failure.append(exc)

    reader = LiveMatReader(path)
    thread = threading.Thread(target=run, daemon=True)
    start = clock.monotonic()
    thread.start()
    tick = 0
    while thread.is_alive():
        tick += 1
        clock.sleep(max(0.0, start + tick - clock.monotonic()))
        size = declared = None
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                declared = _scan_mat4_headers(f).declared
        except OSError:
            pass
        snap = watch.trace.take(reader.poll(NAMES))
        if thread.is_alive():
            watch.polls.append((snap, size, declared))
    thread.join()
    if failure:
        raise failure[0]
    watch.trace.take(reader.poll(NAMES))
    return watch


def _assert_trace_is_the_file(watch: Watch) -> int:
    """The samples the polls returned, concatenated, are the file's series
    as the full reader reads them now — bit for bit. Returns the rows."""
    full = read_result_mat(watch.path, set(NAMES))
    assert full is not None and set(full) == set(NAMES)
    for name in NAMES:
        t, v = watch.trace.series(name)
        assert t.dtype == full[name][0].dtype
        assert t.tobytes() == full[name][0].tobytes(), name
        assert v.tobytes() == full[name][1].tobytes(), name
    return len(full[NAMES[0]][0])


def _assert_grew_in_order(watch: Watch, run: int) -> None:
    mid = [s for s, _, _ in watch.polls if s.run == run and s.state == RUNNING]
    rows = [s.rows for s in mid]
    times = [s.time for s in mid if s.time is not None]
    assert rows == sorted(rows) and times == sorted(times)
    assert len(set(rows)) >= 3, f"the file was not seen growing: {rows}"


# ---------------------------------------------------------------------------
# Dymola
# ---------------------------------------------------------------------------


@real_dymola
def test_dymola_two_runs_watched_by_one_reader(tmp_path):
    """The trace grows while dymosim runs, the worker's wipe of the test
    directory before the second run reads as "gone" and a new run, and what
    the polls returned for that run is the finished file."""
    watch = _watch(tmp_path, "Dymola", repeat=2)
    assert [r.success for r in watch.runs] == [True, True]
    states = [s.state for s, _, _ in watch.polls]
    assert states[0] == GONE  # the file is not there before the first run
    assert {s.run for s, _, _ in watch.polls} == {0, 1}
    wiped = [s for s, _, _ in watch.polls if s.run == 1 and s.state == GONE]
    assert wiped, "the directory wipe was never seen"
    _assert_grew_in_order(watch, 0)
    _assert_grew_in_order(watch, 1)
    # dymosim's own header count is behind its rows for the whole run: what
    # a reader trusting it could not have followed
    lagging = [
        (s.rows, declared)
        for s, _, declared in watch.polls
        if s.state == RUNNING and declared is not None and declared < s.rows
    ]
    assert len(lagging) >= 3
    assert watch.trace.run == 1
    assert _assert_trace_is_the_file(watch) == 801
    assert mat_shortfall(watch.path) is None
    assert mat_reaches_time(watch.path, 10.0) == (True, 10.0)


@real_dymola
def test_dymola_timed_out_run_is_shown_and_not_passed(tmp_path):
    """Killed by the framework's own timeout a few seconds into a minute's
    run: the verdict is the timeout, the rows written are readable to where
    they stop, and reading the directory again does not turn them into a
    result."""
    watch = _watch(tmp_path, "Dymola", stop=60.0, timeout=10)
    (run,) = watch.runs
    assert run.success is False and run.timed_out is True
    assert run.error_message.startswith("Timed out after 10s")
    assert not (watch.path.parent / "dsfinal.txt").exists()

    rows = _assert_trace_is_the_file(watch)
    last = watch.trace.snapshots[-1]
    assert rows == last.rows > 100
    assert 1.0 < last.time < 60.0
    note = mat_shortfall(watch.path)
    assert note is not None
    assert note.startswith(f"Partial result: dsres.mat holds {rows} time points")
    assert mat_reaches_time(watch.path, 60.0) == (False, None)

    # a later read of the directory (compare, a reload): shown, not scored
    reread = watch.backend.read_result(watch.test, "test_0001", None)
    assert reread.success is False
    assert reread.error_message == note
    assert [v.name for v in reread.variables] == NAMES
    assert len(reread.variables[0].time) == rows


# ---------------------------------------------------------------------------
# OpenModelica
# ---------------------------------------------------------------------------


@real_openmodelica
def test_openmodelica_writes_in_blocks_unless_the_run_is_watched(tmp_path):
    """Without ``live_results`` the rows reach the file a 4 KB block at a
    time under a header that counts 0: polls go by with nothing new. With it
    (``-mat_sync=1``) every poll has new rows and the header keeps count.
    Either way the polls end as the finished file."""
    plain_dir, live_dir = tmp_path / "plain", tmp_path / "live"
    plain_dir.mkdir()
    live_dir.mkdir()

    plain = _watch(plain_dir, "OpenModelica")
    assert [r.success for r in plain.runs] == [True]
    rows = _assert_trace_is_the_file(plain)
    writing = [
        (size, declared) for s, size, declared in plain.polls if 0 < s.rows < rows
    ]
    assert len(writing) >= 4
    assert {declared for _, declared in writing} == {0}
    steps = [b - a for (a, _), (b, _) in zip(writing, writing[1:])]
    assert all(step % 4096 == 0 for step in steps), steps
    assert steps.count(0) >= 2 and max(steps) >= 4096, steps
    _assert_grew_in_order(plain, 0)

    live = _watch(live_dir, "OpenModelica", live_results=True)
    assert [r.success for r in live.runs] == [True]
    writing = [(s, declared) for s, _, declared in live.polls if 0 < s.rows < rows]
    assert len(writing) >= 6
    fresh = [len(s.series[NAMES[0]].time) for s, _ in writing]
    assert min(fresh) > 0, fresh  # no poll went by with nothing new
    assert sum(declared > 0 for _, declared in writing) >= len(writing) - 1
    _assert_grew_in_order(live, 0)
    assert _assert_trace_is_the_file(live) == rows  # the same result
    assert mat_shortfall(live.path) is None

    def call(watch: Watch) -> str:
        return (watch.path.parent / "omc_stdout.txt").read_text()

    assert "mat_sync" not in call(plain) and "-mat_sync=1" in call(live)


@real_openmodelica
@pytest.mark.parametrize("live", [False, True], ids=["plain", "watched"])
def test_openmodelica_timed_out_run_is_shown_and_not_passed(tmp_path, live):
    """Killed by the timeout mid-run. Plain: the rows to the last block
    written, under a header counting 0 — a partial result by its own file.
    Watched: the rows to the kill, under a header that kept count — what
    stops it passing is that it stopped short of the stop time."""
    watch = _watch(tmp_path, "OpenModelica", timeout=8, live_results=live)
    (run,) = watch.runs
    assert run.success is False and run.timed_out is True
    assert run.error_message.startswith("Timed out after 8s")

    rows = _assert_trace_is_the_file(watch)
    last = watch.trace.snapshots[-1]
    assert rows == last.rows > 100
    assert 1.0 < last.time < 10.0
    note = mat_shortfall(watch.path)
    reread = watch.backend.read_result(watch.test, "test_0001", None)
    if live:
        assert note is None
        reached, reached_time = mat_reaches_time(watch.path, 10.0)
        assert reached is False and reached_time == last.time
        # the file alone reads as a result; the run's recorded verdict is
        # what `read_results` fails it with
        assert reread.success is True
        recorded = watch.backend.read_last_results([watch.test])["LiveLib.Slow"]
        assert recorded.success is False
        assert recorded.error_message == "Run recorded timed_out"
        assert len(recorded.variables[0].time) == rows
    else:
        assert note.startswith(f"Partial result: result_res.mat holds {rows} time")
        assert "its header counts 0" in note
        assert mat_reaches_time(watch.path, 10.0) == (False, None)
        assert reread.success is False and reread.error_message == note
        assert len(reread.variables[0].time) == rows
        # what `compare` stores says the run's verdict AND the file's state
        recorded = watch.backend.read_last_results([watch.test])["LiveLib.Slow"]
        assert recorded.error_message == f"Run recorded timed_out — {note}"
    assert np.all(np.diff(watch.trace.series(NAMES[0])[0]) >= 0)
