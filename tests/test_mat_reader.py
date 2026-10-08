"""The one ``.mat`` reader: the trajectory's row count comes from the file's
SIZE, never from the count in its header.

Three things are pinned here. A complete file reads exactly as its header
says it should (the repo's own Dymola and OpenModelica results, and
synthetic ones in both layouts and precisions). A file that is short — cut
anywhere, or a run in progress whose header lags its rows — yields the
whole rows it holds and never raises. And a verdict still wants a FINISHED
file: a partial result is shown, never scored and never rescued as a pass.
"""

from __future__ import annotations

import logging
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.common.mat_reader import (
    list_result_mat_variables,
    mat_reaches_time,
    mat_shortfall,
    read_mat_time_extents,
    read_result_mat,
)
from mat4_files import ResultMat, write_result_mat

FIXTURES = Path(__file__).parent / "fixtures"
STORED = [
    FIXTURES / "results" / "Dymola" / "dsres.mat",
    FIXTURES / "results_openmodelica" / "pid_controller_res.mat",
]
_DTYPES = {0: "<f8", 1: "<f4", 2: "<i4", 3: "<i2", 4: "<u2", 5: "<u1"}


@pytest.fixture(autouse=True)
def _quiet_reader(caplog):
    # A short file is logged at ERROR by design; hundreds of cuts would
    # bury a real failure's output.
    caplog.set_level(logging.CRITICAL, logger="dstf.backends.common.mat_reader")


def _by_header(path: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """The file read the way its HEADERS describe it — every matrix as large
    as its header counts, nothing taken from the file's size. An independent
    reading to hold the reader to on a complete file."""
    raw = path.read_bytes()
    matrices: dict[str, np.ndarray] = {}
    pos = 0
    while pos + 20 <= len(raw):
        mopt, mrows, ncols, _imagf, namlen = struct.unpack_from("<5i", raw, pos)
        name = raw[pos + 20 : pos + 20 + namlen].rstrip(b"\x00").decode("latin1")
        dtype = np.dtype(_DTYPES[(mopt % 100) // 10])
        start = pos + 20 + namlen
        matrices[name] = np.frombuffer(
            raw, dtype=dtype, count=mrows * ncols, offset=start
        ).reshape((mrows, ncols), order="F")
        pos = start + mrows * ncols * dtype.itemsize
    assert pos == len(raw), "the fixture is not a complete file"
    names = [
        "".join(chr(int(c)) for c in column).strip("\x00").strip()
        for column in matrices["name"].T
    ]
    data_1, data_2 = matrices["data_1"], matrices["data_2"]
    time = data_2[0, :]
    out = {}
    for name, (matrix, column, _interp, _protected) in zip(
        names, matrices["dataInfo"].T
    ):
        if column == 0 or matrix not in (1, 2):
            continue
        row = abs(int(column)) - 1
        values = (
            data_2[row, :] if matrix == 2 else np.full_like(time, float(data_1[row, 0]))
        )
        out[name] = (time, -values if column < 0 else values)
    return out


def _same(got: np.ndarray, want: np.ndarray) -> bool:
    return (
        got.dtype == want.dtype
        and got.shape == want.shape
        and got.tobytes() == want.tobytes()
    )


def _assert_series(got, want) -> None:
    assert got is not None
    assert list(got) == list(want)
    for name in want:
        assert _same(got[name][0], want[name][0]), f"time of {name}"
        assert _same(got[name][1], want[name][1]), f"values of {name}"


def _synthetic(path: Path, tool: str, dtype: str, n: int, **kw) -> ResultMat:
    t = np.linspace(0.0, 2.0, n) if n else np.zeros(0)
    return write_result_mat(
        path,
        tool=tool,
        dtype=dtype,
        time=t,
        trajectories={"x": np.sin(3.0 * t) + 0.25, "y": 10.0 - 7.0 * t},
        parameters={"k": 2.5, "m": -0.125},
        aliases={
            "x.neg": ("x", True),
            "y.same": ("y", False),
            "k.neg": ("k", True),
        },
        **kw,
    )


def _expected(mat: ResultMat, rows: int | None = None):
    return {n: (mat.time[:rows], v[:rows]) for n, v in mat.expected.items()}


LAYOUTS = [("dymola", "<f4"), ("dymola", "<f8"), ("openmodelica", "<f8")]


# ---------------------------------------------------------------------------
# A complete file reads as its header says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", STORED, ids=lambda p: p.parent.name)
def test_a_stored_result_reads_as_its_header_describes_it(path):
    want = _by_header(path)
    assert len(want) > 1
    _assert_series(read_result_mat(path), want)
    some = set(list(want)[::2])
    _assert_series(
        read_result_mat(path, variable_names=some),
        {n: s for n, s in want.items() if n in some},
    )
    last = next(iter(want.values()))[0]
    assert read_mat_time_extents(path) == (float(last[0]), float(last[-1]))
    assert mat_shortfall(path) is None


@pytest.mark.parametrize(("tool", "dtype"), LAYOUTS)
@pytest.mark.parametrize("n", [1, 2, 7])
def test_a_synthetic_result_reads_back(tmp_path, tool, dtype, n):
    mat = _synthetic(tmp_path / "r.mat", tool, dtype, n)
    got = read_result_mat(mat.path)
    _assert_series(got, _expected(mat))
    _assert_series(_by_header(mat.path), _expected(mat))  # the helper is honest
    # the alias sign and the parameter, by value
    assert np.array_equal(got["x.neg"][1], -got["x"][1])
    assert np.all(got["k"][1] == np.dtype(dtype).type(2.5))
    assert np.all(got["k.neg"][1] == np.dtype(dtype).type(-2.5))
    assert got["x"][1].dtype == np.dtype(dtype)
    assert mat_shortfall(mat.path) is None
    assert read_mat_time_extents(mat.path) == (0.0, float(mat.time[-1]))


@pytest.mark.parametrize(("tool", "dtype"), LAYOUTS)
def test_a_result_with_no_row_reads_as_empty_series(tmp_path, tool, dtype):
    """A run that has written its header and no row yet: every variable is
    there, with nothing in it. It is not a result — no extents, a shortfall."""
    mat = _synthetic(tmp_path / "r.mat", tool, dtype, 0)
    got = read_result_mat(mat.path)
    assert got is not None and set(got) == set(mat.expected)
    assert all(len(t) == 0 and len(v) == 0 for t, v in got.values())
    assert read_mat_time_extents(mat.path) is None
    assert mat_reaches_time(mat.path, 0.0) == (False, None)
    assert "no time point" in mat_shortfall(mat.path)


# ---------------------------------------------------------------------------
# The count comes from the size: a header that lags, or says 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "dtype"), LAYOUTS)
@pytest.mark.parametrize("declared", [0, 1, 3, 7, 50])
def test_the_rows_read_are_the_rows_present_whatever_the_header_counts(
    tmp_path, tool, dtype, declared
):
    """OpenModelica's header counts 0 until the run ends, Dymola's starts at
    1 and lags; a header counting MORE than the file holds is a cut file."""
    mat = _synthetic(tmp_path / "r.mat", tool, dtype, 7, declared=declared)
    _assert_series(read_result_mat(mat.path), _expected(mat))
    _assert_series(
        read_result_mat(mat.path, variable_names={"x.neg", "k"}),
        {n: s for n, s in _expected(mat).items() if n in {"x.neg", "k"}},
    )


def test_the_row_count_rounds_down_to_whole_rows(tmp_path):
    """A row being written is not a row: one byte short of the next row
    reads as the rows before it, one byte into it reads the same."""
    mat = _synthetic(tmp_path / "r.mat", "dymola", "<f4", 7, points=4)
    head = mat.path.read_bytes()
    for extra in (1, mat.row_bytes // 2, mat.row_bytes - 1):
        mat.path.write_bytes(head + mat.rows(4, 5)[:extra])
        _assert_series(read_result_mat(mat.path), _expected(mat, 4))
    mat.path.write_bytes(head + mat.rows(4, 5))
    _assert_series(read_result_mat(mat.path), _expected(mat, 5))


# ---------------------------------------------------------------------------
# Truncation: every cut of a file, class by class
# ---------------------------------------------------------------------------


def _cut_classes(mat: ResultMat, size: int) -> dict[str, list[int]]:
    """Every cut of the file, sorted into the classes a reader can meet."""
    name_end = mat.spans["name"][2]
    d1_start, _d1_data, d1_end = mat.spans["data_1"]
    d2_start, d2_data, _ = mat.spans["data_2"]
    classes: dict[str, list[int]] = {
        "before the names are whole": [],
        "inside a later header matrix": [],
        "inside data_1": [],
        "inside data_2's header": [],
        "on a row boundary": [],
        "mid-row": [],
    }
    for cut in range(size + 1):
        if cut < name_end:
            key = "before the names are whole"
        elif cut < d1_start:
            key = "inside a later header matrix"
        elif cut < d1_end:
            key = "inside data_1"
        elif cut < d2_data:
            key = "inside data_2's header"
        elif (cut - d2_data) % mat.row_bytes == 0:
            key = "on a row boundary"
        else:
            key = "mid-row"
        classes[key].append(cut)
    assert all(classes.values()), "a cut class with no cut in it tests nothing"
    return classes


@pytest.mark.parametrize(("tool", "dtype"), LAYOUTS)
def test_every_cut_of_a_file_reads_the_whole_rows_present(tmp_path, tool, dtype):
    mat = _synthetic(tmp_path / "full.mat", tool, dtype, 5)
    raw = mat.path.read_bytes()
    full = read_result_mat(mat.path)
    names = list_result_mat_variables(mat.path)
    cut_path = tmp_path / "cut.mat"
    for label, cuts in _cut_classes(mat, len(raw)).items():
        for cut in cuts:
            cut_path.write_bytes(raw[:cut])
            got = read_result_mat(cut_path)  # never raises
            listed = list_result_mat_variables(cut_path)
            where = f"{label}, cut at {cut} of {len(raw)}"
            if label in ("on a row boundary", "mid-row"):
                rows = (cut - mat.data_offset) // mat.row_bytes
                assert got is not None, where
                assert list(got) == list(full), where
                for n in full:  # prefix-identical
                    assert _same(got[n][0], full[n][0][:rows]), where
                    assert _same(got[n][1], full[n][1][:rows]), where
            else:
                assert got is None, where
            if label == "before the names are whole":
                assert listed is None, where
            else:
                assert listed == names, where
            # a verdict wants the finished file, and only that
            finished = cut == len(raw)
            assert (read_mat_time_extents(cut_path) is not None) == finished, where
            assert mat_reaches_time(cut_path, float(mat.time[-1]))[0] == finished, where
            assert (mat_shortfall(cut_path) is None) == finished, where


@pytest.mark.parametrize("path", STORED, ids=lambda p: p.parent.name)
def test_a_stored_result_cut_short_reads_its_whole_rows(tmp_path, path):
    raw = path.read_bytes()
    full = read_result_mat(path)
    with open(path, "rb") as f:
        from dstf.backends.common.mat_reader import _scan_mat4_headers

        offset, dtype, n_vars, points = _scan_mat4_headers(f).blocks["data_2"]
    row_bytes = n_vars * dtype.itemsize
    assert offset + points * row_bytes == len(raw)
    cut_path = tmp_path / "cut.mat"
    for rows in (0, 1, points // 2, points - 1):
        for extra in (0, 1, row_bytes - 1):
            cut_path.write_bytes(raw[: offset + rows * row_bytes + extra])
            got = read_result_mat(cut_path)
            assert got is not None and list(got) == list(full)
            for n in full:
                assert _same(got[n][0], full[n][0][:rows])
                assert _same(got[n][1], full[n][1][:rows])
            assert read_mat_time_extents(cut_path) is None
    for cut in (0, 19, 20, 500, offset - 1):
        cut_path.write_bytes(raw[:cut])
        assert read_result_mat(cut_path) is None


# ---------------------------------------------------------------------------
# Verdicts: a finished file, by the header's own count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "dtype"), LAYOUTS)
def test_a_finished_file_reaches_the_time_it_reached(tmp_path, tool, dtype):
    mat = _synthetic(tmp_path / "r.mat", tool, dtype, 7)
    assert mat_reaches_time(mat.path, 2.0) == (True, 2.0)
    assert mat_reaches_time(mat.path, 2.0 + 1e-6) == (True, 2.0)  # the tolerance
    assert mat_reaches_time(mat.path, 2.1) == (False, 2.0)
    assert mat_reaches_time(tmp_path / "missing.mat", 2.0) == (False, None)


@pytest.mark.parametrize(
    ("points", "declared", "says"),
    [
        # every row to the stop time is on disk and the header was never
        # finished: OpenModelica killed after its last row (count still 0),
        # Dymola killed there (count lagging). The rows are readable now;
        # the run still did not finish.
        (7, 0, "its header counts 0"),
        (7, 4, "its header counts 4"),
        # cut short: fewer rows than the header counts
        (4, 7, "its header counts 7"),
        (0, 7, "no time point"),
        (0, 0, "no time point"),
    ],
)
def test_a_file_whose_header_disagrees_with_its_rows_is_no_verdict(
    tmp_path, points, declared, says
):
    mat = _synthetic(
        tmp_path / "r.mat", "dymola", "<f4", 7, points=points, declared=declared
    )
    got = read_result_mat(mat.path)
    assert got is not None and len(got["x"][0]) == points  # readable, to show
    assert read_mat_time_extents(mat.path) is None
    assert mat_reaches_time(mat.path, 2.0) == (False, None)
    note = mat_shortfall(mat.path)
    assert note is not None and says in note and "r.mat" in note


def test_bytes_past_the_last_whole_row_are_no_verdict(tmp_path):
    """The header counts exactly the whole rows present, and a row was
    being written behind them: still not a finished file."""
    mat = _synthetic(tmp_path / "r.mat", "dymola", "<f4", 7, points=7)
    assert mat_reaches_time(mat.path, 2.0) == (True, 2.0)
    with open(mat.path, "ab") as f:
        f.write(b"\x00\x00\x80\x3f")
    assert len(read_result_mat(mat.path)["x"][0]) == 7
    assert read_mat_time_extents(mat.path) is None
    assert mat_reaches_time(mat.path, 2.0) == (False, None)
    assert "4 bytes of a further one" in mat_shortfall(mat.path)


def test_a_shortfall_says_how_far_the_rows_go(tmp_path):
    mat = _synthetic(tmp_path / "r.mat", "openmodelica", "<f8", 7, points=4, declared=0)
    note = mat_shortfall(mat.path)
    assert note == (
        "Partial result: r.mat holds 4 time points up to t=1, "
        "its header counts 0 — the run did not finish writing it"
    )
    assert mat_shortfall(tmp_path / "missing.mat") == "missing.mat unreadable"


# ---------------------------------------------------------------------------
# The backends: a partial result is shown, never scored, never rescued
# ---------------------------------------------------------------------------


def _backend(tmp_path, backend: str):
    """The backend whose ``read_result`` reads ``tmp_path/work``."""
    from dstf.backends.dymola.backend import DymolaBackend
    from dstf.backends.openmodelica.backend import OpenModelicaBackend
    from dstf.config import Config

    if backend == "Dymola":
        return DymolaBackend(
            SimpleNamespace(
                show_ide=False,
                backend_setup=[],
                diagnostic_variables=[],
                work_dir=tmp_path / "work",
                backend_path="dymola-fake",
                timeout=1,
                parallel=1,
            )
        )
    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    return OpenModelicaBackend(
        Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
    )


def _test_model():
    return make_test_model("Lib.A", stop_time=2.0, variable_patterns=["x", "k"])


@pytest.fixture
def killed_at_the_end(tmp_path):
    """A result directory per backend holding every row to the stop time
    under a header that was never finished — and, for Dymola, the
    ``dsfinal.txt`` its completion check asks for first."""

    def make(backend: str, filename: str, declared: int) -> Path:
        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        tool = "dymola" if backend == "Dymola" else "openmodelica"
        dtype = "<f4" if backend == "Dymola" else "<f8"
        _synthetic(test_dir / filename, tool, dtype, 7, declared=declared)
        (test_dir / "dsfinal.txt").write_text("final\n")
        return test_dir

    return make


@pytest.mark.parametrize("declared", [0, 4])
def test_dymola_does_not_call_an_unfinished_file_completed(killed_at_the_end, declared):
    from dstf.backends.dymola.backend import _sim_completed_on_disk

    test_dir = killed_at_the_end("Dymola", "dsres.mat", declared)
    assert _sim_completed_on_disk(test_dir, 2.0) == (False, "dsres.mat unreadable")
    # the same directory with a finished file is completed
    _synthetic(test_dir / "dsres.mat", "dymola", "<f4", 7)
    assert _sim_completed_on_disk(test_dir, 2.0) == (True, None)


@pytest.mark.parametrize("declared", [0, 4])
def test_the_openmodelica_rescue_does_not_pass_an_unfinished_file(
    tmp_path, killed_at_the_end, declared
):
    from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

    test_dir = killed_at_the_end("OpenModelica", "result_res.mat", declared)
    worker = OpenModelicaWorker.__new__(OpenModelicaWorker)
    worker.config = SimpleNamespace(work_dir=tmp_path / "work")
    rescued = worker._evaluate_from_disk(_test_model(), "test_0001", 1.0)
    assert rescued.success is False
    assert rescued.error_message == "result_res.mat unreadable"
    _synthetic(test_dir / "result_res.mat", "openmodelica", "<f8", 7)
    assert worker._evaluate_from_disk(_test_model(), "test_0001", 1.0).success is True


@pytest.mark.parametrize(
    ("backend", "filename"),
    [("Dymola", "dsres.mat"), ("OpenModelica", "result_res.mat")],
)
def test_a_partial_result_is_read_to_show_and_is_not_a_success(
    tmp_path, killed_at_the_end, backend, filename
):
    """The tracked-variable read of a file whose header was never finished:
    the rows are there for a plot, the result is a failure that says why —
    so nothing scores it and nothing offers it as a baseline."""
    test_dir = killed_at_the_end(backend, filename, 0)
    reader = _backend(tmp_path, backend)
    partial = reader.read_result(_test_model(), "test_0001", None)
    assert partial.success is False
    assert partial.error_message.startswith(f"Partial result: {filename} holds 7")
    assert [v.name for v in partial.variables] == ["x", "k"]
    assert len(partial.variables[0].time) == 7

    tool = "dymola" if backend == "Dymola" else "openmodelica"
    dtype = "<f4" if backend == "Dymola" else "<f8"
    _synthetic(test_dir / filename, tool, dtype, 7)
    whole = reader.read_result(_test_model(), "test_0001", None)
    assert whole.success is True and whole.error_message is None
    assert np.array_equal(whole.variables[0].values, partial.variables[0].values)


def test_the_tool_reads_any_variable_of_a_partial_result(tmp_path):
    """``result_series`` — what a plot of a failed or running test asks —
    returns the rows present, and ``result_names`` every name."""
    from dstf.backends.dymola.backend import DymolaBackend

    mat = _synthetic(tmp_path / "dsres.mat", "dymola", "<f4", 7, points=3, declared=1)
    assert DymolaBackend.result_names(tmp_path) == list(mat.expected)
    got = DymolaBackend.result_series(tmp_path, ["y", "k.neg"])
    assert _same(got["y"][1], mat.expected["y"][:3])
    assert _same(got["k.neg"][1], mat.expected["k.neg"][:3])


def test_a_stored_fixture_copied_whole_still_passes_the_rescue(tmp_path):
    """The complete OpenModelica fixture, through the same rescue helper."""
    path = tmp_path / "result_res.mat"
    shutil.copy(STORED[1], path)
    assert mat_reaches_time(path, 1.0) == (True, 1.0)
