"""Edit on plot (§4.6 table, §4.7; the on-plot editing plan's Task 7): a
drag reaches ``act`` as ``kind="handle"`` with the figure hook's per-shape
payload, read by the handle's primitive type; a table edit as
``kind="rows"``. Pure ``act`` / ``available`` tests over the fixture
project, then the panel over a shell-less host in NiceGUI's in-process
user simulation — no browser."""

from __future__ import annotations

import asyncio
import copy

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

from capability_host import host  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import EditOnPlot  # noqa: E402
from dstf.authoring.capabilities import Job, Patch  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from dstf.comparison.handles import HandleEditError  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import A, D, _Gate, _project  # noqa: E402


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


def test_a_resized_ring_is_not_a_move(session):
    """The review's off-centre grab, verbatim: a ring's x0..y1 are pixel
    offsets, never a position (read as one, the band lower would accept
    a width of 7.3)."""
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


def test_ids_that_name_no_movable_handle_are_refused(session):
    _tree_on_d(session)
    with pytest.raises(HandleEditError) as box:
        _drag(session, "/metrics/children/2/points/box/0", {"y0": 0.3, "y1": 0.4})
    # Several leaves on one variable can refuse the same local: the leaf
    # path comes first.
    assert str(box.value) == (
        "/metrics/children/2: points/box/0 cannot be moved on the plot"
    )
    with pytest.raises(HandleEditError, match="range/max/outside"):
        _drag(session, "/metrics/children/1/range/max/outside", {"y0": 1.0, "y1": 1.0})
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


def test_metrics_without_an_editor_refuse_before_reading_a_position(session):
    with pytest.raises(
        HandleEditError, match="^/metrics/children/0: nrmse has no on-plot handles"
    ):
        _drag(session, "/metrics/children/0/anything", {"y0": 1.0}, model_id=A)
    _root_leaf_on_a(
        session, {"metric": "event-timing", "variable": "x", "events": [{"time": 0.5}]}
    )
    ids = {p.id for leaf in session.view(A, "x").leaves for p in leaf.geometry}
    assert "/metrics/events/window/0" in ids  # measured: the leaf's handle Box
    with pytest.raises(
        HandleEditError, match="^/metrics: event-timing has no on-plot handles"
    ):
        _drag(session, "/metrics/events/window/0", {"x0": 0.4, "x1": 0.6}, model_id=A)


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
        with pytest.raises(ValueError, match=f"^{path} names no leaf of {D}$"):
            _rows(session, ok, path=path)
    with pytest.raises(ValueError, match="^/metrics is a combinator, not a leaf$"):
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


def test_available_names_the_job_holding_the_lock(session):
    _tree_on_d(session)
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
    try:
        assert (
            EditOnPlot().available(SessionView(session, ON_D))
            == f"rerun is running on {D}"
        )
    finally:
        gate.release.set()


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
        await user.should_see("/metrics/children/2 · points · points")
        p2 = "/metrics/children/2:points"
        assert isinstance(_cell(user, f"{p2}:0:tolerance_mode"), ui.select)
        assert isinstance(_cell(user, f"{p2}:1:value"), ui.number)
        assert _cell(user, f"{p2}:1:value").value == 0.75
    async with host(session, EditOnPlot(), ON_D) as (user, errors, _figure):
        await user.should_see("/metrics/children/0 · tube · tube_points")
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
    the whole list."""
    _tree_on_d(session)
    async with host(session, EditOnPlot(), ON_D) as (user, errors, _figure):
        marker = "/metrics/children/0:tube_points:0:time"
        cell = _cell(user, marker)
        pending = list(session.spec.pending)
        cell.set_value(None)
        await asyncio.sleep(0.25)  # two ticks: anything submitted has landed
        assert session.spec.pending == pending
        assert "edit-on-plot" not in errors
        assert "error" in cell.props
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
        assert "error" not in cell.props
        await asyncio.sleep(0.25)
        assert _cell(user, marker) is cell


async def test_add_row_on_an_empty_points_table(session):
    _root_leaf_on_a(session, {"metric": "points", "variable": "x", "points": []})
    async with host(session, EditOnPlot(), ON_A) as (user, errors, _figure):
        await user.should_see("/metrics · points · points")
        user.find(marker="/metrics:points:add").click()
        await _settled(
            lambda: (_pending_value(session, "/metrics") or {}).get("points")
        )
        assert _pending_value(session, "/metrics")["points"] == [
            {"time": 0.5, "tolerance_mode": "abs"}
        ]


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
