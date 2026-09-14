"""Edit on plot (§4.6 table, §4.7; the on-plot editing plan's Task 7): a
drag reaches ``act`` as ``kind="handle"`` with the figure hook's per-shape
payload, read by the handle's primitive type; a table edit as
``kind="rows"``. Pure ``act`` / ``available`` tests over the fixture
project, then the panel and the drag through the hook with NiceGUI's
in-process user simulation — no browser."""

from __future__ import annotations

import asyncio
import copy

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.app import Shell  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import EditOnPlot  # noqa: E402
from dstf.authoring.capabilities import Job, Patch  # noqa: E402
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
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell()
    s.open(cfg, work_dir=work)
    return s


async def _settled(predicate, tries: int = 40) -> None:
    """Refreshes are deferred; wait for a region's rebuild to land (the
    helper `tests/test_shell.py` uses)."""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _tree_on_d(shell) -> None:
    shell.session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    shell.session.select(tests={D}, variable="x", leaf_path=None)


def _root_leaf_on_a(shell, leaf: dict) -> None:
    shell.session.stage([PatchOp(A, "add", "/metrics", leaf)])
    shell.session.select(tests={A}, variable="x", leaf_path=None)


def _drag(shell, handle: str, shape: dict, model_id: str = D):
    return EditOnPlot().act(
        shell.view,
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


def test_a_range_bound_drag_replaces_the_leaf(shell):
    _tree_on_d(shell)
    op = _only_op(
        _drag(
            shell,
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
    op = _only_op(_drag(shell, "/metrics/children/1/range/max", {"x0": 0, "y0": 1.7}))
    assert op.value["max_value"] == 1.7
    # A horizontal-only drag moves no bound.
    assert _drag(shell, "/metrics/children/1/range/max", {"x0": 0.1, "x1": 1.1}) is None


def test_a_tube_ring_drag_stages_one_replace_that_collapses(shell):
    _tree_on_d(shell)
    handle = "/metrics/children/0/tube/upper/0"
    outcome = _drag(shell, handle, {"xanchor": 0.5, "yanchor": 0.8})
    op = _only_op(outcome)
    assert (op.model_id, op.op, op.path) == (D, "replace", "/metrics/children/0")
    assert op.value["tube_points"][0]["upper"] == pytest.approx(0.3)
    shell.session.apply(outcome)
    assert shell.session.spec.pending[-1] == op
    shell.session.apply(_drag(shell, handle, {"xanchor": 0.5, "yanchor": 0.9}))
    ops = [p for p in shell.session.spec.pending if p.path == "/metrics/children/0"]
    assert len(ops) == 1
    assert ops[0].value["tube_points"][0]["upper"] == pytest.approx(0.4)


def test_a_resized_ring_is_not_a_move(shell):
    """The review's off-centre grab, verbatim: a ring's x0..y1 are pixel
    offsets, never a position (read as one, the band lower would accept
    a width of 7.3)."""
    _tree_on_d(shell)
    pending = list(shell.session.spec.pending)
    assert (
        _drag(
            shell,
            "/metrics/children/0/tube/lower/0",
            {"x0": -7, "x1": 7, "y0": -7, "y1": 33},
        )
        is None
    )
    assert shell.session.spec.pending == pending


def test_a_baseline_following_checkpoint_dragged_vertically_stages_nothing(shell):
    _tree_on_d(shell)
    assert (
        _drag(
            shell,
            "/metrics/children/2/points/target/0",
            {"xanchor": 0.25, "yanchor": 0.9},
        )
        is None
    )


def test_ids_that_name_no_movable_handle_are_refused(shell):
    _tree_on_d(shell)
    with pytest.raises(HandleEditError) as box:
        _drag(shell, "/metrics/children/2/points/box/0", {"y0": 0.3, "y1": 0.4})
    # Several leaves on one variable can refuse the same local: the leaf
    # path comes first.
    assert str(box.value) == (
        "/metrics/children/2: points/box/0 cannot be moved on the plot"
    )
    with pytest.raises(HandleEditError, match="range/max/outside"):
        _drag(shell, "/metrics/children/1/range/max/outside", {"y0": 1.0, "y1": 1.0})
    with pytest.raises(ValueError, match="/metrics/children/9/tube/upper/0") as none:
        _drag(shell, "/metrics/children/9/tube/upper/0", {"xanchor": 0.5, "yanchor": 1})
    assert not isinstance(none.value, HandleEditError)
    assert _drag(shell, "", {"y0": 1.0}) is None


def test_a_leaf_path_is_a_prefix_only_up_to_a_slash(shell):
    """`/metrics/children/10/...` names no leaf of a six-child tree; read
    as a prefix without the slash it would edit child 1's range bound."""
    _tree_on_d(shell)
    with pytest.raises(ValueError, match="/metrics/children/10/range/max"):
        _drag(shell, "/metrics/children/10/range/max", {"y0": 1.5, "y1": 1.5})


def test_a_single_leaf_root_is_its_own_leaf_path(shell):
    _root_leaf_on_a(shell, {"metric": "range", "variable": "x", "max_value": 5.0})
    op = _only_op(
        _drag(shell, "/metrics/range/max", {"y0": 4.0, "y1": 4.0}, model_id=A)
    )
    assert op == PatchOp(
        A, "replace", "/metrics", {"metric": "range", "variable": "x", "max_value": 4.0}
    )


def test_metrics_without_an_editor_refuse_before_reading_a_position(shell):
    shell.session.select(tests={A}, variable="x", leaf_path=None)
    with pytest.raises(HandleEditError, match="nrmse has no on-plot handles"):
        _drag(shell, "/metrics/children/0/anything", {"y0": 1.0}, model_id=A)
    _root_leaf_on_a(
        shell, {"metric": "event-timing", "variable": "x", "events": [{"time": 0.5}]}
    )
    ids = {p.id for leaf in shell.session.view(A, "x").leaves for p in leaf.geometry}
    assert "/metrics/events/window/0" in ids  # measured: the leaf's handle Box
    with pytest.raises(HandleEditError, match="event-timing has no on-plot handles"):
        _drag(shell, "/metrics/events/window/0", {"x0": 0.4, "x1": 0.6}, model_id=A)


# -- act: a table -----------------------------------------------------------


def test_a_rows_edit_replaces_the_leaf_dropping_blank_cells(shell):
    _tree_on_d(shell)
    op = _only_op(
        EditOnPlot().act(
            shell.view,
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


def test_a_blank_required_cell_is_refused_by_name(shell):
    _tree_on_d(shell)
    pending = list(shell.session.spec.pending)
    inputs = dict(
        kind="rows",
        model_id=D,
        path="/metrics/children/0",
        field="tube_points",
        rows=[{"time": None, "upper": 0.1, "lower": 0.1}],
    )
    with pytest.raises(ValueError, match="row 0: time is required"):
        EditOnPlot().act(shell.view, **inputs)
    (cap,) = [c for c in caps.registered_capabilities() if c.name == "edit-on-plot"]
    shell.act(cap, **inputs)  # the path a panel's submit takes
    assert shell.errors["edit-on-plot"] == "ValueError: row 0: time is required"
    assert shell.session.spec.pending == pending


def _rows(shell, rows, *, model_id=D, path="/metrics/children/0", field="tube_points"):
    return EditOnPlot().act(
        shell.view,
        kind="rows",
        model_id=model_id,
        path=path,
        field=field,
        rows=rows,
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["time", "upper"])
def test_a_non_finite_cell_is_refused_by_row_and_column(shell, bad, key):
    _tree_on_d(shell)
    rows = [
        {"time": 0.5, "upper": 0.1, "lower": 0.2},
        {"time": 0.9, "upper": 0.1, "lower": 0.1},
    ]
    rows[1][key] = bad
    with pytest.raises(ValueError, match=f"row 1: {key} = .* is not a finite number"):
        _rows(shell, rows)


def test_a_tube_table_with_no_width_in_any_row_is_refused(shell):
    """The reader's own refusal (`_interpolate_tube_widths`), by name."""
    _tree_on_d(shell)
    with pytest.raises(ValueError, match="tube_points: no row has an upper or a lower"):
        _rows(shell, [{"time": 0.5, "upper": None, "lower": None}, {"time": 0.9}])
    # One width in one row is enough; an empty table is no tube width at all.
    assert _rows(shell, [{"time": 0.5, "lower": 0.1}, {"time": 0.9}]) is not None
    assert _rows(shell, []) is not None


def test_a_rows_edit_names_what_its_path_or_model_is_instead(shell):
    _tree_on_d(shell)
    ok = [{"time": 0.5, "upper": 0.1}]
    for path in ("/metrics/children/9", "/metrics/children/0/tube_points", "/x/0"):
        with pytest.raises(ValueError, match=f"^{path} names no leaf of {D}$"):
            _rows(shell, ok, path=path)
    with pytest.raises(ValueError, match="^/metrics is a combinator, not a leaf$"):
        _rows(shell, ok, path="/metrics")
    with pytest.raises(ValueError, match="^Nope.Model is not a test of this project$"):
        _rows(shell, ok, model_id="Nope.Model")


def test_a_rows_edit_equal_to_the_current_list_stages_nothing(shell):
    _tree_on_d(shell)
    current = shell.session.tree(D)["children"][0]["tube_points"]
    assert _rows(shell, copy.deepcopy(current)) is None


def test_a_rows_edit_of_a_field_without_columns_is_refused(shell):
    _tree_on_d(shell)
    with pytest.raises(ValueError, match="tube has no table 'tube_width_mode'"):
        _rows(shell, [], field="tube_width_mode")
    with pytest.raises(ValueError, match="range has no table 'tube_points'"):
        _rows(shell, [], path="/metrics/children/1")


# -- available --------------------------------------------------------------


def test_available_needs_one_test_and_a_handle_bearing_leaf(shell):
    cap = EditOnPlot()
    assert cap.available(shell.view) == "select one test"
    shell.session.select(tests={A, D}, variable="x", leaf_path=None)
    assert cap.available(shell.view) == "select one test"
    shell.session.select(tests={A}, variable="x", leaf_path=None)
    assert cap.available(shell.view) == "no criterion on x has on-plot handles"
    _tree_on_d(shell)
    assert cap.available(shell.view) is True


def test_a_leaf_with_a_table_but_no_drag_editor_is_available(shell):
    """event-timing has no `edit_handle`, but its `events` declare columns."""
    _root_leaf_on_a(
        shell, {"metric": "event-timing", "variable": "x", "events": [{"time": 0.5}]}
    )
    assert EditOnPlot().available(shell.view) is True


def test_available_names_the_job_holding_the_lock(shell):
    _tree_on_d(shell)
    gate = _Gate()
    shell.session.apply(Job(run=gate, label="rerun", models=frozenset({D})))
    try:
        assert EditOnPlot().available(shell.view) == f"rerun is running on {D}"
    finally:
        gate.release.set()


# -- the panel ---------------------------------------------------------------


def _pending_value(shell, path: str):
    ops = [p for p in shell.session.spec.pending if p.path == path]
    return ops[-1].value if ops else None


async def _open_edit_on_plot(shell, user) -> None:
    await user.open("/")
    await user.should_see("Edit on plot")
    (tab,) = [
        t for t in user.find(ui.tab).elements if t._props.get("label") == "Edit on plot"
    ]
    assert tab.enabled
    (panels,) = user.find(ui.tab_panels).elements
    panels.set_value("Edit on plot")
    await user.should_see("drag a handle on the plot, or edit a table")


def _cell(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def test_the_panel_draws_a_table_per_list_field_and_stages_edits(shell):
    _tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_edit_on_plot(shell, user)
        for path, name, field in (
            ("/metrics/children/0", "tube", "tube_points"),
            ("/metrics/children/2", "points", "points"),
            ("/metrics/children/4", "tube", "tube_points"),
            ("/metrics/children/5", "tube", "tube_points"),
        ):
            await user.should_see(f"{path} · {name} · {field}")
        t0 = "/metrics/children/0:tube_points"
        assert isinstance(_cell(user, f"{t0}:1:lower"), ui.number)
        with pytest.raises(AssertionError):
            user.find(marker=f"{t0}:2:time")  # two rows, no third
        p2 = "/metrics/children/2:points"
        assert isinstance(_cell(user, f"{p2}:0:tolerance_mode"), ui.select)
        assert isinstance(_cell(user, f"{p2}:1:value"), ui.number)
        assert _cell(user, f"{p2}:1:value").value == 0.75

        _cell(user, f"{t0}:0:upper").set_value(0.25)
        await _settled(
            lambda: (
                (_pending_value(shell, "/metrics/children/0") or {})
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
                    (_pending_value(shell, "/metrics/children/0") or {}).get(
                        "tube_points", []
                    )
                )
                == 3
            )
        )
        assert _pending_value(shell, "/metrics/children/0")["tube_points"][2] == added
        user.find(marker=f"{t0}:2:remove").click()
        await _settled(
            lambda: (
                len(_pending_value(shell, "/metrics/children/0")["tube_points"]) == 2
            )
        )
        assert shell.session.tree(D)["children"][0]["tube_points"] == [
            {"time": 0.5, "upper": 0.25, "lower": 0.2},
            {"time": 0.9, "upper": 0.1, "lower": 0.1},
        ]


async def test_a_blank_required_cell_is_marked_and_held_until_it_is_filled(shell):
    """Clearing a required cell to retype it: nothing is submitted (a refusal
    would rebuild the slot and lose the field being typed in), the cell is
    marked, and the keystroke that fills it submits the whole list."""
    _tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_edit_on_plot(shell, user)
        marker = "/metrics/children/0:tube_points:0:time"
        cell = _cell(user, marker)
        pending = list(shell.session.spec.pending)
        cell.set_value(None)
        await asyncio.sleep(0.25)  # two ticks: anything submitted has landed
        assert shell.session.spec.pending == pending
        assert "edit-on-plot" not in shell.errors
        assert "error" in cell.props
        assert _cell(user, marker) is cell  # not rebuilt
        cell.set_value(0.6)
        await _settled(
            lambda: (
                (_pending_value(shell, "/metrics/children/0") or {})
                .get("tube_points", [{}])[0]
                .get("time")
                == 0.6
            )
        )
        assert _pending_value(shell, "/metrics/children/0")["tube_points"] == [
            {"time": 0.6, "upper": 0.1, "lower": 0.2},
            {"time": 0.9, "upper": 0.1, "lower": 0.1},
        ]
        assert "error" not in cell.props
        await asyncio.sleep(0.25)
        assert _cell(user, marker) is cell


async def test_add_row_on_an_empty_points_table(shell):
    _root_leaf_on_a(shell, {"metric": "points", "variable": "x", "points": []})
    async with user_simulation(root=shell.root) as user:
        await _open_edit_on_plot(shell, user)
        await user.should_see("/metrics · points · points")
        user.find(marker="/metrics:points:add").click()
        await _settled(lambda: (_pending_value(shell, "/metrics") or {}).get("points"))
        assert _pending_value(shell, "/metrics")["points"] == [
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


# -- the drag, end to end through the hook -----------------------------------


def _shape_index(plot, name: str) -> int:
    (index,) = [
        i
        for i, s in enumerate(plot.figure["layout"]["shapes"])
        if s.get("name") == name
    ]
    return index


async def test_a_ring_drag_through_the_hook_stages_and_redraws_in_place(shell):
    _tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_edit_on_plot(shell, user)
        (plot,) = user.find(ui.plotly).elements
        rev = plot.figure["layout"].get("editrevision")
        i = _shape_index(plot, "/metrics/children/0/tube/upper/0")
        shell.figure_hook._dispatch(
            "plotly_relayout",
            {f"shapes[{i}].xanchor": 0.5, f"shapes[{i}].yanchor": 0.8},
        )
        last = shell.session.spec.pending[-1]
        assert (last.op, last.path) == ("replace", "/metrics/children/0")
        assert last.value["tube_points"][0]["upper"] == pytest.approx(0.3)
        await _settled(lambda: plot.figure["layout"].get("editrevision") != rev)
        assert [p.id for p in user.find(ui.plotly).elements] == [plot.id]


async def test_a_refused_drag_through_the_hook_shows_in_the_slot_and_snaps_back(shell):
    _tree_on_d(shell)
    async with user_simulation(root=shell.root) as user:
        await _open_edit_on_plot(shell, user)
        (plot,) = user.find(ui.plotly).elements
        rev = plot.figure["layout"].get("editrevision")
        pending = list(shell.session.spec.pending)
        k = _shape_index(plot, "/metrics/children/2/points/box/0")
        assert plot.figure["layout"]["shapes"][k]["type"] == "rect"
        shell.figure_hook._dispatch(
            "plotly_relayout", {f"shapes[{k}].y0": 0.3, f"shapes[{k}].y1": 0.4}
        )
        await user.should_see("cannot be moved on the plot", retries=20)
        assert "cannot be moved on the plot" in shell.errors["edit-on-plot"]
        assert shell.session.spec.pending == pending
        await _settled(lambda: plot.figure["layout"].get("editrevision") != rev)
