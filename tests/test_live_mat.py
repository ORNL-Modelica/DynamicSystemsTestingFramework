"""The live reader: a result file polled while its run writes it.

Every row is served exactly once and in order, whatever the header says;
the file may be short, half-written, removed or recreated at any poll; and
nothing is held between polls. The synthetic files are the two tools'
layouts (``mat4_files``) written a few bytes at a time.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time as clock
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.common import live_mat
from dstf.backends.common.live_mat import (
    GONE,
    RUNNING,
    STARTING,
    LiveMatReader,
    LiveSnapshot,
)
from dstf.backends.common.mat_reader import read_result_mat
from mat4_files import ResultMat, Trace, write_result_mat

FIXTURES = Path(__file__).parent / "fixtures"
STORED = [
    FIXTURES / "results" / "Dymola" / "dsres.mat",
    FIXTURES / "results_openmodelica" / "pid_controller_res.mat",
]
NAMES = ["x", "y", "k", "x.neg", "k.neg"]
#: (tool, dtype, what the header counts while the run writes)
RUNS = [("dymola", "<f4", 1), ("dymola", "<f8", 1), ("openmodelica", "<f8", 0)]


def _result(path: Path, tool="dymola", dtype="<f4", n=40, scale=1.0, **kw) -> ResultMat:
    t = np.linspace(0.0, 4.0, n)
    return write_result_mat(
        path,
        tool=tool,
        dtype=dtype,
        time=t,
        trajectories={"x": scale * (np.sin(3.0 * t) + 0.25), "y": 10.0 - 7.0 * t},
        parameters={"k": 2.5 * scale},
        aliases={"x.neg": ("x", True), "k.neg": ("k", True)},
        **kw,
    )


def _same(got: np.ndarray, want: np.ndarray) -> bool:
    return (
        got.dtype == want.dtype
        and got.shape == want.shape
        and got.tobytes() == want.tobytes()
    )


def _assert_trace(trace: Trace, mat: ResultMat, rows: int | None = None) -> None:
    for name in NAMES:
        t, v = trace.series(name)
        assert _same(t, mat.time[:rows]), f"time of {name}"
        assert _same(v, mat.expected[name][:rows]), f"values of {name}"


def _open_handles(path: Path) -> list[str]:
    fds = Path("/proc/self/fd")
    held = []
    for fd in fds.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.split(" (deleted)")[0] == str(path):
            held.append(target)
    return held


# ---------------------------------------------------------------------------
# A growing file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "dtype", "declared"), RUNS)
def test_every_row_is_served_once_in_order_while_a_writer_appends(
    tmp_path, tool, dtype, declared
):
    """A writer thread appends the rows 37 bytes at a time — never a whole
    row per write — under a header that lags (Dymola) or says 0
    (OpenModelica), while the reader polls as fast as it can."""
    path = tmp_path / "res.mat"
    mat = _result(path, tool, dtype, n=300, points=0, declared=declared)
    payload = mat.rows(0)
    done = threading.Event()

    def write() -> None:
        with open(path, "ab", buffering=0) as f:
            for i in range(0, len(payload), 37):
                f.write(payload[i : i + 37])
                if i % (37 * 40) == 0:
                    clock.sleep(0.001)
        done.set()

    reader = LiveMatReader(path)
    trace = Trace()
    writer = threading.Thread(target=write)
    writer.start()
    try:
        while not done.is_set():
            trace.take(reader.poll(NAMES))
    finally:
        writer.join()
    last = trace.take(reader.poll(NAMES))

    assert trace.run == 0
    assert last.state == RUNNING and last.rows == 300
    assert last.time == float(mat.time[-1])
    _assert_trace(trace, mat)
    rows = [s.rows for s in trace.snapshots]
    assert rows == sorted(rows)
    times = [s.time for s in trace.snapshots if s.time is not None]
    assert times == sorted(times)
    # the polls did see the file grow: this is not one read at the end
    assert len({s.rows for s in trace.snapshots}) > 3


def test_a_half_written_row_waits_for_its_other_half(tmp_path):
    path = tmp_path / "res.mat"
    mat = _result(path, points=3, declared=1)
    half = mat.row_bytes // 2
    reader = LiveMatReader(path)
    trace = Trace()
    with open(path, "ab") as f:
        f.write(mat.rows(3, 4)[:half])
    snap = trace.take(reader.poll(NAMES))
    assert snap.rows == 3 and snap.time == float(mat.time[2])
    assert len(snap.series["x"].time) == 3
    again = trace.take(reader.poll(NAMES))  # nothing new: empty, not absent
    assert again.rows == 3 and len(again.series["x"].time) == 0
    with open(path, "ab") as f:
        f.write(mat.rows(3, 4)[half:])
    snap = trace.take(reader.poll(NAMES))
    assert snap.rows == 4 and len(snap.series["x"].time) == 1
    _assert_trace(trace, mat, 4)


@pytest.mark.parametrize(("tool", "dtype", "declared"), RUNS)
def test_the_file_is_starting_until_its_header_matrices_are_whole(
    tmp_path, tool, dtype, declared
):
    """Cut anywhere before the trajectory's rows the file says "starting";
    from there on it is running, with the whole rows it holds. No cut
    raises."""
    full = tmp_path / "full.mat"
    mat = _result(full, tool, dtype, n=5, declared=declared)
    raw = full.read_bytes()
    path = tmp_path / "res.mat"
    for cut in range(len(raw) + 1):
        path.write_bytes(raw[:cut])
        snap = LiveMatReader(path).poll(NAMES)
        if cut < mat.data_offset:
            assert snap == LiveSnapshot(STARTING), cut
        else:
            rows = (cut - mat.data_offset) // mat.row_bytes
            assert (snap.state, snap.rows) == (RUNNING, rows), cut
            assert snap.time == (float(mat.time[rows - 1]) if rows else None), cut
            assert _same(snap.series["x.neg"].values, mat.expected["x.neg"][:rows])


@pytest.mark.parametrize(("tool", "dtype", "declared"), RUNS)
def test_the_reader_lists_the_file_s_names_from_the_header_it_reads_through(
    tmp_path, monkeypatch, tool, dtype, declared
):
    """What `result_files.mat_names` lists of the same file — with no
    second parse of its header — and nothing before the header is whole."""
    from dstf.backends.common import mat_reader
    from dstf.backends.common.result_files import mat_names

    full = tmp_path / "full.mat"
    mat = _result(full, tool, dtype, n=5, points=2, declared=declared)
    listed = mat_names(full)
    raw = full.read_bytes()
    path = tmp_path / "res.mat"
    reader = LiveMatReader(path)
    assert reader.names is None
    path.write_bytes(raw[: mat.data_offset - 1])
    assert reader.poll(NAMES).state == STARTING and reader.names is None

    parsed: list[int] = []
    real = mat_reader._parse_name_matrix
    monkeypatch.setattr(
        mat_reader,
        "_parse_name_matrix",
        lambda matrix: parsed.append(1) or real(matrix),
    )
    path.write_bytes(raw)
    assert reader.poll(NAMES).state == RUNNING
    assert list(reader.names) == listed and "x" in listed
    assert reader.poll(NAMES).rows == 2 and list(reader.names) == listed
    assert parsed == [1]
    path.unlink()
    assert reader.poll(NAMES).state == GONE and reader.names is None


def test_one_reader_follows_a_file_from_nothing_to_its_rows(tmp_path):
    path = tmp_path / "res.mat"
    reader = LiveMatReader(path)
    assert reader.poll(NAMES) == LiveSnapshot(GONE)  # not created yet
    full = tmp_path / "full.mat"
    mat = _result(full, points=0, declared=1)
    raw = full.read_bytes()
    path.write_bytes(raw[:50])
    assert reader.poll(NAMES) == LiveSnapshot(STARTING)
    path.write_bytes(raw)
    snap = reader.poll(NAMES)
    assert (snap.state, snap.run, snap.rows, snap.time) == (RUNNING, 0, 0, None)
    assert set(snap.series) == set(NAMES)
    assert all(len(s.time) == 0 for s in snap.series.values())
    trace = Trace()
    with open(path, "ab") as f:
        f.write(mat.rows(0, 6))
    trace.take(reader.poll(NAMES))
    _assert_trace(trace, mat, 6)


def test_a_name_the_file_lacks_is_left_out(tmp_path):
    mat = _result(tmp_path / "res.mat")
    snap = LiveMatReader(mat.path).poll(["x", "no.such", "Time", "x"])
    assert list(snap.series) == ["x"]  # the abscissa is every series' time
    assert LiveMatReader(mat.path).poll().series == {}
    assert LiveMatReader(mat.path).poll().time == float(mat.time[-1])


def test_a_name_first_asked_for_late_gets_its_rows_from_the_start(tmp_path):
    """The page's visible variables change while a run is in progress: a
    name's samples, concatenated, are its series whenever it was first
    asked for — and a name not asked for at a poll loses nothing."""
    path = tmp_path / "res.mat"
    mat = _result(path, points=10, declared=1)
    reader = LiveMatReader(path)
    trace = Trace()
    trace.take(reader.poll(["x"]))
    with open(path, "ab") as f:
        f.write(mat.rows(10, 25))
    snap = trace.take(reader.poll(["x", "y", "k.neg"]))
    assert len(snap.series["x"].time) == 15
    assert len(snap.series["y"].time) == 25
    with open(path, "ab") as f:
        f.write(mat.rows(25, 40))
    trace.take(reader.poll(["y"]))  # x sits this one out
    trace.take(reader.poll(["x", "y", "k.neg"]))
    for name in ("x", "y", "k.neg"):
        t, v = trace.series(name)
        assert _same(t, mat.time) and _same(v, mat.expected[name])


# ---------------------------------------------------------------------------
# The same handling as the full reader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", STORED, ids=lambda p: p.parent.name)
def test_a_poll_of_a_whole_file_is_what_the_full_reader_reads(path):
    """Every variable of a stored result — aliases with their sign,
    parameters as constants — bit for bit, in the same precision."""
    full = read_result_mat(path)
    assert full is not None and len(full) > 1
    snap = LiveMatReader(path, max_rows=10**6).poll(list(full))
    assert list(snap.series) == list(full)
    for name, (t, v) in full.items():
        assert _same(snap.series[name].time, t), name
        assert _same(snap.series[name].values, v), name
    assert snap.rows == len(next(iter(full.values()))[0])


@pytest.mark.parametrize(("tool", "dtype", "declared"), RUNS)
def test_an_alias_keeps_its_sign_and_a_parameter_is_a_constant(
    tmp_path, tool, dtype, declared
):
    mat = _result(tmp_path / "res.mat", tool, dtype, declared=declared)
    s = LiveMatReader(mat.path).poll(NAMES).series
    assert np.array_equal(s["x.neg"].values, -s["x"].values)
    assert np.any(s["x"].values != 0)
    assert np.all(s["k"].values == np.dtype(dtype).type(2.5))
    assert np.all(s["k.neg"].values == np.dtype(dtype).type(-2.5))
    assert s["k"].values.shape == s["k"].time.shape == (40,)
    assert s["x"].values.dtype == s["x"].time.dtype == np.dtype(dtype)


# ---------------------------------------------------------------------------
# A new run under the same path
# ---------------------------------------------------------------------------


def test_a_deleted_file_is_gone_and_the_next_one_is_read_from_its_start(tmp_path):
    path = tmp_path / "res.mat"
    old = _result(path, n=40)
    reader = LiveMatReader(path)
    trace = Trace()
    assert trace.take(reader.poll(NAMES)).rows == 40
    path.unlink()
    gone = trace.take(reader.poll(NAMES))
    assert gone == LiveSnapshot(GONE, run=1)
    assert trace.take(reader.poll(NAMES)) == LiveSnapshot(GONE, run=1)  # once
    new = _result(path, n=12, scale=3.0)  # smaller, and other values
    snap = trace.take(reader.poll(NAMES))
    assert (snap.state, snap.run, snap.rows) == (RUNNING, 1, 12)
    _assert_trace(trace, new)
    assert not _same(new.expected["x"][:12], old.expected["x"][:12])


def test_the_directory_removed_between_polls(tmp_path):
    """What a persistent worker does before each run: the whole test
    directory goes, and comes back with the next run's file."""
    test_dir = tmp_path / "test_0001"
    test_dir.mkdir()
    path = test_dir / "dsres.mat"
    _result(path, n=40)
    reader = LiveMatReader(path)
    trace = Trace()
    assert trace.take(reader.poll(NAMES)).state == RUNNING
    shutil.rmtree(test_dir)  # nothing of ours holds it
    assert trace.take(reader.poll(NAMES)).state == GONE
    test_dir.mkdir()
    assert trace.take(reader.poll(NAMES)).state == GONE
    new = _result(path, n=9, scale=2.0, points=4, declared=1)
    assert trace.take(reader.poll(NAMES)).rows == 4
    with open(path, "ab") as f:
        f.write(new.rows(4, 9))
    snap = trace.take(reader.poll(NAMES))
    assert (snap.run, snap.rows) == (1, 9)
    _assert_trace(trace, new)


def test_a_file_that_got_smaller_is_another_run(tmp_path):
    """Truncated and rewritten in place — the same inode — with no poll
    having seen it missing."""
    path = tmp_path / "res.mat"
    _result(path, n=40)
    reader = LiveMatReader(path)
    trace = Trace()
    trace.take(reader.poll(NAMES))
    inode = path.stat().st_ino
    new = _result(path, n=12, scale=3.0)
    assert path.stat().st_ino == inode
    snap = trace.take(reader.poll(NAMES))
    assert (snap.state, snap.run, snap.rows) == (RUNNING, 1, 12)
    _assert_trace(trace, new)


def test_a_smaller_file_before_any_row_was_served_is_another_run(tmp_path):
    """With no row served there is no last row to check: the size alone
    says the header matrices on record belong to another file — here
    another model's, rewritten in place before the first row was read."""
    path = tmp_path / "res.mat"
    _result(path, n=40, points=0, declared=1)
    reader = LiveMatReader(path)
    first = reader.poll(NAMES)
    assert (first.state, first.run, first.rows) == (RUNNING, 0, 0)
    size, inode = path.stat().st_size, path.stat().st_ino
    t = np.linspace(0.0, 1.0, 6)
    new = write_result_mat(path, time=t, trajectories={"x": 5.0 * t})
    assert path.stat().st_size < size and path.stat().st_ino == inode
    snap = reader.poll(NAMES)
    assert (snap.state, snap.run, snap.rows) == (RUNNING, 1, 6)
    assert list(snap.series) == ["x"]
    assert _same(snap.series["x"].values, new.expected["x"])


def test_another_inode_is_another_run_even_when_it_continues_the_old_one(tmp_path):
    """A replacement that is larger and whose first rows ARE the old file's
    — nothing but its identity says it is a different file."""
    path = tmp_path / "res.mat"
    mat = _result(path, n=40, points=25, declared=1)
    reader = LiveMatReader(path)
    trace = Trace()
    assert trace.take(reader.poll(NAMES)).rows == 25
    staged = tmp_path / "staged.mat"
    _result(staged, n=40, declared=1)
    assert staged.stat().st_ino != path.stat().st_ino
    os.replace(staged, path)
    snap = trace.take(reader.poll(NAMES))
    assert (snap.state, snap.run, snap.rows) == (RUNNING, 1, 40)
    assert len(snap.series["x"].time) == 40  # from its start, not 15 more
    _assert_trace(trace, mat)


@pytest.mark.parametrize("asked", [NAMES, []], ids=["names", "no-names"])
def test_the_same_inode_grown_past_the_old_file_is_another_run(tmp_path, asked):
    """ext4 hands a recreated file the inode of the one just removed, and a
    quick rerun can outgrow the old file between two polls: the size and
    the identity say "the same file, longer". The last row served no longer
    reading back as it did is what says otherwise."""
    path = tmp_path / "res.mat"
    _result(path, n=40, points=10, declared=1)
    reader = LiveMatReader(path)
    trace = Trace()
    assert trace.take(reader.poll(asked)).rows == 10
    inode = path.stat().st_ino
    new = _result(path, n=40, scale=3.0, declared=1)  # rewritten in place
    assert path.stat().st_ino == inode
    snap = trace.take(reader.poll(NAMES))
    assert (snap.run, snap.rows) == (1, 40)
    _assert_trace(trace, new)


def test_an_unreadable_file_is_no_news_not_a_new_run(tmp_path, monkeypatch):
    """A sharing violation or a mount hiccup at one poll: the reader keeps
    its place and the next poll carries on."""
    path = tmp_path / "res.mat"
    mat = _result(path, n=40, points=10, declared=1)
    reader = LiveMatReader(path)
    trace = Trace()
    trace.take(reader.poll(NAMES))
    real_open = open

    def denied(file, *a, **kw):
        if Path(file) == path:
            raise PermissionError(13, "sharing violation")
        return real_open(file, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr("builtins.open", denied)
        snap = trace.take(reader.poll(NAMES))
    assert (snap.state, snap.run, snap.rows, dict(snap.series)) == (RUNNING, 0, 10, {})
    with real_open(path, "ab") as f:
        f.write(mat.rows(10, 40))
    assert trace.take(reader.poll(NAMES)).run == 0
    _assert_trace(trace, mat)


# ---------------------------------------------------------------------------
# What a poll costs and holds
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc/self/fd")
def test_no_handle_is_held_between_polls(tmp_path):
    path = tmp_path / "res.mat"
    mat = _result(path, points=10, declared=1)
    with open(path, "rb") as probe:
        assert _open_handles(path)  # the probe sees a held handle
        assert probe.read(1)
    reader = LiveMatReader(path)
    for last in (20, 30, 40):
        reader.poll(NAMES)
        assert _open_handles(path) == []
        with open(path, "ab") as f:
            f.write(mat.rows(last - 10, last))
    path.unlink()
    reader.poll(NAMES)
    assert _open_handles(path) == []


def test_a_poll_maps_nothing_into_memory(tmp_path, monkeypatch):
    """A map over a file truncated in place kills the process."""
    mat = _result(tmp_path / "res.mat")

    def no_map(*a, **kw):
        raise AssertionError("the live reader must not memory-map the file")

    monkeypatch.setattr(np, "memmap", no_map)
    import mmap

    monkeypatch.setattr(mmap, "mmap", no_map)
    assert LiveMatReader(mat.path).poll(NAMES).rows == 40


def test_a_steady_poll_is_one_read_of_the_new_rows(tmp_path, monkeypatch):
    """After the first poll: one read, starting at the last row served and
    as long as the rows that came since — never the file from its start."""
    path = tmp_path / "res.mat"
    mat = _result(path, n=400, points=300, declared=1)
    reader = LiveMatReader(path)
    reader.poll(NAMES)
    reads: list[tuple[int, int]] = []
    real_read = live_mat._read

    def spy(f, offset, n):
        reads.append((offset, n))
        return real_read(f, offset, n)

    monkeypatch.setattr(live_mat, "_read", spy)
    before = 300
    for last in (301, 340, 400, 400):
        with open(path, "ab") as f:
            f.write(mat.rows(before, last))
        reads.clear()
        assert reader.poll(NAMES).rows == last
        # from the last row served, through the newest
        assert reads == [
            (
                mat.data_offset + (before - 1) * mat.row_bytes,
                (last - before + 1) * mat.row_bytes,
            )
        ]
        before = last


@pytest.mark.parametrize("read_bytes", [16 << 20, 64], ids=["one-read", "row-by-row"])
def test_a_long_backlog_is_thinned_to_the_cap_and_ends_on_the_newest_row(
    tmp_path, monkeypatch, read_bytes
):
    """The first poll of a run long under way: ``max_rows`` samples, evenly
    spaced, the newest row among them — read in one piece and thinned, or a
    row at a time when the span is too long for one read. The rows after
    that poll come whole."""
    path = tmp_path / "res.mat"
    mat = _result(path, n=1100, points=1003, declared=1)
    assert mat.row_bytes <= 64 < 1003 * mat.row_bytes
    monkeypatch.setattr(live_mat, "_READ_BYTES", max(read_bytes, mat.row_bytes * 100))
    if read_bytes == 64:
        # room for the cap's 100 rows, not for the backlog's span
        assert live_mat._READ_BYTES < 1003 * mat.row_bytes
    reader = LiveMatReader(path, max_rows=100)
    trace = Trace()
    snap = trace.take(reader.poll(NAMES))
    assert snap.rows == 1003 and snap.time == float(mat.time[1002])
    t = snap.series["x"].time
    assert 90 <= len(t) <= 100
    picked = np.searchsorted(mat.time, t)
    assert _same(mat.time[picked], t)
    assert picked[-1] == 1002
    assert len(set(np.diff(picked))) == 1 and picked[0] < np.diff(picked)[0]
    for name in NAMES:
        assert _same(snap.series[name].values, mat.expected[name][picked]), name
    with open(path, "ab") as f:
        f.write(mat.rows(1003, 1100))
    snap = trace.take(reader.poll(NAMES))
    assert _same(snap.series["x"].time, mat.time[1003:1100])
    assert _same(snap.series["x.neg"].values, mat.expected["x.neg"][1003:1100])


def test_wide_rows_lower_the_cap_so_a_read_stays_bounded(tmp_path, monkeypatch):
    path = tmp_path / "res.mat"
    mat = _result(path, n=200)
    monkeypatch.setattr(live_mat, "_READ_BYTES", 10 * mat.row_bytes)
    reads: list[int] = []
    real_read = live_mat._read

    def spy(f, offset, n):
        reads.append(n)
        return real_read(f, offset, n)

    monkeypatch.setattr(live_mat, "_read", spy)
    snap = LiveMatReader(path, max_rows=2000).poll(NAMES)
    assert snap.rows == 200 and len(snap.series["x"].time) == 10
    assert snap.series["x"].time[-1] == mat.time[-1]
    assert max(reads) <= 10 * mat.row_bytes


def test_max_rows_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="max_rows"):
        LiveMatReader(tmp_path / "res.mat", max_rows=0)


def test_a_snapshot_cannot_be_changed(tmp_path):
    mat = _result(tmp_path / "res.mat")
    snap = LiveMatReader(mat.path).poll(NAMES)
    with pytest.raises(AttributeError):
        snap.rows = 0  # type: ignore[misc]
    with pytest.raises(TypeError):
        snap.series["z"] = snap.series["x"]  # type: ignore[index]
    with pytest.raises(ValueError):
        snap.series["x"].values[0] = 1.0
    with pytest.raises(ValueError):
        snap.series["k"].time[0] = 1.0


def test_a_file_that_is_not_a_result_never_raises(tmp_path):
    path = tmp_path / "res.mat"
    rng = np.random.default_rng(7)
    for n in (0, 1, 19, 20, 21, 500, 5000):
        path.write_bytes(rng.bytes(n))
        assert LiveMatReader(path).poll(NAMES).state == STARTING
    binnormal = _result(path, aclass="binNormal")
    assert LiveMatReader(binnormal.path).poll(NAMES) == LiveSnapshot(STARTING)
