"""`baseline plot`'s images and page (``reporting.baseline_gallery``): one
image per variable of each folder drawn, at a path safe on every OS; the
notes each image carries; the self-contained gallery; and the output
directory the command may write — read here without a browser."""

import json
from pathlib import Path

import pytest

from conftest import GitRepo, resaved_single
from dstf.reporting.baseline_diff_text import overview_cells, steps_folder
from dstf.reporting.baseline_gallery import (
    GALLERY,
    MARKER,
    OutputRefused,
    gallery_html,
    legend_name,
    plan_images,
    stale_files,
    write_marker,
)
from dstf.storage.baseline_diff import diff_baselines
from dstf.storage.baseline_history import MAX_KEPT, Revision, baseline_history
from dstf.storage.git_revisions import Commit

LINUX = "Dymola/linux"


def _ref(model_id, variables, time=(0.0, 1.0, 2.0)) -> str:
    return json.dumps(
        {
            "model_id": model_id,
            "simulation": {"stop_time": 2.0},
            "time": list(time),
            "variables": [
                {"index": i, "name": n, "expression": e, "values": list(v)}
                for i, (n, e, v) in enumerate(variables, start=1)
            ],
        }
    )


@pytest.fixture
def lib(tmp_path):
    repo = GitRepo(tmp_path / "lib")
    (repo.top / "refs").mkdir()
    return repo


def _write(repo, path, text):
    target = repo.top / "refs" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _two(lib, tests):
    """Two commits; ``tests`` maps a file path to (model id, old, new)
    variables."""
    for path, (model_id, old, _) in tests.items():
        _write(lib, path, _ref(model_id, old))
    one = lib.commit("first accept")
    for path, (model_id, _, new) in tests.items():
        _write(lib, path, _ref(model_id, new))
    two = lib.commit("a model fix")
    return [one, two]


def test_a_legend_name_is_the_date_the_short_hash_and_the_subject_cut_at_48():
    commit = Commit("a" * 40, "aaaaaaa", "x" * 60)
    got = legend_name(Revision(commit, "2026-11-10"))
    assert got == "2026-11-10 aaaaaaa " + "x" * 47 + "…"
    short = Commit("b" * 40, "bbbbbbb", "PR #26: geometry equations")
    assert legend_name(Revision(short, "2026-11-10")) == (
        "2026-11-10 bbbbbbb PR #26: geometry equations"
    )


def test_an_image_per_variable_at_a_safe_path_named_by_its_label(lib):
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.Pkg.A",
                [("unitTests.x[1]", "pipe.T[3]", [0, 1, 2]), ("y", "", [1, 2, 3])],
                [("unitTests.x[1]", "pipe.T[3]", [0, 1, 3]), ("y", "", [1, 2, 4])],
            )
        },
    )
    images = plan_images(baseline_history(lib.top / "refs", revs))
    assert [i.path for i in images] == [
        "A/Dymola-linux/01-pipe_T_3.png",
        "A/Dymola-linux/02-y.png",
    ]
    fig = images[0].figure
    assert fig["layout"]["title"]["text"] == "A · Dymola/linux · pipe.T[3]"
    assert fig["layout"]["yaxis"]["title"]["text"] == "pipe.T[3]"


def test_two_tests_of_one_short_name_are_named_by_their_model_ids(lib):
    """One short name in two packages: by its short name alone, the second
    test's images overwrote the first's. A clash in letter case alone is a
    clash too (Windows and macOS file systems)."""
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.P.Test",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 3])],
            ),
            f"{LINUX}/ref_b.json": (
                "Lib.Q.Test",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 4])],
            ),
            f"{LINUX}/ref_c.json": (
                "Lib.R.test",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 5])],
            ),
        },
    )
    images = plan_images(baseline_history(lib.top / "refs", revs))
    assert sorted({i.path.split("/")[0] for i in images}) == [
        "Lib.P.Test",
        "Lib.Q.Test",
        "Lib.R.test",
    ]


def test_a_path_stays_short_and_never_a_reserved_name(lib):
    """`CON` is no file name on Windows, whatever its extension; a model id
    a test folder is named by on a clash is cut, as a label is, so a path
    stays under 200 characters from the directory."""
    long_id = "Lib." + ".".join(["VeryLongPackageName"] * 12)
    label = "a.b[1]." + "c" * 200
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                long_id + ".CON",
                [("x", label, [0, 1, 2])],
                [("x", label, [0, 1, 3])],
            ),
            f"{LINUX}/ref_b.json": (
                long_id + ".NUL",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 3])],
            ),
            f"{LINUX}/ref_c.json": (
                "Other.NUL",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 3])],
            ),
        },
    )
    images = plan_images(baseline_history(lib.top / "refs", revs))
    by_test = {i.test: i.path for i in images}
    assert (
        by_test[long_id + ".CON"]
        == "_CON/Dymola-linux/01-" + ("a_b_1_" + "c" * 34) + ".png"
    )
    assert by_test["Other.NUL"] == "Other.NUL/Dymola-linux/01-x.png"
    clashed = by_test[long_id + ".NUL"]
    assert len(clashed.split("/")[0]) <= 100
    assert len(set(by_test.values())) == 3
    assert all(len(path) <= 200 for path in by_test.values())


def _history_with_notes(lib):
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("PHS.p", "", [0, 1, 2])]))
    one = lib.commit("one")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("unitTests.x[1]", "PHS.p", [0, 1, 2])]),
    )
    rename = lib.commit("rename only")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("unitTests.x[1]", "PHS.p", [0, float("nan"), 3])]),
    )
    three = lib.commit("three")
    return baseline_history(lib.top / "refs", [one, rename, three])


def test_an_image_says_what_was_skipped_renamed_and_not_finite(lib):
    history = _history_with_notes(lib)
    (image,) = plan_images(history)
    rev = history.revisions
    # the rename is the skipped revision's: it renamed and changed nothing
    assert image.notes == [
        f"Skipped: {rev[1].commit.short} (equal to {rev[0].commit.short}).",
        f"Renamed PHS.p → unitTests.x[1] at {rev[1].commit.short}.",
        f"{rev[2].commit.short}: 1 point not finite from 1 s (× on the panel).",
    ]
    assert image.figure["layout"]["annotations"][-1]["text"] == "<br>".join(image.notes)


def test_skipped_revisions_are_said_with_the_revision_each_equals(lib):
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 2])]))
    revs = [lib.commit("one")]
    for values in ([0, 1, 2], [0, 1, 3], [0, 1, 3], [0, 1, 3], [0, 1, 4]):
        _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", values)]))
        revs.append(lib.commit(f"x {values}"))
    (image,) = plan_images(baseline_history(lib.top / "refs", revs))
    one, two, three, four, five, _ = (r[:7] for r in revs)
    assert image.notes[0] == (
        f"Skipped: {two} (equal to {one}); {four}, {five} (equal to {three})."
    )


def test_only_a_variable_that_moved_is_drawn_the_rest_named_in_one_line(lib):
    """A variable that is the same at every revision, or is stored at one
    only, is no image: the gallery names them under the folder."""
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.A",
                [
                    ("x", "", [0, 1, 2]),
                    ("same", "", [1, 2, 3]),
                    ("gone", "", [5, 5, 5]),
                ],
                [
                    ("x", "", [0, 1, 3]),
                    ("same", "", [1, 2, 3]),
                    ("added", "", [1, 2, 3]),
                ],
            )
        },
    )
    history = baseline_history(lib.top / "refs", revs)
    images = plan_images(history)
    assert [i.path for i in images] == ["A/Dymola-linux/01-x.png"]
    page = gallery_html(history, images, {i.path: b"png" for i in images})
    assert '<p class="said">Not drawn, unchanged: same, gone, added.</p>' in page


def test_the_unchanged_line_is_cut_past_ten_names(lib):
    names = [f"v{i:02d}" for i in range(13)]
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.A",
                [("x", "", [0, 1, 2])] + [(n, "", [1, 2, 3]) for n in names],
                [("x", "", [0, 1, 3])] + [(n, "", [1, 2, 3]) for n in names],
            )
        },
    )
    history = baseline_history(lib.top / "refs", revs)
    images = plan_images(history)
    page = gallery_html(history, images, {i.path: b"png" for i in images})
    shown = ", ".join(names[:10])
    assert f'<p class="said">Not drawn, unchanged: {shown} … and 3 more.</p>' in page


def test_a_variable_moved_by_rounding_only_is_named_beside_the_unchanged(lib):
    """A baseline accepted again at single precision: its values differ by
    the old file's rounding only, so none is drawn; they are named in the
    same line as the unchanged ones, apart from them."""
    old, new = resaved_single()
    for doc, last in ((old, 3.0), (new, 5.0)):
        doc["variables"] += [
            # a constant: a sloped series reads the rounding of the float32 times
            {"index": 4, "name": "same", "expression": "", "values": [2, 2, 2, 2]},
            {"index": 5, "name": "y", "expression": "", "values": [0, 1, 2, last]},
        ]
    _write(lib, f"{LINUX}/ref_a.json", json.dumps(old))
    one = lib.commit("one")
    _write(lib, f"{LINUX}/ref_a.json", json.dumps(new))
    two = lib.commit("two")
    history = baseline_history(lib.top / "refs", [one, two])
    images = plan_images(history)
    assert [i.path for i in images] == ["A/Dymola-linux/01-y.png"]
    page = gallery_html(history, images, {i.path: b"png" for i in images})
    assert (
        '<p class="said">Not drawn, unchanged: same. Not drawn, rounding of the '
        "saved digits only: unitTests.x[1], unitTests.x[2], unitTests.x[3].</p>"
    ) in page.splitlines()


def test_a_folder_where_nothing_moved_draws_nothing_and_says_so(lib):
    """A variable added is a new baseline, and the folder holds two
    revisions of different values; but nothing it held moved."""
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.A",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 2]), ("added", "", [1, 2, 3])],
            )
        },
    )
    history = baseline_history(lib.top / "refs", revs)
    assert plan_images(history) == []
    page = gallery_html(history, [], {})
    section = page[page.index("<h3>Dymola/linux</h3>") :]
    assert section.startswith(
        '<h3>Dymola/linux</h3>\n<p class="said">Not drawn, unchanged: x, added.</p>'
    )


def test_a_test_with_no_folder_drawn_says_none_holds_two_different_values(lib):
    """Named with --folder, a test that changed only in another folder: no
    folder DRAWN holds two revisions of different values (the other does)."""
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 2])]))
    _write(lib, "Dymola/windows/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 2])]))
    one = lib.commit("one")
    _write(lib, "Dymola/windows/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 5])]))
    two = lib.commit("two")
    history = baseline_history(lib.top / "refs", [one, two], folders=[LINUX])
    page = gallery_html(history, plan_images(history), {})
    assert (
        '<p class="said">No folder drawn holds two revisions with different values.</p>'
    ) in page


def test_a_variable_first_stored_later_is_measured_from_there_and_says_so(lib):
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 2])]))
    one = lib.commit("one")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("x", "", [0, 1, 3]), ("y", "", [0, 5, 10])]),
    )
    two = lib.commit("two")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("x", "", [0, 1, 3]), ("y", "", [0, 6, 10])]),
    )
    three = lib.commit("three")
    images = {
        i.label: i
        for i in plan_images(baseline_history(lib.top / "refs", [one, two, three]))
    }
    assert images["y"].notes == [
        f"First stored at {two[:7]}; nothing earlier to measure against."
    ]


def test_a_variable_first_stored_at_a_revision_left_out_says_so(lib):
    """Over twelve kept, y first appears at one that is left out (its step
    is the smallest): its first line is a later revision's."""
    ends = [100, 110, 110.001, *(110 + 50 * k for k in range(1, MAX_KEPT - 1))]
    revs = []
    for k, end in enumerate(ends):
        variables = [("x", "", [0, 100, end])]
        if k >= 2:
            variables.append(("y", "", [0, 1, 2 + k]))
        _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", variables))
        revs.append(lib.commit(f"step {k}"))
    history = baseline_history(lib.top / "refs", revs)
    (folder,) = history.tests[0].folders
    assert [r.commit.sha for r in folder.thinned] == [revs[2]]
    images = {i.label: i for i in plan_images(history)}
    assert (
        f"First stored at {revs[2][:7]}, which is left out; measured from "
        f"{revs[3][:7]}."
    ) in images["y"].notes


def test_the_panel_drawn_peaks_at_the_change_and_marks_each_point_not_finite(lib):
    """The figure plan_images builds, read where it is drawn (the y2
    traces): its line peaks at the Change baseline diff prints, and its
    markers sit at the times the difference is not finite."""
    time = (0.0, 1.0, 2.0, 3.0)
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 5, 10, 10])], time))
    one = lib.commit("one")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("x", "", [0, 6, float("nan"), 10])], time),
    )
    two = lib.commit("two")
    root = lib.top / "refs"
    ((x,),) = [
        b.variables for b in diff_baselines(root, one, two).tests[0].baselines.values()
    ]
    (image,) = plan_images(baseline_history(root, [one, two]))
    panel = [t for t in image.figure["data"] if t["yaxis"] == "y2"]
    (line,) = [t for t in panel if t["mode"] == "lines"]
    (marks,) = [t for t in panel if t["mode"] == "markers"]
    assert max(abs(y) for y in line["y"] if y is not None) == x.change.largest == 10.0
    assert marks["x"] == [2.0]
    assert x.change.non_finite_at == 2.0


def test_an_image_says_the_points_of_an_event_stored_otherwise_and_leaves_a_gap(lib):
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("x", "", [0, 0, 5, 10, 10])], time=(0, 1, 1, 1, 2)),
    )
    one = lib.commit("an event stored three times")
    _write(
        lib,
        f"{LINUX}/ref_a.json",
        _ref("Lib.A", [("x", "", [0, 0, 10, 11])], time=(0, 1, 1, 2)),
    )
    two = lib.commit("twice")
    (image,) = plan_images(baseline_history(lib.top / "refs", [one, two]))
    assert image.notes == [
        f"{two[:7]}: 1 point of the first inside an event it stores another "
        "number of times, not compared."
    ]
    (panel,) = [t for t in image.figure["data"] if t["yaxis"] == "y2"]
    assert panel["y"] == [0.0, 0.0, None, 0.0, 10.0]


def test_the_gallery_is_one_file_with_no_script_and_nothing_fetched(lib):
    revs = _two(
        lib,
        {
            f"{LINUX}/ref_a.json": (
                "Lib.A",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 3])],
            ),
            f"{LINUX}/ref_b.json": (
                "Lib.B",
                [("x", "", [0, 1, 2])],
                [("x", "", [0, 1, 9])],
            ),
        },
    )
    history = baseline_history(lib.top / "refs", revs)
    images = plan_images(history)
    pngs = {i.path: b"\x89PNG-" + i.path.encode() for i in images}
    page = gallery_html(history, images, pngs)
    assert "<script" not in page and "http" not in page
    assert page.count('src="data:image/png;base64,') == 2
    # largest change first, as baseline diff sorts them
    assert page.index("<h2><code>B</code>") < page.index("<h2><code>A</code>")
    # each test's row is baseline diff's
    diff = history.diff
    steps = steps_folder(diff)
    for test in diff.tests:
        cells = "".join(f"<td>{c}</td>" for c in overview_cells(diff, test, steps))
        assert cells in page
    # the revisions with their subjects head it
    assert "first accept" in page and "a model fix" in page


def test_a_named_test_baseline_diff_does_not_list_goes_last_with_no_row(lib):
    _write(lib, f"{LINUX}/ref_b.json", _ref("Lib.B", [("x", "", [0, 1, 2])]))
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 2])]))
    one = lib.commit("one")
    _write(lib, f"{LINUX}/ref_b.json", _ref("Lib.B", [("x", "", [0, 1, 7])]))
    _write(lib, f"{LINUX}/ref_a.json", _ref("Lib.A", [("x", "", [0, 1, 3])]))
    two = lib.commit("two")
    _write(lib, f"{LINUX}/ref_b.json", _ref("Lib.B", [("x", "", [0, 1, 2])]))
    three = lib.commit("three")
    history = baseline_history(lib.top / "refs", [one, two, three], tests=["A", "B"])
    images = plan_images(history)
    page = gallery_html(history, images, {i.path: b"png" for i in images})
    assert page.index("<h2><code>A</code>") < page.index("<h2><code>B</code>")
    b = page[page.index("<h2><code>B</code>") :]
    assert "not changed between the first and last revision" in b
    assert "<table" not in b


# --- the output directory ------------------------------------------------------


def test_a_directory_not_there_or_empty_is_written(tmp_path):
    assert stale_files(tmp_path / "new") == []
    (tmp_path / "empty").mkdir()
    assert stale_files(tmp_path / "empty") == []


def test_a_directory_holding_a_file_this_command_did_not_write_is_refused(tmp_path):
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    (out / "sub" / "mine.txt").write_text("a user's file")
    with pytest.raises(OutputRefused) as e:
        stale_files(out)
    assert str(e.value) == (
        f"{out} holds files baseline plot did not write (sub/mine.txt): "
        "name a new or empty directory"
    )


def test_a_rewrite_removes_exactly_the_files_it_wrote_before(tmp_path):
    out = tmp_path / "out"
    written = ["A/Dymola-linux/01-x.png", GALLERY]
    for path in written:
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(b"x")
    write_marker(out, written)
    assert sorted(stale_files(out)) == sorted(out / p for p in [*written, MARKER])
    # a file it did not write beside them is refused
    (out / "A" / "notes.txt").write_text("mine")
    with pytest.raises(OutputRefused, match=r"\(A/notes.txt\)"):
        stale_files(out)


def test_a_marker_naming_a_path_outside_the_directory_removes_nothing_there(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "precious.txt").write_text("keep")
    (out / MARKER).write_text(json.dumps({"files": ["../precious.txt", "a.png"]}))
    (out / "a.png").write_bytes(b"x")
    got = stale_files(out)
    assert sorted(got) == sorted([out / "a.png", out / MARKER])
    assert (tmp_path / "precious.txt") not in got


def test_a_file_named_as_the_directory_is_refused(tmp_path):
    (tmp_path / "f").write_text("x")
    with pytest.raises(OutputRefused) as e:
        stale_files(tmp_path / "f")
    assert str(e.value) == f"{tmp_path / 'f'} is a file: name a directory"


def test_the_marker_lists_what_was_written(tmp_path):
    write_marker(tmp_path, ["b.png", GALLERY])
    data = json.loads((tmp_path / MARKER).read_text())
    assert data["files"] == ["b.png", GALLERY]
    assert Path(tmp_path / MARKER).name == MARKER
