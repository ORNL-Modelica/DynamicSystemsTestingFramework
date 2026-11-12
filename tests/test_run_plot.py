"""`dstf plot`'s facts and page (``reporting.run_history``, the runs'
gallery of ``reporting.baseline_gallery``): runs read as `compare` reads
them and never written, paired by backend/os, the first label the
reference; a failed run no line; constants in a table; the tests drawn and
the ones where nothing moved — read here without a browser, over work dirs
of the Python example library written as its backend writes them."""

import hashlib
import json
import os

import pytest

from conftest import python_example_copy, write_python_run
from dstf.config import Config
from dstf.reporting.baseline_gallery import (
    nothing_moved_line,
    plan_images,
    run_gallery_html,
)
from dstf.reporting.run_history import RunError, run_history
from dstf.storage.baseline_diff import DiffError

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"
POINTS = "PythonTestingLib.Examples.PointsCheck"


@pytest.fixture
def lib(tmp_path):
    python_example_copy(tmp_path)
    return tmp_path / "lib" / "Resources" / "ReferenceResults" / "testing.json"


def _history(lib, runs, **kw):
    return run_history(
        [(label, path) for label, path in runs],
        lambda work_dir, backend: Config(
            config_file=lib, work_dir=work_dir, backend_name=backend
        ),
        **kw,
    )


def _page(history):
    images = plan_images(history.tests)
    return images, run_gallery_html(history, images, {i.path: b"png" for i in images})


RAMP_MAIN = {"x": [0.0, 2.0, 4.0, 6.0, 8.0]}
RAMP_FIX = {"x": [0.0, 2.1, 4.4, 6.9, 9.6]}
ONE = {"x": [1.0] * 5}
WAVE = {"x": [0.0, 0.8, 0.9, 0.1, -0.7]}


def test_the_variable_that_moved_is_drawn_against_the_first_run(lib, tmp_path):
    main = write_python_run(
        tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE, POINTS: WAVE}
    )
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX, CSV: ONE, POINTS: WAVE})
    history = _history(lib, [("main", main), ("branch", fix)])
    images, page = _page(history)
    assert [i.path for i in images] == ["SimpleRamp/Python-linux/01-x.png"]
    fig = images[0].figure
    legend = [t["name"] for t in fig["data"] if t["yaxis"] == "y"]
    assert legend == ["main (main)", "branch (fix)"]
    caption = fig["layout"]["annotations"][0]["text"]
    assert caption.startswith("each later run minus the first (main), ")
    # the panel's peak is the Change: 1.6 of a range of 8
    (line,) = [t for t in fig["data"] if t["yaxis"] == "y2"]
    assert max(abs(y) for y in line["y"]) == pytest.approx(20.0)
    lines = page.splitlines()
    assert "<h1>Runs main → branch</h1>" in lines
    assert "<ul><li>Python/linux: main (main) → branch (fix)</li></ul>" in lines
    assert (
        "<table><tr><th>Test</th><th>Change, Python/linux</th></tr><tr><td><code>"
        "SimpleRamp</code></td><td>20 %</td></tr></table>"
    ) in lines
    assert (
        '<p class="said">Nothing moved in 2 tests: ConstantCsv, PointsCheck.</p>'
    ) in lines


def test_the_first_label_given_is_the_reference_for_every_later_run(lib, tmp_path):
    """Three runs: each later one minus the FIRST; a constant's Change is
    the last run's against the first."""
    a = write_python_run(tmp_path / "a", {RAMP: RAMP_MAIN, CSV: {"x": [1.0] * 5}})
    b = write_python_run(tmp_path / "b", {RAMP: RAMP_FIX, CSV: {"x": [2.0] * 5}})
    c = write_python_run(tmp_path / "c", {RAMP: RAMP_MAIN, CSV: {"x": [4.0] * 5}})
    history = _history(lib, [("a", a), ("b", b), ("c", c)])
    images, page = _page(history)
    (image,) = images
    panel = [t for t in image.figure["data"] if t["yaxis"] == "y2"]
    # b moved 20 % of a's range; c is a again
    assert [max(abs(y) for y in t["y"]) for t in panel] == [pytest.approx(20.0), 0.0]
    lines = page.splitlines()
    assert (
        "<tr><th>Constant</th><th>a</th><th>b</th><th>c</th><th>Change</th></tr>"
        in (lines)
    )
    assert (
        "<tr><td><code>x</code></td><td>1</td><td>2</td><td>4</td>"
        "<td>300 % of value</td></tr>"
    ) in lines


def _hashes(*dirs):
    out = {}
    for d in dirs:
        for root, names, files in os.walk(d):
            for name in names + files:
                p = os.path.join(root, name)
                st = os.lstat(p)
                data = open(p, "rb").read() if os.path.isfile(p) else b""
                out[p] = (st.st_mtime_ns, hashlib.sha256(data).hexdigest())
    return out


def test_a_run_is_only_read(lib, tmp_path):
    """Nothing is written into a run: no status.json stamp, no wipe, no
    default testing.json, no manifest rewritten."""
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX, CSV: ONE}, failed={CSV})
    before = _hashes(main, fix)
    _page(_history(lib, [("main", main), ("fix", fix)]))
    assert _hashes(main, fix) == before


def test_a_failed_run_is_no_line_whatever_its_file_holds(lib, tmp_path):
    """A run recorded as failed leaves a partial file: never drawn, said
    with why; the other runs are still measured against the first."""
    a = write_python_run(tmp_path / "a", {RAMP: RAMP_MAIN})
    b = write_python_run(tmp_path / "b", {RAMP: RAMP_FIX}, failed={RAMP})
    c = write_python_run(tmp_path / "c", {RAMP: RAMP_FIX})
    history = _history(lib, [("a", a), ("b", b), ("c", c)])
    images, page = _page(history)
    (image,) = images
    legend = [t["name"] for t in image.figure["data"] if t["yaxis"] == "y"]
    assert legend == ["a (a)", "c (c)"]
    assert image.notes == ["b: the run failed (Run recorded failed)."]
    assert '<p class="said">b: the run failed (Run recorded failed).</p>' in (
        page.splitlines()
    )


def test_a_test_whose_run_failed_on_one_side_has_its_section(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_MAIN, CSV: ONE}, failed={CSV})
    history = _history(lib, [("main", main), ("fix", fix)])
    images, page = _page(history)
    assert images == []
    section = page[page.index("<h2><code>ConstantCsv</code>") :]
    assert section.splitlines()[3:6] == [
        '<p class="said">fix: the run failed (Run recorded failed).</p>',
        '<p class="said">No two runs of it to compare.</p>',
        "</section>",
    ]
    assert '<p class="said">Nothing moved in 1 test: SimpleRamp.</p>' in page


def test_a_test_one_run_holds_no_result_of_is_not_run_there(lib, tmp_path):
    a = write_python_run(tmp_path / "a", {RAMP: RAMP_MAIN, CSV: ONE})
    b = write_python_run(tmp_path / "b", {CSV: ONE})
    c = write_python_run(tmp_path / "c", {RAMP: RAMP_FIX, CSV: ONE})
    history = _history(lib, [("a", a), ("b", b), ("c", c)])
    (image,) = plan_images(history.tests)
    assert image.notes == ["b: not run."]
    # held by one run of two: no section, and not "nothing moved" either
    history = _history(lib, [("a", a), ("b", b)])
    assert history.tests == [] and history.nothing_moved == [
        "PythonTestingLib.Examples.ConstantCsv"
    ]


def test_a_named_test_is_drawn_in_full_even_where_nothing_moved(lib, tmp_path):
    """How a reviewer sees a passing fix's values: its constants' table and
    the line of the variables that did not move."""
    main = write_python_run(
        tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE, POINTS: WAVE}
    )
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX, CSV: ONE, POINTS: WAVE})
    history = _history(
        lib, [("main", main), ("fix", fix)], tests=["ConstantCsv", "PointsCheck"]
    )
    images, page = _page(history)
    assert images == [] and history.nothing_moved == []
    lines = page.splitlines()
    assert "<tr><td><code>x</code></td><td>1</td><td>1</td><td>0 %</td></tr>" in lines
    assert '<p class="said">Not drawn, unchanged: x.</p>' in lines
    assert "SimpleRamp" not in page


def test_a_test_name_no_run_holds_is_refused(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    with pytest.raises(DiffError) as e:
        _history(lib, [("main", main), ("fix", fix)], tests=["Nope"])
    assert str(e.value) == (
        "--test: no run given is of a test named Nope (a short name such as "
        "SimpleRamp, or a full model id)"
    )


def test_runs_pair_only_within_one_backend_and_os(lib, tmp_path):
    """One label per change, two OS: each folder its own section, each run
    measured against the same label's run of its own folder."""
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        (
            "main",
            write_python_run(tmp_path / "mw", {RAMP: RAMP_FIX}, os_name="windows"),
        ),
        ("fix", write_python_run(tmp_path / "fl", {RAMP: RAMP_FIX})),
        ("fix", write_python_run(tmp_path / "fw", {RAMP: RAMP_FIX}, os_name="windows")),
    ]
    history = _history(lib, runs)
    images, page = _page(history)
    # windows did not move: main's windows run is already the fix's values
    assert [i.path for i in images] == ["SimpleRamp/Python-linux/01-x.png"]
    assert history.nothing_moved == []
    assert [f.folder for t in history.tests for f in t.folders] == ["Python/linux"]
    assert (
        "<ul><li>Python/linux: main (ml) → fix (fl)</li>"
        "<li>Python/windows: main (mw) → fix (fw)</li></ul>"
    ) in page.splitlines()


def test_a_backend_and_os_of_one_run_draws_nothing_and_is_said(lib, tmp_path):
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        ("fix", write_python_run(tmp_path / "fl", {RAMP: RAMP_FIX})),
        ("fix", write_python_run(tmp_path / "fw", {RAMP: RAMP_FIX}, os_name="windows")),
    ]
    _, page = _page(_history(lib, runs))
    assert (
        '<p class="said">Python/windows: one run only (fix), not drawn.</p>'
    ) in page.splitlines()


def test_no_backend_and_os_of_two_runs_is_refused(lib, tmp_path):
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        ("fix", write_python_run(tmp_path / "fw", {RAMP: RAMP_FIX}, os_name="windows")),
    ]
    with pytest.raises(RunError) as e:
        _history(lib, runs)
    assert str(e.value) == (
        "plot draws two runs or more of one backend/os; there is one of each: "
        "Python/linux (main), Python/windows (fix)"
    )


def test_a_folder_named_that_no_run_is_of_is_refused(lib, tmp_path):
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        ("fix", write_python_run(tmp_path / "fl", {RAMP: RAMP_FIX})),
    ]
    with pytest.raises(RunError) as e:
        _history(lib, runs, folders=["Dymola/linux"])
    assert str(e.value) == (
        "--folder Dymola/linux: no run of it (the runs are of Python/linux)"
    )


def test_a_variable_one_run_holds_is_named_not_drawn(lib, tmp_path):
    spec = lib.parent / "test_spec.json"
    data = json.loads(spec.read_text())
    for entry in data["tests"]:
        if entry["model"] == RAMP:
            entry["variables"] = ["x", "y*"]
    spec.write_text(json.dumps(data))
    main = write_python_run(
        tmp_path / "main", {RAMP: {**RAMP_MAIN, "y1": [0, 1, 0, 1, 0]}}
    )
    fix = write_python_run(tmp_path / "fix", {RAMP: {**RAMP_FIX, "yk": [5.0] * 5}})
    images, page = _page(_history(lib, [("main", main), ("fix", fix)]))
    assert [i.label for i in images] == ["x"]
    lines = page.splitlines()
    assert '<p class="said">Not drawn, only in main: y1.</p>' in lines
    # a constant one run holds: in the table, a dash in the other column
    assert "<tr><td><code>yk</code></td><td>–</td><td>5</td><td>–</td></tr>" in lines


def test_a_constant_not_a_number_in_both_runs_did_not_change(lib, tmp_path):
    nan = float("nan")
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, CSV: {"x": [nan] * 5}})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX, CSV: {"x": [nan] * 5}})
    history = _history(lib, [("main", main), ("fix", fix)], tests=["ConstantCsv"])
    _, page = _page(history)
    assert "<tr><td><code>x</code></td><td>nan</td><td>nan</td><td>0 %</td></tr>" in (
        page.splitlines()
    )


def test_a_run_s_constant_is_shown_at_the_digits_its_array_holds(lib, tmp_path):
    """The Python backend reads double precision: 15 digits, and no
    rounding of saved digits is read into a run's numbers."""
    main = write_python_run(tmp_path / "main", {CSV: {"x": [1 / 3] * 5}})
    fix = write_python_run(tmp_path / "fix", {CSV: {"x": [0.3333334] * 5}})
    _, page = _page(_history(lib, [("main", main), ("fix", fix)]))
    assert (
        "<tr><td><code>x</code></td><td>0.333333333333333</td><td>0.3333334</td>"
        "<td>2.0e-05 % of value</td></tr>"
    ) in page.splitlines()


def test_sections_are_largest_change_first(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, POINTS: WAVE})
    big = {"x": [0.0, 0.8, 0.9, 0.1, 0.5]}
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX, POINTS: big})
    _, page = _page(_history(lib, [("main", main), ("fix", fix)]))
    assert page.index("<h2><code>PointsCheck</code>") < page.index(
        "<h2><code>SimpleRamp</code>"
    )


def test_a_model_the_library_no_longer_has_is_no_failed_run(lib, tmp_path):
    main = write_python_run(
        tmp_path / "main", {RAMP: RAMP_MAIN, "PythonTestingLib.Gone": ONE}
    )
    fix = write_python_run(
        tmp_path / "fix", {RAMP: RAMP_MAIN, "PythonTestingLib.Gone": ONE}
    )
    history = _history(lib, [("main", main), ("fix", fix)])
    assert history.tests == []
    assert history.nothing_moved == [RAMP]


def test_the_nothing_moved_line_is_cut_past_ten_names():
    ids = [f"Lib.T{i:02d}" for i in range(12)]
    shown = ", ".join(f"T{i:02d}" for i in range(10))
    assert nothing_moved_line(ids) == (
        f"Nothing moved in 12 tests: {shown} … and 2 more."
    )


# --- refused runs ------------------------------------------------------------


def _refused(lib, runs):
    with pytest.raises(RunError) as e:
        _history(lib, runs)
    return str(e.value)


def test_a_folder_with_no_manifest_is_refused(lib, tmp_path):
    (tmp_path / "empty").mkdir()
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", tmp_path / "empty"), ("fix", fix)])
    assert got == (
        f"main={tmp_path / 'empty'}: not a run's work dir (no batch_manifest.json "
        "in it; `dstf run` writes one)"
    )


def test_a_manifest_that_cannot_be_read_is_refused_and_left_where_it_is(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    (main / "batch_manifest.json").write_text("[1, 2]")
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == f"main={main}: its batch_manifest.json holds no JSON object"
    assert (main / "batch_manifest.json").read_text() == "[1, 2]"


def test_a_run_with_no_status_json_is_refused(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    (main / "status.json").unlink()
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == (
        f"main={main}: no status.json in it to say which backend and os ran it"
    )


def test_a_status_json_with_no_metadata_is_refused(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN}, metadata=False)
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == (
        f"main={main}: its status.json names no backend and os (written before "
        "a run recorded them)"
    )


def test_a_run_of_another_library_is_refused(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {"OtherLib.Test": ONE})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == (
        f"main={main}: its run holds none of this library's tests (a run of "
        "another library?)"
    )


def test_two_runs_of_one_label_and_backend_os_are_refused(lib, tmp_path):
    one = write_python_run(tmp_path / "one", {RAMP: RAMP_MAIN})
    two = write_python_run(tmp_path / "two", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", one), ("main", two)])
    assert got == (
        f"main: two runs of Python/linux, {one} and {two}: give each its own label"
    )


def test_one_directory_given_twice_is_refused(lib, tmp_path):
    one = write_python_run(tmp_path / "one", {RAMP: RAMP_MAIN})
    got = _refused(lib, [("main", one), ("fix", one)])
    assert got == f"fix={one}: this directory is given twice (also as main)"
