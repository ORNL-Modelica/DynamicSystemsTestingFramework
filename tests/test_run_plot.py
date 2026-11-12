"""`dstf plot`'s facts and page (``reporting.run_history``, the runs'
gallery of ``reporting.baseline_gallery``): runs read as `compare` reads
them and never written, paired by backend/os, the first label the
reference; a failed run no line; constants in a table; the tests drawn and
the ones where nothing moved — read here without a browser, over work dirs
of the Python example library written as its backend writes them."""

import hashlib
import json
import os

import numpy as np
import pytest

from conftest import python_example_copy, write_python_run
from dstf.config import Config
from dstf.reporting.baseline_gallery import (
    nothing_moved_line,
    plan_images,
    run_gallery_html,
)
from dstf.reporting.run_history import RunError, RunHistory, run_history
from dstf.storage.baseline_diff import DiffError
from mat4_files import write_result_mat

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
    """Three runs: each later one minus the FIRST; a constant's Change in
    the table is the last run's against the first."""
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
    # the section's Change: the largest of any run, c against a (b's is 100 %)
    assert (
        "<table><tr><th>Test</th><th>Change, Python/linux</th></tr><tr><td><code>"
        "ConstantCsv</code></td><td>300 % of value</td></tr></table>"
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


def test_a_test_a_run_never_finished_has_its_section_not_nothing_moved(lib, tmp_path):
    """A run stopped part way leaves its entry pending: that run has no
    result, which is a difference — never "nothing moved"."""
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_MAIN, CSV: ONE}, pending={CSV})
    history = _history(lib, [("main", main), ("fix", fix)])
    assert history.nothing_moved == [RAMP]
    _, page = _page(history)
    section = page[page.index("<h2><code>ConstantCsv</code>") :]
    assert section.splitlines()[3:6] == [
        '<p class="said">fix: not run.</p>',
        '<p class="said">No two runs of it to compare.</p>',
        "</section>",
    ]


def test_a_named_test_one_run_holds_has_its_section(lib, tmp_path):
    """--test names a test only one run ran: it is drawn as far as it can
    be, never an empty page."""
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, CSV: ONE})
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_MAIN})
    history = _history(lib, [("main", main), ("fix", fix)], tests=["ConstantCsv"])
    _, page = _page(history)
    section = page[page.index("<h2><code>ConstantCsv</code> · Python/linux") :]
    assert section.splitlines()[3:6] == [
        '<p class="said">fix: not run.</p>',
        '<p class="said">No two runs of it to compare.</p>',
        "</section>",
    ]


def test_a_named_test_is_drawn_in_full_even_where_nothing_moved(lib, tmp_path):
    """How a reviewer sees a passing fix's values: one table of every
    tracked variable — a constant's value, a varying one's at the end."""
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
    assert (
        "<tr><td><code>x</code> (end)</td><td>-0.7</td><td>-0.7</td><td>0 %</td></tr>"
    ) in lines
    assert "Not drawn" not in page
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


def test_each_folder_s_reference_is_the_run_of_the_label_given_first(lib, tmp_path):
    """The labels' order, not the arguments' per folder: fix's windows run
    given before main's is still measured against main's."""
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        ("fix", write_python_run(tmp_path / "fw", {RAMP: RAMP_FIX}, os_name="windows")),
        ("fix", write_python_run(tmp_path / "fl", {RAMP: RAMP_FIX})),
        (
            "main",
            write_python_run(tmp_path / "mw", {RAMP: RAMP_MAIN}, os_name="windows"),
        ),
    ]
    history = _history(lib, runs)
    assert {f: [r.label for r in got] for f, got in history.folders.items()} == {
        "Python/linux": ["main", "fix"],
        "Python/windows": ["main", "fix"],
    }
    images = plan_images(history.tests)
    for image in images:
        legend = [t["name"] for t in image.figure["data"] if t["yaxis"] == "y"]
        assert legend[0].startswith("main ("), legend


def test_a_folder_named_restricts_the_runs_drawn(lib, tmp_path):
    runs = [
        ("main", write_python_run(tmp_path / "ml", {RAMP: RAMP_MAIN})),
        (
            "main",
            write_python_run(tmp_path / "mw", {RAMP: RAMP_MAIN}, os_name="windows"),
        ),
        ("fix", write_python_run(tmp_path / "fl", {RAMP: RAMP_FIX})),
        ("fix", write_python_run(tmp_path / "fw", {RAMP: RAMP_FIX}, os_name="windows")),
    ]
    history = _history(lib, runs, folders=["Python/windows"])
    assert list(history.folders) == ["Python/windows"]
    assert [i.path for i in plan_images(history.tests)] == [
        "SimpleRamp/Python-windows/01-x.png"
    ]


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


def _track_y(lib):
    spec = lib.parent / "test_spec.json"
    data = json.loads(spec.read_text())
    for entry in data["tests"]:
        if entry["model"] == RAMP:
            entry["variables"] = ["x", "y*"]
    spec.write_text(json.dumps(data))


def test_a_variable_only_one_run_holds_is_a_difference(lib, tmp_path):
    """A variable renamed or added on the branch, nothing else moved: the
    test has its section, never "nothing moved"."""
    _track_y(lib)
    main = write_python_run(
        tmp_path / "main", {RAMP: {**RAMP_MAIN, "y1": [0, 1, 0, 1, 0]}}
    )
    fix = write_python_run(
        tmp_path / "fix", {RAMP: {**RAMP_MAIN, "y2": [0, 1, 0, 1, 0]}}
    )
    history = _history(lib, [("main", main), ("fix", fix)])
    assert history.nothing_moved == []
    _, page = _page(history)
    assert (
        '<p class="said">Not drawn, unchanged: x. Not drawn, only in main: y1. '
        "Not drawn, only in fix: y2.</p>"
    ) in page.splitlines()


def test_a_run_s_image_notes_say_run(lib, tmp_path):
    """Three runs, y1 not in the second: the note says so in a run's words."""
    _track_y(lib)
    a = write_python_run(tmp_path / "a", {RAMP: {**RAMP_MAIN, "y1": [0, 1, 0, 1, 0]}})
    b = write_python_run(tmp_path / "b", {RAMP: RAMP_MAIN})
    c = write_python_run(tmp_path / "c", {RAMP: {**RAMP_MAIN, "y1": [0, 1, 0, 1, 2]}})
    d = write_python_run(tmp_path / "d", {RAMP: {**RAMP_MAIN, "y2": [0, 1, 0, 1, 2]}})
    images = plan_images(_history(lib, [("a", a), ("b", b), ("c", c)]).tests)
    assert {i.label: i.notes for i in images} == {"y1": ["Not in run b."]}
    images = plan_images(_history(lib, [("a", a), ("d", d), ("c", c)]).tests)
    assert {i.label: i.notes for i in images} == {
        "y1": ["Not in run d."],
    }
    e = write_python_run(tmp_path / "e", {RAMP: {**RAMP_MAIN, "y3": [0, 1, 0, 1, 0]}})
    f = write_python_run(tmp_path / "f", {RAMP: {**RAMP_MAIN, "y3": [0, 1, 0, 1, 3]}})
    images = plan_images(_history(lib, [("a", a), ("e", e), ("f", f)]).tests)
    assert {i.label: i.notes for i in images} == {
        "y3": ["First in run e; nothing earlier to measure against."]
    }


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


def test_three_runs_rank_a_section_by_its_largest_move_of_any_run(lib, tmp_path):
    """SimpleRamp moved 100 % in try1 and came back in try2; PointsCheck
    moved 75 % in try2 only. The largest move of any later run against the
    first ranks, so SimpleRamp comes first, and its Change says that move."""
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN, POINTS: WAVE})
    try1 = write_python_run(
        tmp_path / "try1", {RAMP: {"x": [0.0, 2.0, 4.0, 6.0, 16.0]}, POINTS: WAVE}
    )
    big = {"x": [0.0, 0.8, 0.9, 0.1, 0.5]}
    try2 = write_python_run(tmp_path / "try2", {RAMP: RAMP_MAIN, POINTS: big})
    _, page = _page(_history(lib, [("main", main), ("try1", try1), ("try2", try2)]))
    assert page.index("<h2><code>SimpleRamp</code>") < page.index(
        "<h2><code>PointsCheck</code>"
    )
    lines = page.splitlines()
    assert (
        "<table><tr><th>Test</th><th>Change, Python/linux</th></tr><tr><td><code>"
        "SimpleRamp</code></td><td>100 %</td></tr></table>"
    ) in lines
    assert (
        "<table><tr><th>Test</th><th>Change, Python/linux</th></tr><tr><td><code>"
        "PointsCheck</code></td><td>75 %</td></tr></table>"
    ) in lines


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
    history = RunHistory(
        folders={}, alone=[], tests=[], nothing_moved=ids, held=ids, only=[]
    )
    assert nothing_moved_line(history) == (
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


def test_a_manifest_entry_with_no_model_id_is_refused_and_left_as_it_is(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    manifest = main / "batch_manifest.json"
    data = json.loads(manifest.read_text())
    data["test_0009"] = {"ref_id": None}
    manifest.write_text(json.dumps(data))
    before = manifest.read_bytes()
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == (
        f"main={main}: its batch_manifest.json holds an entry with no model id "
        "(test_0009)"
    )
    assert manifest.read_bytes() == before


def test_a_status_json_that_cannot_be_read_is_refused_as_such(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    (main / "status.json").write_text("{not json")
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_FIX})
    got = _refused(lib, [("main", main), ("fix", fix)])
    assert got == (
        f"main={main}: its status.json cannot be read, so the backend and os "
        "that ran it are not known"
    )


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


# --- a float32 run -------------------------------------------------------------


def _dymola_run(where, value):
    """A Dymola run's work dir: its result file float32, as Dymola writes."""
    where.mkdir()
    (where / "test_0001").mkdir()
    write_result_mat(
        where / "test_0001" / "dsres.mat",
        time=np.array([0.0, 1.0, 2.0]),
        trajectories={"x": np.array([value] * 3)},
    )
    (where / "batch_manifest.json").write_text(
        json.dumps({"test_0001": {"model_id": "Lib.A", "last_run_status": "passed"}})
    )
    meta = {"backend": "Dymola", "backend_name": "Dymola", "os": "linux"}
    (where / "status.json").write_text(json.dumps({"metadata": meta, "tests": []}))
    return where


def test_a_float32_run_s_constant_is_shown_at_7_digits(tmp_path):
    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text(
        "within ;\npackage Lib\n  model A\n    Real x = 1;\n  end A;\nend Lib;\n"
    )
    config = tmp_path / "testing.json"
    config.write_text(
        json.dumps(
            {"source_path": "Lib", "test_spec": "test_spec.json", "backend": "Dymola"}
        )
    )
    (tmp_path / "test_spec.json").write_text(
        json.dumps({"tests": [{"model": "Lib.A", "variables": ["x"]}]})
    )
    a = _dymola_run(tmp_path / "a", 2.233511e10)
    b = _dymola_run(tmp_path / "b", 1.548207e10)
    _, page = _page(_history(config, [("main", a), ("fix", b)]))
    assert (
        "<tr><td><code>x</code></td><td>2.233511e+10</td><td>1.548207e+10</td>"
        "<td>31 % of value</td></tr>"
    ) in page.splitlines()


# --- --test: every tracked variable's value ---------------------------------------

KEY = (
    '<p class="said">(end) = the value at the end of each run; Change = the '
    "biggest move over the whole run.</p>"
)


def _section_table(page):
    """The section's value table and the line under it, as lines."""
    lines = page.splitlines()
    start = lines.index('<table class="values">')
    end = lines.index("</table>", start)
    return lines[start : end + 2]


def test_varying_values_identical_in_both_runs_are_shown_at_their_end(lib, tmp_path):
    """Every tracked value varies and nothing moved: before, the page said
    only "unchanged" — now each one's end value, and 0 %."""
    _track_y(lib)
    same = {**RAMP_MAIN, "y1": [3.0, 1.0, 4.0, 1.0, 5.5]}
    main = write_python_run(tmp_path / "main", {RAMP: same})
    fix = write_python_run(tmp_path / "fix", {RAMP: same})
    images, page = _page(
        _history(lib, [("main", main), ("fix", fix)], tests=["SimpleRamp"])
    )
    assert images == []
    assert _section_table(page) == [
        '<table class="values">',
        "<tr><th>Value</th><th>main</th><th>fix</th><th>Change</th></tr>",
        "<tr><td><code>x</code> (end)</td><td>8</td><td>8</td><td>0 %</td></tr>",
        "<tr><td><code>y1</code> (end)</td><td>5.5</td><td>5.5</td><td>0 %</td></tr>",
        "</table>",
        KEY,
    ]
    assert "Not drawn" not in page


def test_a_varying_value_that_moved_is_drawn_and_a_row_with_the_panel_s_change(
    lib, tmp_path
):
    """Its Change is the biggest move over the run (1.6 of a range of 8 at
    2 s), not the difference of its end values, which is none."""
    main = write_python_run(tmp_path / "main", {RAMP: RAMP_MAIN})
    fix = write_python_run(tmp_path / "fix", {RAMP: {"x": [0.0, 2.0, 5.6, 6.0, 8.0]}})
    images, page = _page(
        _history(lib, [("main", main), ("fix", fix)], tests=["SimpleRamp"])
    )
    (image,) = images
    (line,) = [t for t in image.figure["data"] if t["yaxis"] == "y2"]
    assert max(abs(y) for y in line["y"]) == pytest.approx(20.0)
    assert (
        "<tr><td><code>x</code> (end)</td><td>8</td><td>8</td><td>20 %</td></tr>"
        in (page.splitlines())
    )


def test_a_constant_and_a_varying_value_are_one_table(lib, tmp_path):
    _track_y(lib)
    main = write_python_run(tmp_path / "main", {RAMP: {**RAMP_MAIN, "yk": [5.0] * 5}})
    fix = write_python_run(tmp_path / "fix", {RAMP: {**RAMP_MAIN, "yk": [6.0] * 5}})
    _, page = _page(_history(lib, [("main", main), ("fix", fix)], tests=["SimpleRamp"]))
    assert _section_table(page) == [
        '<table class="values">',
        "<tr><th>Value</th><th>main</th><th>fix</th><th>Change</th></tr>",
        "<tr><td><code>x</code> (end)</td><td>8</td><td>8</td><td>0 %</td></tr>",
        "<tr><td><code>yk</code></td><td>5</td><td>6</td><td>20 % of value</td></tr>",
        "</table>",
        KEY,
    ]


def test_a_table_of_constants_only_has_no_end_key(lib, tmp_path):
    main = write_python_run(tmp_path / "main", {CSV: ONE})
    fix = write_python_run(tmp_path / "fix", {CSV: ONE})
    _, page = _page(
        _history(lib, [("main", main), ("fix", fix)], tests=["ConstantCsv"])
    )
    assert _section_table(page) == [
        '<table class="values">',
        "<tr><th>Value</th><th>main</th><th>fix</th><th>Change</th></tr>",
        "<tr><td><code>x</code></td><td>1</td><td>1</td><td>0 %</td></tr>",
        "</table>",
        "</section>",
    ]


def test_a_value_one_run_holds_is_a_dash_in_the_other(lib, tmp_path):
    _track_y(lib)
    main = write_python_run(
        tmp_path / "main", {RAMP: {**RAMP_MAIN, "y1": [0, 1, 0, 1, 3]}}
    )
    fix = write_python_run(tmp_path / "fix", {RAMP: RAMP_MAIN})
    _, page = _page(_history(lib, [("main", main), ("fix", fix)], tests=["SimpleRamp"]))
    lines = page.splitlines()
    assert (
        "<tr><td><code>y1</code> (end)</td><td>3</td><td>–</td><td>–</td></tr>" in lines
    )
    assert '<p class="said">Not drawn, only in main: y1.</p>' in lines


def test_a_failed_run_s_column_is_a_dash(lib, tmp_path):
    a = write_python_run(tmp_path / "a", {RAMP: RAMP_MAIN})
    b = write_python_run(tmp_path / "b", {RAMP: RAMP_FIX}, failed={RAMP})
    c = write_python_run(tmp_path / "c", {RAMP: RAMP_MAIN})
    _, page = _page(_history(lib, [("a", a), ("b", b), ("c", c)], tests=["SimpleRamp"]))
    lines = page.splitlines()
    assert _section_table(page)[1:3] == [
        "<tr><th>Value</th><th>a</th><th>b</th><th>c</th><th>Change</th></tr>",
        "<tr><td><code>x</code> (end)</td><td>8</td><td>–</td><td>8</td><td>0 %</td></tr>",
    ]
    assert '<p class="said">b: the run failed (Run recorded failed).</p>' in lines
