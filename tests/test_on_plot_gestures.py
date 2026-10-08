"""The gestures on a plot (D149), below the browser: a placed value is
snapped; a handle names the field it writes; a Ctrl+click adds a row that
settles on the data; a click sets an armed field through its handle; a held
handle's preview is its release; a move that arrives late is dropped."""

from __future__ import annotations

import copy

import numpy as np
import pytest

pytest.importorskip("nicegui")

from capability_host import host  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.edit_criteria import EditCriteria  # noqa: E402
from dstf.authoring.builtin.edit_on_plot import (  # noqa: E402
    EditOnPlot,
    clicked_row,
    dragged,
    placed,
)
from dstf.authoring.capabilities import Patch  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402
from dstf.comparison.geometry import Series, VLine  # noqa: E402
from dstf.comparison.handles import (  # noqa: E402
    HandleEdit,
    HandleEditError,
    RowPlace,
    handle_fields,
    place_events_row,
    place_peaks_row,
    place_points_row,
    place_tube_row,
)
from dstf.comparison.registry import metric  # noqa: E402
from on_plot_project import (  # noqa: E402
    CONSTANT_TUBE,
    EVENT_TIMING,
    FREQUENCY,
    NRMSE,
    POINTS,
    RANGE,
    SCALAR,
    TREE,
    TUBE,
    wave,
    wave_project,
)
from test_session import A  # noqa: E402

REF = (np.linspace(0.0, 1.0, 101), np.linspace(0.0, 2.0, 101))  # ref(t) = 2t
#: A plot one unit wide and ten tall: steps of 0.001 in x and 0.01 in y.
SEEN = (1.0, 10.0)


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


def _edit(leaf, local, x=None, y=None, visible=SEEN, target=None, reference=REF):
    before = copy.deepcopy(leaf)
    new = metric(leaf["metric"]).edit_handle(
        HandleEdit(
            leaf=leaf,
            local=local,
            x=x,
            y=y,
            reference=reference,
            target=target,
            visible=visible,
        )
    )
    assert leaf == before
    return new


# -- a placed value is snapped --------------------------------------------------

BOUNDS = {"metric": "range", "variable": "x", "min_value": 0.0, "max_value": 5.0}
CHECKS = {
    "metric": "points",
    "variable": "x",
    "tolerance": 0.2,
    "points": [
        {"time": 0.25, "value": 0.5, "tolerance": 0.1, "time_tolerance": 0.05},
        {"time": 0.5},
        {"time": 0.75, "tolerance": 0.1, "tolerance_mode": "rel"},
    ],
}
BAND = {
    "metric": "tube",
    "variable": "x",
    "tube_width_mode": "band",
    "tube_points": [
        {"time": 0.2, "upper": 0.1, "lower": 0.1},
        {"time": 0.8, "upper": 0.3, "lower": 0.2},
    ],
}
EVENTS = {
    "metric": "event-timing",
    "variable": "x",
    "time_tolerance": 0.02,
    "events": [{"time": 0.4, "tolerance": 0.05}, {"time": 0.8}],
}
SPECTRUM = {
    "metric": "dominant-frequency",
    "variable": "x",
    "peaks": [
        {"freq": 3.0, "tolerance": 0.2, "tolerance_mode": "rel"},
        {"freq": 8.0, "tolerance": 0.5, "tolerance_mode": "abs"},
    ],
}


@pytest.mark.parametrize(
    "leaf, local, at, read, want",
    [
        (BOUNDS, "range/max", (None, 3.14159), lambda n: n["max_value"], 3.14),
        (
            CHECKS,
            "points/target/0",
            (0.31415, 0.27182),
            lambda n: (n["points"][0]["time"], n["points"][0]["value"]),
            (0.314, 0.27),
        ),
        # A side in the axis' units: the SIDE is a multiple of the step
        # (0.5 + 0.1234 -> 0.12), not its edge.
        (
            CHECKS,
            "points/upper/0",
            (0.25, 0.6234),
            lambda n: n["points"][0]["tolerance"],
            0.12,
        ),
        (
            CHECKS,
            "points/before/0",
            (0.21234, None),
            lambda n: n["points"][0]["time_tolerance"],
            0.038,
        ),
        # A RELATIVE side: 3 significant digits of the fraction (target 1.5).
        (
            CHECKS,
            "points/upper/2",
            (0.75, 1.7777),
            lambda n: n["points"][2]["tolerance"],
            0.185,
        ),
        # A deferring row writes the criterion's.
        (CHECKS, "points/lower/1", (0.5, 0.8765), lambda n: n["tolerance"], 0.12),
        (
            BAND,
            "tube/upper/0",
            (0.33333, 0.87654),
            lambda n: (n["tube_points"][0]["time"], n["tube_points"][0]["upper"]),
            (0.333, 0.21),
        ),
        (
            {**BAND, "tube_width_mode": "rel"},
            "tube/lower/1",
            (None, 1.23456),
            lambda n: n["tube_points"][1]["lower"],
            0.228,
        ),
        (
            {**BAND, "tube_width_mode": "abs"},
            "tube/upper/1",
            (None, 1.23456),
            lambda n: n["tube_points"][1]["upper"],
            1.23,
        ),
        (
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_abs": 0.3,
            },
            "tube/upper/const",
            (0.5, 1.23456),
            lambda n: n["tube_abs"],
            0.23,
        ),
        (
            EVENTS,
            "events/late/0",
            (0.46789, None),
            lambda n: n["events"][0],
            {"time": 0.4, "tolerance": 0.068},
        ),
        (
            EVENTS,
            "events/early/1",
            (0.76543, None),
            lambda n: n["time_tolerance"],
            0.035,
        ),
        (
            SPECTRUM,
            "peaks/declared/0",
            (3.14159, None),
            lambda n: n["peaks"][0]["freq"],
            3.142,
        ),
        (
            SPECTRUM,
            "peaks/upper/0",
            (3.77777, None),
            lambda n: n["peaks"][0]["tolerance"],
            0.259,
        ),
        (
            SPECTRUM,
            "peaks/lower/1",
            (7.65432, None),
            lambda n: n["peaks"][1]["tolerance"],
            0.346,
        ),
    ],
)
def test_every_value_a_drag_writes_is_snapped_to_its_axis(leaf, local, at, read, want):
    got = read(_edit(leaf, local, x=at[0], y=at[1]))
    assert got == want
    # A caller that says no span is no pointer: 12 digits, as before.
    plain = read(_edit(leaf, local, x=at[0], y=at[1], visible=(None, None)))
    assert plain != want


def test_a_scalars_side_is_snapped_in_its_own_units_and_as_a_fraction():
    leaf = {"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}}
    new = _edit({**leaf, "tolerance": 0.5}, "scalar/upper", y=2.34567, target=2.0)
    assert new["tolerance"] == 0.35
    rel = {**leaf, "tolerance": 0.5, "tolerance_mode": "rel"}
    assert _edit(rel, "scalar/upper", y=2.34567, target=2.0)["tolerance"] == 0.173


def test_the_step_follows_what_the_axis_showed():
    """Zoomed to a tenth of the span, the same position is written one more
    digit fine."""
    wide = _edit(BOUNDS, "range/max", y=3.14159, visible=(1.0, 10.0))
    near = _edit(BOUNDS, "range/max", y=3.14159, visible=(1.0, 1.0))
    assert (wide["max_value"], near["max_value"]) == (3.14, 3.142)


def test_a_value_is_snapped_first_and_then_refused_or_clamped():
    """What is refused and clamped is the value that would be written."""
    # 0.0004 across its target snaps onto it: a zero side, which is taken.
    crossed = _edit(CHECKS, "points/upper/0", x=0.25, y=0.4996)
    assert crossed["points"][0]["tolerance"] == 0.0
    with pytest.raises(HandleEditError, match="would cross its target"):
        _edit(CHECKS, "points/upper/0", x=0.25, y=0.48)
    # Past the reference's end the time is CLAMPED onto it, not snapped off.
    ends = (np.array([0.0, 0.98765]), np.array([0.0, 2.0]))
    moved = _edit(CHECKS, "points/target/1", x=1.4, y=None, reference=ends)
    assert moved["points"][1]["time"] == 0.98765
    with pytest.raises(HandleEditError, match="must be positive"):
        _edit(SPECTRUM, "peaks/declared/0", x=0.0004)


def test_an_axis_that_did_not_move_is_not_snapped():
    """A hand-written 0.123456 stays until its axis is really moved: the
    page says no position on an axis the pointer went under 8 px along."""
    leaf = copy.deepcopy(CHECKS)
    leaf["points"][0].update(time=0.123456, value=0.654321)
    sideways = _edit(leaf, "points/target/0", x=0.31415, y=None)
    assert sideways["points"][0] == {**leaf["points"][0], "time": 0.314}
    upright = _edit(leaf, "points/target/0", x=None, y=0.27182)
    assert upright["points"][0] == {**leaf["points"][0], "value": 0.27}


# -- a handle names the field it writes -------------------------------------------

#: (leaf, local, a position that moves it, what moved it measures from)
HANDLES = [
    (BOUNDS, "range/min", (None, 1.0), None),
    (BOUNDS, "range/max", (None, 4.0), None),
    (CHECKS, "points/target/0", (0.3, 0.4), None),
    (CHECKS, "points/upper/0", (0.25, 0.9), None),
    (CHECKS, "points/lower/0", (0.25, 0.1), None),
    (CHECKS, "points/before/0", (0.1, None), None),
    (CHECKS, "points/after/0", (0.4, None), None),
    (CHECKS, "points/upper/1", (0.5, 1.6), None),
    (CHECKS, "points/lower/2", (0.75, 1.0), None),
    (BAND, "tube/upper/1", (0.6, 2.2), None),
    (BAND, "tube/lower/0", (0.3, 0.1), None),
    (
        {"metric": "tube", "variable": "x", "tube_width_mode": "rel", "tube_rel": 0.1},
        "tube/lower/const",
        (0.5, 0.6),
        None,
    ),
    (EVENTS, "events/early/0", (0.3, None), None),
    (EVENTS, "events/late/1", (0.9, None), None),
    (SPECTRUM, "peaks/declared/1", (9.0, None), None),
    (SPECTRUM, "peaks/lower/0", (2.0, None), None),
    (SPECTRUM, "peaks/upper/1", (9.0, None), None),
    (
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": "max"},
            "tolerance": 0.5,
        },
        "scalar/lower",
        (None, 1.0),
        2.0,
    ),
]


def _differs(old, new, at=()):
    """The fields of ``new`` that are not ``old``'s, as `handle_fields`
    names a field: a number or pair that changed whole is named by its
    holder's key and the side the handle is of is not told apart here."""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) == set(new) == {"lower", "upper"}:
            return {at} if old != new else set()
        return set().union(
            *(_differs(old.get(k), new.get(k), (*at, k)) for k in {*old, *new})
        )
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return set().union(
            *(_differs(a, b, (*at, i)) for i, (a, b) in enumerate(zip(old, new)))
        )
    return {at} if old != new else set()


@pytest.mark.parametrize("leaf, local, at, target", HANDLES)
def test_a_handles_fields_are_the_ones_its_editor_writes(leaf, local, at, target):
    said = handle_fields(leaf, local)
    assert said, local
    for axis, field in said.items():
        x, y = at
        # Along ONE axis: a grip — an arrow, not a ring or a line — still
        # needs the other to measure from.
        parts = local.split("/")
        ring = parts[1] in ("target", "declared") or (
            parts[0] == "tube" and parts[-1] != "const"
        )
        grip = len(parts) == 3 and not ring
        moved = _edit(
            leaf,
            local,
            x=x if axis == "x" or grip else None,
            y=y if axis == "y" or grip else None,
            target=target,
        )
        sided = len(field) > 1 and field[-2] in (
            "tolerance",
            "time_tolerance",
            "tube_abs",
            "tube_rel",
        )
        holder = field[:-1] if sided else field
        assert _differs(leaf, moved) == {holder}, (axis, field)
    assert handle_fields(leaf, "no/such/handle") == {}
    assert handle_fields(leaf, "points/box/0") == {}


# -- a Ctrl+click adds a row that settles on the data -----------------------------


def _place(leaf, table, x, y=0.0, panel="time", **kw):
    blank = copy.deepcopy((leaf.get(table) or [{}])[-1])
    place = RowPlace(
        leaf=leaf,
        blanks={table: blank},
        panel=panel,
        x=x,
        y=y,
        visible=SEEN,
        **{"reference": REF, **kw},
    )
    before = copy.deepcopy(leaf)
    new = metric(leaf["metric"]).place_row(place)
    assert leaf == before
    return new


def test_a_clicked_checkpoint_takes_the_time_and_follows_the_reference():
    new = _place(CHECKS, "points", 0.61234, y=99.0)
    assert new["points"][:3] == CHECKS["points"]
    # The last row's other fields (`Add row`'s), the clicked time, snapped;
    # and NO value: it follows the baseline there, whatever y was clicked.
    assert new["points"][3] == {
        "time": 0.612,
        "tolerance": 0.1,
        "tolerance_mode": "rel",
    }


def test_a_clicked_checkpoint_with_no_reference_takes_the_runs_value():
    actual = (REF[0], 3.0 * REF[0])
    leaf = {**CHECKS, "points": [{"time": 0.25, "value": 0.5, "tolerance": 0.1}]}
    new = _place(leaf, "points", 0.61234, reference=None, actual=actual)
    assert new["points"][1] == {"time": 0.612, "value": 1.836, "tolerance": 0.1}
    with pytest.raises(HandleEditError, match="no signal"):
        _place(leaf, "points", 0.6, reference=None)


def test_a_clicked_checkpoint_is_held_to_where_it_is_scored():
    new = _place(CHECKS, "points", 1.7)
    assert new["points"][3]["time"] == 1.0
    other = {**CHECKS, "against": "golden"}
    with pytest.raises(HandleEditError, match="scores against 'golden'"):
        _place(other, "points", 0.5)
    with pytest.raises(HandleEditError, match="on the time plot"):
        _place(CHECKS, "points", 0.5, panel="spectrum")


def _events(*at):
    return [VLine(id=f"p/events/act/{i}", kind="act", time=t) for i, t in enumerate(at)]


def test_a_clicked_event_snaps_to_the_nearest_actual_event():
    drawn = [*_events(0.4, 0.8123456), VLine(id="p/events/ref/0", kind="ref", time=0.7)]
    new = _place(EVENTS, "events", 0.72, geometry=drawn)
    # The run's event, at its own time — not the click's, not snapped, and
    # not the reference event nearer the click.
    assert new["events"] == [*EVENTS["events"], {"time": 0.8123456}]
    with pytest.raises(HandleEditError, match="already declared"):
        _place(EVENTS, "events", 0.45, geometry=drawn)
    with pytest.raises(HandleEditError, match="the run has no event"):
        _place(EVENTS, "events", 0.45, geometry=[])


def _spectrum():
    freqs = np.arange(0.0, 20.0, 0.5)
    amps = np.full_like(freqs, 0.001)
    for peak, height in ((3.0, 1.0), (8.0, 0.4), (15.0, 0.0015)):  # the last: noise
        amps[int(peak * 2)] = height
    return [Series(id="p/spectrum/act", kind="act", panel="spectrum", x=freqs, y=amps)]


def test_a_clicked_peak_snaps_to_the_nearest_spectral_peak():
    lone = {**SPECTRUM, "peaks": SPECTRUM["peaks"][:1]}
    new = _place(lone, "peaks", 6.2, panel="spectrum", geometry=_spectrum())
    assert new["peaks"][1] == {"freq": 8.0, "tolerance": 0.2, "tolerance_mode": "rel"}
    # A bump under the noise floor the scorer matches over is no peak: a
    # click on it settles on the nearest real one.
    far = _place(lone, "peaks", 15.0, panel="spectrum", geometry=_spectrum())
    assert far["peaks"][1]["freq"] == 8.0
    with pytest.raises(HandleEditError, match="already declared"):
        _place(lone, "peaks", 3.4, panel="spectrum", geometry=_spectrum())
    with pytest.raises(HandleEditError, match="on the spectrum"):
        _place(SPECTRUM, "peaks", 0.5, geometry=_spectrum())
    with pytest.raises(HandleEditError, match="no peak"):
        _place(SPECTRUM, "peaks", 6.2, panel="spectrum", geometry=[])


@pytest.mark.parametrize("mode", ["band", "rel", "abs"])
def test_a_clicked_control_point_takes_the_tubes_own_widths_there(mode):
    """In every width mode the new row holds what the tube already is at
    that time, so adding it changes nothing until it is dragged."""
    leaf = {**BAND, "tube_width_mode": mode}
    new = _place(leaf, "tube_points", 0.50049)
    assert new["tube_points"][:2] == leaf["tube_points"]
    assert new["tube_points"][2] == {"time": 0.5, "upper": 0.2, "lower": 0.15}
    with pytest.raises(HandleEditError, match="already stands at t=0.2"):
        _place(leaf, "tube_points", 0.2001)


def test_a_click_adds_nothing_to_a_tube_of_constant_width():
    constant = {
        "metric": "tube",
        "variable": "x",
        "tube_width_mode": "band",
        "tube_abs": 0.3,
    }
    assert _place(constant, "tube_points", 0.5) == constant
    assert metric("tube").row_noun(constant) is None
    assert metric("tube").row_noun(BAND) == "a control point"
    assert [metric(m).place_row for m in ("nrmse", "range", "scalar")] == [None] * 3


def test_the_placers_are_the_four_metrics_with_rows():
    assert place_points_row is metric("points").place_row
    assert place_events_row is metric("event-timing").place_row
    assert place_peaks_row is metric("dominant-frequency").place_row
    assert place_tube_row is metric("tube").place_row


# -- over the Session ----------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    cfg, work = wave_project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def _view(session, path, variable="x"):
    return SessionView(
        session, Selection(tests=frozenset({A}), variable=variable, leaf_path=path)
    )


def _click(x, y=0.0, axis="x", **keys):
    spans = {"x": [0.0, 1.0], "x2": [0.0, 12.0]}
    return {
        "xaxis": axis,
        "yaxis": "y2" if axis == "x2" else "y",
        "x": x,
        "y": y,
        "xrange": spans[axis],
        "yrange": [-2.0, 2.0],
        **keys,
    }


def _leaf(session, path):
    index = int(path.rsplit("/", 1)[1])
    return session.tree(A)["children"][index]


@pytest.mark.parametrize(
    "path, table, click, added",
    [
        (POINTS, "points", _click(0.78123, 1.0), {"time": 0.781}),
        (EVENT_TIMING, "events", _click(0.7), {"time": 0.8, "tolerance": 0.05}),
        (
            FREQUENCY,
            "peaks",
            _click(7.4, 0.1, "x2"),
            {"freq": 7.96875, "tolerance": 0.2, "tolerance_mode": "rel"},
        ),
        (
            TUBE,
            "tube_points",
            _click(0.5004),
            {"time": 0.5, "upper": 0.4, "lower": 0.3},
        ),
    ],
)
def test_a_ctrl_click_stages_one_row_for_each_metric_with_rows(
    session, path, table, click, added
):
    before = _leaf(session, path)
    outcome = EditOnPlot().act(
        _view(session, path),
        kind="click",
        model_id=A,
        variable="x",
        path=path,
        click=click,
    )
    assert isinstance(outcome, Patch)
    (op,) = outcome.ops
    assert (op.op, op.path) == ("replace", path)
    assert op.value[table] == [*before[table], added]
    assert {k: v for k, v in op.value.items() if k != table} == {
        k: v for k, v in before.items() if k != table
    }
    session.apply(outcome)
    assert len(session.spec.pending) == 1


@pytest.mark.parametrize(
    "path, variable", [(RANGE, "x"), (NRMSE, "x"), (SCALAR, "x"), (CONSTANT_TUBE, "y")]
)
def test_a_ctrl_click_on_a_metric_without_rows_is_nothing(session, path, variable):
    for click in (_click(0.5, 0.2), _click(5.0, 0.2, "x2")):
        assert (
            clicked_row(
                _view(session, path, variable),
                model_id=A,
                variable=variable,
                path=path,
                click=click,
            )
            is None
        )


def _set(session, path, pointer, click, variable="x"):
    return placed(
        _view(session, path, variable),
        model_id=A,
        variable=variable,
        path=path,
        pointer=pointer,
        click=click,
    )


def test_an_armed_bound_is_set_through_its_line(session):
    new = _set(session, RANGE, ("max_value",), _click(0.3, 1.23456))
    assert new == {**_leaf(session, RANGE), "max_value": 1.23}
    # The line's own refusal: a click is a drag released there.
    with pytest.raises(HandleEditError, match="would exceed max_value"):
        _set(session, RANGE, ("min_value",), _click(0.3, 2.5))


def test_an_armed_side_puts_its_edge_at_the_click(session):
    """Whatever the side is measured from, its EDGE passes through the
    click, to the snapped step (0.01 over a y span of 4)."""
    # A row's own tolerance: from its value, -1.0.
    new = _set(session, POINTS, ("points", 0, "tolerance", "upper"), _click(0.9, -0.5))
    assert new["points"][0]["tolerance"] == 0.5
    # A row's time tolerance: from its time, 0.25.
    new = _set(
        session, POINTS, ("points", 0, "time_tolerance", "lower"), _click(0.18, 9.0)
    )
    assert new["points"][0]["time_tolerance"] == 0.07
    # The criterion's, which row 1 defers to: from the reference at ITS time.
    target = 0.95 * wave(0.6)
    new = _set(session, POINTS, ("tolerance", "upper"), _click(0.9, target + 0.4321))
    assert new["tolerance"] == 0.43
    assert new["points"] == _leaf(session, POINTS)["points"]
    # A constant tube's: from the reference at the CLICK's time (y = 2.1 t).
    new = _set(session, CONSTANT_TUBE, ("tube_abs", "upper"), _click(0.5, 1.5), "y")
    assert new["tube_abs"] == 0.45
    # A scalar's, on its baseline scalar; an event's window; a peak's.
    reference = max(0.95 * wave(i / 200) for i in range(201))
    new = _set(session, SCALAR, ("tolerance", "lower"), _click(0.5, reference - 0.25))
    assert new["tolerance"] == 0.25
    new = _set(session, EVENT_TIMING, ("events", 0, "tolerance", "upper"), _click(0.47))
    assert new["events"][0]["tolerance"] == 0.07
    new = _set(
        session, FREQUENCY, ("peaks", 0, "tolerance", "lower"), _click(2.1, 0.1, "x2")
    )
    assert new["peaks"][0]["tolerance"] == 0.3  # 0.9 Hz under 3 Hz, rel


def test_an_armed_row_field_is_set_through_its_ring(session):
    new = _set(session, POINTS, ("points", 0, "time"), _click(0.33333, 9.0))
    assert new["points"][0] == {**_leaf(session, POINTS)["points"][0], "time": 0.333}
    new = _set(session, POINTS, ("points", 0, "value"), _click(0.9, 0.5678))
    assert new["points"][0] == {**_leaf(session, POINTS)["points"][0], "value": 0.57}
    new = _set(session, FREQUENCY, ("peaks", 0, "freq"), _click(7.7777, 0.1, "x2"))
    assert new["peaks"][0]["freq"] == 7.78
    new = _set(session, TUBE, ("tube_points", 1, "time"), _click(0.71234, 9.0))
    assert new["tube_points"][1] == {"time": 0.712, "upper": 0.6, "lower": 0.4}
    # The ring's own refusal: two control points cannot share a time.
    with pytest.raises(HandleEditError, match="share time"):
        _set(session, TUBE, ("tube_points", 1, "time"), _click(0.1, 0.0))


def test_an_armed_field_with_no_handle_is_set_from_the_click(session):
    # A window's ends and an event's time have no handle: the click's x.
    new = _set(session, NRMSE, ("window", "start"), _click(0.21234, 0.7))
    assert new == {**_leaf(session, NRMSE), "window": {"start": 0.212}}
    new = _set(session, EVENT_TIMING, ("events", 0, "time"), _click(0.81234))
    assert new["events"][0] == {"time": 0.812, "tolerance": 0.05}
    # A checkpoint that follows the baseline has no value to drag: armed,
    # its value field takes the click's y.
    new = _set(session, POINTS, ("points", 1, "value"), _click(0.9, 0.5678))
    assert new["points"][1] == {"time": 0.6, "value": 0.57}
    # A row with no tolerance of its own takes one first, from what it
    # deferred to, and the clicked side is set on it.
    target = 0.95 * wave(0.6)
    new = _set(
        session, POINTS, ("points", 1, "tolerance", "lower"), _click(0.9, target - 0.3)
    )
    assert new["points"][1] == {"time": 0.6, "tolerance": 0.3}
    assert new["tolerance"] == 0.2
    new = _set(session, POINTS, ("points", 1, "time_tolerance", "upper"), _click(0.66))
    assert new["points"][1] == {"time": 0.6, "time_tolerance": 0.06}
    with pytest.raises(ValueError, match="nothing on this plot sets that field"):
        _set(session, NRMSE, ("tolerance",), _click(0.5, 0.5))


def test_a_refused_armed_click_is_the_handles_own_refusal(session):
    with pytest.raises(HandleEditError, match="would cross its target") as err:
        _set(session, POINTS, ("points", 0, "tolerance", "upper"), _click(0.9, -1.5))
    assert str(err.value).startswith(POINTS + ": ")
    assert err.value.reason == "the upper side would cross its target"


def test_edit_criteria_stages_its_own_armed_fields(session):
    outcome = EditCriteria().act(
        _view(session, RANGE),
        kind="place",
        model_id=A,
        variable="x",
        path=RANGE,
        pointer=("min_value",),
        click=_click(0.5, -1.23456),
    )
    (op,) = outcome.ops
    assert (op.op, op.path, op.value["min_value"]) == ("replace", RANGE, -1.23)


# -- a held handle's preview is its release -----------------------------------------

#: A handle of each kind — ring, value arrow, time arrow, window arrow,
#: spectrum ring and arrow, bound line, tolerance line — with the per-shape
#: payload its release would carry.
HELD = [
    (POINTS, "points/target/0", {"xanchor": 0.31234, "yanchor": -0.71234}),
    (POINTS, "points/upper/0", {"xanchor": 0.25, "yanchor": -0.61234}),
    (POINTS, "points/before/0", {"xanchor": 0.17654, "yanchor": -1.0}),
    (POINTS, "points/lower/1", {"xanchor": 0.6, "yanchor": -1.61234}),
    (EVENT_TIMING, "events/late/0", {"xanchor": 0.46789, "yanchor": 0.5}),
    (FREQUENCY, "peaks/declared/0", {"xanchor": 3.21234, "yanchor": 0.2}),
    (FREQUENCY, "peaks/upper/0", {"xanchor": 3.91234, "yanchor": 0.5}),
    (TUBE, "tube/upper/1", {"xanchor": 0.81234, "yanchor": 1.71234}),
    (RANGE, "range/max", {"y0": 1.71234, "y1": 1.71234}),
    (SCALAR, "scalar/upper", {"y0": 1.91234, "y1": 1.91234}),
]


@pytest.mark.parametrize("path, local, shape", HELD)
async def test_a_held_handles_preview_is_what_its_release_stages(
    session, path, local, shape
):
    """The move and the release reach ONE function with one payload: the
    leaf previewed is the leaf staged, snapped — and a move stages nothing."""
    span = {"x": 12.0 if "peaks" in local else 1.0, "y": 4.0}
    said = {**shape, "visible": span, "drag": 7}
    selection = Selection(tests=frozenset({A}), variable="x", leaf_path=path)
    async with host(session, EditOnPlot(), selection) as (_user, errors, figure):
        handlers = dict(figure.handlers)
        handlers["dstf_move"](f"{path}/{local}", said)
        assert errors == {} and session.spec.pending == []
        (previewed, revision) = figure.panel.previews[-1]
        assert revision == 7 and previewed != _leaf(session, path)
        # ... and it is snapped: no number of it has more than 4 decimals.
        moved = _differs(_leaf(session, path), previewed)
        assert moved
        handlers["plotly_relayout"](f"{path}/{local}", said)
        assert errors == {}
        (op,) = session.spec.pending
        assert op.value == previewed
        # The release ends the preview.
        assert figure.panel.previews[-1] == (None, 0)


async def test_a_refused_or_ended_move_previews_nothing(session):
    selection = Selection(tests=frozenset({A}), variable="x", leaf_path=POINTS)
    async with host(session, EditOnPlot(), selection) as (_user, errors, figure):
        move = dict(figure.handlers)["dstf_move"]
        crossed = {
            "xanchor": 0.25,
            "yanchor": -1.9,
            "visible": {"x": 1, "y": 4},
            "drag": 3,
        }
        move(f"{POINTS}/points/upper/0", crossed)
        assert figure.panel.previews[-1][0] is None
        move(f"{POINTS}/points/upper/0", {**crossed, "yanchor": -0.5})
        assert figure.panel.previews[-1][0] is not None
        move("", {"end": True})
        assert figure.panel.previews[-1] == (None, 0)
        assert errors == {} and session.spec.pending == []


def test_dragged_is_pure_and_reuses_a_view_it_is_handed(session):
    view = _view(session, RANGE)
    held = session.view(A, "x")
    reads = []
    session.view = lambda *a: reads.append(a) or held  # type: ignore[method-assign]
    shape = {"y0": 1.5, "y1": 1.5}
    first = dragged(
        view,
        model_id=A,
        variable="x",
        handle=f"{RANGE}/range/max",
        shape=shape,
        view=held,
    )
    assert reads == [] and first is not None
    path, raw, new = first
    assert (path, raw, new["max_value"]) == (RANGE, _leaf(session, RANGE), 1.5)
    assert session.spec.pending == []


# -- a move that arrives after its release is dropped --------------------------------


def _hook():
    hook = FigureHook()
    heard = []
    hook.on("dstf_move", lambda handle, payload: heard.append((handle, payload)))
    hook.on(
        "plotly_relayout", lambda handle, payload: heard.append(("released", payload))
    )
    return hook, heard


def test_a_move_numbered_under_a_release_is_dropped():
    hook, heard = _hook()
    hook.move({"seq": 5, "update": {"shapes[0].xanchor": 0.3}})
    assert heard == [("", {"xanchor": 0.3})]
    hook._dispatch("plotly_relayout", {"shapes[0].xanchor": 0.4, "shapes[0].seq": 8})
    assert heard[-1] == ("released", {"xanchor": 0.4, "seq": 8})
    # Sent before the release, delivered after it: nothing hears it.
    for late in (6, 7, 8):
        hook.move({"seq": late, "update": {"shapes[0].xanchor": 0.35}})
    assert len(heard) == 2
    # The next drag's moves are numbered past the release.
    hook.move({"seq": 9, "update": {"shapes[0].xanchor": 0.5}})
    assert heard[-1] == ("", {"xanchor": 0.5})


def test_a_drags_end_is_heard_once_and_closes_it():
    hook, heard = _hook()
    hook.move({"seq": 3, "end": True})
    assert heard == [("", {"end": True})]
    hook.move({"seq": 2, "update": {"shapes[0].xanchor": 0.1}})
    hook.move({"seq": 3, "end": True})
    assert len(heard) == 1


@pytest.mark.parametrize(
    "bad", [None, {}, {"seq": "9"}, {"seq": True}, {"update": {}}, 7]
)
def test_a_move_that_says_no_number_is_dropped(bad):
    hook, heard = _hook()
    hook.move(bad)
    assert heard == []


def test_the_fixtures_tree_has_every_metric(session):
    kinds = [leaf["metric"] for leaf in TREE["children"]]
    assert kinds == [
        "points",
        "event-timing",
        "dominant-frequency",
        "tube",
        "range",
        "nrmse",
        "scalar",
        "tube",
    ]
