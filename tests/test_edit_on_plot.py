"""Edit on plot (§4.6 table, §4.7; the on-plot editing plan's Task 7): a
drag reaches ``act`` as ``kind="handle"`` with the figure hook's per-shape
payload, read by the handle's primitive type; a table edit as
``kind="rows"``. Pure ``act`` / ``available`` tests over the fixture
project, then the panel over a shell-less host in NiceGUI's in-process
user simulation — no browser."""

from __future__ import annotations

import asyncio
import copy
import json
import logging

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

from capability_host import host  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import EditOnPlot  # noqa: E402
from dstf.authoring.capabilities import Patch  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from dstf.comparison.handles import HandleEditError  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import A, D, _project, _session_with_entry  # noqa: E402
from test_session_queue import drop_item, fake_item  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it."""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def session(tmp_path):
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


#: The page's selection each test's staging is read under (D129): the
#: criterion open in the editor, whose handles are the ones that drag.
ON_D = Selection(tests=frozenset({D}), variable="x", leaf_path="/metrics/children/0")
ON_A = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics")


def _on(session, model_id: str, handle: str) -> Selection:
    """The selection a page would hold for a drag of ``handle``: the open
    criterion is the leaf the handle belongs to (None when it names none)."""
    from dstf.authoring.builtin.edit_on_plot import _leaf_path_of, _leaf_paths

    view = SessionView(session, Selection(tests=frozenset({model_id}), variable="x"))
    paths = [p for p, _ in _leaf_paths(view, model_id, "x")]
    return Selection(
        tests=frozenset({model_id}),
        variable="x",
        leaf_path=_leaf_path_of(handle, paths),
    )


async def _settled(predicate, tries: int = 40) -> None:
    """Refreshes are deferred; wait for a region's rebuild to land."""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _tree_on_d(session) -> None:
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])


def _root_leaf_on_a(session, leaf: dict) -> None:
    session.stage([PatchOp(A, "add", "/metrics", leaf)])


def _drag(session, handle: str, shape: dict, model_id: str = D):
    return EditOnPlot().act(
        SessionView(session, _on(session, model_id, handle)),
        kind="handle",
        model_id=model_id,
        variable="x",
        handle=handle,
        shape=shape,
    )


def _only_op(outcome) -> PatchOp:
    assert isinstance(outcome, Patch)
    (op,) = outcome.ops
    return op


# -- act: a drag ------------------------------------------------------------


def test_a_range_bound_drag_replaces_the_leaf(session):
    _tree_on_d(session)
    op = _only_op(
        _drag(
            session,
            "/metrics/children/1/range/max",
            {"x0": 0, "x1": 1, "y0": 1.5, "y1": 1.5},
        )
    )
    assert op == PatchOp(
        D,
        "replace",
        "/metrics/children/1",
        {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 1.5},
    )
    # One end of the line dragged: only that end's keys arrive.
    op = _only_op(_drag(session, "/metrics/children/1/range/max", {"x0": 0, "y0": 1.7}))
    assert op.value["max_value"] == 1.7
    # A horizontal-only drag moves no bound.
    assert (
        _drag(session, "/metrics/children/1/range/max", {"x0": 0.1, "x1": 1.1}) is None
    )


def test_a_tube_ring_drag_stages_one_replace_that_collapses(session):
    _tree_on_d(session)
    handle = "/metrics/children/0/tube/upper/0"
    outcome = _drag(session, handle, {"xanchor": 0.5, "yanchor": 0.8})
    op = _only_op(outcome)
    assert (op.model_id, op.op, op.path) == (D, "replace", "/metrics/children/0")
    assert op.value["tube_points"][0]["upper"] == pytest.approx(0.3)
    session.apply(outcome)
    assert session.spec.pending[-1] == op
    session.apply(_drag(session, handle, {"xanchor": 0.5, "yanchor": 0.9}))
    ops = [p for p in session.spec.pending if p.path == "/metrics/children/0"]
    assert len(ops) == 1
    assert ops[0].value["tube_points"][0]["upper"] == pytest.approx(0.4)


def test_a_rings_pixel_offsets_are_not_a_move(session):
    """A payload naming no anchor: the x0..y1 of a pixel-sized shape are
    offsets about its anchor, never a position (read as one, the band
    lower would accept a width of 7.3). A circle ring sent them from an
    off-centre grab; a path ring sends none."""
    _tree_on_d(session)
    pending = list(session.spec.pending)
    assert (
        _drag(
            session,
            "/metrics/children/0/tube/lower/0",
            {"x0": -7, "x1": 7, "y0": -7, "y1": 33},
        )
        is None
    )
    assert session.spec.pending == pending


def test_a_baseline_following_checkpoint_dragged_vertically_stages_nothing(session):
    _tree_on_d(session)
    assert (
        _drag(
            session,
            "/metrics/children/2/points/target/0",
            {"xanchor": 0.25, "yanchor": 0.9},
        )
        is None
    )


def test_a_ring_with_its_own_value_let_go_past_the_run_lands_on_its_end(session):
    """The time axis runs a little past the data, and the page holds a
    release to the axis: a checkpoint with a value of its own came back at
    t = 1.00077 on a run 0..1, read "not scored — checkpoint time outside
    actual trajectory" and lost its ring. The editor is told the run's
    span — the actual series' first and last time — and the time lands on
    its end, either side; the checkpoint is scored, and drawn there."""
    _tree_on_d(session)
    ring = "/metrics/children/2/points/target/1"
    for released, landed in ((1.00077, 1.0), (-0.0567, 0.0)):
        op = _only_op(_drag(session, ring, {"xanchor": released, "yanchor": None}))
        assert op.value["points"][1] == {
            "time": landed,
            "value": 0.75,
            "tolerance": 0.05,
        }
    session.stage([op])
    (leaf,) = [
        lv for lv in session.view(D, "x").leaves if lv.path == "/metrics/children/2"
    ]
    assert "not scored" not in leaf.summary.criterion, leaf.summary
    (drawn,) = [p for p in leaf.geometry if p.id == ring]
    assert (drawn.x, drawn.y) == (0.0, 0.75)


def test_a_following_ring_let_go_past_a_short_runs_end_lands_on_it(session):
    """A checkpoint that follows the baseline was held to the BASELINE's
    span alone. On a run shorter than its baseline — the baseline 0..1, the
    run 0..0.75 — a ring let go at 0.9 stayed there, read "not scored —
    checkpoint time outside actual trajectory" and was no longer drawn. It
    lands on the run's end: scored, its ring there."""
    work = session.project.config.work_dir
    t = [0.0, 0.25, 0.5, 0.75]
    (work / "test_0004" / "result.json").write_text(
        json.dumps(
            {"success": True, "time": t, "variables": {"x": [v * 1.001 for v in t]}}
        ),
        encoding="utf-8",
    )
    session._reload_results()
    _tree_on_d(session)
    ring = "/metrics/children/2/points/target/0"  # follows the baseline
    assert "value" not in TREE["children"][2]["points"][0]
    op = _only_op(_drag(session, ring, {"xanchor": 0.9, "yanchor": None}))
    assert op.value["points"][0] == {"time": 0.75, "tolerance": 0.05}
    session.stage([op])
    (leaf,) = [
        lv for lv in session.view(D, "x").leaves if lv.path == "/metrics/children/2"
    ]
    assert "not scored" not in leaf.summary.criterion, leaf.summary
    (drawn,) = [p for p in leaf.geometry if p.id == ring]
    assert drawn.x == 0.75


def test_the_runs_span_a_ring_is_held_to_is_the_one_inside_the_leafs_window(session):
    """The scorer slices the run to the leaf's window before a checkpoint
    is scored: the span is the samples INSIDE it — of a run sampled every
    0.25, the last at or before 0.8 is 0.75 — not the window's own end,
    where no sample stands for a checkpoint to meet."""
    tree = copy.deepcopy(TREE)
    tree["children"][2]["window"] = {"start": 0.1, "end": 0.8}
    tree["children"][2]["points"][1]["time"] = 0.5
    session.stage([PatchOp(D, "add", "/metrics", tree)])
    ring = "/metrics/children/2/points/target/1"
    for released, landed in ((1.00077, 0.75), (-0.0567, 0.25)):
        op = _only_op(_drag(session, ring, {"xanchor": released, "yanchor": None}))
        assert op.value["points"][1]["time"] == landed


def test_a_window_edge_that_is_a_sample_is_in_the_runs_span(session):
    """The scorer's slice takes both edges of the window (inclusive), and
    so does the span a ring is held to: a run sampled every 0.25 under a
    window from 0.25 to 0.75 spans exactly that, and a ring let go past
    either end lands on the sample AT the edge — not on the one inside it,
    0.5, where the ring already stands."""
    from dstf.authoring.builtin.edit_on_plot import _run_span

    tree = copy.deepcopy(TREE)
    tree["children"][2]["window"] = {"start": 0.25, "end": 0.75}
    tree["children"][2]["points"][1]["time"] = 0.5
    session.stage([PatchOp(D, "add", "/metrics", tree)])
    view = session.view(D, "x")
    (leaf,) = [leaf for leaf in view.leaves if leaf.path == "/metrics/children/2"]
    assert _run_span(view, leaf) == (0.25, 0.75)
    ring = "/metrics/children/2/points/target/1"
    for released, landed in ((1.00077, 0.75), (-0.0567, 0.25)):
        op = _only_op(_drag(session, ring, {"xanchor": released, "yanchor": None}))
        assert op.value["points"][1]["time"] == landed


def test_a_window_no_sample_stands_in_has_no_span_and_nothing_to_drag(session):
    """A window between two samples holds none of the run: there is no
    span to hold a time to, said as None — not the first and last of an
    empty series — and the declared event in it is not scored (D163), so
    nothing of it is drawn: a drag on its late edge snaps back."""
    from dstf.authoring.builtin.edit_on_plot import _run_span

    leaf = {
        "metric": "event-timing",
        "variable": "x",
        "events": [{"time": 0.35, "tolerance": 0.02}],
        "window": {"start": 0.3, "end": 0.4},
    }
    _root_leaf_on_a(session, leaf)
    view = session.view(A, "x")
    (drawn,) = view.leaves
    assert list(view.act_time) == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert _run_span(view, drawn) is None
    assert [p.id for p in drawn.geometry] == ["/metrics/window"]  # its window alone
    late = {"xanchor": 0.38, "yanchor": 0.5, "path": "M 10,0 L 17,6 L 17,-6 Z"}
    assert _drag(session, "/metrics/events/late/0", late, model_id=A) is None


def test_ids_that_name_no_movable_handle_snap_back(session):
    """A box, a window, a result line: drawn, not editable. Plotly lets
    every shape be dragged (`edits.shapePosition` has no per-shape switch),
    so such a drag snaps back and is not an error the user made."""
    _tree_on_d(session)
    assert (
        _drag(session, "/metrics/children/2/points/box/0", {"y0": 0.3, "y1": 0.4})
        is None
    )
    # A bound's dotted continuation outside its window is the bound.
    op = _only_op(
        _drag(session, "/metrics/children/1/range/max/outside", {"y0": 1.0, "y1": 1.0})
    )
    assert op.value["max_value"] == 1.0
    with pytest.raises(ValueError, match="/metrics/children/9/tube/upper/0") as none:
        _drag(
            session, "/metrics/children/9/tube/upper/0", {"xanchor": 0.5, "yanchor": 1}
        )
    assert not isinstance(none.value, HandleEditError)
    assert _drag(session, "", {"y0": 1.0}) is None


def test_a_leaf_path_is_a_prefix_only_up_to_a_slash(session):
    """`/metrics/children/10/...` names no leaf of a six-child tree; read
    as a prefix without the slash it would edit child 1's range bound."""
    _tree_on_d(session)
    with pytest.raises(ValueError, match="/metrics/children/10/range/max"):
        _drag(session, "/metrics/children/10/range/max", {"y0": 1.5, "y1": 1.5})


def test_a_single_leaf_root_is_its_own_leaf_path(session):
    _root_leaf_on_a(session, {"metric": "range", "variable": "x", "max_value": 5.0})
    op = _only_op(
        _drag(session, "/metrics/range/max", {"y0": 4.0, "y1": 4.0}, model_id=A)
    )
    assert op == PatchOp(
        A, "replace", "/metrics", {"metric": "range", "variable": "x", "max_value": 4.0}
    )


def test_metrics_without_an_editor_snap_back_before_reading_a_position(session):
    assert (
        _drag(session, "/metrics/children/0/anything", {"y0": 1.0}, model_id=A) is None
    )
    _root_leaf_on_a(
        session, {"metric": "event-timing", "variable": "x", "events": [{"time": 0.5}]}
    )
    ids = {p.id for leaf in session.view(A, "x").leaves for p in leaf.geometry}
    assert "/metrics/events/window/0" in ids  # measured: the leaf's handle Box
    assert (
        _drag(session, "/metrics/events/window/0", {"x0": 0.4, "x1": 0.6}, model_id=A)
        is None
    )


def test_a_refused_drag_reads_its_reason_without_a_location(session):
    """Several leaves on one variable can refuse the same local id, so the
    error names its leaf; the editor it is shown in already does."""
    from dstf.authoring.errors import user_text

    _tree_on_d(session)
    with pytest.raises(HandleEditError) as box:
        _drag(session, "/metrics/children/1/range/max", {"y0": -1.0, "y1": -1.0})
    assert str(box.value).startswith("/metrics/children/1: range/max: ")
    assert user_text(box.value) == "min_value -0.5 would exceed max_value -1"


def test_a_local_no_editor_knows_reads_a_plain_reason():
    from dstf.comparison.handles import HandleEdit, edit_range_handle

    with pytest.raises(HandleEditError) as box:
        edit_range_handle(
            HandleEdit(
                leaf={"metric": "range", "variable": "x", "max_value": 1.0},
                local="range/nope",
                x=None,
                y=1.0,
                reference=None,
            )
        )
    assert str(box.value) == "range/nope cannot be moved on the plot"
    assert box.value.reason == "this cannot be moved on the plot"


#: A bound drawn solid inside its window and dotted (`<id>/outside`, full
#: width) beyond it.
WINDOWED_RANGE = {
    "metric": "range",
    "variable": "x",
    "max_value": 0.9,
    "window": {"start": 0.25, "end": 0.75},
}


def test_a_drag_on_a_bounds_outside_line_moves_the_bound(session):
    """The `/outside` line is full width, so inside the window it lies over
    the bound too: a grab there arrives under its name (measured in
    Chromium, 2026-09-26) and must move the bound."""
    _root_leaf_on_a(session, copy.deepcopy(WINDOWED_RANGE))
    op = _only_op(
        _drag(session, "/metrics/range/max/outside", {"y0": 1.2, "y1": 1.2}, model_id=A)
    )
    assert op.op == "replace" and op.path == "/metrics"
    assert op.value["max_value"] == 1.2


def test_a_drag_on_a_window_stages_nothing(session):
    _root_leaf_on_a(session, copy.deepcopy(WINDOWED_RANGE))
    ids = {p.id for leaf in session.view(A, "x").leaves for p in leaf.geometry}
    assert "/metrics/window" in ids
    assert _drag(session, "/metrics/window", {"x0": 0.3, "x1": 0.9}, model_id=A) is None


@pytest.mark.parametrize(
    "leaf, handle",
    [
        (
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "final"},
                "tolerance_mode": "rel",
                "tolerance": 0.01,
            },
            "/metrics/scalar/actual",
        ),
        (
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "final"},
                "tolerance_mode": "rel",
                "tolerance": 0.01,
            },
            "/metrics/scalar/reference",
        ),
        (
            {
                "metric": "dominant-frequency",
                "variable": "x",
                "peaks": [{"freq": 1.0, "tolerance": 0.5, "tolerance_mode": "rel"}],
            },
            "/metrics/peaks/detected/0",
        ),
    ],
    ids=["scalar-actual", "scalar-reference", "detected-peak"],
)
def test_a_drag_on_a_result_line_or_marker_stages_nothing(session, leaf, handle):
    """An HLine or a Marker is dragged only when it is `kind="handle"`: a
    result line or a detected peak is drawn, not edited."""
    _root_leaf_on_a(session, copy.deepcopy(leaf))
    kinds = {
        p.id: (type(p).__name__, p.kind)
        for lv in session.view(A, "x").leaves
        for p in lv.geometry
    }
    assert kinds[handle][0] in ("HLine", "Marker") and kinds[handle][1] != "handle"
    # A move for either type: a Marker's anchors, an HLine's y0.
    shape = {"xanchor": 0.3, "yanchor": 0.9, "y0": 0.9}
    assert _drag(session, handle, shape, model_id=A) is None


def test_another_leafs_box_snaps_back_while_one_is_open(session):
    """What is not a handle snaps back whichever criterion is open: it is
    resolved before the open-leaf check, which refuses only a handle of
    another criterion."""
    _tree_on_d(session)
    outcome = EditOnPlot().act(
        SessionView(session, ON_D),  # the tube at children/0 is open
        kind="handle",
        model_id=D,
        variable="x",
        handle="/metrics/children/2/points/box/0",
        shape={"x0": 0.3, "x1": 0.4},
    )
    assert outcome is None


# -- act: a table -----------------------------------------------------------


def test_a_rows_edit_replaces_the_leaf_dropping_blank_cells(session):
    _tree_on_d(session)
    op = _only_op(
        EditOnPlot().act(
            SessionView(session, ON_D),
            kind="rows",
            model_id=D,
            path="/metrics/children/0",
            field="tube_points",
            rows=[
                {"time": 0.5, "upper": 0.1, "lower": 0.2},
                {"time": 0.9, "upper": 0.1, "lower": None},
            ],
        )
    )
    assert op.path == "/metrics/children/0" and op.op == "replace"
    assert op.value["tube_points"][1] == {"time": 0.9, "upper": 0.1}


def test_a_blank_required_cell_is_refused_by_name(session):
    _tree_on_d(session)
    pending = list(session.spec.pending)
    inputs = dict(
        kind="rows",
        model_id=D,
        path="/metrics/children/0",
        field="tube_points",
        rows=[{"time": None, "upper": 0.1, "lower": 0.1}],
    )
    with pytest.raises(ValueError, match="row 0: time is required"):
        EditOnPlot().act(SessionView(session, ON_D), **inputs)
    with pytest.raises(ValueError, match="row 0: time is required"):
        session.apply(EditOnPlot().act(SessionView(session, ON_D), **inputs))
    assert session.spec.pending == pending


def _rows(
    session, rows, *, model_id=D, path="/metrics/children/0", field="tube_points"
):
    return EditOnPlot().act(
        SessionView(session, ON_A if model_id == A else ON_D),
        kind="rows",
        model_id=model_id,
        path=path,
        field=field,
        rows=rows,
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["time", "upper"])
def test_a_non_finite_cell_is_refused_by_row_and_column(session, bad, key):
    _tree_on_d(session)
    rows = [
        {"time": 0.5, "upper": 0.1, "lower": 0.2},
        {"time": 0.9, "upper": 0.1, "lower": 0.1},
    ]
    rows[1][key] = bad
    with pytest.raises(ValueError, match=f"row 1: {key} = .* is not a finite number"):
        _rows(session, rows)


def test_a_tube_table_with_no_width_in_any_row_is_refused(session):
    """The reader's own refusal (`_interpolate_tube_widths`), by name."""
    _tree_on_d(session)
    with pytest.raises(ValueError, match="tube_points: no row has an upper or a lower"):
        _rows(session, [{"time": 0.5, "upper": None, "lower": None}, {"time": 0.9}])
    # One width in one row is enough; an empty table is no tube width at all.
    assert _rows(session, [{"time": 0.5, "lower": 0.1}, {"time": 0.9}]) is not None
    assert _rows(session, []) is not None


def test_a_rows_edit_names_what_its_path_or_model_is_instead(session):
    _tree_on_d(session)
    ok = [{"time": 0.5, "upper": 0.1}]
    for path in ("/metrics/children/9", "/metrics/children/0/tube_points", "/x/0"):
        with pytest.raises(ValueError, match=f"^{path} names no criterion of {D}$"):
            _rows(session, ok, path=path)
    with pytest.raises(ValueError, match="^/metrics is a group, not a criterion$"):
        _rows(session, ok, path="/metrics")
    with pytest.raises(ValueError, match="^Nope.Model is not a test of this project$"):
        _rows(session, ok, model_id="Nope.Model")


def test_a_rows_edit_equal_to_the_current_list_stages_nothing(session):
    _tree_on_d(session)
    current = session.tree(D)["children"][0]["tube_points"]
    assert _rows(session, copy.deepcopy(current)) is None


def test_a_rows_edit_of_a_field_without_columns_is_refused(session):
    _tree_on_d(session)
    with pytest.raises(ValueError, match="tube has no table 'tube_width_mode'"):
        _rows(session, [], field="tube_width_mode")
    with pytest.raises(ValueError, match="range has no table 'tube_points'"):
        _rows(session, [], path="/metrics/children/1")


# -- available --------------------------------------------------------------


def test_available_needs_one_open_criterion_with_handles(session):
    _tree_on_d(session)
    cap = EditOnPlot()
    one = frozenset({D})

    def at(path):
        return SessionView(session, Selection(tests=one, variable="x", leaf_path=path))

    assert (
        cap.available(SessionView(session, Selection(tests=one, variable="x")))
        == "open one criterion"
    )
    both = Selection(tests=frozenset({A, D}), variable="x", leaf_path="/metrics")
    assert cap.available(SessionView(session, both)) == "open one criterion"
    assert cap.available(at("/metrics/children/0")) is True
    # scalar HAS an edit_handle; only nrmse has neither a handle nor a
    # table — A's untouched implicit leaf.
    on_a = Selection(
        tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0"
    )
    assert cap.available(SessionView(session, on_a)) == "nrmse has no on-plot handles"
    assert cap.available(at("/metrics/children/9")) == (
        "/metrics/children/9 is not a criterion on x"
    )


@pytest.mark.parametrize(
    ("reduce", "expected"),
    [("max", True), ("integral", "this criterion draws nothing on its plot to drag")],
)
def test_a_scalar_is_edited_on_its_plot_only_where_it_draws_a_line(
    session, reduce, expected
):
    # An integral is in the signal's units times seconds: no line, nothing
    # to drag, so the tool does not offer the plot as a place to edit it.
    _root_leaf_on_a(
        session,
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": reduce},
            "tolerance": 0.1,
            "tolerance_mode": "rel",
        },
    )
    on_a = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics")
    assert EditOnPlot().available(SessionView(session, on_a)) == expected


@pytest.mark.parametrize(
    ("run_holds_v", "reason"),
    [
        (True, "variable 'v' not found in the baseline"),
        (False, "variable 'v' not found in the result"),
    ],
)
def test_a_criterion_not_scored_is_not_edited_on_the_plot(
    tmp_path, run_holds_v, reason
):
    """A points or tube criterion on a variable one side lacks carries its
    own metric and no geometry: nothing of it is on the plot to drag or
    click, and its place says why in the criterion's own words."""
    cfg, work = _project(tmp_path)
    if run_holds_v:
        result = work / "test_0004" / "result.json"
        data = json.loads(result.read_text(encoding="utf-8"))
        data["variables"]["v"] = list(data["variables"]["x"])
        result.write_text(json.dumps(data), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)
    session.stage(
        [
            PatchOp(
                D,
                "add",
                "/metrics",
                {
                    "combinator": "and",
                    "children": [
                        {"metric": "nrmse", "variable": "x"},
                        {
                            "metric": "points",
                            "variable": "v",
                            "points": [{"time": 0.5}],
                        },
                        {
                            "metric": "tube",
                            "variable": "v",
                            "tube_width_mode": "band",
                            "tube_abs": 0.1,
                        },
                    ],
                },
            )
        ]
    )
    said = (
        f"Not scored: {reason}. Nothing of it is drawn on the plot to drag; "
        "the fields above still edit it."
    )
    for leaf_path in ("/metrics/children/1", "/metrics/children/2"):
        on_v = Selection(tests=frozenset({D}), variable="v", leaf_path=leaf_path)
        assert EditOnPlot().available(SessionView(session, on_v)) == said


@pytest.mark.parametrize(
    "leaf",
    [
        {"metric": "points", "variable": "x", "points": [{"time": 5.5}]},
        {"metric": "event-timing", "variable": "x"},
    ],
    ids=["points", "events"],
)
def test_a_window_off_the_run_is_not_edited_on_the_plot(tmp_path, leaf):
    """A points or event-timing criterion whose window holds none of the
    run's samples is not scored, as a range so windowed is: its place
    says why, and nothing of it is offered to drag."""
    cfg, work = _project(tmp_path)
    session = Session()
    session.open(cfg, work_dir=work)
    windowed = {**leaf, "window": {"start": 5.0, "end": 6.0}}
    session.stage(
        [
            PatchOp(
                D,
                "add",
                "/metrics",
                {"combinator": "and", "children": [windowed]},
            )
        ]
    )
    on_x = Selection(
        tests=frozenset({D}), variable="x", leaf_path="/metrics/children/0"
    )
    assert EditOnPlot().available(SessionView(session, on_x)) == (
        "Not scored: no actual samples to check (empty trajectory or window). "
        "Nothing of it is drawn on the plot to drag; the fields above still edit it."
    )


def test_a_drag_on_another_leafs_handle_is_refused_by_name(session):
    """The editor is the open criterion's. Another leaf gets no rings while
    this one is open, but its bound LINES are still drawn and draggable —
    dragging one must not edit a criterion nobody opened."""
    _tree_on_d(session)
    view = SessionView(
        session,
        Selection(tests=frozenset({D}), variable="x", leaf_path="/metrics/children/0"),
    )
    with pytest.raises(
        HandleEditError,
        match="/metrics/children/1/range/max is not a handle of /metrics/children/0",
    ):
        EditOnPlot().act(
            view,
            kind="handle",
            model_id=D,
            variable="x",
            handle="/metrics/children/1/range/max",
            shape={"y0": 1.5, "y1": 1.5},
        )


def test_a_drag_with_no_open_criterion_still_stages(session):
    """The refusal is "another leaf's", so it needs an open one to be
    another THAN: with none open (a drag reaching the capability outside the
    editor) the handle's own leaf is edited."""
    _tree_on_d(session)
    view = SessionView(session, Selection(tests=frozenset({D}), variable="x"))
    outcome = EditOnPlot().act(
        view,
        kind="handle",
        model_id=D,
        variable="x",
        handle="/metrics/children/1/range/max",
        shape={"y0": 1.2, "y1": 1.2},
    )
    assert _only_op(outcome).path == "/metrics/children/1"


async def test_a_tables_heading_is_the_fields_own_label(session):
    """The heading said `{path} · {metric} · {field}` — every word of which
    the open criterion's row says directly above the editor (the Task 6
    screenshot). It names the field, in the words the Config declared."""
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, _errors, _figure):
        await user.should_see("Control points")
        (heading,) = [
            e
            for e in user.find(ui.label).elements
            if e.text == "Control points"  # TubeConfig.tube_points' label
        ]
        assert "text-bold" in heading.classes and "text-xs" in heading.classes
        assert not [e for e in user.find(ui.label).elements if " · tube · " in e.text]


async def test_the_panel_draws_only_the_open_leafs_tables(session):
    _tree_on_d(session)
    sel = Selection(tests=frozenset({D}), variable="x", leaf_path="/metrics/children/0")
    async with host(session, EditOnPlot(), sel) as (user, _errors, _figure):
        await user.should_see(marker="/metrics/children/0:tube_points:0:time")
        await user.should_not_see(marker="/metrics/children/2:points:0:time")


def test_a_leaf_with_a_table_but_no_drag_editor_is_available(session):
    """event-timing has no `edit_handle`, but its `events` declare columns."""
    _root_leaf_on_a(
        session, {"metric": "event-timing", "variable": "x", "events": [{"time": 0.5}]}
    )
    assert EditOnPlot().available(SessionView(session, ON_A)) is True


def test_available_does_not_read_the_queue(session):
    """A queued or running test stays editable (D150): `available` answers
    for the test as it stands, and its place in the queue is no reason."""
    _tree_on_d(session)
    view = SessionView(session, ON_D)
    waiting = fake_item(session, D, ItemState.WAITING)
    assert EditOnPlot().available(view) is True
    op = _only_op(
        _drag(session, "/metrics/children/1/range/max", {"y0": 1.5, "y1": 1.5})
    )
    session.apply(Patch(ops=(op,)))
    assert session.spec.pending[-1].value["max_value"] == 1.5
    drop_item(session, waiting)

    running = fake_item(session, D, ItemState.RUNNING)
    assert EditOnPlot().available(view) is True
    # The Session takes the start in: the last result goes, and the answer
    # is the one a test with no result gets — the queue's item or not.
    session._item_started(running)
    assert D not in session.project.results
    held = EditOnPlot().available(view)
    drop_item(session, running)
    assert held == EditOnPlot().available(view)
    assert "running" not in str(held)


# -- the panel ---------------------------------------------------------------


def _pending_value(session, path: str):
    ops = [p for p in session.spec.pending if p.path == path]
    return ops[-1].value if ops else None


def _cell(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def test_the_panel_draws_a_table_per_list_field_and_stages_edits(session):
    _tree_on_d(session)
    on_points = Selection(
        tests=frozenset({D}), variable="x", leaf_path="/metrics/children/2"
    )
    # One criterion is open at a time, so its tables are drawn one panel at
    # a time: two hosts, each its own simulation, run here in sequence.
    async with host(session, EditOnPlot(), on_points) as (user, errors, _figure):
        await user.should_see("Declared points")
        p2 = "/metrics/children/2:points"
        assert isinstance(_cell(user, f"{p2}:0:tolerance_mode"), ui.select)
        assert isinstance(_cell(user, f"{p2}:1:value"), ui.number)
        assert _cell(user, f"{p2}:1:value").value == 0.75
    async with host(session, EditOnPlot(), ON_D) as (user, errors, _figure):
        await user.should_see("Control points")
        t0 = "/metrics/children/0:tube_points"
        assert isinstance(_cell(user, f"{t0}:1:lower"), ui.number)
        with pytest.raises(AssertionError):
            user.find(marker=f"{t0}:2:time")  # two rows, no third

        _cell(user, f"{t0}:0:upper").set_value(0.25)
        await _settled(
            lambda: (
                (_pending_value(session, "/metrics/children/0") or {})
                .get("tube_points", [{}])[0]
                .get("upper")
                == 0.25
            )
        )

        user.find(marker=f"{t0}:add").click()
        # Times 0.5 and 0.9 over the reference [0, 1]: the largest gap is
        # [0, 0.5], so the new row lands at 0.25 (Add row never lands on an
        # existing time; it used to put the row at the span's end).
        added = {"time": 0.25, "upper": 0.1, "lower": 0.1}
        await _settled(
            lambda: (
                len(
                    (_pending_value(session, "/metrics/children/0") or {}).get(
                        "tube_points", []
                    )
                )
                == 3
            )
        )
        assert _pending_value(session, "/metrics/children/0")["tube_points"][2] == added
        user.find(marker=f"{t0}:2:remove").click()
        await _settled(
            lambda: (
                len(_pending_value(session, "/metrics/children/0")["tube_points"]) == 2
            )
        )
        assert session.tree(D)["children"][0]["tube_points"] == [
            {"time": 0.5, "upper": 0.25, "lower": 0.2},
            {"time": 0.9, "upper": 0.1, "lower": 0.1},
        ]


async def test_the_tables_line_says_the_plot_and_the_table(session):
    """Not "drag a ring": the plot has an arrow per tolerance side too,
    which the editor's own hint line names."""
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, _errors, _figure):
        await user.should_see("drag on the plot, or edit the table")
        await user.should_not_see("drag a ring")


async def test_the_tables_line_is_said_only_where_a_table_is_drawn(session):
    """A range has bound lines and no table: "drag on the plot, or edit
    the table" named a table that is not there. The editor's own hint line
    says what its plot drags."""
    _tree_on_d(session)
    on_range = Selection(
        tests=frozenset({D}), variable="x", leaf_path="/metrics/children/1"
    )
    async with host(session, EditOnPlot(), on_range) as (user, _errors, figure):
        await user.should_not_see("edit the table")
        assert [kind for kind, _ in figure.handlers] == [
            "plotly_relayout",
            "dstf_move",
            "dstf_click",
        ]


async def test_the_rows_scroll_and_add_row_does_not(session):
    """A long table would push the editor off the card, so the rows sit in a
    scrolling container — `Add row` sits after it, where a table of twenty
    rows cannot scroll it out of reach."""
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, _errors, _figure):
        (cell,) = user.find(marker="/metrics/children/0:tube_points:0:time").elements
        assert any("dstf-table-scroll" in a.classes for a in cell.ancestors())
        (add,) = user.find(marker="/metrics/children/0:tube_points:add").elements
        assert not any("dstf-table-scroll" in a.classes for a in add.ancestors())


async def test_a_blank_required_cell_is_marked_and_held_until_it_is_filled(session):
    """Clearing a required cell to retype it: nothing is submitted (a blank
    would be refused, standing as an error above a table whose cell says
    nothing), the cell is marked, and the keystroke that fills it submits
    the whole list. The mark is the cell's own VALIDATION: written as an
    `error` prop it never showed in a browser — NiceGUI validates a field
    after its change handlers and resets an `error` it did not set — while
    this test, reading the prop's key, passed."""
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, errors, _figure):
        marker = "/metrics/children/0:tube_points:0:time"
        cell = _cell(user, marker)
        pending = list(session.spec.pending)
        cell.set_value(None)
        await asyncio.sleep(0.25)  # two ticks: anything submitted has landed
        assert session.spec.pending == pending
        assert "edit-on-plot" not in errors
        assert cell.error == "time is required"
        # Only the blank one: its neighbours say nothing.
        assert _cell(user, "/metrics/children/0:tube_points:1:time").error is None
        assert _cell(user, marker) is cell  # not rebuilt
        cell.set_value(0.6)
        await _settled(
            lambda: (
                (_pending_value(session, "/metrics/children/0") or {})
                .get("tube_points", [{}])[0]
                .get("time")
                == 0.6
            )
        )
        assert _pending_value(session, "/metrics/children/0")["tube_points"] == [
            {"time": 0.6, "upper": 0.1, "lower": 0.2},
            {"time": 0.9, "upper": 0.1, "lower": 0.1},
        ]
        assert cell.error is None
        await asyncio.sleep(0.25)
        assert _cell(user, marker) is cell


async def test_add_row_on_an_empty_points_table(session):
    _root_leaf_on_a(session, {"metric": "points", "variable": "x", "points": []})
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        await user.should_see("Declared points")
        user.find(marker="/metrics:points:add").click()
        await _settled(
            lambda: (_pending_value(session, "/metrics") or {}).get("points")
        )
        assert _pending_value(session, "/metrics")["points"] == [
            {"time": 0.5, "tolerance_mode": "abs"}
        ]


_PAIR = {"lower": 0.05, "upper": 0.3}


@pytest.mark.parametrize(
    ("leaf", "widths"),
    [
        ({"tube_width_mode": "band", "tube_abs": 0.3}, (0.3, 0.3)),
        ({"tube_width_mode": "band", "tube_abs": _PAIR}, (0.3, 0.05)),
        ({"tube_width_mode": "rel", "tube_rel": 0.3}, (0.3, 0.3)),
        ({"tube_width_mode": "rel", "tube_rel": _PAIR}, (0.3, 0.05)),
    ],
    ids=["band, a number", "band, a pair", "rel, a number", "rel, a pair"],
)
async def test_add_row_on_a_constant_tube_changes_no_verdict(session, leaf, widths):
    """A tube of constant width has no control point. Its FIRST one takes
    the width in effect — the constant's, a number or a lower/upper pair,
    in the unit its width mode reads a row in — so `Add row` alone moves
    nothing: it gave upper 0 and lower 0, and a passing tube read "0.0%
    inside → FAIL" at one click."""
    tube = {"metric": "tube", "variable": "x", **leaf}
    _root_leaf_on_a(session, tube)
    before = session.comparisons[A].variables[0]
    assert before.passed and before.tube_points_inside == 1
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        user.find(marker="/metrics:tube_points:add").click()
        await _settled(
            lambda: (_pending_value(session, "/metrics") or {}).get("tube_points")
        )
        upper, lower = widths
        assert _pending_value(session, "/metrics") == {
            **tube,
            "tube_points": [{"time": 0.5, "upper": upper, "lower": lower}],
        }
        assert "edit-on-plot" not in errors
    after = session.comparisons[A].variables[0]
    assert (after.passed, after.tube_points_inside) == (True, 1)


def test_a_first_control_point_takes_the_width_in_effect():
    """Per width mode: `band` the constant `tube_abs`, `rel` the constant
    `tube_rel` (a row's widths are fractions there). `abs` has no constant
    width — its rows are literal bounds and it cannot be scored without
    one — so its first row is still 0 / 0, as is a tube that names no
    width."""
    import dataclasses

    from dstf.authoring.builtin.edit_on_plot import _new_row
    from dstf.comparison.modes import TubeConfig

    (field,) = [f for f in dataclasses.fields(TubeConfig) if f.name == "tube_points"]
    tube, span = field.metadata["columns"], (0.0, 1.0)

    def first(**leaf):
        row = _new_row(tube, [], span, {"metric": "tube", **leaf})
        assert row.pop("time") == 0.5
        return row

    assert first(tube_width_mode="band", tube_abs=0.05) == {
        "upper": 0.05,
        "lower": 0.05,
    }
    assert first(tube_width_mode="band", tube_abs=_PAIR, tube_rel=9) == {
        "upper": 0.3,
        "lower": 0.05,
    }
    assert first(tube_width_mode="rel", tube_rel=0.02, tube_abs=9) == {
        "upper": 0.02,
        "lower": 0.02,
    }
    assert first(tube_width_mode="rel", tube_rel=_PAIR) == {"upper": 0.3, "lower": 0.05}
    assert first(tube_width_mode="abs", tube_abs=0.05) == {"upper": 0.0, "lower": 0.0}
    assert first(tube_width_mode="band") == {"upper": 0.0, "lower": 0.0}
    # a width nobody can read is no width: the row is the table's own
    assert first(tube_width_mode="band", tube_abs="wide") == {
        "upper": 0.0,
        "lower": 0.0,
    }
    # and after a row, the next is a copy of the last, whatever the constant
    last = {"time": 0.9, "upper": 0.1, "lower": 0.2}
    assert _new_row(tube, [last], span, {"tube_width_mode": "band", "tube_abs": 7}) == {
        "time": 0.45,
        "upper": 0.1,
        "lower": 0.2,
    }


def test_add_row_rules():
    """The empty-table rule per column type, and the tube exception."""
    from dstf.authoring.builtin.edit_on_plot import _new_row
    from dstf.comparison.modes import (
        DominantFrequencyConfig,
        EventTimingConfig,
        PointsConfig,
        TubeConfig,
    )

    def columns(cls, name):
        import dataclasses

        (f,) = [f for f in dataclasses.fields(cls) if f.name == name]
        return f.metadata["columns"]

    span = (0.0, 1.0)
    tube = columns(TubeConfig, "tube_points")
    assert _new_row(tube, [], span) == {"time": 0.5, "upper": 0.0, "lower": 0.0}
    assert _new_row(tube, [], None) == {"time": 0.0, "upper": 0.0, "lower": 0.0}
    assert _new_row(columns(PointsConfig, "points"), [], span) == {
        "time": 0.5,
        "tolerance_mode": "abs",
    }
    assert _new_row(columns(EventTimingConfig, "events"), [], span) == {"time": 0.5}
    assert _new_row(columns(DominantFrequencyConfig, "peaks"), [], span) == {
        "freq": 0.0,
        "tolerance_mode": "rel",
    }
    last = {"time": 0.9, "upper": 0.1, "lower": 0.1}
    assert _new_row(tube, [last], span) == {"time": 0.45, "upper": 0.1, "lower": 0.1}


def test_add_row_lands_in_the_largest_gap_never_on_an_existing_time():
    """The midpoint of the largest gap among the span's ends and the rows'
    times (the first such on a tie); without a reference, among the rows'
    times alone, or past the last row when there are fewer than two."""
    import dataclasses

    from dstf.authoring.builtin.edit_on_plot import _new_row
    from dstf.comparison.modes import TubeConfig

    (f,) = [f for f in dataclasses.fields(TubeConfig) if f.name == "tube_points"]
    tube = f.metadata["columns"]
    span = (0.0, 1.0)

    def times(rows):
        return [r["time"] for r in rows]

    rows = [
        {"time": 0.5, "upper": 0.1, "lower": 0.1},
        {"time": 1.0, "upper": 0.2, "lower": 0.2},
    ]
    rows.append(_new_row(tube, rows, span))
    assert rows[-1] == {"time": 0.25, "upper": 0.2, "lower": 0.2}
    rows.append(_new_row(tube, rows, span))
    assert times(rows) == [0.5, 1.0, 0.25, 0.75]
    rows.append(_new_row(tube, rows, span))  # four gaps of 0.25: the first
    assert times(rows)[-1] == 0.125
    two = [{"time": 0.0, "upper": 0.1}, {"time": 1.0, "upper": 0.1}]
    assert _new_row(tube, two, span)["time"] == 0.5
    assert _new_row(tube, [], span)["time"] == 0.5
    # No reference.
    assert _new_row(tube, [{"time": 0.2}, {"time": 1.0}, {"time": 0.4}], None)[
        "time"
    ] == pytest.approx(0.7)
    assert _new_row(tube, [{"time": 0.2, "upper": 0.1}], None)["time"] == 1.2
    assert _new_row(tube, [], None)["time"] == 0.0


# -- a scalar's two lines: what its baseline form allows ----------------------

BASELINE_SCALAR_LEAF = {
    "metric": "scalar",
    "variable": "x",
    "quantity": {"reduce": "final"},
    "tolerance": 0.25,
}


def _scalar_reference(session) -> float:
    lines = {
        p.id: p.value for leaf in session.view(A, "x").leaves for p in leaf.geometry
    }
    assert {"/metrics/scalar/upper", "/metrics/scalar/lower"} <= set(lines)
    return lines["/metrics/scalar/reference"]


def test_a_drag_on_a_scalars_line_sets_that_side_of_its_tolerance(session):
    """The baseline's number reaches the editor from the leaf's own
    `scalar/reference` line."""
    _root_leaf_on_a(session, copy.deepcopy(BASELINE_SCALAR_LEAF))
    ref = _scalar_reference(session)
    y = ref + 0.5
    op = _only_op(
        _drag(session, "/metrics/scalar/upper", {"y0": y, "y1": y}, model_id=A)
    )
    assert op == PatchOp(
        A, "replace", "/metrics", {**BASELINE_SCALAR_LEAF, "tolerance": 0.5}
    )
    # A pair: the lower line moves the lower side alone.
    pair = {**BASELINE_SCALAR_LEAF, "tolerance": {"lower": 0.25, "upper": 0.5}}
    session.stage([PatchOp(A, "replace", "/metrics", copy.deepcopy(pair))])
    y = _scalar_reference(session) - 1.0
    op = _only_op(
        _drag(session, "/metrics/scalar/lower", {"y0": y, "y1": y}, model_id=A)
    )
    assert op.value["tolerance"] == {"lower": 1.0, "upper": 0.5}


def test_a_scalars_line_dragged_across_its_baseline_is_refused(session):
    from dstf.authoring.errors import user_text

    _root_leaf_on_a(session, copy.deepcopy(BASELINE_SCALAR_LEAF))
    y = _scalar_reference(session) - 0.5
    with pytest.raises(HandleEditError) as box:
        _drag(session, "/metrics/scalar/upper", {"y0": y, "y1": y}, model_id=A)
    assert str(box.value) == (
        "/metrics: scalar/upper: the upper side would cross its target"
    )
    assert user_text(box.value) == "the upper side would cross its target"


def test_a_scalars_line_of_a_criterion_that_is_not_open_is_refused(session):
    _root_leaf_on_a(session, copy.deepcopy(BASELINE_SCALAR_LEAF))
    y = _scalar_reference(session) + 0.5
    elsewhere = Selection(
        tests=frozenset({A}), variable="x", leaf_path="/metrics/children/9"
    )
    with pytest.raises(HandleEditError, match="is not a handle of") as box:
        EditOnPlot().act(
            SessionView(session, elsewhere),
            kind="handle",
            model_id=A,
            variable="x",
            handle="/metrics/scalar/upper",
            shape={"y0": y, "y1": y},
        )
    assert box.value.reason == "Open that criterion to drag its lines."


# -- a row's tolerance has two sides ------------------------------------------

#: Per list field whose rows take a two-sided tolerance: a leaf on A's root
#: holding one row with the number 0.1, the field, and what the row's other
#: keys are.
SIDED = {
    "points": (
        {
            "metric": "points",
            "variable": "x",
            "points": [{"time": 0.5, "value": 0.5, "tolerance": 0.1}],
        },
        {"time": 0.5, "value": 0.5},
    ),
    "events": (
        {
            "metric": "event-timing",
            "variable": "x",
            "events": [{"time": 0.5, "tolerance": 0.1}],
        },
        {"time": 0.5},
    ),
    "peaks": (
        {
            "metric": "dominant-frequency",
            "variable": "x",
            "peaks": [{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
        },
        {"freq": 1.0, "tolerance_mode": "rel"},
    ),
}
PAIR = {"lower": 0.1, "upper": 0.3}


def _a_rows(session, field: str) -> list[dict]:
    return session.tree(A)[field]


@pytest.mark.parametrize("field", list(SIDED))
async def test_a_pair_typed_in_a_row_stages_one_replace_holding_it(session, field):
    """A row's tolerance is the form's control: a number opens linked, the
    toggle writes it twice, and a side typed stages the pair — under the
    column's own key, in one pending replace of the leaf."""
    leaf, rest = SIDED[field]
    _root_leaf_on_a(session, leaf)
    pending = len(session.spec.pending)
    cell = f"/metrics:{field}:0:tolerance"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        lower, upper = _cell(user, f"{cell}:lower"), _cell(user, f"{cell}:upper")
        link = _cell(user, f"{cell}:link")
        assert (lower.value, upper.value) == (0.1, 0.1)
        assert link.props["icon"] == "link"
        user.find(marker=f"{cell}:link").click()
        await _settled(
            lambda: (
                _a_rows(session, field)[0]["tolerance"] == {"lower": 0.1, "upper": 0.1}
            )
        )
        upper.set_value(0.3)
        await _settled(lambda: _a_rows(session, field)[0]["tolerance"] == PAIR)
        assert not errors
        # The pair, under `tolerance` and nowhere else.
        assert _a_rows(session, field) == [{**rest, "tolerance": PAIR}]
        assert len(session.spec.pending) == pending
        op = session.spec.pending[-1]
        assert (op.op, op.path) in (("add", "/metrics"), ("replace", "/metrics"))


async def test_a_rows_pair_opens_unlinked_and_a_blank_one_is_left_out(session):
    """What the file holds is what the row shows: a pair unlinked with both
    values, a number linked, nothing as two empty fields — and an optional
    tolerance left empty is not written (the row follows the criterion's)."""
    leaf = {
        "metric": "points",
        "variable": "x",
        "points": [{"time": 0.5, "tolerance": PAIR, "time_tolerance": 0.2}],
    }
    _root_leaf_on_a(session, leaf)
    row = "/metrics:points:0"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        assert _cell(user, f"{row}:tolerance:link").props["icon"] == "link_off"
        assert _cell(user, f"{row}:tolerance:lower").value == 0.1
        assert _cell(user, f"{row}:tolerance:upper").value == 0.3
        assert _cell(user, f"{row}:time_tolerance:link").props["icon"] == "link"
        assert _cell(user, f"{row}:time_tolerance:lower").value == 0.2
        _cell(user, f"{row}:time_tolerance:upper").set_value(None)
        await _settled(lambda: "time_tolerance" not in _a_rows(session, "points")[0])
        assert not errors
        assert _a_rows(session, "points") == [{"time": 0.5, "tolerance": PAIR}]
        assert _cell(user, f"{row}:time_tolerance:lower").value is None


async def test_a_cells_sides_are_named_by_its_column(session):
    """A value's sides are `−` and `+`; a point's time tolerance is how
    much BEFORE and AFTER, an event's how much EARLY and LATE — the
    column's own third element, not the default."""
    points = {
        "metric": "points",
        "variable": "x",
        "points": [{"time": 0.5, "time_tolerance": 0.1}],
    }
    _root_leaf_on_a(session, points)
    async with host(session, EditOnPlot(), ON_A) as (user, _errors, _figure):
        row = "/metrics:points:0"

        def labels(key):
            return tuple(
                _cell(user, f"{row}:{key}:{side}").props["label"]
                for side in ("lower", "upper")
            )

        assert labels("tolerance") == ("− tolerance", "+ tolerance")
        assert labels("time_tolerance") == (
            "before time_tolerance",
            "after time_tolerance",
        )
        # The time column's write goes to the time column.
        _cell(user, f"{row}:time_tolerance:lower").set_value(0.2)
        await _settled(
            lambda: _a_rows(session, "points") == [{"time": 0.5, "time_tolerance": 0.2}]
        )
    session.discard()
    _root_leaf_on_a(session, SIDED["events"][0])
    async with host(session, EditOnPlot(), ON_A) as (user, _errors, _figure):
        cell = "/metrics:events:0:tolerance"
        assert _cell(user, f"{cell}:lower").props["label"] == "early tolerance"
        assert _cell(user, f"{cell}:upper").props["label"] == "late tolerance"


async def test_a_half_pair_in_a_row_stages_nothing_and_marks_the_blank_side(session):
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = dict(PAIR)
    _root_leaf_on_a(session, leaf)
    cell = "/metrics:points:0:tolerance"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        lower = _cell(user, f"{cell}:lower")
        lower.set_value(None)
        await asyncio.sleep(0.25)  # two ticks: anything submitted has landed
        assert session.spec.pending == pending and "edit-on-plot" not in errors
        assert lower.error == "required"
        assert _cell(user, f"{cell}:upper").error is None
        assert _cell(user, f"{cell}:lower") is lower  # not rebuilt


async def test_a_half_pair_holds_the_whole_list_until_it_is_filled(session):
    """With one side blank the control hands the table nothing, so the row
    still holds the pair it had: another cell's edit, submitted, staged
    that STALE pair beside the new value. While any cell is half a pair the
    table submits nothing; the keystroke that fills it submits the list."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = dict(PAIR)
    _root_leaf_on_a(session, leaf)
    row = "/metrics:points:0"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _cell(user, f"{row}:tolerance:lower").set_value(None)
        _cell(user, f"{row}:time").set_value(0.6)
        await asyncio.sleep(0.25)
        assert session.spec.pending == pending and "edit-on-plot" not in errors
        assert _a_rows(session, "points")[0]["time"] == 0.5
        _cell(user, f"{row}:tolerance:lower").set_value(0.2)
        await _settled(lambda: _a_rows(session, "points")[0]["time"] == 0.6)
        assert _a_rows(session, "points") == [
            {"time": 0.6, "value": 0.5, "tolerance": {"lower": 0.2, "upper": 0.3}}
        ]


def _keys(user, marker: str, *texts: str) -> None:
    """Keystrokes in the field marked ``marker``, each its own change
    event carrying the field's whole text after it, as the browser sends
    them."""
    for text in texts:
        user.find(marker=marker).trigger("update:modelValue", text)


async def test_a_rows_side_backspaced_to_empty_puts_back_the_pair_it_held(session):
    """`0.3` backspaced away in a row's `+` passes through `0.` and `0`,
    which the table staged: the cell blank and "required", the row holding
    an upper side of 0. Going blank stages the list with the pair the cell
    held when its field took the focus — and the cell, half, then holds the
    list back as any half cell does."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = dict(PAIR)
    _root_leaf_on_a(session, leaf)
    row = "/metrics:points:0"
    upper = f"{row}:tolerance:upper"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        user.find(marker=upper).trigger("focus")
        _keys(user, upper, "0.", "0")
        zero = {"lower": 0.1, "upper": 0.0}
        await _settled(lambda: _a_rows(session, "points")[0]["tolerance"] == zero)
        _keys(user, upper, "")
        await _settled(lambda: _a_rows(session, "points")[0]["tolerance"] == PAIR)
        assert _cell(user, upper).value is None
        assert _cell(user, upper).error == "required" and not errors
        # Half: an edit of another cell is held with it.
        _cell(user, f"{row}:time").set_value(0.6)
        await asyncio.sleep(0.25)
        assert _a_rows(session, "points")[0] == {
            "time": 0.5,
            "value": 0.5,
            "tolerance": PAIR,
        }
        _keys(user, upper, "0.4")
        await _settled(lambda: _a_rows(session, "points")[0]["time"] == 0.6)
        assert _a_rows(session, "points")[0]["tolerance"] == {
            "lower": 0.1,
            "upper": 0.4,
        }
        assert not errors


async def test_a_side_emptied_beside_another_half_cell_stages_nothing(session):
    """With another cell already half, the values typed on the way were
    held, not staged: there is nothing to put back, and nothing is."""
    leaf = _two_rows_one_pair()
    leaf["points"][1]["tolerance"] = dict(PAIR)
    _root_leaf_on_a(session, leaf)
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _cell(user, "/metrics:points:1:tolerance:lower").set_value(None)
        upper = "/metrics:points:0:tolerance:upper"
        user.find(marker=upper).trigger("focus")
        _keys(user, upper, "0.", "0", "")
        await asyncio.sleep(0.25)
        assert session.spec.pending == pending and not errors
        assert _cell(user, upper).error == "required"


def _two_rows_one_pair() -> dict:
    """A points leaf of two rows, the first holding `PAIR`."""
    return {
        "metric": "points",
        "variable": "x",
        "points": [
            {"time": 0.25, "value": 0.5, "tolerance": dict(PAIR)},
            {"time": 0.75, "value": 0.5},
        ],
    }


@pytest.mark.parametrize(
    ("button", "rows_after"),
    [("/metrics:points:add", 3), ("/metrics:points:1:remove", 1)],
)
async def test_add_row_and_remove_row_keep_a_half_pair_blank_and_stage_nothing(
    session, button, rows_after
):
    """The same rule for the two buttons. Both rebuild the rows, and
    rebuilt from what a row HOLDS the blank side came back filled with the
    pair's old value — the list was staged with it, the held edits and the
    new row too. The half cell is rebuilt as it stands: blank, marked
    "required", holding the list back; the key that fills it stages the
    lot."""
    _root_leaf_on_a(session, _two_rows_one_pair())
    cell = "/metrics:points:0:tolerance"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _cell(user, f"{cell}:lower").set_value(None)
        _cell(user, "/metrics:points:0:time").set_value(0.3)  # held with it
        user.find(marker=button).click()
        await asyncio.sleep(0.25)  # two ticks: anything submitted has landed
        assert session.spec.pending == pending and "edit-on-plot" not in errors
        assert len(_a_rows(session, "points")) == 2
        # The rows as the table shows them: one more, or one fewer.
        drawn = user.find(marker=f"/metrics:points:{rows_after - 1}:remove").elements
        assert len(drawn) == 1
        with pytest.raises(AssertionError, match="expected to find"):
            user.find(marker=f"/metrics:points:{rows_after}:remove")
        lower = _cell(user, f"{cell}:lower")
        assert lower.value is None and lower.error == "required"
        assert _cell(user, f"{cell}:upper").value == 0.3
        assert _cell(user, f"{cell}:upper").error is None
        assert _cell(user, f"{cell}:link").props["icon"] == "link_off"
        lower.set_value(0.2)
        await _settled(lambda: len(_a_rows(session, "points")) == rows_after)
        assert _a_rows(session, "points")[0] == {
            "time": 0.3,
            "value": 0.5,
            "tolerance": {"lower": 0.2, "upper": 0.3},
        }
        assert not errors


async def test_removing_the_row_whose_pair_is_half_stages_the_list_without_it(
    session,
):
    """A removed row takes its controls with it: its half pair is no blank
    of the table's any more, and the list — one row — is staged. Kept, the
    removed control held the list back for good."""
    _root_leaf_on_a(session, _two_rows_one_pair())
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _cell(user, "/metrics:points:0:tolerance:lower").set_value(None)
        user.find(marker="/metrics:points:0:remove").click()
        await _settled(lambda: len(_a_rows(session, "points")) == 1)
        assert _a_rows(session, "points") == [{"time": 0.75, "value": 0.5}]
        assert not errors
        # ... and the row left is editable: its edit is staged.
        _cell(user, "/metrics:points:0:time").set_value(0.8)
        await _settled(lambda: _a_rows(session, "points")[0]["time"] == 0.8)


# -- what a cell shows that the spec does not hold outlives its panel ----------


def _same_pair_twice() -> dict:
    leaf = _two_rows_one_pair()
    leaf["points"][1]["tolerance"] = dict(PAIR)
    return leaf


async def test_a_half_cell_is_blank_in_the_panel_built_after_its_own(session):
    """The table writes a half cell where it outlives the panel
    (`Panel.kept`), by field, row and column — and the panel built next,
    whatever rebuilt it, reads it back: blank, "required", unlinked, and
    holding the list back. Row 0 holds the SAME pair and is untouched: the
    record names its row. Filled, the cell is no record any more."""
    _root_leaf_on_a(session, _same_pair_twice())
    cell = "/metrics:points:1:tolerance"
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        _cell(user, f"{cell}:lower").set_value(None)
        assert kept == {
            ("points", 1, "tolerance"): {
                "base": PAIR,
                "shown": {"lower": None, "upper": 0.3},
            }
        }
    pending = list(session.spec.pending)
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        lower, upper = _cell(user, f"{cell}:lower"), _cell(user, f"{cell}:upper")
        assert (lower.value, lower.error) == (None, "required")
        assert (upper.value, upper.error) == (0.3, None)
        assert _cell(user, f"{cell}:link").props["icon"] == "link_off"
        other = _cell(user, "/metrics:points:0:tolerance:lower")
        assert (other.value, other.error) == (0.1, None)
        _cell(user, "/metrics:points:0:time").set_value(0.3)  # held with it
        await asyncio.sleep(0.25)
        assert session.spec.pending == pending and not errors
        lower.set_value(0.2)
        await _settled(
            lambda: (
                _a_rows(session, "points")[1].get("tolerance")
                == {"lower": 0.2, "upper": 0.3}
            )
        )
        assert _a_rows(session, "points")[0]["time"] == 0.3
        assert kept == {} and not errors


async def test_a_blank_required_cell_is_blank_in_the_panel_built_after_its_own(
    session,
):
    """The same record for a required cell cleared to be retyped."""
    _tree_on_d(session)
    marker = "/metrics/children/0:tube_points:0:time"
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_D, kept=kept) as (user, errors, _figure):
        _cell(user, marker).set_value(None)
        assert kept == {("tube_points", 0, "time"): {"base": 0.5, "shown": None}}
    async with host(session, EditOnPlot(), ON_D, kept=kept) as (user, errors, _figure):
        cell = _cell(user, marker)
        assert (cell.value, cell.error) == (None, "time is required")
        assert _cell(user, "/metrics/children/0:tube_points:1:time").error is None
        cell.set_value(0.6)
        await _settled(
            lambda: session.tree(D)["children"][0]["tube_points"][0]["time"] == 0.6
        )
        assert kept == {} and not errors


async def test_a_half_cell_whose_row_changed_in_the_spec_shows_the_specs(session):
    """The record holds what the spec's row held under the cell. Where the
    spec has since moved there — that side dragged on the plot — the cell
    shows the spec's value, whole, and the record is gone: half of a pair
    the row no longer holds is nothing a person typed against it."""
    _root_leaf_on_a(session, _two_rows_one_pair())
    cell = "/metrics:points:0:tolerance"
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        _cell(user, f"{cell}:lower").set_value(None)
        assert kept
    dragged = {"lower": 0.1, "upper": 0.5}
    session.stage([PatchOp(A, "replace", "/metrics/points/0/tolerance", dragged)])
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        lower, upper = _cell(user, f"{cell}:lower"), _cell(user, f"{cell}:upper")
        assert (lower.value, lower.error) == (0.1, None)
        assert (upper.value, upper.error) == (0.5, None)
        assert kept == {}


async def test_a_half_cell_whose_row_is_gone_from_the_spec_is_forgotten(session):
    _root_leaf_on_a(session, _same_pair_twice())
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        _cell(user, "/metrics:points:1:tolerance:lower").set_value(None)
        assert kept
    one = [{"time": 0.25, "value": 0.5, "tolerance": dict(PAIR)}]
    session.stage([PatchOp(A, "replace", "/metrics/points", one)])
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        lower = _cell(user, "/metrics:points:0:tolerance:lower")
        assert (lower.value, lower.error) == (0.1, None)
        assert kept == {}


async def test_a_half_cell_is_recorded_by_its_rows_place_in_the_spec(session):
    """Not by where the row is drawn. A row removed while a cell holds the
    list back is still in the spec: the cell's row keeps its index there.
    A row removed and STAGED is gone from the spec too: the index moves."""
    _root_leaf_on_a(session, _same_pair_twice())
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        _cell(user, "/metrics:points:1:tolerance:lower").set_value(None)
        user.find(marker="/metrics:points:0:remove").click()
        await asyncio.sleep(0.25)
        assert len(_a_rows(session, "points")) == 2  # held back
        assert list(kept) == [("points", 1, "tolerance")]
    async with host(session, EditOnPlot(), ON_A, kept={}) as (user, errors, _figure):
        user.find(marker="/metrics:points:0:remove").click()
        await _settled(lambda: len(_a_rows(session, "points")) == 1)
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        assert kept == {}  # row 1 is gone from the spec
        _cell(user, "/metrics:points:0:tolerance:lower").set_value(None)
        assert list(kept) == [("points", 0, "tolerance")]


OTHER = {"lower": 0.2, "upper": 0.4}


def _three_rows(pairs: list[dict]) -> dict:
    """A points leaf of three rows, each holding its pair of ``pairs``."""
    return {
        "metric": "points",
        "variable": "x",
        "points": [
            {"time": t, "value": 0.5, "tolerance": dict(pair)}
            for t, pair in zip((0.25, 0.5, 0.75), pairs)
        ],
    }


def _blank_sides(user, rows: int) -> list[tuple]:
    """Per row shown, its tolerance's `−` field: (value, error)."""
    return [
        (cell.value, cell.error)
        for cell in (
            _cell(user, f"/metrics:points:{i}:tolerance:lower") for i in range(rows)
        )
    ]


@pytest.mark.parametrize(
    "pairs",
    [
        # The same pair in every row: only the row's INDEX tells them apart.
        [PAIR, PAIR, PAIR],
        # A pair of its own in each: the record's base is its own row's.
        [PAIR, OTHER, {"lower": 0.3, "upper": 0.5}],
    ],
    ids=["one pair", "three pairs"],
)
async def test_a_half_cell_after_a_staged_remove_is_on_the_row_it_was_typed_in(
    session, pairs
):
    """Remove row 0, staged: the rows left are rows 0 and 1 of the spec's
    list now, and the list the table holds as the spec's is that one. A
    side cleared in the new row 0 is recorded THERE, under what that row
    holds — so the editor rebuilt by a drag of another row's ring shows the
    blank on row 0. Recorded by the index the row had before the remove, it
    came back on row 1; under the removed row's pair, not at all."""
    _root_leaf_on_a(session, _three_rows(pairs))
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        user.find(marker="/metrics:points:0:remove").click()
        await _settled(lambda: len(_a_rows(session, "points")) == 2)
        _cell(user, "/metrics:points:0:tolerance:lower").set_value(None)
        assert kept == {
            ("points", 0, "tolerance"): {
                "base": pairs[1],
                "shown": {"lower": None, "upper": pairs[1]["upper"]},
            }
        }
        assert not errors
    # Another row's ring, dragged: its time moves, and the editor is rebuilt.
    session.stage([PatchOp(A, "replace", "/metrics/points/1/time", 0.8)])
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        assert _blank_sides(user, 2) == [
            (None, "required"),
            (pairs[2]["lower"], None),
        ]
        assert (
            _cell(user, "/metrics:points:0:tolerance:upper").value
            == (pairs[1]["upper"])
        )
        assert _cell(user, "/metrics:points:1:time").value == 0.8


async def test_a_half_cell_in_a_row_just_added_is_on_that_row_after_a_rebuild(
    session,
):
    """The twin: Add row, staged — the new row is the spec's last one, a
    copy of the row before it — then its `−` cleared. The record names the
    ADDED row's place in the spec, so the rebuilt editor shows the blank
    there and nowhere else."""
    _root_leaf_on_a(session, _three_rows([PAIR, PAIR, PAIR]))
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        user.find(marker="/metrics:points:add").click()
        await _settled(lambda: len(_a_rows(session, "points")) == 4)
        _cell(user, "/metrics:points:3:tolerance:lower").set_value(None)
        assert kept == {
            ("points", 3, "tolerance"): {
                "base": PAIR,
                "shown": {"lower": None, "upper": 0.3},
            }
        }
        assert not errors
    session.stage([PatchOp(A, "replace", "/metrics/points/1/time", 0.6)])
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        assert _blank_sides(user, 4) == [(0.1, None)] * 3 + [(None, "required")]


async def test_a_half_cell_in_a_row_the_spec_does_not_hold_yet_is_not_recorded(
    session, caplog
):
    """A row added while another cell holds the list back is in no spec
    list: it has no index there, and a side cleared in it is recorded
    nowhere — the one record stays the held-back cell's. Given the index
    it would have, the record read a row the spec's list does not hold:
    an IndexError in the cell's handler, which NiceGUI logs and swallows."""
    _root_leaf_on_a(session, _three_rows([PAIR, PAIR, PAIR]))
    kept: dict = {}
    async with host(session, EditOnPlot(), ON_A, kept=kept) as (user, errors, _figure):
        _cell(user, "/metrics:points:1:tolerance:lower").set_value(None)
        user.find(marker="/metrics:points:add").click()
        await asyncio.sleep(0.25)
        assert len(_a_rows(session, "points")) == 3  # held back
        with caplog.at_level(logging.ERROR):
            _cell(user, "/metrics:points:3:tolerance:upper").set_value(None)
            await asyncio.sleep(0.1)
        assert [r.getMessage() for r in caplog.records if r.exc_info] == []
        assert list(kept) == [("points", 1, "tolerance")]
        assert not errors


async def test_add_row_after_a_row_holding_a_pair(session):
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = dict(PAIR)
    _root_leaf_on_a(session, leaf)
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        user.find(marker="/metrics:points:add").click()
        await _settled(lambda: len(_a_rows(session, "points")) == 2)
        first, second = _a_rows(session, "points")
        assert second["tolerance"] == PAIR and second["time"] != first["time"]
        assert second["tolerance"] is not first["tolerance"]  # a copy
        new = "/metrics:points:1:tolerance"
        assert _cell(user, f"{new}:link").props["icon"] == "link_off"
        assert _cell(user, f"{new}:lower").value == 0.1
        # The copy is its own: a side typed in the new row leaves the first.
        _cell(user, f"{new}:upper").set_value(0.9)
        await _settled(
            lambda: (
                _a_rows(session, "points")[1]["tolerance"]
                == {"lower": 0.1, "upper": 0.9}
            )
        )
        assert _a_rows(session, "points")[0]["tolerance"] == PAIR
        assert not errors


async def test_a_tolerance_cell_is_as_wide_as_its_label(session):
    """An empty cell draws its label full size, and the cells' 120 px cut
    "before time_tolerance": each side of a tolerance cell asks for its own
    label's width (`forms.label_width`), the longer label the wider."""
    from dstf.authoring.forms import label_width

    _root_leaf_on_a(session, SIDED["points"][0])
    async with host(session, EditOnPlot(), ON_A) as (user, _errors, _figure):
        widths = {}
        for key in ("tolerance", "time_tolerance"):
            for side in ("lower", "upper"):
                cell = _cell(user, f"/metrics:points:0:{key}:{side}")
                widths[cell.props["label"]] = cell._style["min-width"]
        assert widths == {label: f"{label_width(label)}px" for label in widths}
        assert set(widths) >= {"before time_tolerance", "after time_tolerance"}
        # 146 px is what Chromium draws the longest of them in.
        assert label_width("before time_tolerance") >= 146
        assert label_width("after time_tolerance") < label_width(
            "before time_tolerance"
        )


async def test_a_tables_row_wraps_inside_itself(session):
    """A points row holds ten controls: it wraps inside the row rather
    than run past the editor — no inline `flex-wrap`, no `no-wrap`."""
    _root_leaf_on_a(session, SIDED["points"][0])
    async with host(session, EditOnPlot(), ON_A) as (user, _errors, _figure):
        cell = _cell(user, "/metrics:points:0:time")
        (row,) = [a for a in cell.ancestors() if "dstf-table-row" in a.classes]
        assert "no-wrap" not in row.classes and "flex-wrap" not in row._style
        assert any("dstf-table-scroll" in a.classes for a in row.ancestors())
        group = _cell(user, "/metrics:points:0:tolerance:lower").parent_slot.parent
        assert group.parent_slot.parent is row
        # No bottom line reserved in a table — a validated field holds one
        # from its first validation on unless told not to: a row is one
        # control high until a cell says "required".
        for mark in ("tolerance:lower", "tolerance:upper", "time_tolerance:lower"):
            props = _cell(user, f"/metrics:points:0:{mark}").props
            assert "bottom-slots" not in props and "hide-bottom-space" in props
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, _errors, _figure):
        required = _cell(user, "/metrics/children/0:tube_points:0:time")
        assert "hide-bottom-space" in required.props


@pytest.mark.parametrize(
    "bad",
    [
        {"lower": 1},
        {"upper": 1},
        {"lower": 1, "upper": float("inf")},
        {"lower": float("nan"), "upper": 1},
        {"lower": 1, "upper": 2, "extra": 3},
        {"lower": "1", "upper": 2},
        "0.1",
        True,
    ],
)
@pytest.mark.parametrize(
    ("field", "key"),
    [("points", "tolerance"), ("points", "time_tolerance"), ("events", "tolerance")],
)
def test_a_row_whose_tolerance_is_no_number_or_pair_is_refused_by_name(
    session, field, key, bad
):
    """`parse_sides` is the check, and its sentence the refusal: the row and
    the column, then what a tolerance is."""
    _root_leaf_on_a(session, SIDED["events" if field == "events" else "points"][0])
    pending = list(session.spec.pending)
    with pytest.raises(ValueError, match=f"^row 1: {key} must be a number or "):
        _rows(
            session,
            [{"time": 0.25}, {"time": 0.5, key: bad}],
            model_id=A,
            path="/metrics",
            field=field,
        )
    assert session.spec.pending == pending


@pytest.mark.parametrize(
    ("field", "key", "bad", "sentence"),
    [
        ("points", "tolerance", -0.5, "tolerance must not be negative; got −0.5"),
        (
            "points",
            "tolerance",
            {"lower": -0.5, "upper": 0.1},
            "tolerance (lower side) must not be negative; got −0.5",
        ),
        (
            "points",
            "tolerance",
            {"lower": 0.1, "upper": -2},
            "tolerance (upper side) must not be negative; got −2",
        ),
        (
            "points",
            "time_tolerance",
            -0.25,
            "time_tolerance must not be negative; got −0.25",
        ),
        (
            "points",
            "time_tolerance",
            {"lower": -0.25, "upper": 0.0},
            "time_tolerance (before side) must not be negative; got −0.25",
        ),
        (
            "points",
            "time_tolerance",
            {"lower": 0.0, "upper": -0.25},
            "time_tolerance (after side) must not be negative; got −0.25",
        ),
        ("events", "tolerance", -1e-3, "tolerance must not be negative; got −0.001"),
        (
            "events",
            "tolerance",
            {"lower": 0.1, "upper": -0.1},
            "tolerance (late side) must not be negative; got −0.1",
        ),
        ("peaks", "tolerance", -0.05, "tolerance must not be negative; got −0.05"),
    ],
)
def test_a_negative_tolerance_in_a_row_is_refused_by_row_and_column(
    session, field, key, bad, sentence
):
    """A row's tolerance below zero — a number or either side of a pair, a
    value's or a time's — can never be met: the table staged it. Refused by
    `act`, naming the row, the column (its side as the cell's fields name
    it) and the value."""
    leaf, rest = SIDED[field]
    _root_leaf_on_a(session, leaf)
    pending = list(session.spec.pending)
    with pytest.raises(ValueError) as box:
        _rows(
            session,
            [dict(leaf[field][0]), {**rest, key: bad}],
            model_id=A,
            path="/metrics",
            field=field,
        )
    assert str(box.value) == f"row 1: {sentence}"
    assert session.spec.pending == pending
    # A zero is a tolerance, as a number and as either side.
    zero = [{**rest, key: 0}, {**rest, key: {"lower": 0.0, "upper": 0.0}}]
    op = _only_op(_rows(session, zero, model_id=A, path="/metrics", field=field))
    assert [row[key] for row in op.value[field]] == [0, {"lower": 0.0, "upper": 0.0}]


def test_a_tube_rows_negative_width_is_not_a_tolerance(session):
    """`tube_points` in `abs` mode holds literal bounds: a negative
    ``lower`` is one (`test_handles.TREE`'s last leaf), never refused."""
    _tree_on_d(session)
    rows = [{"time": 0.0, "upper": 1.5, "lower": -0.75}]
    path = "/metrics/children/5"
    op = _only_op(
        EditOnPlot().act(
            SessionView(session, _on(session, D, path + "/tube/upper/0")),
            kind="rows",
            model_id=D,
            path=path,
            field="tube_points",
            rows=rows,
        )
    )
    assert op.value["tube_points"] == rows


async def test_a_negative_typed_in_a_rows_tolerance_puts_back_what_the_cell_held(
    session,
):
    """`-0.5` typed over a row's tolerance of 0.1 passes through nothing
    (the `-`: the row then follows the criterion's, which the table
    stages) and `-0`. Refused, the CELL goes back to what it held when it
    was entered — a tolerance of 0 nobody typed was left in the spec beside
    a field reading −0.5 — the field keeps what was typed, and the
    sentence says why. An edit of another cell made meanwhile stays."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"].append({"time": 0.75, "value": 0.5})
    _root_leaf_on_a(session, leaf)
    lower = "/metrics:points:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _cell(user, "/metrics:points:1:time").set_value(0.8)
        await _settled(lambda: _a_rows(session, "points")[1]["time"] == 0.8)
        user.find(marker=lower).trigger("focus")
        _keys(user, lower, "")
        await _settled(lambda: "tolerance" not in _a_rows(session, "points")[0])
        _keys(user, lower, "-0")
        await _settled(lambda: _a_rows(session, "points")[0].get("tolerance") == 0)
        _keys(user, lower, "-0.", "-0.5")
        await _settled(lambda: _a_rows(session, "points")[0].get("tolerance") == 0.1)
        assert errors["edit-on-plot"] == (
            "Refused: row 0: tolerance must not be negative; got −0.5"
        )
        assert _cell(user, lower).value == -0.5
        assert _a_rows(session, "points") == [
            {"time": 0.5, "value": 0.5, "tolerance": 0.1},
            {"time": 0.8, "value": 0.5},
        ]
        # A value the row takes is staged as ever, and the sentence goes.
        _keys(user, lower, "0.2")
        await _settled(lambda: _a_rows(session, "points")[0].get("tolerance") == 0.2)
        assert "edit-on-plot" not in errors


async def test_a_refused_cell_puts_back_what_it_held_when_it_was_entered(session):
    """Not what the table opened on: 0.2 typed and taken, the cell left
    and entered again, then `-1` (`-`: unset, then refused). It is 0.2."""
    _root_leaf_on_a(session, copy.deepcopy(SIDED["events"][0]))
    lower = "/metrics:events:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _keys(user, lower, "0.2")
        await _settled(lambda: _a_rows(session, "events")[0].get("tolerance") == 0.2)
        user.find(marker="/metrics:events").trigger("focusin")
        _keys(user, lower, "", "-1")
        await _settled(lambda: "edit-on-plot" in errors)
        assert "row 0: tolerance must not be negative; got −1" in errors["edit-on-plot"]
        assert _a_rows(session, "events") == [{"time": 0.5, "tolerance": 0.2}]


@pytest.mark.parametrize(
    ("button", "kept_value"),
    [
        # Row 0 goes: the row now first is the one that held 0.3.
        ("/metrics:events:0:remove", 0.3),
        # A row is added: row 0 is where it was, holding the 0.2 typed.
        ("/metrics:events:add", 0.2),
    ],
)
async def test_a_refused_cell_after_the_rows_moved_puts_back_its_own_rows_value(
    session, button, kept_value
):
    """Add row and Remove row rebuild the rows, and a cell is known by its
    place among them: the cell edited before the button is not the cell
    edited after it, though it sits where that one sat. Refused, it puts
    back what ITS row held — not what the first one held when entered."""
    leaf = copy.deepcopy(SIDED["events"][0])
    leaf["events"].append({"time": 0.75, "tolerance": 0.3})
    _root_leaf_on_a(session, leaf)
    lower = "/metrics:events:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _keys(user, lower, "0.2")  # entered at 0.1
        await _settled(lambda: _a_rows(session, "events")[0]["tolerance"] == 0.2)
        rows = len(_a_rows(session, "events"))
        user.find(marker=button).click()
        await _settled(lambda: len(_a_rows(session, "events")) != rows)
        _keys(user, lower, "", "-1")
        await _settled(lambda: "edit-on-plot" in errors)
        assert _a_rows(session, "events")[0]["tolerance"] == kept_value


#: A row of each table, the tolerance cell a negative is typed into, and
#: what that row's sentence calls it.
REENTERED = {
    "points tolerance": ("points", "tolerance", "tolerance"),
    "points time tolerance": ("points", "time_tolerance", "time_tolerance"),
    "an events row": ("events", "tolerance", "tolerance"),
}


@pytest.mark.parametrize("case", REENTERED)
async def test_a_refused_cell_entered_again_puts_back_what_the_spec_holds(
    session, case
):
    """A refused cell still SHOWS what was typed, and the spec holds what
    it held: entered again and typed over with another negative — nothing,
    `-0`, `-0.`, `-0.7` — the cell took what it showed (−0.5) for its start,
    so the put-back was refused too and the `-0` staged on the way stayed, a
    tolerance of −0.0 nobody typed, under a sentence about −0.5. Its start is
    what the staged list holds for it when it takes the focus."""
    field, key, named = REENTERED[case]
    leaf = copy.deepcopy(SIDED[field][0])
    leaf[field][0][key] = 0.05
    _root_leaf_on_a(session, leaf)
    held = copy.deepcopy(leaf[field])
    lower = f"/metrics:{field}:0:{key}:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        user.find(marker=f"/metrics:{field}").trigger("focusin")
        _keys(user, lower, "", "-0", "-0.", "-0.5")
        await _settled(lambda: "got −0.5" in errors.get("edit-on-plot", ""))
        await _settled(lambda: _a_rows(session, field) == held)
        assert session.spec.pending == pending
        # Left, and entered again: the field still reads −0.5.
        user.find(marker=f"/metrics:{field}").trigger("focusin")
        assert _cell(user, lower).value == -0.5
        _keys(user, lower, "", "-0", "-0.", "-0.7")
        await _settled(lambda: "got −0.7" in errors.get("edit-on-plot", ""))
        await _settled(lambda: _a_rows(session, field) == held)
        assert errors["edit-on-plot"] == (
            f"Refused: row 0: {named} must not be negative; got −0.7"
        )
        assert session.spec.pending == pending
        assert _cell(user, lower).value == -0.7


@pytest.mark.parametrize("field", ["points", "peaks"])
@pytest.mark.parametrize("saved", [0.05, 0])
async def test_a_refused_cell_backspaced_to_a_negative_zero_stages_a_zero(
    tmp_path, field, saved
):
    """A refused −0.5 in a row's tolerance, the cell left, entered again
    and backspaced to `-0.`: that is a tolerance of 0, and is staged as
    one — it was −0.0, one pending edit that Save wrote as `-0.0`. Where
    the file's row holds 0 it is no edit at all."""
    import math

    leaf = copy.deepcopy(SIDED[field][0])
    leaf[field][0]["tolerance"] = saved
    session = _session_with_entry(tmp_path, {"metrics": leaf})
    lower = f"/metrics:{field}:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _keys(user, lower, "-0.5")
        await _settled(lambda: "got −0.5" in errors.get("edit-on-plot", ""))
        assert session.spec.pending == []
        user.find(marker=f"/metrics:{field}").trigger("focusin")
        _keys(user, lower, "-0.")
        await _settled(lambda: "edit-on-plot" not in errors)
        await asyncio.sleep(0.25)
        staged = _a_rows(session, field)[0]["tolerance"]
        assert staged == 0 and math.copysign(1.0, staged) == 1.0
        assert len(session.spec.pending) == (1 if saved else 0)
        assert "-0.0" not in json.dumps(session.export())


async def test_a_refused_cell_in_a_row_the_spec_does_not_hold_puts_back_its_own(
    session,
):
    """A row added while the list is refused is in no staged list: its cell
    has no staged value to go back to, and starts from what the row was
    added with. Row 0 shows a refused −0.5; `Add row` copies it; the new
    row's cell, typed over, puts nothing back — the list was refused whole
    the whole way — and the spec holds its one row."""
    leaf = copy.deepcopy(SIDED["events"][0])
    _root_leaf_on_a(session, leaf)
    first = "/metrics:events:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _keys(user, first, "-0.5")
        await _settled(lambda: "row 0" in errors.get("edit-on-plot", ""))
        user.find(marker="/metrics:events:add").click()
        await asyncio.sleep(0.25)
        _keys(user, "/metrics:events:1:tolerance:lower", "", "-0", "-3")
        await asyncio.sleep(0.25)
        assert _a_rows(session, "events") == leaf["events"]
        assert session.spec.pending == pending
        assert errors["edit-on-plot"] == (
            "Refused: row 0: tolerance must not be negative; got −0.5"
        )


async def test_the_sentence_names_the_first_refusal_of_the_rows_as_shown(session):
    """Two cells show a refused negative, and row 0's is fixed: the list is
    still refused — by row 1 — and that is the sentence. The put-back of
    row 0, refused too, had the last word: "row 0: tolerance must not be
    negative; got −0.5", of a cell reading 0.2."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"].append({"time": 0.75, "value": 0.5, "tolerance": 0.1})
    _root_leaf_on_a(session, leaf)
    held = copy.deepcopy(leaf["points"])
    cell = "/metrics:points:{}:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _keys(user, cell.format(0), "-0.5")
        await _settled(lambda: "row 0" in errors.get("edit-on-plot", ""))
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(1), "-0.3")
        await asyncio.sleep(0.25)
        assert (
            "row 0: tolerance must not be negative; got −0.5" in errors["edit-on-plot"]
        )
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(0), "", "0", "0.", "0.2")
        await _settled(lambda: "row 1" in errors.get("edit-on-plot", ""))
        await asyncio.sleep(0.25)
        assert errors["edit-on-plot"] == (
            "Refused: row 1: tolerance must not be negative; got −0.3"
        )
        assert _a_rows(session, "points") == held
        assert session.spec.pending == pending
        # Row 1 fixed too, the list as shown is staged and the sentence goes.
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(1), "0.4")
        await _settled(
            lambda: [r["tolerance"] for r in _a_rows(session, "points")] == [0.2, 0.4]
        )
        assert "edit-on-plot" not in errors


async def test_two_negatives_the_file_holds_are_named_as_the_rows_show_them(session):
    """A spec written by hand holds two negative tolerances in one table
    (read and scored as ever, `..unscorable`). Row 0 fixed, the list is
    refused by row 1 and says so; row 1 fixed, it is staged."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = -0.5
    leaf["points"].append({"time": 0.75, "value": 0.5, "tolerance": -0.3})
    _root_leaf_on_a(session, leaf)
    cell = "/metrics:points:{}:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(0), "", "0", "0.", "0.2")
        await _settled(lambda: "edit-on-plot" in errors)
        await asyncio.sleep(0.25)
        assert errors["edit-on-plot"] == (
            "Refused: row 1: tolerance must not be negative; got −0.3"
        )
        assert session.spec.pending == pending
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(1), "", "0", "0.", "0.4")
        await _settled(
            lambda: (
                [r.get("tolerance") for r in _a_rows(session, "points")] == [0.2, 0.4]
            )
        )
        assert "edit-on-plot" not in errors


async def test_a_refusal_above_another_is_the_one_named_after_its_put_back(session):
    """Row 1 shows a refused −0.3 and row 0 is typed over with −0.7: the
    put-back of row 0 is refused by row 1 ("row 1: … got −0.3"), and that is
    not the list as shown — whose first refusal is row 0's −0.7. The rows as
    shown are handed over once more after a put-back, refused or not."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"].append({"time": 0.75, "value": 0.5, "tolerance": 0.1})
    _root_leaf_on_a(session, leaf)
    held = copy.deepcopy(leaf["points"])
    cell = "/metrics:points:{}:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        _keys(user, cell.format(1), "-0.3")
        await _settled(lambda: "row 1" in errors.get("edit-on-plot", ""))
        user.find(marker="/metrics:points").trigger("focusin")
        _keys(user, cell.format(0), "", "-0", "-0.", "-0.7")
        await _settled(lambda: "row 0" in errors.get("edit-on-plot", ""))
        await asyncio.sleep(0.25)
        assert errors["edit-on-plot"] == (
            "Refused: row 0: tolerance must not be negative; got −0.7"
        )
        assert _a_rows(session, "points") == held
        assert session.spec.pending == pending


async def test_a_refused_cell_goes_back_to_its_own_rows_staged_value(session):
    """The staged value a cell goes back to is its OWN row's: row 1 of two
    rows that differ, typed over with a negative by way of blank and `-0`
    (both staged), goes back to row 1's 0.1 — not row 0's 0.05 — the first
    time and entered again."""
    leaf = copy.deepcopy(SIDED["points"][0])
    leaf["points"][0]["tolerance"] = 0.05
    leaf["points"].append({"time": 0.75, "value": 0.5, "tolerance": 0.1})
    _root_leaf_on_a(session, leaf)
    held = copy.deepcopy(leaf["points"])
    lower = "/metrics:points:1:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        for typed in ("-0.5", "-0.7"):
            user.find(marker="/metrics:points").trigger("focusin")
            _keys(user, lower, "", "-0", "-0.", typed)
            sentence = (
                f"Refused: row 1: tolerance must not be negative; got −{typed[1:]}"
            )
            await _settled(lambda s=sentence: errors.get("edit-on-plot") == s)
            await _settled(lambda: _a_rows(session, "points") == held)
            assert session.spec.pending == pending


async def test_a_cell_of_a_row_the_spec_does_not_hold_takes_its_first_key(session):
    """A row added while the list is refused has no row in the staged list
    to start from — and its cell's FIRST key is taken like any other: with
    row 0's refused value fixed, the list staged holds what was typed in
    the new row, not the value it was added with."""
    leaf = copy.deepcopy(SIDED["events"][0])
    _root_leaf_on_a(session, leaf)
    first = "/metrics:events:0:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _keys(user, first, "-0.5")
        await _settled(lambda: "row 0" in errors.get("edit-on-plot", ""))
        user.find(marker="/metrics:events:add").click()
        await asyncio.sleep(0.25)
        _keys(user, "/metrics:events:1:tolerance:lower", "0.3")
        await asyncio.sleep(0.25)
        assert _a_rows(session, "events") == leaf["events"]
        user.find(marker="/metrics:events").trigger("focusin")
        _keys(user, first, "0.2")
        await _settled(lambda: len(_a_rows(session, "events")) == 2)
        assert [r["tolerance"] for r in _a_rows(session, "events")] == [0.2, 0.3]
        assert "edit-on-plot" not in errors


@pytest.mark.parametrize("field", ["points", "events"])
@pytest.mark.parametrize(
    "typed",
    [("", "-0", "-0.", "-0.7"), ("-0.7",)],
    ids=["by way of blank and -0", "at once"],
)
async def test_a_refused_cell_of_an_added_row_entered_again_goes_back_to_its_own(
    session, field, typed
):
    """A row added while the list is held back is in no staged list, and
    sits where the staged list holds ANOTHER row: three rows, the third
    removed and one added in its place while row 0 shows a refused −0.5.
    The added row copies the row above it (0.3); the staged list still
    holds 0.4 there, the removed row's. Its cell refused (−3), row 0 fixed,
    and the cell entered again and typed over with another negative: what
    goes back, and is staged, is what the row was ADDED with — not the −3
    it showed (that put-back was refused in turn, and the `-0` typed on the
    way stayed staged), and never the 0.4 of the row that was there."""
    leaf = copy.deepcopy(SIDED[field][0])
    first = leaf[field][0]
    leaf[field] = [
        {**first, "time": 0.2, "tolerance": 0.05},
        {**first, "time": 0.5, "tolerance": 0.3},
        {**first, "time": 0.8, "tolerance": 0.4},
    ]
    _root_leaf_on_a(session, leaf)
    held = copy.deepcopy(leaf[field])
    table = f"/metrics:{field}"
    cell = table + ":{}:tolerance:lower"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        _keys(user, cell.format(0), "-0.5")
        await _settled(lambda: "row 0" in errors.get("edit-on-plot", ""))
        user.find(marker=f"{table}:2:remove").click()
        await asyncio.sleep(0.25)
        user.find(marker=f"{table}:add").click()
        await asyncio.sleep(0.25)
        assert _cell(user, cell.format(2)).value == 0.3
        _keys(user, cell.format(2), "-3")
        await asyncio.sleep(0.25)
        user.find(marker=table).trigger("focusin")
        _keys(user, cell.format(0), "0.2")
        await _settled(lambda: "row 2" in errors.get("edit-on-plot", ""))
        # Refused whole the whole way: the spec holds its three rows.
        assert _a_rows(session, field) == held
        user.find(marker=table).trigger("focusin")
        _keys(user, cell.format(2), *typed)
        await _settled(lambda: "got −0.7" in errors.get("edit-on-plot", ""))
        await asyncio.sleep(0.25)
        assert errors["edit-on-plot"] == (
            "Refused: row 2: tolerance must not be negative; got −0.7"
        )
        assert [r.get("tolerance") for r in _a_rows(session, field)] == [0.2, 0.3, 0.3]
        assert _cell(user, cell.format(2)).value == -0.7


def test_a_rows_edit_takes_a_pair_and_a_number_and_drops_a_blank(session):
    _root_leaf_on_a(session, SIDED["points"][0])
    rows = [
        {"time": 0.25, "tolerance": {"lower": 1, "upper": 2}, "time_tolerance": None},
        {"time": 0.5, "tolerance": 0.1, "time_tolerance": {"lower": 0, "upper": 0.2}},
    ]
    op = _only_op(_rows(session, rows, model_id=A, path="/metrics", field="points"))
    assert op.value["points"] == [
        {"time": 0.25, "tolerance": {"lower": 1, "upper": 2}},
        {"time": 0.5, "tolerance": 0.1, "time_tolerance": {"lower": 0, "upper": 0.2}},
    ]
    # A non-finite NUMBER keeps the refusal it had.
    with pytest.raises(ValueError, match="row 0: tolerance = nan is not a finite"):
        _rows(
            session,
            [{"time": 0.5, "tolerance": float("nan")}],
            model_id=A,
            path="/metrics",
            field="points",
        )


def test_a_required_sides_column_is_required(session, monkeypatch):
    """The kind `sides` (no `?`) is a required tolerance: a blank one is
    refused by name, as a blank required number is, and Add row on an empty
    table starts it at 0."""
    from dstf.authoring.builtin import edit_on_plot
    from dstf.authoring.builtin.edit_on_plot import _new_row

    columns = (("time", "float"), ("tolerance", "sides", ("early", "late")))
    monkeypatch.setattr(
        edit_on_plot, "_list_fields", lambda name: [("events", columns)]
    )
    _root_leaf_on_a(session, SIDED["events"][0])

    def act(rows):
        return _rows(session, rows, model_id=A, path="/metrics", field="events")

    with pytest.raises(ValueError, match="^row 0: tolerance is required$"):
        act([{"time": 0.5, "tolerance": None}])
    with pytest.raises(ValueError, match="^row 0: tolerance is required$"):
        act([{"time": 0.5}])
    assert _only_op(act([{"time": 0.5, "tolerance": PAIR}])).value["events"] == [
        {"time": 0.5, "tolerance": PAIR}
    ]
    assert _new_row(columns, [], (0.0, 1.0)) == {"time": 0.5, "tolerance": 0.0}


async def test_a_blank_required_sides_cell_is_marked_and_held(session, monkeypatch):
    from dstf.authoring.builtin import edit_on_plot

    columns = (("time", "float"), ("tolerance", "sides", ("early", "late")))
    monkeypatch.setattr(
        edit_on_plot, "_list_fields", lambda name: [("events", columns)]
    )
    _root_leaf_on_a(session, SIDED["events"][0])
    cell = "/metrics:events:0:tolerance"
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        pending = list(session.spec.pending)
        lower, upper = _cell(user, f"{cell}:lower"), _cell(user, f"{cell}:upper")
        assert lower.error is None and upper.error is None
        lower.set_value(None)  # linked: both clear
        await asyncio.sleep(0.25)
        assert session.spec.pending == pending and "edit-on-plot" not in errors
        assert (lower.error, upper.error) == ("required", "required")
        upper.set_value(0.2)
        await _settled(lambda: _a_rows(session, "events")[0]["tolerance"] == 0.2)
        assert lower.error is None and upper.error is None


# -- a dragged grip: one side of a tolerance (D147) ---------------------------

C = "/metrics/children"

#: One leaf per way a mode emits grips, on D's x (baseline x = t on 0..1).
GRIP_TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "points",
            "variable": "x",
            "tolerance": 0.05,
            "points": [
                {"time": 0.25},
                {
                    "time": 0.75,
                    "value": 0.75,
                    "tolerance": 0.05,
                    "time_tolerance": 0.1,
                },
            ],
        },
        {"metric": "points", "variable": "x", "tolerance": 0.3},
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_abs": 0.5,
        },
        {
            "metric": "event-timing",
            "variable": "x",
            "time_tolerance": 0.02,
            "events": [{"time": 0.5}, {"time": 0.75, "tolerance": 0.05}],
        },
        {
            "metric": "dominant-frequency",
            "variable": "x",
            "peaks": [{"freq": 1.0, "tolerance": 0.1}],
        },
    ],
}

#: The claim: the address in its leaf each grip writes — the row's own
#: tolerance where the row has one, else the criterion's.
GRIP_ADDRESS = {
    f"{C}/0/points/upper/0": "/tolerance",
    f"{C}/0/points/lower/0": "/tolerance",
    f"{C}/0/points/upper/1": "/points/1/tolerance",
    f"{C}/0/points/lower/1": "/points/1/tolerance",
    f"{C}/0/points/before/1": "/points/1/time_tolerance",
    f"{C}/0/points/after/1": "/points/1/time_tolerance",
    f"{C}/1/points/upper/final": "/tolerance",
    f"{C}/1/points/lower/final": "/tolerance",
    f"{C}/2/tube/upper/const": "/tube_abs",
    f"{C}/2/tube/lower/const": "/tube_abs",
    f"{C}/3/events/early/0": "/time_tolerance",
    f"{C}/3/events/late/0": "/time_tolerance",
    f"{C}/3/events/early/1": "/events/1/tolerance",
    f"{C}/3/events/late/1": "/events/1/tolerance",
    f"{C}/4/peaks/lower/0": "/peaks/0/tolerance",
    f"{C}/4/peaks/upper/0": "/peaks/0/tolerance",
}


def _grips_on_d(session) -> dict:
    """Every grip the view of D's x holds, by id: its primitive."""
    return {
        p.id: p
        for leaf in session.view(D, "x").leaves
        for p in leaf.geometry
        if p.kind == "grip"
    }


def _own(grip) -> tuple[str, float]:
    """The axis a grip moves on and where it stands on it."""
    from dstf.comparison.geometry import VLine, grip_axis

    axis = grip_axis(grip)
    if isinstance(grip, VLine):
        return axis, grip.time
    return axis, grip.x if axis == "x" else grip.y


def _released(grip, to: float) -> dict:
    """The relayout a release carries: BOTH anchors and the path. The one
    off the grip's axis has drifted — Plotly re-snaps it, and past 8 px of
    sideways drift moves it in full."""
    axis, _ = _own(grip)
    drifted = 7.5
    return {
        "xanchor": to if axis == "x" else drifted,
        "yanchor": to if axis == "y" else drifted,
        "path": "M 0,10 L 6,17 L -6,17 Z",
    }


def test_the_tree_emits_exactly_the_grips_it_claims(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    assert set(_grips_on_d(session)) == set(GRIP_ADDRESS)


@pytest.mark.parametrize("handle", GRIP_ADDRESS)
def test_a_dragged_grip_writes_its_side_and_stands_where_it_was_released(
    session, handle
):
    """Every grip a mode emits reaches its editor through `act`, reads ONLY
    its own axis, writes at the address it claims — and, staged and scored
    again, stands where it was released: the edge it is anchored on is the
    one that moved."""
    from dstf.comparison.geometry import grip_outward
    from test_handles import _diff, _walk

    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    grip = _grips_on_d(session)[handle]
    axis, was = _own(grip)
    to = was + 0.0625 * grip_outward(grip)  # outward: it cannot cross
    op = _only_op(_drag(session, handle, _released(grip, to)))
    leaf_path = handle[: handle.index("/", len(C) + 1)]
    assert (op.op, op.path) == ("replace", leaf_path)
    changed = list(_diff(_walk(session.tree(D), leaf_path), op.value))
    address = GRIP_ADDRESS[handle]
    assert changed and all(
        pointer == address or pointer.startswith(address + "/") for pointer in changed
    ), changed
    session.stage([op])
    moved = _grips_on_d(session)[handle]
    assert _own(moved) == (axis, pytest.approx(to))
    # The drifted anchor wrote nothing: off its axis the grip is where it was.
    if not hasattr(grip, "time"):
        assert (moved.x, moved.y)[axis == "x"] == pytest.approx(
            (grip.x, grip.y)[axis == "x"]
        )


def test_a_grip_released_without_its_own_anchor_is_no_move(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    pending = list(session.spec.pending)
    for handle, shape in (
        (f"{C}/0/points/upper/1", {"xanchor": 0.9}),
        (f"{C}/0/points/before/1", {"yanchor": 0.9}),
        (f"{C}/3/events/late/0", {"yanchor": 0.3}),
        (f"{C}/4/peaks/upper/0", {"x0": 1.5, "x1": 1.5}),
        (f"{C}/2/tube/upper/const", {}),
    ):
        assert _drag(session, handle, shape) is None, handle
    assert session.spec.pending == pending


def test_a_grip_dragged_across_its_target_is_refused_by_name(session):
    from dstf.authoring.errors import user_text

    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    grips = _grips_on_d(session)
    upper = f"{C}/0/points/upper/1"
    with pytest.raises(HandleEditError) as box:
        _drag(session, upper, _released(grips[upper], 0.5))  # the target is 0.75
    assert str(box.value) == (
        f"{C}/0: points/upper/1: the upper side would cross its target"
    )
    assert user_text(box.value) == "the upper side would cross its target"
    early = f"{C}/3/events/early/0"
    with pytest.raises(HandleEditError) as box:
        _drag(session, early, _released(grips[early], 0.6))  # the event is at 0.5
    assert user_text(box.value) == "the early side would cross its event's time"


def test_a_grip_of_a_criterion_that_is_not_open_is_refused(session):
    """Only the open criterion's grips are on the page, so this is reached
    through `act` alone — where it is refused as another leaf's line is."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    handle = f"{C}/0/points/upper/1"
    grip = _grips_on_d(session)[handle]
    elsewhere = Selection(tests=frozenset({D}), variable="x", leaf_path=f"{C}/2")
    pending = list(session.spec.pending)
    with pytest.raises(HandleEditError, match="is not a handle of") as box:
        EditOnPlot().act(
            SessionView(session, elsewhere),
            kind="handle",
            model_id=D,
            variable="x",
            handle=handle,
            shape=_released(grip, 0.9),
        )
    assert box.value.reason == "Open that criterion to drag its lines."
    assert session.spec.pending == pending


def test_the_final_checks_grip_measures_from_the_windowed_reference(session):
    """D's baseline ends at 1.0; windowed to end at 0.5, the final value the
    scorer holds the result to — and the grip is measured from — is 0.5."""
    leaf = {
        "metric": "points",
        "variable": "x",
        "tolerance": 0.25,
        "window": {"end": 0.5},
    }
    session.stage([PatchOp(D, "add", "/metrics", leaf)])
    grips = _grips_on_d(session)
    upper = grips["/metrics/points/upper/final"]
    assert (upper.x, upper.y) == (0.5, 0.75)
    op = _only_op(_drag(session, upper.id, _released(upper, 1.0)))
    assert op.value == {**leaf, "tolerance": 0.5}
    with pytest.raises(HandleEditError, match="the upper side would cross"):
        _drag(session, upper.id, _released(upper, 0.375))


def test_a_box_and_a_window_still_drag_nothing(session):
    """A grip is accepted by its KIND and its type: a tolerance box and an
    event's window are `handle` Boxes, drawn and never dragged."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(GRIP_TREE))])
    for handle in (f"{C}/0/points/box/1", f"{C}/3/events/window/0"):
        shape = {"x0": 0.1, "x1": 0.9, "y0": 0.1, "y1": 0.9}
        assert _drag(session, handle, shape) is None, handle
    assert _drag(session, f"{C}/3/events/ref/0", {"x0": 0.6, "x1": 0.6}) is None


def test_a_grips_position_is_its_own_axis_and_where_it_stands_on_the_other():
    """What `HandleEdit` carries for a grip: the released anchor on the
    axis it moves on; on the other, the grip's own resolved position — a
    line has none — and never the payload's, which is not where the
    pointer went."""
    from dstf.authoring.builtin.edit_on_plot import _position
    from dstf.comparison.geometry import Marker, VLine

    released = {"xanchor": 0.4, "yanchor": 99.0, "path": "M 0,0 Z"}
    before = Marker(id="/metrics/points/before/0", kind="grip", x=0.5, y=0.25)
    assert _position(before, released) == (0.4, 0.25)
    upper = Marker(id="/metrics/points/upper/0", kind="grip", x=0.5, y=0.25)
    assert _position(upper, released) == (0.5, 99.0)
    late = VLine(id="/metrics/events/late/0", kind="grip", time=0.5)
    assert _position(late, released) == (0.4, None)
    # A peak's `upper` is a line too: its axis is x, whatever its edge's name.
    peak = VLine(id="/metrics/peaks/upper/0", kind="grip", panel="spectrum", time=2.0)
    assert _position(peak, released) == (0.4, None)
    assert _position(peak, {"yanchor": 0.1}) is None
