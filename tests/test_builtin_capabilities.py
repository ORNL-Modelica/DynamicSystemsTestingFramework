"""The capabilities the tool ships (§4.6 table, §4.7): `act` and `available`
over the fixture project, and each panel alone in NiceGUI's in-process user
simulation through `capability_host` — no shell, no browser."""

from __future__ import annotations

import asyncio
import copy
from functools import partial
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

from capability_host import host  # noqa: E402
from conftest import series
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.accept import Accept  # noqa: E402
from dstf.authoring.builtin.edit_criteria import EditCriteria  # noqa: E402
from dstf.authoring.builtin.edit_tree import EditTree, seed_data  # noqa: E402
from dstf.authoring.builtin.enqueue import EnqueueTests  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from dstf.authoring.spec_document import PatchOp  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from dstf.comparison.seeds import seeded_leaf  # noqa: E402
from test_handles import TREE  # noqa: E402
from test_session import (  # noqa: E402
    LOOSEN,
    A,
    B,
    C,
    D,
    _project,
    _session_with_entry,
    _two_var_project,
)
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


@pytest.fixture
def two_var_session(tmp_path):
    """A's tree is `and[nrmse x, nrmse y]`: a second variable is what tells
    a form that reads the selection apart from one that takes the first."""
    cfg, work = _two_var_project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


#: The page's selection the leaf panels are built for (D129).
LEAF0 = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0")


async def test_edit_criteria_stages_on_change(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        # derive_schema's label for NrmseConfig.tolerance.
        await user.should_see("Tolerance")
        form_tol = next(
            c
            for c in user.find(ui.number).elements
            if "olerance" in str(c.props.get("label", ""))
        )
        form_tol.set_value(0.5)
        assert len(session.spec.pending) == 2  # materialization + the replace
        assert session.spec.pending[-1].op == "replace"
        assert session.spec.pending[-1].path == "/metrics/children/0"
        assert session.spec.pending[-1].value["tolerance"] == 0.5
        from dstf.reporting.status import PASS, classify

        assert classify(session.comparisons[A]) == PASS  # rescored live


async def test_edit_criteria_switches_mode_and_offers_against_and_window(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("mode")
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        assert set(mode.options) >= {"nrmse", "tube", "range"}
        mode.set_value("range")
        # RangeConfig.max_value's label is "Upper bound (optional)".
        await user.should_see("Upper bound")
        against = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "against"
        )
        assert against.options == ["primary"]
        start = next(
            c
            for c in user.find(ui.number).elements
            if c.props.get("label") == "window start"
        )
        start.set_value(0.25)
        leaf = session.spec.pending[-1].value
        assert leaf["metric"] == "range" and leaf["window"]["start"] == 0.25


def _ordered(element):
    """Every element under ``element``, in document order."""
    for slot in element.slots.values():
        for child in slot.children:
            yield child
            yield from _ordered(child)


def _labels(element) -> list[str]:
    """The labelled controls under ``element``, in document order."""
    return [
        str(e.props["label"])
        for e in _ordered(element)
        if isinstance(e, (ui.select, ui.number, ui.input, ui.textarea))
        and e.props.get("label")
    ]


async def test_the_criterion_editors_fields_are_one_row_with_the_mode_first(session):
    """The board: the plot and its editor have to fit a screen together, so
    the fields sit in ONE wrapping row — and the mode select, which decides
    what the other fields ARE, comes first. It was created after the form's
    slot, so it rendered below the fields it controls; and each field is
    wide enough for its label ("Width mode" was clipped to "Wid…")."""
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _figure):
        await user.should_see("Tolerance")
        (row,) = [e for e in user.find(ui.row).elements if "dstf-fields" in e.classes]
        assert "wrap" in row.classes
        labels = _labels(row)
        assert labels[0] == "mode"
        assert "Tolerance" in labels
        assert labels[-3:] == ["window start", "window end", "against"]
        fields = [
            e
            for e in _ordered(row)
            if isinstance(e, (ui.select, ui.number, ui.input)) and e.props.get("label")
        ]
        assert fields and all("dstf-field" in e.classes for e in fields)


async def test_a_list_field_takes_its_own_line_of_the_editors_row(session):
    """A mode with a list field (tube's control points) shows it as JSON: a
    multi-line control belongs on its own full-width line of the wrapping
    row, not squeezed between two numbers."""
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _figure):
        await user.should_see("mode")
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("tube")
        await user.should_see("Width mode")
        (row,) = [e for e in user.find(ui.row).elements if "dstf-fields" in e.classes]
        assert _labels(row)[0] == "mode" and "Width mode" in _labels(row)
        (points,) = [
            e
            for e in _ordered(row)
            if isinstance(e, ui.textarea) and e.props.get("label") == "Control points"
        ]
        assert "dstf-field-wide" in points.classes
        # In SOURCE order it sits between two numbers ("Min width" before,
        # "Interpolation" after), which broke the row into three lines. The
        # stylesheet is what puts it last of the row.
        assert _labels(row).index("Control points") < len(_labels(row)) - 1
        from dstf.authoring.shell.style import STYLE

        assert ".dstf-fields .dstf-field-wide { order: 1; }" in STYLE


async def test_a_mode_switch_starts_from_the_new_modes_seed(session):
    """Set a tolerance, switch to range, set a bound: the leaf carries no
    stale nrmse tolerance, and the bound not typed is the seed's — a range
    with neither bound does not resolve."""
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("Tolerance")
        tol = next(
            c
            for c in user.find(ui.number).elements
            if str(c.props.get("label")) == "Tolerance"
        )
        tol.set_value(0.5)
        assert len(session.spec.pending) == 2
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("range")
        await user.should_see("Upper bound")
        seeded = session.spec.pending[-1].value
        assert seeded == seeded_leaf("range", "x", seed_data(_view(session), A, "x"))
        assert set(seeded) == {"metric", "variable", "min_value", "max_value"}
        upper = next(
            c
            for c in user.find(ui.number).elements
            if "Upper" in str(c.props.get("label"))
        )
        upper.set_value(2.0)
        leaf = session.spec.pending[-1].value
        assert "tolerance" not in leaf, leaf
        assert leaf == {**seeded, "max_value": 2.0}


async def test_a_windowed_criterion_is_seeded_from_its_window(session):
    """The window stays across a mode switch, so the seed reads the samples
    the window scores: A's baseline is x = t, and over [0.5, 1] a range
    brackets 0.5..1, not 0..1."""
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("mode")
        numbers = {c.props.get("label"): c for c in user.find(ui.number).elements}
        numbers["window start"].set_value(0.5)
        numbers["window end"].set_value(1.0)
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("range")
        await user.should_see("Upper bound")
        leaf = session.spec.pending[-1].value
        window = {"start": 0.5, "end": 1.0}
        assert leaf["window"] == window
        expected = seeded_leaf(
            "range", "x", seed_data(_view(session), A, "x", window=window)
        )
        assert {k: leaf[k] for k in ("min_value", "max_value")} == {
            k: expected[k] for k in ("min_value", "max_value")
        }
        assert 0.4 < leaf["min_value"] < 0.5, leaf


WINDOWED = {
    "metric": "range",
    "variable": "x",
    "min_value": -1.0,
    "max_value": 2.0,
    "window": {"start": 0.3, "end": 0.9},
}
ROOT_LEAF = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics")
ENDS_BEFORE = "The window must end after it starts"


def _keys(user, field, *texts: str) -> None:
    """Keystrokes in ``field``, each its own change event carrying the
    field's whole text after it, as the browser sends them: `-0.5` typed
    over a number is nothing (`-` is no number yet), `-0`, `-0.`, `-0.5`."""
    from nicegui.testing.user_interaction import UserInteraction

    for text in texts:
        UserInteraction(user, {field}, None).trigger("update:modelValue", text)


def _number(user, label: str):
    (field,) = [c for c in user.find(ui.number).elements if c.props["label"] == label]
    return field


async def test_a_refused_window_end_leaves_the_window_it_had(session):
    """`-0.5` typed over a window end of 0.9: the `-` on the way is no
    number, which UNSET the end — accepted and staged — and every key after
    it was refused, changing nothing. The error line showed beside a field
    reading -0.5 while the leaf's window had no end at all. Refused, the
    leaf goes back to what the field held when it was entered."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        end = _number(user, "window end")
        _keys(user, end, "")
        assert "end" not in session.tree(A)["window"]  # the `-`, accepted
        _keys(user, end, "-0", "-0.", "-0.5")
        assert session.tree(A)["window"] == {"start": 0.3, "end": 0.9}
        assert ENDS_BEFORE in errors["edit-criteria"]
        assert end.value == -0.5  # what was typed stays in the field
        # One staged state, as if the field had not been touched.
        assert session.tree(A) == WINDOWED
        # A value the leaf takes is staged as ever, and the sentence goes.
        _keys(user, end, "0.7")
        assert session.tree(A)["window"] == {"start": 0.3, "end": 0.7}
        assert "edit-criteria" not in errors


async def test_a_refusal_puts_back_what_the_field_held_when_it_was_entered(session):
    """Not what the editor opened on: 0.8 typed and taken, the field left
    and entered again, then `1e-3` — `1`, `1e` (no number: unset), `1e-`,
    `1e-3`, which ends before the window starts. The end is 0.8 again."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        end = _number(user, "window end")
        _keys(user, end, "0.8")
        assert session.tree(A)["window"]["end"] == 0.8
        user.find(marker="edit-criteria:fields").trigger("focusin")
        _keys(user, end, "1", "", "", "1e-3")
        assert ENDS_BEFORE in errors["edit-criteria"]
        assert session.tree(A)["window"] == {"start": 0.3, "end": 0.8}


async def test_a_refusal_in_one_field_keeps_another_fields_edit(session):
    """What goes back is the field's own start: a bound typed before it —
    with no focus heard between, as a value set from Python — stays."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        _number(user, "Upper bound (optional)").set_value(5.0)
        end = _number(user, "window end")
        _keys(user, end, "", "-0.5")
        assert ENDS_BEFORE in errors["edit-criteria"]
        assert session.tree(A) == {**WINDOWED, "max_value": 5.0}


async def test_a_refusal_with_nothing_staged_on_the_way_stages_nothing(session):
    """`9` over a window start of 0.3 is refused at once (the window ends
    at 0.9): nothing was staged on the way, so nothing is put back."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        pending = list(session.spec.pending)
        _keys(user, _number(user, "window start"), "9")
        assert ENDS_BEFORE in errors["edit-criteria"]
        assert session.spec.pending == pending


# -- values that can never score are refused, as a refused window is ----------

MAX = {"quantity": {"reduce": "max"}}


def _criteria_act(session, metric: str, params: dict):
    return EditCriteria().act(
        SessionView(session, LEAF0),
        model_id=A,
        path="/metrics/children/0",
        metric=metric,
        variable="x",
        params=params,
        against="primary",
        window={},
    )


@pytest.mark.parametrize(
    ("metric", "params", "sentence"),
    [
        ("nrmse", {"tolerance": -0.5}, "tolerance must not be negative; got −0.5"),
        ("points", {"tolerance": -1}, "tolerance must not be negative; got −1"),
        (
            "points",
            {"tolerance": {"lower": -0.25, "upper": 0.1}},
            "tolerance (lower side) must not be negative; got −0.25",
        ),
        (
            "points",
            {"tolerance": {"lower": 0.1, "upper": -2e-3}},
            "tolerance (upper side) must not be negative; got −0.002",
        ),
        (
            "scalar",
            {**MAX, "tolerance": {"lower": 0.0, "upper": -1.0}},
            "tolerance (upper side) must not be negative; got −1",
        ),
        (
            "event-timing",
            {"time_tolerance": -0.5},
            "time_tolerance must not be negative; got −0.5",
        ),
        (
            "event-timing",
            {"time_tolerance": {"lower": -0.5, "upper": 0.1}},
            "time_tolerance (early side) must not be negative; got −0.5",
        ),
        (
            "event-timing",
            {"time_tolerance": {"lower": 0.1, "upper": -0.5}},
            "time_tolerance (late side) must not be negative; got −0.5",
        ),
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": -0.3},
            "tube_abs must not be negative; got −0.3",
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": -0.02},
            "tube_rel must not be negative; got −0.02",
        ),
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": {"lower": -0.1, "upper": -0.5}},
            "tube_abs sides must not sum below zero; got lower −0.1, upper −0.5",
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": {"lower": 0.02, "upper": -0.05}},
            "tube_rel sides must not sum below zero; got lower 0.02, upper −0.05",
        ),
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": 0.3, "tube_min_width": -0.3},
            "tube_min_width must not be negative; got −0.3",
        ),
        # A minimum width that is no positive number floors nothing, and
        # the width under it is refused as one with no floor is: a zero, a
        # negative one, and what is no number at all.
        (
            "tube",
            {
                "tube_width_mode": "band",
                "tube_abs": {"lower": -0.1, "upper": -0.5},
                "tube_min_width": 0,
            },
            "tube_abs sides must not sum below zero; got lower −0.1, upper −0.5",
        ),
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": -0.3, "tube_min_width": -0.2},
            "tube_abs must not be negative; got −0.3",
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": -0.02, "tube_min_width": True},
            "tube_rel must not be negative; got −0.02",
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": -0.02, "tube_min_width": "0.2"},
            "tube_rel must not be negative; got −0.02",
        ),
        (
            "range",
            {"min_value": 9, "max_value": 2},
            "min_value 9 exceeds max_value 2",
        ),
        (
            "range",
            {"min_value": -1.0, "max_value": -2.5},
            "min_value −1 exceeds max_value −2.5",
        ),
        (
            "scalar",
            {**MAX, "min_value": 9, "max_value": 2},
            "min_value 9 exceeds max_value 2",
        ),
        (
            "scalar",
            {**MAX, "min_value": 2, "max_value": 2.0},
            "min_value 2 equals max_value 2; a scalar criterion needs min_value < max_value",
        ),
    ],
)
def test_edit_criteria_refuses_a_value_that_can_never_score(
    session, metric, params, sentence
):
    """A negative tolerance — a number or either side of a pair, a value's
    or a time's — a tube's width whose two sides leave nothing between
    them, and bounds out of order were staged as typed, and the criterion
    could only ever fail (a scalar with equal bounds: never score); a
    tube's minimum width below zero was staged too, a floor under nothing.
    Refused by `act`, in a sentence naming the field and the value, before
    anything is staged."""
    with pytest.raises(ValueError) as box:
        _criteria_act(session, metric, params)
    assert str(box.value) == sentence


@pytest.mark.parametrize(
    ("metric", "params"),
    [
        ("nrmse", {}),
        ("nrmse", {"tolerance": 0.0}),
        ("points", {"tolerance": {"lower": 0.0, "upper": 0.1}}),
        ("event-timing", {"time_tolerance": 0}),
        ("tube", {"tube_width_mode": "band", "tube_abs": 0.0}),
        # A pair with a negative side is a band shifted off the reference,
        # and can pass: what never scores is a width whose sides sum below
        # zero — a negative NUMBER, or a pair that does.
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": {"lower": -0.1, "upper": 0.3}},
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": {"lower": 0.2, "upper": -0.05}},
        ),
        # Sides that sum to exactly zero are a band of no width, shifted:
        # a zero is a tolerance.
        (
            "tube",
            {"tube_width_mode": "band", "tube_abs": {"lower": -0.1, "upper": 0.1}},
        ),
        (
            "tube",
            {"tube_width_mode": "rel", "tube_rel": {"lower": 0.05, "upper": -0.05}},
        ),
        ("tube", {"tube_width_mode": "band", "tube_abs": {"lower": 0, "upper": 0}}),
        ("tube", {"tube_width_mode": "rel", "tube_rel": 0.1, "tube_min_width": 0}),
        ("tube", {"tube_width_mode": "rel", "tube_rel": 0.1, "tube_min_width": 0.3}),
        # Over a POSITIVE minimum width no width leaves nothing inside: the
        # floor widens both sides to itself, whatever the width says.
        (
            "tube",
            {
                "tube_width_mode": "band",
                "tube_abs": {"lower": -0.1, "upper": -0.5},
                "tube_min_width": 0.2,
            },
        ),
        ("tube", {"tube_width_mode": "band", "tube_abs": -0.3, "tube_min_width": 0.2}),
        (
            "tube",
            {
                "tube_width_mode": "rel",
                "tube_rel": {"lower": 0.02, "upper": -0.05},
                "tube_min_width": 1e-9,
            },
        ),
        ("tube", {"tube_width_mode": "rel", "tube_rel": -0.02, "tube_min_width": 5}),
        ("range", {"min_value": -9}),
        ("range", {"min_value": 2, "max_value": 2}),  # equal bounds score
        ("scalar", {**MAX, "min_value": 1, "max_value": 2}),
        # No number: the leaf's config error, said by its row — not this rule's.
        ("nrmse", {"tolerance": "-0.5"}),
    ],
)
def test_edit_criteria_takes_a_zero_and_a_range_with_equal_bounds(
    session, metric, params
):
    (op,) = _criteria_act(session, metric, params).ops
    assert (op.op, op.path) == ("replace", "/metrics/children/0")
    assert {k: op.value[k] for k in params} == params


@pytest.mark.parametrize("name", ["range", "scalar"])
def test_equal_bounds_are_refused_where_the_scorer_refuses_them(name):
    """`STRICT_BOUNDS` is the scorer's rule, said before the stage: a
    metric is in it exactly when building its mode with equal bounds
    raises."""
    from dstf.authoring.unscorable import STRICT_BOUNDS
    from dstf.comparison.modes import resolve_mode

    extra = MAX if name == "scalar" else {}
    override = {"mode": name, "min_value": 1.0, "max_value": 1.0, **extra}
    try:
        resolve_mode(override, 0.01)
        refused = False
    except ValueError:
        refused = True
    assert (name in STRICT_BOUNDS) is refused


@pytest.mark.parametrize("key", ["tube_abs", "tube_rel"])
def test_a_negative_tube_width_never_scores_and_a_shifted_band_can(key):
    """Why a width is refused where its sides sum below zero and nowhere
    else: a negative NUMBER is both sides, a tube whose upper bound is
    under its lower one — nothing is inside it, whatever the run (it was
    staged, and read "0.0% inside tube") — and so is a PAIR whose sides sum
    below zero, both negative or one. A pair with one negative side and a
    sum above zero is a band that does not hold its reference, and a run
    that lies in it passes; one whose sides sum to zero is a band of no
    width, which a run on it passes."""
    import numpy as np

    from dstf.comparison.modes import resolve_mode

    mode = "band" if key == "tube_abs" else "rel"
    t = np.linspace(0.0, 1.0, 11)
    ref = 1.0 + t

    def scored(width, act):
        override = {"mode": "tube", "tube_width_mode": mode, key: width}
        return resolve_mode(override, 0.01).compare(t, ref, t, act)

    def off(by):
        """The reference moved by ``by``, in the width's own unit."""
        return ref + by * (1.0 if key == "tube_abs" else ref)

    nothing_inside = (
        -0.2,
        {"lower": -0.1, "upper": -0.5},
        {"lower": 0.2, "upper": -0.3},
    )
    for width in nothing_inside:
        for by in (0.0, 0.1, -0.1, 0.3, -0.3):
            result = scored(width, off(by))
            assert not result.passed and result.tube_points_inside == 0, (width, by)
    shifted = {"lower": -0.05, "upper": 0.2}
    assert scored(shifted, off(0.1)).passed
    assert not scored(shifted, ref).passed  # the reference itself is outside
    line = {"lower": -0.1, "upper": 0.1}  # no width, 0.1 above the reference
    assert scored(line, off(0.1)).passed


@pytest.mark.parametrize("key", ["tube_abs", "tube_rel"])
def test_a_negative_tube_width_over_a_positive_min_width_holds_its_reference(key):
    """Why a positive minimum width lifts the refusal: the scorer raises
    BOTH sides to it, so the widths that hold nothing alone — a negative
    number, a pair summing below zero — are a tube of that half-width, and
    a run on its reference is 100 % inside. A floor of zero or below is no
    floor: the same widths hold nothing, as with none."""
    import numpy as np

    from dstf.comparison.modes import resolve_mode

    mode = "band" if key == "tube_abs" else "rel"
    t = np.linspace(0.0, 1.0, 11)
    ref = 1.0 + t

    def scored(width, floor, act):
        override = {"mode": "tube", "tube_width_mode": mode, key: width}
        if floor is not None:
            override["tube_min_width"] = floor
        return resolve_mode(override, 0.01).compare(t, ref, t, act)

    for width in (-0.2, {"lower": -0.1, "upper": -0.5}, {"lower": 0.2, "upper": -0.3}):
        for floor in (0.2, 1e-9):
            result = scored(width, floor, ref)
            assert result.passed and result.tube_points_inside == 1, (width, floor)
        assert scored(width, 0.2, ref + 0.19).passed  # ... and the floor is its width
        assert not scored(width, 0.2, ref + 0.21).passed
        for floor in (None, 0, 0.0, -0.2):
            result = scored(width, floor, ref)
            assert not result.passed and result.tube_points_inside == 0, (width, floor)


def test_a_hand_written_negative_tolerance_is_staged_and_scored_as_it_was(session):
    """The editor's rule is not the Session's, the parser's or the
    scorer's: a patch holding a negative tolerance is staged, and its leaf
    fails — a nrmse under −0.5 never is."""
    from dstf.comparison.sides import Sides
    from dstf.reporting.status import FAIL, classify

    leaf = {"metric": "nrmse", "variable": "x", "tolerance": -0.5}
    session.stage([PatchOp(D, "add", "/metrics", leaf)])
    assert session.tree(D) == leaf
    comp = session.comparisons[D]
    assert classify(comp) == FAIL and comp.error_message is None
    assert comp.variables[0].tolerance_used == Sides(-0.5, -0.5)


NRMSE_ROOT = {"metric": "nrmse", "variable": "x", "tolerance": 0.3}


async def test_a_negative_tolerance_typed_puts_the_leaf_back(session):
    """`-0.5` typed over a tolerance of 0.3 passes through nothing (the
    `-`: unset, which the leaf takes) and `-0`; refused, the leaf goes back
    to what the field held when it was entered, as a refused window does.
    The field keeps what was typed, and the sentence says why."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(NRMSE_ROOT))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        tol = _number(user, "Tolerance")
        _keys(user, tol, "")
        assert "tolerance" not in session.tree(A)  # the `-`, accepted
        _keys(user, tol, "-0", "-0.", "-0.5")
        assert errors["edit-criteria"] == (
            "Refused: tolerance must not be negative; got −0.5"
        )
        assert session.tree(A) == NRMSE_ROOT
        assert tol.value == -0.5
        _keys(user, tol, "0.2")
        assert session.tree(A)["tolerance"] == 0.2
        assert "edit-criteria" not in errors


@pytest.mark.parametrize("saved", [0.3, 0])
async def test_a_negative_zero_typed_in_a_criterions_field_is_staged_as_zero(
    tmp_path, saved
):
    """`-0` in a criterion's tolerance — what `-0.5`, refused, backspaces
    to — is a tolerance of 0, and is staged as that: it was −0.0, which
    Save wrote as `-0.0`. Over a saved tolerance of 0 it is no edit."""
    import json
    import math

    s = _session_with_entry(tmp_path, {"metrics": {**NRMSE_ROOT, "tolerance": saved}})
    async with host(s, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        tol = _number(user, "Tolerance")
        _keys(user, tol, "-0.5")
        assert "edit-criteria" in errors and s.spec.pending == []
        _keys(user, tol, "-0.")
        staged = s.tree(A)["tolerance"]
        assert staged == 0 and math.copysign(1.0, staged) == 1.0
        assert len(s.spec.pending) == (1 if saved else 0)
        assert "-0.0" not in json.dumps(s.export())
        assert "edit-criteria" not in errors


async def test_a_tiny_number_typed_in_a_criterions_field_is_staged_as_typed(session):
    """What makes a typed `-0` a zero makes nothing else one: `1e-5` in a
    tolerance and `-1e-9` in a bound are staged as those numbers."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        _keys(user, _number(user, "Lower bound (optional)"), "-1e-9")
        assert session.tree(A)["min_value"] == -1e-9
        _keys(user, _number(user, "Upper bound (optional)"), "1e-5")
        assert session.tree(A) == {**WINDOWED, "min_value": -1e-9, "max_value": 1e-5}
        assert "edit-criteria" not in errors


async def test_a_negative_tube_width_typed_puts_the_leaf_back(session):
    """`-0.3` typed over a tube's width of 0.3 — linked: both sides —
    passes through nothing and `-0`, which the leaf takes; refused, the
    leaf holds the width the field had when it was entered."""
    leaf = {
        "metric": "tube",
        "variable": "x",
        "tube_width_mode": "band",
        "tube_abs": 0.3,
    }
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(leaf))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        width = _number(user, "− Tube abs")
        _keys(user, width, "", "-0", "-0.", "-0.3")
        assert errors["edit-criteria"] == (
            "Refused: tube_abs must not be negative; got −0.3"
        )
        assert session.tree(A) == leaf
        assert width.value == -0.3
        _keys(user, width, "0.2")
        assert session.tree(A)["tube_abs"] == 0.2
        assert "edit-criteria" not in errors


async def test_a_negative_min_width_typed_puts_the_leaf_back(tmp_path):
    """`-0.3` typed into a tube's Min width was staged, one pending edit: a
    floor below zero is under every width and floors nothing. Refused, the
    leaf is the saved one again and nothing is pending; the field keeps
    what was typed."""
    leaf = {
        "metric": "tube",
        "variable": "x",
        "tube_width_mode": "rel",
        "tube_rel": 0.1,
    }
    s = _session_with_entry(tmp_path, {"metrics": leaf})
    async with host(s, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        floor = _number(user, "Min width")
        _keys(user, floor, "-0.3")
        assert errors["edit-criteria"] == (
            "Refused: tube_min_width must not be negative; got −0.3"
        )
        assert s.tree(A) == leaf and s.spec.pending == []
        assert floor.value == -0.3
        _keys(user, floor, "0.3")
        assert s.tree(A) == {**leaf, "tube_min_width": 0.3}
        assert len(s.spec.pending) == 1 and "edit-criteria" not in errors


async def test_a_tube_pair_typed_to_leave_nothing_inside_puts_the_leaf_back(session):
    """An unlinked width of 0.1 below and 0.3 above, its upper side typed
    over. `-0.05` is taken: the sides still sum above zero — a band shifted
    under its reference. `-0.5` is refused, by the SUM (the upper bound
    would be under the lower one, and the criterion read "0.0% inside
    tube"): the leaf holds the pair the field had when it was entered, the
    field keeps what was typed and the sentence says both sides. `-0.1`,
    sides summing to exactly zero, is taken."""
    pair = {"lower": 0.1, "upper": 0.3}
    leaf = {
        "metric": "tube",
        "variable": "x",
        "tube_width_mode": "band",
        "tube_abs": pair,
    }
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(leaf))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        upper = _number(user, "+ Tube abs")
        _keys(user, upper, "0", "-0.05")
        assert session.tree(A)["tube_abs"] == {"lower": 0.1, "upper": -0.05}
        assert "edit-criteria" not in errors
        _keys(user, upper, "-0.5")
        assert errors["edit-criteria"] == (
            "Refused: tube_abs sides must not sum below zero; got lower 0.1, upper −0.5"
        )
        assert session.tree(A) == leaf
        assert upper.value == -0.5
        _keys(user, upper, "-0.1")
        assert session.tree(A)["tube_abs"] == {"lower": 0.1, "upper": -0.1}
        assert "edit-criteria" not in errors


FLOORED = {
    "metric": "tube",
    "variable": "x",
    "tube_width_mode": "band",
    "tube_abs": {"lower": -0.1, "upper": -0.5},
    "tube_min_width": 0.2,
}


async def test_a_negative_tube_width_over_a_positive_min_width_is_edited(tmp_path):
    """A leaf written by hand: sides that sum below zero over a minimum
    width of 0.2, which floors both — a tube 0.2 either side. Every edit of
    it was refused with the width's sentence, a window end typed beside it
    too. It is taken. Its floor cleared, or typed to zero, IS refused, and
    with that sentence: the width then holds nothing; the leaf keeps the
    floor the field had when it was entered."""
    refused = (
        "Refused: tube_abs sides must not sum below zero; got lower −0.1, upper −0.5"
    )
    s = _session_with_entry(tmp_path, {"metrics": FLOORED})
    async with host(s, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        _keys(user, _number(user, "window end"), "0.9")
        windowed = {**FLOORED, "window": {"end": 0.9}}
        assert s.tree(A) == windowed and "edit-criteria" not in errors
        upper = _number(user, "+ Tube abs")
        _keys(user, upper, "-0.6")  # further below zero still, and taken
        wider = {**windowed, "tube_abs": {"lower": -0.1, "upper": -0.6}}
        assert s.tree(A) == wider and "edit-criteria" not in errors
        _keys(user, upper, "-0.5")
        floor = _number(user, "Min width")
        for typed in ("", "0"):
            _keys(user, floor, typed)
            assert errors["edit-criteria"] == refused, typed
            assert s.tree(A) == windowed, typed
        _keys(user, floor, "0.25")
        assert s.tree(A) == {**windowed, "tube_min_width": 0.25}
        assert "edit-criteria" not in errors


async def test_a_min_width_typed_under_a_negative_tube_width_is_staged(tmp_path):
    """A leaf written by hand whose sides sum below zero, no floor: nothing
    is inside it. `0.5` typed into Min width is what repairs it, and was
    refused — "tube_abs sides must not sum below zero" — like every other
    edit of the leaf. The `0` on the way is refused still, a floor of zero
    being none; `0.5` is staged, and the sentence goes."""
    leaf = {
        "metric": "tube",
        "variable": "x",
        "tube_width_mode": "band",
        "tube_abs": {"lower": 0.2, "upper": -0.3},
    }
    s = _session_with_entry(tmp_path, {"metrics": leaf})
    assert s.comparisons[A].variables[0].tube_points_inside == 0
    async with host(s, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        floor = _number(user, "Min width")
        _keys(user, floor, "0", "0.")
        assert errors["edit-criteria"] == (
            "Refused: tube_abs sides must not sum below zero; got lower 0.2, upper −0.3"
        )
        assert s.tree(A) == leaf and s.spec.pending == []
        _keys(user, floor, "0.5")
        assert s.tree(A) == {**leaf, "tube_min_width": 0.5}
        assert len(s.spec.pending) == 1 and "edit-criteria" not in errors
        assert s.comparisons[A].variables[0].tube_points_inside > 0


async def test_arrow_down_twice_from_an_empty_tolerance_stages_nothing(session):
    """A number field steps by 1: from empty, ArrowDown is 0 and then −1,
    which was staged as the tolerance. The 0 on the way is taken; the −1 is
    refused and the leaf is as it was entered — no tolerance of its own."""
    leaf = {"metric": "nrmse", "variable": "x"}
    session.stage([PatchOp(A, "add", "/metrics", dict(leaf))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        tol = _number(user, "Tolerance")
        _keys(user, tol, "0")
        assert session.tree(A)["tolerance"] == 0.0
        _keys(user, tol, "-1")
        assert "tolerance must not be negative; got −1" in errors["edit-criteria"]
        assert session.tree(A) == leaf


async def test_a_negative_side_of_a_pair_typed_puts_the_pair_back(session):
    pair = {"lower": 0.1, "upper": 0.3}
    leaf = {"metric": "points", "variable": "x", "tolerance": pair}
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(leaf))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        lower = _number(user, "− Default tolerance")
        _keys(user, lower, "0", "-0.2")
        assert errors["edit-criteria"] == (
            "Refused: tolerance (lower side) must not be negative; got −0.2"
        )
        assert session.tree(A) == leaf
        assert lower.value == -0.2


async def test_bounds_typed_out_of_order_put_the_leaf_back(session):
    """`25` typed over a lower bound of −1 under an upper bound of 2: `2`
    on the way equals the upper bound, which a range takes; `25` is
    refused, and the bound is −1 again."""
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(WINDOWED))])
    async with host(session, EditCriteria(), ROOT_LEAF) as (user, errors, _):
        lower = _number(user, "Lower bound (optional)")
        _keys(user, lower, "2")
        assert session.tree(A)["min_value"] == 2.0
        _keys(user, lower, "25")
        assert errors["edit-criteria"] == ("Refused: min_value 25 exceeds max_value 2")
        assert session.tree(A) == WINDOWED
        assert lower.value == 25


def _session_with_soft_check(tmp_path, *ops):
    """A fresh Session per simulation (a page cannot outlive its client):
    A has a soft_check `om_cross`, plus whatever ops are staged."""
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    t = [i / 4 for i in range(5)]
    s.project.store.add_soft_check(
        A,
        series(
            "om_cross",
            time=list(t),
            variables=[{"index": 1, "name": "x", "values": list(t)}],
        ),
    )
    s._reload_results()
    if ops:
        s.stage(list(ops))
    return s


WARN_LEAF = {
    "combinator": "warn",
    "children": [{"metric": "nrmse", "variable": "x", "against": "om_cross"}],
}

#: The leaf beneath the `warn` the two soft-check tests open the panel on.
UNDER_WARN = Selection(
    tests=frozenset({A}), variable="x", leaf_path="/metrics/children/1/children/0"
)


async def _against_select(user):
    await user.should_see("against")
    return next(
        c for c in user.find(ui.select).elements if c.props.get("label") == "against"
    )


async def test_against_offers_no_soft_check_outside_warn(tmp_path):
    """D66: a soft_check is a valid target only beneath a `warn`."""
    s = _session_with_soft_check(tmp_path)
    async with host(s, EditCriteria(), LEAF0) as (user, errors, _):
        assert (await _against_select(user)).options == ["primary"]


async def test_against_offers_the_soft_check_under_warn(tmp_path):
    from dstf.authoring.spec_document import PatchOp

    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", WARN_LEAF)
    )
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert (
            against.options == ["primary", "om_cross"] and against.value == "om_cross"
        )


async def test_against_offers_the_soft_check_to_a_primary_leaf_under_warn(tmp_path):
    """Under a `warn`, a leaf scored against primary may be pointed at the
    soft_check: `advisory` comes from the AUTHORED tree. (The test above's
    leaf already names om_cross, which the panel keeps selectable either
    way.)"""
    under = {"combinator": "warn", "children": [{"metric": "nrmse", "variable": "x"}]}
    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", under)
    )
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert against.options == ["primary", "om_cross"]
        assert against.value == "primary"


async def test_edit_criteria_on_a_combinator_says_it_is_no_leaf(tmp_path):
    """Edit tree wrapped the selected leaf: the selection's path names a
    combinator, which the editor reads as no leaf."""
    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", WARN_LEAF)
    )
    sel = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/1")
    async with host(s, EditCriteria(), sel) as (user, errors, _):
        await user.should_see("this criterion is not in the scored tree")


async def test_against_keeps_a_value_the_store_no_longer_knows(tmp_path):
    """The leaf one opens this panel to repair — its baseline was removed
    after the spec was written; the validator refuses to STAGE such a leaf,
    so it can only arrive from disk — must not crash the panel."""
    import json

    cfg, work = _project(tmp_path)
    doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
    entry = next(e for e in doc["tests"] if e["model"] == A)
    entry["metrics"] = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {
                "combinator": "warn",
                "children": [{"metric": "nrmse", "variable": "x", "against": "ghost"}],
            },
        ],
    }
    (cfg.parent / "test_spec.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )
    s = Session()
    s.open(cfg, work_dir=work)
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert "ghost" in against.options and against.value == "ghost"


#  -- Edit tree, as a node's controls and a variable's add form ------------

ROOT = "/metrics"

#: A tree two levels deep, the shapes a cluster's forms each need: a root
#: leaf, a `k-of-n` of two (a combinator unwrap must refuse) and a `warn` of
#: one (the combinator unwrap takes).
NESTED = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        {
            "combinator": "k-of-n",
            "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "range", "variable": "x", "max_value": 9.0},
            ],
        },
        {
            "combinator": "warn",
            "children": [{"metric": "range", "variable": "x", "max_value": 5.0}],
        },
    ],
}


def _node(model, path, **selection):
    return Selection(tests=frozenset({model}), node_path=path, **selection)


async def _settled(predicate, tries: int = 40) -> None:
    """Refreshes are deferred; wait for a staged op to land."""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


#: The k-of-n field's label (the board's words).
K = "k — how many must pass"
#: Every per-kind field of a combinator form.
FIELDS = {K, "Weights, one per child", "Threshold", "Direction"}


def _labelled(user, kind, label):
    return [e for e in user.find(kind=kind).elements if e.props.get("label") == label]


def _labels_shown(user) -> set[str]:
    """The labels of every field on the page — a kind's fields that are not
    shown are not built at all."""
    out: set[str] = set()
    for kind in (ui.select, ui.number, ui.input):
        try:
            elements = user.find(kind=kind).elements
        except AssertionError:
            continue
        out |= {str(e.props.get("label")) for e in elements}
    return out


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


def _none(user, marker: str) -> bool:
    try:
        user.find(marker=marker)
    except AssertionError:
        return True
    return False


def _inside_lead(user, marker: str) -> bool:
    """Whether ``marker`` is drawn in the host's lead place (`Panel.lead`)."""
    element = _one(user, marker)
    lead = _one(user, "host-lead")
    while element.parent_slot is not None:
        element = element.parent_slot.parent
        if element is lead:
            return True
    return False


def _tips(element) -> list[str]:
    return [e.text for e in element.descendants() if isinstance(e, ui.tooltip)]


def _shown_tips(element) -> list[str]:
    """The tooltips a hover over ``element`` would raise: a hidden one says
    nothing."""
    return [
        e.text for e in element.descendants() if isinstance(e, ui.tooltip) and e.visible
    ]


def _by_label(select, label: str):
    """The option VALUE a person picks by reading ``label``."""
    (value,) = [v for v, text in select.options.items() if text == label]
    return value


def test_edit_tree_needs_one_test_and_a_node_or_a_variable(session):
    cap = EditTree()
    assert cap.available(SessionView(session, Selection(tests=frozenset({A})))) == (
        "select a tree node or a variable"
    )
    assert cap.available(SessionView(session, _node(A, ROOT))) is True
    assert (
        cap.available(
            SessionView(session, Selection(tests=frozenset({A}), variable="x"))
        )
        is True
    )


def test_available_answers_the_missing_tree_first(session):
    """It holds with nothing selected in the tree: a page whose selection is
    only a test must read why it cannot edit, not "select a tree node"."""
    only_b = SessionView(session, Selection(tests=frozenset({B})))
    assert EditTree().available(only_b) == "nothing is scored"


async def test_the_plot_plus_reads_the_scope_the_selection_carries(two_var_session):
    """The `+` reads `Selection.texts` — the page's one read of the test's
    label scope, handed down with the selection — and derives nothing: a
    caption that built the scope for itself did it twice per card, which
    was the Test page's whole rebuild cost (40 cards: 82 scope builds,
    0.37 s). A selection carrying no scope says the NAME. A selection of
    two tests is refused at VARIABLE by `available`."""
    from test_session import with_expressions

    with_expressions(two_var_session, A, {"x": "a.Q"})
    with_expressions(two_var_session, B, {"x": "b.Q"})
    scope = two_var_session.variable_texts(A)
    one = Selection(tests=frozenset({A}), variable="x", texts=scope)
    async with host(two_var_session, EditTree(), one) as (user, _e, _f):
        plus = _one(user, "add-for:x")
        assert _tips(plus) == ["Add a criterion for a.Q"]
        assert plus.props["aria-label"] == "Add a criterion for a.Q"
    bare = Selection(tests=frozenset({A}), variable="x")
    async with host(two_var_session, EditTree(), bare) as (user, _e, _f):
        assert _tips(_one(user, "add-for:x")) == ["Add a criterion for x"]
    two = SessionView(two_var_session, Selection(tests=frozenset({A, B}), variable="x"))
    assert EditTree().available(two) == "select exactly one test"


def test_the_caption_is_the_label(session):
    """NODE and VARIABLE are inline: nothing reads a caption there, and the
    capability's is its label."""
    for sel in (
        _node(A, ROOT, variable="x"),
        Selection(tests=frozenset({A}), variable="x"),
    ):
        assert EditTree().caption(SessionView(session, sel)) == "Edit tree"


def test_add_beside_a_root_leaf_wraps_the_root_in_and(session):
    # A's implicit tree is `and[nrmse x]`, never a root leaf: a root leaf has
    # to be staged.
    session.stage([PatchOp(A, "add", "/metrics", {"metric": "nrmse", "variable": "x"})])
    view = SessionView(session, _node(A, ROOT))
    assert "combinator" not in session.tree(A)
    outcome = EditTree().act(
        view, action="add", model_id=A, path=ROOT, variable="x", metric="range"
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", ROOT)
    assert op.value["combinator"] == "and"
    assert [c.get("metric", "nrmse") for c in op.value["children"]] == [
        "nrmse",
        "range",
    ]
    session.apply(outcome)
    assert len(session.tree(A)["children"]) == 2


def test_add_beside_a_leaf_lands_right_after_it(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    view = SessionView(session, _node(D, "/metrics/children/1"))
    outcome = EditTree().act(
        view,
        action="add",
        model_id=D,
        path="/metrics/children/1",
        variable="x",
        metric="nrmse",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/2")  # right after it


def test_add_beside_a_nested_leaf_lands_right_after_it_in_its_own_parent(session):
    """Two levels deep: the parent is everything before the LAST
    `/children/` — read from the first, the leaf would join the root."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    path = "/metrics/children/1/children/0"
    outcome = EditTree().act(
        SessionView(session, _node(D, path)),
        action="add",
        model_id=D,
        path=path,
        variable="x",
        metric="range",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/1/children/1")


def test_add_under_a_combinator_is_unchanged(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    outcome = EditTree().act(
        SessionView(session, _node(D, ROOT)),
        action="add",
        model_id=D,
        path=ROOT,
        variable="x",
        metric="nrmse",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", "/metrics/children/-")


def _view(session) -> SessionView:
    return SessionView(session, Selection(tests=frozenset({A}), variable="x"))


def _added(session, variable: str, metric: str) -> dict:
    """The leaf `add` stages right after D's child 1."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    outcome = EditTree().act(
        SessionView(session, _node(D, "/metrics/children/1")),
        action="add",
        model_id=D,
        path="/metrics/children/1",
        variable=variable,
        metric=metric,
    )
    (op,) = outcome.ops
    return op.value


def test_an_added_range_is_seeded_from_the_data(session):
    """A bare range does not resolve ("requires at least one of min_value /
    max_value"): it arrives bracketing the variable's data."""
    leaf = _added(session, "x", "range")
    view = session.view(D, "x")
    data = view.ref_values if view.ref_values is not None else view.act_values
    assert data is not None and len(data)
    assert set(leaf) == {"metric", "variable", "min_value", "max_value"}
    assert leaf["min_value"] < min(data) and leaf["max_value"] > max(data)


def test_an_added_nrmse_is_bare(session):
    assert _added(session, "x", "nrmse") == {"metric": "nrmse", "variable": "x"}


def test_an_added_criterion_on_a_variable_without_data_is_bare(session):
    """An unknown variable has an empty view: nothing to seed from."""
    assert _added(session, "nope", "range") == {"metric": "range", "variable": "nope"}


def test_seed_data_reads_an_unknown_test_as_no_data_and_nothing_else(session):
    """Only ``KeyError`` (an unknown test) is "nothing to seed from"; any
    other failure of the view is a bug, and propagates."""

    class _View:
        def __init__(self, exc):
            self.exc = exc

        def view(self, model_id, variable):
            raise self.exc

    empty = seed_data(_View(KeyError("M")), "M", "x")  # type: ignore[arg-type]
    assert (empty.ref, empty.act) == (None, None)
    with pytest.raises(ValueError, match="broken view"):
        seed_data(_View(ValueError("broken view")), "M", "x")  # type: ignore[arg-type]


def _k_of_n_refusal(session, action: str, k) -> str:
    """What the tool shows when Edit (the k-of-n of two in NESTED) or Wrap
    (its root leaf) sets ``k`` and the staged tree is refused."""
    from dstf.authoring.errors import user_text
    from dstf.discovery.patch_apply import PatchError

    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    path = "/metrics/children/1" if action == "edit" else "/metrics/children/0"
    patch = EditTree().act(
        SessionView(session, _node(D, path)),
        action=action,
        model_id=D,
        path=path,
        kind="k-of-n",
        k=k,
        weights="",
        threshold=None,
        direction="less",
    )
    with pytest.raises(PatchError) as box:
        session.stage(patch.ops)
    return user_text(box.value)


@pytest.mark.parametrize("action", ["edit", "wrap"])
@pytest.mark.parametrize(
    "k, text",
    [
        (0, "k must be a whole number of at least 1; got 0."),
        (-2, "k must be a whole number of at least 1; got -2."),
        (None, "k is empty; it must be a whole number of at least 1."),
        ("", "k is empty; it must be a whole number of at least 1."),
    ],
    ids=["zero", "negative", "cleared", "blank"],
)
def test_a_k_below_one_reads_as_a_sentence_naming_k(session, action, k, text):
    """Shown without its path, "must be a positive integer, got 0" named
    nothing; and a cleared field is not a 0 the user typed."""
    assert _k_of_n_refusal(session, action, k) == text


@pytest.mark.parametrize("action", ["edit", "wrap"])
@pytest.mark.parametrize("k", [2.7, 1.5, 0.4])
def test_a_k_that_is_no_whole_number_is_refused_not_rounded(session, action, k):
    """`2.7` typed as k was taken as 3, silently: the form read 2.7 and the
    tree held another number (1.5 became 2, 0.4 became 0 and was refused
    as a 0 nobody typed). It is refused as what was typed."""
    text = f"k must be a whole number of at least 1; got {k}."
    assert _k_of_n_refusal(session, action, k) == text


def test_a_whole_k_typed_as_a_float_is_that_integer(session):
    """A number field hands `2.0` for a typed 2: the tree holds the int."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    path = "/metrics/children/1"
    patch = EditTree().act(
        SessionView(session, _node(D, path)),
        action="edit",
        model_id=D,
        path=path,
        kind="k-of-n",
        k=1.0,
        weights="",
        threshold=None,
        direction="less",
    )
    session.stage(patch.ops)
    k = session.tree(D)["children"][1]["k"]
    assert k == 1 and isinstance(k, int)


@pytest.mark.parametrize(
    "action, k, text",
    [
        ("edit", 5, "k is 5, but this k-of-n has only 2 criteria."),
        ("wrap", 2, "k is 2, but this k-of-n has only 1 criterion."),
    ],
    ids=["plural", "singular"],
)
def test_a_k_above_the_children_reads_as_what_the_node_has(session, action, k, text):
    """On Edit or Wrap nothing was removed: "only 1 criterion is left" read
    as if something had been."""
    assert _k_of_n_refusal(session, action, k) == text


async def test_the_node_panel_is_the_cluster_and_its_forms_stage(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _figure):
        for marker in (f"add:{leaf}", f"wrap:{leaf}", f"remove:{leaf}"):
            await user.should_see(marker=marker)
        await user.should_not_see(marker="host-click")  # a leaf has no fields
        user.find(marker=f"wrap:{leaf}").click()  # the row is built on the click
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "combinator" in session.tree(D)["children"][1])
        assert errors == {}


async def test_a_cluster_builds_a_form_only_when_its_icon_is_clicked(session):
    """Cost (the executing review): a cluster whose three forms are built
    with it is ~28 elements, and a tree row holds one per node — 40 leaves
    made a 2387-element page with the tree open. No form exists until its
    icon is clicked; the click opens it as a row (moved from the menu,
    whose content was built when the menu opened)."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        await user.should_see(marker=f"add:{leaf}")
        await user.should_not_see(kind=ui.select)  # no form is built yet
        await user.should_not_see(marker=f"apply-add:{leaf}")
        user.find(marker=f"add:{leaf}").click()
        await user.should_see(marker=f"apply-add:{leaf}")
        await user.should_see(kind=ui.select)  # the add form's two selects
        assert len(user.find(marker=f"apply-add:{leaf}").elements) == 1


async def test_the_root_has_no_remove_and_a_combinator_has_its_kind(session):
    """Q3 A: an `and` has no fields, so its kind dropdown is its one control
    of that — no Edit icon; a k-of-n keeps the Edit icon for its k."""
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _errors, _figure):
        await user.should_see(marker=f"kind:{ROOT}")
        assert _inside_lead(user, f"kind:{ROOT}")
        await user.should_not_see(marker="host-click")  # an `and` has no fields
        await user.should_not_see(marker=f"remove:{ROOT}")
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        await user.should_see(marker="host-click")  # its row opens its fields
        assert _one(user, f"kind:{k_of_n}").value == "k-of-n"


async def test_remove_stages_at_once(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    n = len(session.tree(D)["children"])
    async with host(session, EditTree(), _node(D, "/metrics/children/1")) as (
        user,
        _e,
        _f,
    ):
        user.find(marker="remove:/metrics/children/1").click()
        await _settled(lambda: len(session.tree(D)["children"]) == n - 1)


async def test_unwrap_lives_in_the_kind_dropdown(session):
    """Q3 A: no unwrap icon and no Unwrap in the Edit row; it is the kind
    dropdown's last entry, and on the k-of-n of two its children take its
    place in its parent."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        await user.should_not_see(marker=f"unwrap:{k_of_n}")
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        assert _none(user, f"unwrap-in-edit:{k_of_n}")
        kind = _one(user, f"kind:{k_of_n}")
        assert kind.options == {
            "and": "and",
            "or": "or",
            "k-of-n": "k-of-n",
            "warn": "warn",
            "weighted": "weighted",
            "unwrap…": "unwrap — its children take its place",
        }
        kind.set_value("unwrap…")
        await _settled(lambda: len(session.tree(D)["children"]) == 4)
        assert errors == {}


async def test_unwrap_from_the_dropdown_stages_at_once(session):
    """A warn has no Edit icon (no fields); its dropdown's unwrap stages the
    unwrap at once, the child kept."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    warn = "/metrics/children/2"
    async with host(session, EditTree(), _node(D, warn)) as (user, errors, _figure):
        await user.should_not_see(marker="host-click")  # a warn has no fields
        _one(user, f"kind:{warn}").set_value("unwrap…")
        await _settled(lambda: "combinator" not in session.tree(D)["children"][2])
        assert session.tree(D)["children"][2]["max_value"] == 5.0  # the child kept
        assert errors == {}


async def test_the_edit_form_is_prefilled_and_stages_an_edit(session):
    """`tune` opens the form on THAT node's fields, with no kind select (the
    dropdown is where the kind changes); applying it stages an edit — a
    wrap would have nested the node under a new one."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        assert _labelled(user, ui.select, "Combinator") == []
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 2
        await user.should_see("Changes this node's fields; its children stay.")
        k.set_value(1)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(lambda: session.tree(D)["children"][1]["k"] == 1)
        node = session.tree(D)["children"][1]
        assert node["combinator"] == "k-of-n" and len(node["children"]) == 2
        assert errors == {}
    # The kind changes in the dropdown: to `or`, k goes with it.
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        staged = len(session.spec.pending)
        _one(user, f"kind:{k_of_n}").set_value("or")
        await _settled(lambda: session.tree(D)["children"][1]["combinator"] == "or")
        node = session.tree(D)["children"][1]
        assert "k" not in node and len(node["children"]) == 2  # edited, not wrapped
        assert [(op.op, op.path) for op in session.spec.pending[staged:]] == [
            ("replace", f"{k_of_n}/combinator"),
            ("remove", f"{k_of_n}/k"),
        ]
        assert errors == {}


async def test_the_edit_form_refuses_a_fraction_typed_as_k(session):
    """`1.5` typed over k=2 and applied: the sentence shows, the row stays
    open with what was typed and nothing is staged. It was rounded — the
    tree held a 2 beside a field reading 1.5."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        (k,) = _labelled(user, ui.number, K)
        pending = list(session.spec.pending)
        k.set_value(1.5)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(lambda: "edit-tree" in errors)
        assert errors["edit-tree"].endswith(
            "k must be a whole number of at least 1; got 1.5."
        )
        assert session.spec.pending == pending
        assert session.tree(D)["children"][1]["k"] == 2
        (still,) = _labelled(user, ui.number, K)
        assert still is k and k.value == 1.5  # the row and what was typed


async def test_the_cluster_add_form_starts_from_the_leafs_own_variable(
    two_var_session,
):
    """Add beside a criterion on `y` offers `y`, and the leaf it stages
    scores `y` — the variable select's first option is `x`."""
    session = two_var_session
    leaf = "/metrics/children/1"
    assert session.tree(A)["children"][1]["variable"] == "y"
    async with host(session, EditTree(), _node(A, leaf)) as (user, errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        assert var.value == "y"
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(A)["children"]) == 3)
        assert session.tree(A)["children"][-1]["variable"] == "y"
        assert errors == {}


async def test_the_add_form_offers_a_variable_whose_criterion_was_removed(
    two_var_session,
):
    """A one-way door (the Task 5-6 review): the add forms read what was
    SCORED, so removing y's last criterion left no way to add one back
    until Discard. A test's variables are the result's, staged edit or not
    — `Session.tracked`'s union, for the same reason."""
    session = two_var_session
    session.stage([PatchOp(A, "remove", "/metrics/children/1")])
    assert [c["variable"] for c in session.tree(A)["children"]] == ["x"]
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        # Dict options: the VALUE a leaf is written against is the name, the
        # text is what a person reads — here the same, neither has an
        # expression.
        assert var.options == {"x": "x", "y": "y"}


async def test_the_add_form_offers_a_variable_with_an_awkward_expression(tmp_path):
    """TRANSFORM has ~70 tests whose UnitTests variables carry a `cat(...)`
    expression. The add forms read `Session.tracked`'s list, so they offer
    the name the result file gives the variable — listing it under two
    spellings offered the same trajectory twice, the second time under a
    name no leaf can be written against. An AUTHORED tree, because such a
    test had no implicit one to edit: its spec names the variable as the
    file spells it."""
    import json

    from test_session import (
        AWKWARD_EXPRESSION,
        UT1,
        _awkward_expression_project,
        with_expressions,
    )

    cfg, work = _awkward_expression_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = {
                "combinator": "and",
                "children": [{"metric": "nrmse", "variable": "y"}],
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)
    with_expressions(session, A, {UT1: AWKWARD_EXPRESSION})
    assert session.tracked(A) == ["y", UT1]
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        # The VARIABLE panel is the plot's `+`; its form a row.
        user.find(marker="add-for:y").click()
        await user.should_see(marker="apply-add:y:variable")
        (var,) = user.find(marker="apply-add:y:variable").elements
        # The option's VALUE is the result-file name — what the leaf is
        # written against — and its TEXT is the expression the model wrote,
        # which is the only way to tell twelve `unitTests.x[i]` apart.
        assert var.options == {"y": "y", UT1: AWKWARD_EXPRESSION}


async def test_the_cluster_add_form_on_a_nested_leaf_lands_under_its_parent(session):
    """The form's `under` is the node the cluster was drawn for, not the
    root: the new leaf joins the k-of-n."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    leaf = "/metrics/children/1/children/0"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(D)["children"][1]["children"]) == 3)
        assert len(session.tree(D)["children"]) == 3  # the root is untouched
        assert errors == {}


async def test_a_form_opened_after_the_session_moved_shows_the_node_as_it_is(session):
    """The cluster stands while the Session moves under it (the Task 4
    review's probe): a form built when the cluster was drawn showed the k
    the node had then, and an apply from it staged that k back over the one
    already staged. The row is built on the icon's click, from the tree as
    it is at that click."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    assert session.tree(D)["children"][1]["k"] == 2
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _figure):
        session.stage([PatchOp(D, "replace", f"{k_of_n}/k", 1)])
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 1  # the node as it is now
        staged = len(session.spec.pending)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        # An Apply stages only what moved (the Task 6 review), and the form
        # shows the node as it is: nothing is staged — a form built at the
        # first open would have staged its k of 2 back.
        await _settled(lambda: _none(user, "host-row:title"))
        await asyncio.sleep(0.1)
        assert len(session.spec.pending) == staged
        assert session.tree(D)["children"][1]["k"] == 1  # not the k of the first open
        assert errors == {}


async def test_a_form_opened_on_a_node_that_is_gone_says_so_and_offers_no_form(session):
    """The node was removed under a cluster that is still drawn: the row
    reads the tree at THIS click, so it carries the sentence `available`
    would give rather than a form staging against a path that names
    nothing."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    # The LAST child: removing any other only shifts the rest up, and the
    # path would still name a node — a different one.
    leaf = f"/metrics/children/{len(TREE['children']) - 1}"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        session.stage([PatchOp(D, "remove", leaf)])
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(f"{leaf} names no node of {D}")
        await user.should_not_see(marker=f"apply-wrap:{leaf}")


async def test_a_forms_controls_fill_its_row(session):
    """Every dense control fills the row it is drawn in. (A menu at a row's
    right end came out 88 px wide with its labels cropped, and its column
    asked for a width; a row under the node's row is the page's width.)"""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        user.find(marker=f"add:{leaf}").click()
        (var,) = user.find(marker=f"apply-add:{leaf}:variable").elements
        # U2's picker: the column fills the row, its filter box the column
        assert "w-full" in var.classes
        assert "dense" in var.box.needle.props and "col" in var.box.needle.classes


async def test_a_cluster_tooltip_sits_above_its_button(session):
    """Chromium (the review): the default tooltip opens below the icon, over
    what the same click opened below the row (a menu then, a form row
    now)."""
    async with host(session, EditTree(), _node(A, ROOT)) as (user, _errors, _figure):
        (button,) = user.find(marker=f"add:{ROOT}").elements
        (tip,) = [e for e in button.descendants() if isinstance(e, ui.tooltip)]
        assert tip.props.get("anchor") == "top middle"
        assert tip.props.get("self") == "bottom middle"


async def test_a_form_is_rebuilt_on_every_open(session):
    """A form built once carries the values the node had then: a combinator
    edited and then Discarded reopened on the k that is no longer there.
    Each open is the icon's click; each close, Cancel."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _figure):
        user.find(marker="host-click").click()
        (first,) = user.find(marker=f"apply-edit:{k_of_n}").elements
        user.find(marker=f"cancel:{k_of_n}").click()
        user.find(marker="host-click").click()
        (second,) = user.find(marker=f"apply-edit:{k_of_n}").elements
        assert second is not first  # one apply button, and a new one
        session.stage([PatchOp(D, "replace", f"{k_of_n}/k", 1)])
        user.find(marker=f"cancel:{k_of_n}").click()
        user.find(marker="host-click").click()
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 1  # the node as it is now, not as it was


def test_a_node_path_that_names_no_node_says_so(session):
    """A stale selection (the node was removed under it) reaches `available`
    and `act`: both name it, rather than an IndexError out of the page
    build."""
    cap = EditTree()
    gone = "/metrics/children/7"
    view = SessionView(session, _node(A, gone))
    assert cap.available(view) == f"{gone} names no node of {A}"
    with pytest.raises(ValueError, match=f"^{gone} names no node of {A}$"):
        cap.act(view, action="wrap", model_id=A, path=gone, kind="and")
    # A pointer that resolves to something that is not a node at all.
    word = "/metrics/combinator"
    assert cap.available(SessionView(session, _node(A, word))) == (
        f"{word} names no node of {A}"
    )


async def test_a_node_and_a_variable_together_draw_the_nodes_controls(session):
    """A criteria row's selection carries both; the cluster wins, so the
    row's `+` is the node's, not a second "Add a criterion for x"."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf, variable="x")) as (
        user,
        _errors,
        _figure,
    ):
        await user.should_see(marker=f"add:{leaf}")
        await user.should_not_see(marker="apply-add:x")
        await user.should_not_see(marker="add-for:x")


async def test_applying_a_form_closes_its_row(session):
    """The act is staged and the row is gone — a menu then, a row now: left
    open it would offer a form for a node that has just changed."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(TREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _errors, _figure):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker=f"apply-wrap:{leaf}")
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "combinator" in session.tree(D)["children"][1])
        assert _none(user, f"apply-wrap:{leaf}")
        assert _none(user, "host-row:title")


async def test_the_variable_panel_adds_a_criterion_for_it(two_var_session):
    session = two_var_session
    sel = Selection(tests=frozenset({A}), variable="y")
    n = len(session.tree(A)["children"])
    reveals: list[str] = []
    async with host(session, EditTree(), sel, reveals) as (user, errors, _figure):
        # The VARIABLE panel is the plot's `+`: its form is a row, titled
        # for the variable, and a successful Add closes it and reveals the
        # new criterion — the two causes of "the editor does not open".
        await user.should_not_see(marker="apply-add:y")
        user.find(marker="add-for:y").click()
        await user.should_see(marker="apply-add:y")
        assert _one(user, "host-row:title").text == "Add a criterion for y"
        await user.should_see("Values come from the data where the metric needs them.")
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(session.tree(A)["children"]) == n + 1)
        assert errors == {}
        # The variable the `+` belongs to, not the first tracked name, at
        # the end of the root.
        assert session.tree(A)["children"][-1]["variable"] == "y"
        assert reveals == [f"/metrics/children/{n}"]
        await _settled(lambda: _none(user, "apply-add:y"))


async def test_the_variable_plus_under_a_root_leaf_makes_the_root_an_and(session):
    session.stage([PatchOp(D, "add", ROOT, {"metric": "nrmse", "variable": "x"})])
    reveals: list[str] = []
    sel = Selection(tests=frozenset({D}), variable="x")
    async with host(session, EditTree(), sel, reveals) as (user, errors, _figure):
        user.find(marker="add-for:x").click()
        await user.should_see(marker="apply-add:x")
        user.find(marker="apply-add:x").click()
        await _settled(lambda: "combinator" in session.tree(D))
        assert session.tree(D)["combinator"] == "and"
        assert len(session.tree(D)["children"]) == 2
        assert reveals == ["/metrics/children/1"] and errors == {}


async def test_the_variable_plus_is_grey_under_a_weighted_root(session):
    """The validator refuses a child added alone to a weighted node, so the
    `+` is not offered: grey, and its tooltip says why."""
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 1.0],
        "threshold": 0.5,
        "children": [
            {"metric": "range", "variable": "x", "max_value": 9.0},
            {"metric": "range", "variable": "x", "max_value": 5.0},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, weighted)])
    sel = Selection(tests=frozenset({D}), variable="x")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        plus = _one(user, "add-for:x")
        assert not plus.enabled
        reason = (
            "a weighted node has one weight per child; a child cannot be added alone"
        )
        assert _tips(plus) == [reason]
        assert plus.props["aria-label"] == reason


def test_edit_tree_needs_exactly_one_test(session):
    view = SessionView(session, Selection(tests=frozenset({A, B})))
    assert EditTree().available(view) == "select exactly one test"


def test_edit_tree_reaches_nested_nodes(session):
    """The pointer walker consumed `children` and then indexed the LIST with
    the word — every non-root wrap/edit raised TypeError. `act` is pure, so
    this pins it without a page."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [PatchOp(A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "x"})]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    cap = EditTree()
    # wrap a child, not the root: a `replace` at that path
    outcome = cap.act(
        view, action="wrap", model_id=A, path="/metrics/children/1", kind="warn"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.path == "/metrics/children/1"
    assert (
        op.value["combinator"] == "warn" and op.value["children"][0]["variable"] == "x"
    )
    session.apply(outcome)
    tree = session.tree(A)
    assert tree["children"][1]["combinator"] == "warn"
    # edit the nested combinator: k-of-n on the warn's child list is refused
    # by the parser (warn needs one child), so change the ROOT via its path
    # and a nested leaf's removal through a nested pointer.
    outcome = cap.act(
        view, action="remove", model_id=A, path="/metrics/children/1/children/0"
    )
    assert outcome.ops[0].path == "/metrics/children/1/children/0"


def test_edit_tree_unwraps_a_single_child_combinator(session):
    """The browser editor's unwrap: warn(leaf) becomes leaf, params kept;
    remove would have lost the leaf."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {
                    "combinator": "warn",
                    "children": [
                        {"metric": "nrmse", "variable": "x", "tolerance": 0.3}
                    ],
                },
            )
        ]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    outcome = EditTree().act(
        view, action="unwrap", model_id=A, path="/metrics/children/1"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.value == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    session.apply(outcome)
    assert session.tree(A)["children"][1] == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    # The root's two children have no parent to move into (several
    # children take their node's place since the form-row plan's Task 6).
    with pytest.raises(ValueError, match="the root has no parent"):
        EditTree().act(view, action="unwrap", model_id=A, path="/metrics")


# -- the forms as rows under the node (board 1A-1D) -------------------------


async def test_wrap_opens_a_row_titled_for_the_node_with_only_its_kinds_fields(
    session,
):
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker="host-row:title")
        assert _one(user, "host-row:title").text == "Wrap nrmse · x in a combinator"
        (kind,) = _labelled(user, ui.select, "Combinator")
        assert kind.value == "and"
        assert not _labels_shown(user) & FIELDS
        await user.should_see(
            "The criterion becomes the only child of a new and; add siblings to "
            "it with its +."
        )
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        assert _labels_shown(user) & FIELDS == {K}
        await user.should_see(
            "The criterion becomes the only child of a new k-of-n; add siblings "
            "to it with its +."
        )
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        assert _labels_shown(user) & FIELDS == FIELDS - {K}
        await user.should_see(
            "The criterion becomes the only child of a new weighted node, with "
            "the one weight above."
        )
        kind.set_value("or")
        await user.should_see(
            "The criterion becomes the only child of a new or; add siblings to "
            "it with its +."
        )
        kind.set_value("warn")
        await user.should_see(
            "A failure of this criterion becomes a warning; the test still passes."
        )
        assert not _labels_shown(user) & FIELDS


async def test_a_kinds_typed_value_is_kept_for_a_return_to_it(session):
    """Changing the select rebuilds the fields column; a value typed for a
    kind is there again when the user comes back to that kind."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        (k,) = _labelled(user, ui.number, K)
        k.set_value(3)
        kind.set_value("and")
        await _settled(lambda: K not in _labels_shown(user))
        kind.set_value("k-of-n")
        await _settled(lambda: K in _labels_shown(user))
        (k,) = _labelled(user, ui.number, K)
        assert k.value == 3


async def test_a_wrapped_combinator_reads_as_a_node(session):
    """For a combinator being wrapped, "criterion" reads "node"."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        user.find(marker=f"wrap:{k_of_n}").click()
        await user.should_see(
            "The node becomes the only child of a new and; add siblings to it "
            "with its +."
        )
        assert _one(user, "host-row:title").text == (
            "Wrap k-of-n · 2 criteria · k=2 in a combinator"
        )


async def test_edit_carries_the_nodes_direction(session):
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 1.0],
        "threshold": 0.5,
        "direction": "greater",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    node = "/metrics/children/0"
    async with host(session, EditTree(), _node(D, node)) as (user, errors, _f):
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{node}")
        assert _one(user, "host-row:title").text == "Edit this combinator"
        (direction,) = _labelled(user, ui.select, "Direction")
        # Pinned, and chosen BY LABEL below: a test that set the value
        # "less" passed against a select that wrote its labels as values.
        assert direction.options == {
            "less": "lower is better",
            "greater": "higher is better",
        }
        assert direction.options[direction.value] == "higher is better"
        await user.should_see("Changes this node's fields; its children stay.")
        direction.set_value(_by_label(direction, "lower is better"))
        staged = len(session.spec.pending)
        user.find(marker=f"apply-edit:{node}").click()
        await _settled(lambda: len(session.spec.pending) > staged)
        ops = session.spec.pending[staged:]
        assert any(
            op.op == "replace" and op.path == f"{node}/direction" and op.value == "less"
            for op in ops
        ), ops
        assert errors == {}


async def test_the_edit_rows_buttons_read_cancel_apply(session):
    """Unwrap has moved into the kind dropdown."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        row = _one(user, f"apply-edit:{k_of_n}").parent_slot.parent
        assert [b.text for b in row.default_slot.children] == ["Cancel", "Apply"]


def test_unwrap_of_several_children_moves_them_into_the_parent(session):
    a = {"metric": "nrmse", "variable": "x"}
    b = {"metric": "range", "variable": "x", "max_value": 9.0}
    c = {"metric": "range", "variable": "x", "max_value": 5.0}
    tree = {
        "combinator": "and",
        "children": [{"combinator": "k-of-n", "k": 1, "children": [a, b]}, c],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    node = "/metrics/children/0"
    outcome = EditTree().act(
        SessionView(session, _node(D, node)), action="unwrap", model_id=D, path=node
    )
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("remove", node, None),
        ("add", "/metrics/children/0", a),
        ("add", "/metrics/children/1", b),
    ]
    session.apply(outcome)
    assert session.tree(D) == {"combinator": "and", "children": [a, b, c]}


async def test_unwrap_is_not_offered_on_a_root_with_several_children(session):
    """`act` refuses it by rule ("the root has no parent to take its
    children"), and an option cannot carry a why-tooltip: the dropdown of a
    root of several has no unwrap entry. A root of one keeps it."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _errors, _f):
        assert list(_one(user, f"kind:{ROOT}").options) == [
            "and",
            "or",
            "k-of-n",
            "warn",
            "weighted",
        ]
    one = {"combinator": "and", "children": [{"metric": "nrmse", "variable": "x"}]}
    session.stage([PatchOp(D, "add", ROOT, one)])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, errors, _f):
        _one(user, f"kind:{ROOT}").set_value("unwrap…")
        await _settled(lambda: "combinator" not in session.tree(D))
        assert errors == {}


async def test_plus_is_grey_under_warn_and_weighted(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    for path in ("/metrics/children/2", "/metrics/children/2/children/0"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            plus = _one(user, f"add:{path}")
            assert not plus.enabled
            assert _tips(plus) == ["a warn takes exactly one child"]
    # Beside a leaf under an `and`, and under a k-of-n: `+` works.
    for path in ("/metrics/children/0", "/metrics/children/1"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            assert _one(user, f"add:{path}").enabled
    weighted = {
        "combinator": "weighted",
        "weights": [1.0],
        "threshold": 0.5,
        "children": [{"metric": "nrmse", "variable": "x"}],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    for path in ("/metrics/children/0", "/metrics/children/0/children/0"):
        async with host(session, EditTree(), _node(D, path)) as (user, _errors, _f):
            plus = _one(user, f"add:{path}")
            assert not plus.enabled
            assert _tips(plus) == [
                "a weighted node has one weight per child; a child cannot be "
                "added alone"
            ]


async def test_add_reveals_the_new_criterion(session):
    three = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
            {"metric": "range", "variable": "x", "max_value": 5.0},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, three)])
    reveals: list[str] = []
    async with host(session, EditTree(), _node(D, ROOT), reveals) as (user, e, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert _one(user, "host-row:title").text == (
            "Add a criterion at the end of and · 3 criteria"
        )
        await user.should_see("Values come from the data where the metric needs them.")
        user.find(marker=f"apply-add:{ROOT}").click()
        await _settled(lambda: len(session.tree(D)["children"]) == 4)
        assert reveals == ["/metrics/children/3"] and e == {}
    # Beside a ROOT leaf: the root becomes an `and` of both.
    session.stage([PatchOp(D, "add", ROOT, {"metric": "nrmse", "variable": "x"})])
    reveals.clear()
    async with host(session, EditTree(), _node(D, ROOT), reveals) as (user, e, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert _one(user, "host-row:title").text == ("Add a criterion after nrmse · x")
        user.find(marker=f"apply-add:{ROOT}").click()
        await _settled(lambda: "combinator" in session.tree(D))
        assert reveals == ["/metrics/children/1"] and e == {}


async def test_cancel_stages_nothing(session):
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, _errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        await user.should_see(marker=f"cancel:{leaf}")
        user.find(marker=f"cancel:{leaf}").click()
        await _settled(lambda: _none(user, f"cancel:{leaf}"))
        assert _none(user, "host-row:title")  # `close` emptied the row
        assert session.spec.pending == []


# -- the Task 6 review's corrections ----------------------------------------


async def test_an_untouched_weighted_wrap_waits_for_a_typed_threshold(session):
    """A weighted wrap's Threshold starts EMPTY and Wrap is grey, saying
    why, until a number is typed; the one weight starts as 1, one per child
    (the wrapped node). Before, an untouched weighted Wrap was refused every
    time (no weights), and threshold 0 with `less` always fails."""
    leaf = "/metrics/children/0"
    async with host(session, EditTree(), _node(A, leaf)) as (user, errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        (threshold,) = _labelled(user, ui.number, "Threshold")
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        assert threshold.value is None
        assert weights.value == "1"
        wrap = _one(user, f"apply-wrap:{leaf}")
        assert not wrap.enabled
        assert _shown_tips(wrap) == ["enter a threshold"]
        # 0 is a threshold like any other (with `greater`, a real one).
        threshold.set_value(0)
        await _settled(lambda: wrap.enabled)
        threshold.set_value(None)
        await _settled(lambda: not wrap.enabled)
        threshold.set_value(0.25)
        await _settled(lambda: wrap.enabled)
        assert _shown_tips(wrap) == []
        # Another kind needs no threshold; back to weighted, still typed.
        kind.set_value("and")
        await _settled(lambda: "Threshold" not in _labels_shown(user))
        assert wrap.enabled
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        assert wrap.enabled
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: session.tree(A)["children"][0].get("combinator"))
        node = session.tree(A)["children"][0]
        assert node["combinator"] == "weighted"
        assert (node["weights"], node["threshold"]) == ([1.0], 0.25)
        assert errors == {}


async def test_edit_to_weighted_waits_for_a_threshold_too(session):
    """The same rule when Edit switches a node that has no threshold to
    weighted; its weights start as one 1 per child."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _f):
        # The dropdown opens the Edit form preset to weighted.
        staged = len(session.spec.pending)
        _one(user, f"kind:{k_of_n}").set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        assert len(session.spec.pending) == staged  # nothing until Apply
        await user.should_see("Changes this node's kind and fields; its children stay.")
        (threshold,) = _labelled(user, ui.number, "Threshold")
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        assert (threshold.value, weights.value) == (None, "1, 1")
        apply = _one(user, f"apply-edit:{k_of_n}")
        assert not apply.enabled
        assert _shown_tips(apply) == ["enter a threshold"]
        threshold.set_value(0.5)
        await _settled(lambda: apply.enabled)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(
            lambda: session.tree(D)["children"][1]["combinator"] == "weighted"
        )
        node = session.tree(D)["children"][1]
        assert (node["weights"], node["threshold"]) == ([1.0, 1.0], 0.5)
        assert "k" not in node and "direction" not in node  # `less` is the default
        assert errors == {}


def test_a_weighted_act_with_no_threshold_is_refused_by_name(session):
    """The act does not read a missing threshold as 0 — that always fails."""
    with pytest.raises(ValueError, match="enter a threshold"):
        EditTree().act(
            SessionView(session, _node(A, ROOT)),
            action="wrap",
            model_id=A,
            path="/metrics/children/0",
            kind="weighted",
            k=1,
            weights="1",
            threshold=None,
            direction="less",
        )


async def test_an_unchanged_edit_stages_nothing_and_closes_the_row(session):
    """Apply with nothing changed staged four ops (the kind, and every field
    again). Now it stages what moved, and here nothing did."""
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 2.0],
        "threshold": 0.5,
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    session.stage([PatchOp(D, "add", "/metrics/children/-", copy.deepcopy(weighted))])
    for node in ("/metrics/children/1", "/metrics/children/3"):
        async with host(session, EditTree(), _node(D, node)) as (user, errors, _f):
            staged = len(session.spec.pending)
            user.find(marker="host-click").click()
            await user.should_see(marker=f"apply-edit:{node}")
            user.find(marker=f"apply-edit:{node}").click()
            await _settled(lambda: _none(user, "host-row:title"))
            await asyncio.sleep(0.1)
            assert len(session.spec.pending) == staged, session.spec.pending[staged:]
            assert errors == {}


def test_an_edit_stages_only_the_fields_that_moved(session):
    """One field changed is one op; ``direction`` is written only when it
    differs from the node's own, or from `less` when the node has none."""
    weighted = {
        "combinator": "weighted",
        "weights": [1.0, 2.0],
        "threshold": 0.5,
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "x", "max_value": 9.0},
        ],
    }
    session.stage(
        [PatchOp(D, "add", ROOT, {"combinator": "and", "children": [weighted]})]
    )
    node = "/metrics/children/0"
    view = SessionView(session, _node(D, node))
    common = {"action": "edit", "model_id": D, "path": node, "kind": "weighted"}
    common |= {"k": 1, "weights": "1, 2", "threshold": 0.5}
    outcome = EditTree().act(view, **common, direction="greater")
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("add", f"{node}/direction", "greater")
    ]
    outcome = EditTree().act(view, **{**common, "threshold": 0.75}, direction="less")
    assert [(op.op, op.path, op.value) for op in outcome.ops] == [
        ("replace", f"{node}/threshold", 0.75)
    ]
    # Nothing moved: no outcome at all, not an empty patch.
    assert EditTree().act(view, **common, direction="less") is None


def test_unwrap_at_a_later_index_lands_its_children_there(session):
    """`and[l0, k-of-n[a, b], l2]` unwrap the k-of-n → `and[l0, a, b, l2]`:
    the adds count from the node's own index, not from 0."""
    l0 = {"metric": "nrmse", "variable": "x"}
    a = {"metric": "range", "variable": "x", "max_value": 9.0}
    b = {"metric": "range", "variable": "x", "max_value": 8.0}
    l2 = {"metric": "range", "variable": "x", "max_value": 5.0}
    tree = {
        "combinator": "and",
        "children": [l0, {"combinator": "k-of-n", "k": 1, "children": [a, b]}, l2],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    node = "/metrics/children/1"
    outcome = EditTree().act(
        SessionView(session, _node(D, node)), action="unwrap", model_id=D, path=node
    )
    assert [(op.op, op.path) for op in outcome.ops] == [
        ("remove", node),
        ("add", "/metrics/children/1"),
        ("add", "/metrics/children/2"),
    ]
    session.apply(outcome)
    assert session.tree(D) == {"combinator": "and", "children": [l0, a, b, l2]}


async def test_add_beside_a_nested_leaf_reveals_the_place_right_after_it(session):
    """Beside a leaf two levels down: the new criterion lands right after
    it, in its OWN parent, not the root."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    leaf = "/metrics/children/1/children/0"
    reveals: list[str] = []
    async with host(session, EditTree(), _node(D, leaf), reveals) as (user, e, _f):
        user.find(marker=f"add:{leaf}").click()
        await user.should_see(marker=f"apply-add:{leaf}")
        user.find(marker=f"apply-add:{leaf}").click()
        await _settled(lambda: len(session.tree(D)["children"][1]["children"]) == 3)
        assert reveals == ["/metrics/children/1/children/1"] and e == {}


async def test_the_add_rows_fields_read_variable_and_metric(session):
    """The board's table names the Add row's fields `Variable` and
    `Metric`; they read `leaf variable` / `leaf metric`."""
    async with host(session, EditTree(), _node(A, ROOT)) as (user, _errors, _f):
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert {"Variable", "Metric"} <= _labels_shown(user)
        assert not {"leaf variable", "leaf metric"} & _labels_shown(user)


# -- Accept (§4.6 table, §4.7) --------------------------------------------


def test_accept_is_greyed_without_a_result(session):
    """C has no result and is `pending` in the batch manifest, so the shared
    accept function refuses it — and the capability says so."""
    view = SessionView(session, Selection(tests=frozenset({C})))
    assert Accept().available(view) == "re-run before accepting"


async def test_accept_stores_the_rest(session):
    """A and B are stored, C is refused with its row's reason, and the
    reload flips A's and B's verdicts."""
    import json

    from dstf.reporting.status import PASS, classify

    selection = Selection(tests=frozenset({A, B, C}))
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see("Accept 2")  # names what it will store: A and B
        await user.should_see("re-run before accepting")  # C's row, from the manifest
        user.find(kind=ui.button, content="Accept 2 results as baselines").click()
        notice = session.notices[-1]
        assert notice.startswith("Accept: stored 2")
        assert C in notice and "re-run before accepting" in notice
        assert classify(session.comparisons[B]) == PASS  # NO_REF -> PASS
        assert classify(session.comparisons[A]) == PASS  # re-baselined
        rid = session.project.store.index.get_id(B)
        assert rid == "0004"  # A, D and G hold 0001-0003
        raw = (session.project.config.reference_dir / f"ref_{rid}.json").read_bytes()
        assert b"\r" not in raw and json.loads(raw)["model_id"] == B


def test_accept_act_returns_a_store_write_over_the_selection(session):
    """`act` is pure: the StoreWrite names the selection and its `run`
    reports one line — what it stored and what it refused."""
    from dstf.authoring.capabilities import StoreWrite

    view = SessionView(session, Selection(tests=frozenset({A, B, C})))
    out = Accept().act(view)
    assert isinstance(out, StoreWrite) and out.models == {A, B, C}
    line = out.run(session.project.store)
    assert line.startswith("Accept: stored 2") and C in line
    # The line is a report of an effect that happened: B has a baseline now.
    assert session.project.store.index.get_id(B) == "0004"


def test_accept_is_greyed_while_the_queue_holds_a_selected_test(session):
    """The result in memory is about to be replaced by its run's: the
    button says so, for a test waiting and for one running, and for a
    selection that only includes one."""
    view = SessionView(session, Selection(tests=frozenset({A})))
    both = SessionView(session, Selection(tests=frozenset({A, D})))
    item = fake_item(session, A, ItemState.WAITING)
    queued = f"{A} is queued: accept it when its run has ended"
    assert Accept().available(view) == queued
    assert Accept().available(both) == queued
    drop_item(session, item)
    item = fake_item(session, A, ItemState.RUNNING)
    assert Accept().available(view) == (
        f"{A} is running: accept it when its run has ended"
    )
    # A test the queue does not hold is not greyed by one it does.
    assert (
        Accept().available(SessionView(session, Selection(tests=frozenset({D}))))
        is True
    )
    drop_item(session, item)
    assert Accept().available(view) is True and Accept().available(both) is True


async def test_accept_is_greyed_when_the_selection_is_a_pending_test(tmp_path):
    """A's result file parses, but the run never finished, so the shared
    accept function would refuse it and the capability says so instead of
    offering `Accept 0`."""
    import json

    from dstf.authoring.builtin.accept import REFUSED

    cfg, work = _project(tmp_path)
    manifest_path = work / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_0001"]["last_run_status"] = "pending"  # A, in flight
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == REFUSED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(REFUSED)  # the host's reason, in the panel's place
        await user.should_not_see("Accept 0")


async def test_accept_excludes_a_simulate_only_test_from_the_count(session):
    """A simulate-only test is neither stored nor skipped by the shared
    accept function — the forecast says so and the count leaves it out."""
    from dstf.authoring.builtin.accept import SIMULATE_ONLY
    from dstf.reporting.status import NO_REF

    session.project.test(B).simulate_only = True

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {SIMULATE_ONLY}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == SIMULATE_ONLY


def test_accept_names_the_simulate_only_test_it_stored_nothing_for(session):
    """The write's line said what it stored and what it refused, and
    nothing of a simulate-only test in the selection: it says the accept's
    own sentence for it, the one the CLI prints. (Alone, such a test greys
    the button with its row's reason, as above: there is no write.)"""
    session.project.test(B).simulate_only = True
    view = SessionView(session, Selection(tests=frozenset({A, B})))
    line = Accept().act(view).run(session.project.store)
    assert line == (
        f"Accept: stored 1 baseline(s) to {session.project.config.reference_dir}"
        f"; Simulate-only, nothing to store: {B}"
    )
    assert session.project.store.index.get_id(B) is None


async def test_accept_excludes_a_test_that_declares_nothing_to_score(session):
    """No pattern, no UnitTests variable, no tree — what `dstf spec add <model>`
    writes: simulate-only without a recognizer's mark, and the forecast
    says what the shared accept function does with it."""
    from dstf.authoring.builtin.accept import SIMULATE_ONLY

    test = session.project.test(B)
    test.variable_patterns, test.metric_tree_spec = [], None
    assert test.is_simulate_only and not test.simulate_only

    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == SIMULATE_ONLY
    # The add form is grey only on a MARKED test, where a staged criterion
    # is never scored: here the criterion is what makes it a scored test.
    from dstf.authoring.builtin.edit_tree import _simulate_only

    assert _simulate_only(view, B) is False
    test.simulate_only = True
    assert _simulate_only(view, B) is True


async def test_accept_excludes_a_result_with_no_tracked_variables(session):
    """`store_reference` returns False for a variable-less result and
    `accept_results` skips it with this reason; the panel has the result in
    hand, so it never promises the baseline."""
    from dstf.authoring.builtin.accept import NO_VARIABLES
    from dstf.backends.types import TestResult
    from dstf.reporting.status import NO_REF

    session.project.results[B] = TestResult(model_id=B, success=True, variables=[])

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {NO_VARIABLES}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == NO_VARIABLES


async def test_accept_reports_the_row_reason_the_empty_selection_and_a_selection_naming_no_test(
    session,
):
    """With rows to draw, the greyed capability says exactly what the first
    row says; the generic line is left for a selection with no rows at all."""
    from dstf.authoring.builtin.accept import SIM_FAILED
    from dstf.backends.types import TestResult

    session.project.results[A] = TestResult(
        model_id=A, success=False, variables=[], error_message="boom"
    )

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == SIM_FAILED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(SIM_FAILED)  # the host's reason
        await user.should_not_see("Accept 0")
    empty = SessionView(session, Selection(tests=frozenset()))
    assert Accept().available(empty) == "Accept needs a selection"
    assert (
        Accept().available(SessionView(session, Selection(tests=frozenset({"Nope.X"}))))
        == "no successful result in the selection"
    )


def test_accept_is_the_next_action_where_the_result_differs_or_has_no_baseline():
    """The Test page's strip (Q2 A): filled for a failing test and one with
    no baseline; in the ⋮ where there is nothing new to accept."""
    from dstf.authoring.capabilities import Emphasis
    from dstf.authoring.situation import Situation as S

    assert Accept.emphasis == {
        S.FAIL: Emphasis.NEXT,
        S.NO_REF: Emphasis.NEXT,
        S.PASS: Emphasis.MENU,
        S.EDITS: Emphasis.MENU,
        S.EDITS_NEW: Emphasis.MENU,
        S.NEW_VARIABLES: Emphasis.MENU,
        S.SIM_FAIL: Emphasis.MENU,
        S.NO_RESULT: Emphasis.MENU,
        # the queue holds it, or its last run there left no result
        S.QUEUED: Emphasis.MENU,
        S.RUNNING: Emphasis.MENU,
        S.CANCELLED: Emphasis.MENU,
        S.START_FAILED: Emphasis.MENU,
    }


# -- Queue (D150; brief §3.6) ----------------------------------------------


def test_queue_is_the_next_action_where_there_is_no_result_to_read():
    from dstf.authoring.capabilities import Emphasis
    from dstf.authoring.situation import Situation as S

    assert EnqueueTests.emphasis == {
        S.SIM_FAIL: Emphasis.NEXT,
        S.NO_RESULT: Emphasis.NEXT,
        # its last run in the queue was cancelled, or could not start
        S.CANCELLED: Emphasis.NEXT,
        S.START_FAILED: Emphasis.NEXT,
        S.EDITS: Emphasis.MENU,
        # a criterion names a variable the last run did not record
        S.EDITS_NEW: Emphasis.NEXT,
        S.NEW_VARIABLES: Emphasis.NEXT,
        # the strip's own Remove or Cancel is the action while it is held
        S.QUEUED: Emphasis.MENU,
        S.RUNNING: Emphasis.MENU,
    }


def test_the_builtins_register_in_order():
    assert [c.label for c in caps.registered_capabilities()] == [
        "Edit family",
        "Edit criteria",
        "Edit tree",
        "Accept",
        "Queue",
        "Edit on plot",
    ]


def test_queue_act_returns_an_enqueue_of_the_selection_in_project_order(session):
    """`act` is pure: the Enqueue names the selected tests the queue does
    not hold, in project order — the order they run in — and starts
    nothing. The Session owns the queue and the pool it runs on."""
    from dstf.authoring.capabilities import Enqueue

    view = SessionView(session, Selection(tests=frozenset({D, B, A})))
    assert EnqueueTests().act(view) == Enqueue(models=(A, B, D))
    assert session.queue == [] and session._pool is None

    item = fake_item(session, B, ItemState.WAITING)
    assert EnqueueTests().act(view) == Enqueue(models=(A, D))
    drop_item(session, item)
    item = fake_item(session, A, ItemState.RUNNING)
    assert EnqueueTests().act(view) == Enqueue(models=(B, D))
    drop_item(session, item)

    with pytest.raises(ValueError, match="no project open"):
        EnqueueTests().act(SessionView(Session(), Selection(tests=frozenset({A}))))


def test_queue_is_greyed_for_a_selection_the_queue_already_holds(session):
    """A test is never queued twice: one test says which and how, several
    say how many, and a selection with one test left to queue is offered."""
    cap = EnqueueTests()
    one = SessionView(session, Selection(tests=frozenset({A})))
    two = SessionView(session, Selection(tests=frozenset({A, D})))
    three = SessionView(session, Selection(tests=frozenset({A, B, D})))
    assert cap.available(one) is True and cap.available(three) is True

    a = fake_item(session, A, ItemState.RUNNING)
    assert cap.available(one) == f"{A} is already running"
    assert cap.available(two) is True  # D is left
    d = fake_item(session, D, ItemState.WAITING)
    assert cap.available(SessionView(session, Selection(tests=frozenset({D})))) == (
        f"{D} is already queued"
    )
    assert cap.available(two) == "all 2 selected tests are already queued or running"
    assert cap.available(three) is True  # B is left
    drop_item(session, a)
    drop_item(session, d)
    assert cap.available(one) is True and cap.available(two) is True

    assert cap.available(SessionView(session, Selection())) == (
        "Queue needs a selection"
    )
    closed = SessionView(Session(), Selection(tests=frozenset({A})))
    assert cap.available(closed) == "no project open"


async def test_queue_names_the_backend_and_the_tests_it_leaves_out(session):
    selection = Selection(tests=frozenset({A, D}))
    async with host(session, EnqueueTests(), selection) as (user, errors, _):
        await user.should_see("Queue 2")
        # Via the MARKER: a header elsewhere reads `backend: Python`, so a
        # `should_see("Python")` would pass without the panel's label.
        (label,) = user.find(marker="queue-backend").elements
        assert label.text == "runs through Python"
        await user.should_see(A)
        await user.should_see(D)
        await user.should_not_see("already in the queue")
    item = fake_item(session, D, ItemState.WAITING)
    async with host(session, EnqueueTests(), selection) as (user, errors, _):
        await user.should_see("Queue 1 (1 already queued)")
        await user.should_see(f"{D} · already in the queue")
    drop_item(session, item)


def test_the_builtins_declare_their_placements():
    """Brief §3.2: Accept and Queue at suite and test; Edit tree as a tree
    node's controls, a variable's "Add a criterion for x" and a Statistics
    row's `+` (U3); Edit on plot
    and Edit criteria at the open criterion — the two that read as ONE
    editor. Nothing built-in is left at TEST but Accept and Queue."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    S, T, V, C_ = (
        Placement.SUITE,
        Placement.TEST,
        Placement.VARIABLE,
        Placement.CRITERION,
    )
    assert Accept.placements == {S, T} and EnqueueTests.placements == {S, T}
    assert EditTree.placements == {Placement.NODE, V, Placement.STATISTIC}
    assert EditOnPlot.placements == {C_}
    assert EditCriteria.placements == {
        C_,
        Placement.STATISTIC_CRITERION,
        Placement.TREE_CRITERION,
        Placement.FAMILY_CRITERION,
    }
    at_test = {c.name for c in caps.registered_capabilities() if T in c.placements}
    assert at_test == {"accept", "enqueue"}


def test_accept_caption_counts_what_it_will_store_and_needs_a_selection(session):
    """DESIGN.md "Buttons": object, effect, count — and at zero the label
    still says what it would do while `available` carries the sentence."""
    three = SessionView(session, Selection(tests=frozenset({A, B, C})))
    assert Accept().caption(three) == "Accept 2 results as baselines"  # C is refused
    one = SessionView(session, Selection(tests=frozenset({D})))
    assert Accept().caption(one) == "Accept 1 result as baselines"
    none = SessionView(session, Selection())
    assert Accept().caption(none) == "Accept 0 results as baselines"
    assert Accept().available(none) == "Accept needs a selection"


def test_queue_caption_counts_what_it_queues_what_is_held_and_the_unsaved_edits(
    session,
):
    # No backend: the header shows it; the panel names it.
    cap = EnqueueTests()
    one = SessionView(session, Selection(tests=frozenset({A})))
    two = SessionView(session, Selection(tests=frozenset({A, D})))
    three = SessionView(session, Selection(tests=frozenset({A, B, D})))
    assert cap.caption(one) == "Queue 1"
    assert cap.caption(three) == "Queue 3"
    assert cap.caption(SessionView(session, Selection())) == "Queue 0"

    # The tests the queue already holds are left out, and counted.
    held = [fake_item(session, B, ItemState.WAITING)]
    held.append(fake_item(session, D, ItemState.RUNNING))
    assert cap.caption(three) == "Queue 1 (2 already queued)"

    # A run uses the spec with the pending edits applied, as they stand
    # when its test starts; an edit of a test the click does not queue is
    # not this click's.
    session.stage([PatchOp(D, "add", "/comparison/tolerance", 0.5)])
    assert cap.caption(three) == "Queue 1 (2 already queued)"
    session.stage([LOOSEN])  # two ops on A — its tree, and the edit: ONE edit
    assert cap.caption(three) == (
        "Queue 1 (2 already queued; with 1 unsaved edit, as they stand when "
        "each starts)"
    )
    for item in held:
        drop_item(session, item)
    assert cap.caption(two) == (
        "Queue 2 (with 2 unsaved edits, as they stand when each starts)"
    )
    other = SessionView(session, Selection(tests=frozenset({D})))
    assert cap.caption(other) == (
        "Queue 1 (with 1 unsaved edit, as they stand when each starts)"
    )
    # Another model's edits are not this selection's.
    session.discard({A})
    assert cap.caption(one) == "Queue 1"


@pytest.mark.parametrize("ended", [ItemState.CANCELLED, ItemState.FAILED])
def test_queue_caption_says_again_for_one_test_whose_last_run_was_cancelled_or_failed(
    session, ended
):
    """One test whose last item in the queue was cancelled or failed; a
    finished one, and a selection of several, keep the count."""
    cap = EnqueueTests()
    one = SessionView(session, Selection(tests=frozenset({A})))
    two = SessionView(session, Selection(tests=frozenset({A, D})))
    item = fake_item(session, A, ItemState.RUNNING)
    # ended, as the pool ends an item and the Session takes it in
    item.state, item.ended_at = ended, item.started_at
    del session._live[A]

    assert session.queue_state(A) is ended
    assert cap.available(one) is True
    assert cap.caption(one) == "Queue again"
    assert cap.caption(two) == "Queue 2"
    item.state = ItemState.FINISHED
    assert cap.caption(one) == "Queue 1"
    session.clear_finished()
    assert session.queue == [] and cap.caption(one) == "Queue 1"


async def test_a_refused_form_keeps_its_row_and_what_was_typed(session):
    """A form closes when its act went through, never before it submits: a
    refused Wrap or Edit keeps its row open with what was typed, and the
    refusal is the host's to show. Typed right, it stages and the row
    goes."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    leaf, k_of_n = "/metrics/children/0", "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, errors, _f):
        user.find(marker=f"wrap:{leaf}").click()
        (kind,) = _labelled(user, ui.select, "Combinator")
        kind.set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        (threshold,) = _labelled(user, ui.number, "Threshold")
        weights.set_value("1, 2")  # two weights for the one child
        threshold.set_value(0.5)
        await _settled(lambda: _one(user, f"apply-wrap:{leaf}").enabled)
        staged = len(session.spec.pending)
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: "edit-tree" in errors)
        assert "weights" in errors["edit-tree"]
        assert not _none(user, "host-row:title")
        assert weights.value == "1, 2" and len(session.spec.pending) == staged
        weights.set_value("1")
        user.find(marker=f"apply-wrap:{leaf}").click()
        await _settled(lambda: _none(user, "host-row:title"))
        assert "combinator" in session.tree(D)["children"][0]
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _f):
        _one(user, f"kind:{k_of_n}").set_value("weighted")
        await _settled(lambda: "Threshold" in _labels_shown(user))
        (weights,) = _labelled(user, ui.input, "Weights, one per child")
        (threshold,) = _labelled(user, ui.number, "Threshold")
        weights.set_value("1")  # one weight for two children
        threshold.set_value(0.5)
        await _settled(lambda: _one(user, f"apply-edit:{k_of_n}").enabled)
        user.find(marker=f"apply-edit:{k_of_n}").click()
        await _settled(lambda: "edit-tree" in errors)
        assert not _none(user, "host-row:title")
        assert weights.value == "1"
        assert session.tree(D)["children"][1]["combinator"] == "k-of-n"


async def test_a_refused_add_keeps_its_row_and_what_was_chosen(session):
    """An Add row opened under an inner `and`; the node is staged to `warn`
    elsewhere while the row is open, and a `warn` takes one child, so the
    Add is refused. The row stays with what was chosen and nothing is
    revealed; an Add that closed its row before it submitted took both."""
    inner = "/metrics/children/0"
    tree = copy.deepcopy(NESTED)
    tree["children"][0] = {
        "combinator": "and",
        "children": [{"metric": "nrmse", "variable": "x"}],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    reveals: list[str] = []
    async with host(session, EditTree(), _node(D, inner), reveals) as (
        user,
        errors,
        _f,
    ):
        user.find(marker=f"add:{inner}").click()
        await user.should_see(marker=f"apply-add:{inner}")
        (metric,) = _labelled(user, ui.select, "Metric")
        metric.set_value("range")
        session.stage([PatchOp(D, "replace", f"{inner}/combinator", "warn")])
        staged = len(session.spec.pending)
        user.find(marker=f"apply-add:{inner}").click()
        await _settled(lambda: "edit-tree" in errors)
        assert "'warn' combinator requires exactly 1 child" in errors["edit-tree"]
        assert not _none(user, "host-row:title")
        assert metric.value == "range"
        assert len(session.spec.pending) == staged and reveals == []
        assert len(session.tree(D)["children"][0]["children"]) == 1


async def test_a_refused_unwrap_keeps_the_edit_row(session):
    """Unwrap of a `k-of-n` of two under a `warn`: its two children would
    take its place, and a `warn` takes one, so the validator refuses it.
    An Edit row open under the node stays, with what was typed in it, and
    the dropdown goes back to the node's kind."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"combinator": "warn", "children": [copy.deepcopy(NESTED["children"][1])]},
        ],
    }
    session.stage([PatchOp(D, "add", ROOT, tree)])
    k_of_n = "/metrics/children/1/children/0"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, errors, _f):
        user.find(marker="host-click").click()
        await user.should_see(marker=f"apply-edit:{k_of_n}")
        (k,) = _labelled(user, ui.number, K)
        k.set_value(1)
        staged = len(session.spec.pending)
        kind = _one(user, f"kind:{k_of_n}")
        kind.set_value("unwrap…")
        await _settled(lambda: "edit-tree" in errors)
        assert "'warn' combinator requires exactly 1 child" in errors["edit-tree"]
        assert not _none(user, "host-row:title")
        assert kind.value == "k-of-n"
        assert k.value == 1
        assert len(session.spec.pending) == staged
        assert session.tree(D)["children"][1]["children"][0]["combinator"] == "k-of-n"


# -- a criterion's + adds the new one right AFTER it (Q3 A) -----------------

THREE = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x"},
        {"metric": "range", "variable": "x", "max_value": 9.0},
        {"metric": "range", "variable": "x", "max_value": 5.0},
    ],
}


@pytest.mark.parametrize(
    ("tree", "path", "op_path", "added"),
    [
        (THREE, ROOT, "/metrics/children/-", "/metrics/children/3"),
        (THREE, "/metrics/children/1", "/metrics/children/2", "/metrics/children/2"),
        (THREE, "/metrics/children/2", "/metrics/children/3", "/metrics/children/3"),
        (
            NESTED,
            "/metrics/children/1/children/0",
            "/metrics/children/1/children/1",
            "/metrics/children/1/children/1",
        ),
        (NESTED, "/metrics/children/0", "/metrics/children/1", "/metrics/children/1"),
    ],
)
def test_plus_adds_right_after_the_clicked_criterion(
    session, tree, path, op_path, added
):
    """`act` stages it there and `_added_path` — the path `reveal` opens —
    predicts the same place, read from the tree before the add."""
    from dstf.authoring.builtin.edit_tree import _added_path
    from dstf.authoring.tree_pointer import node_or_none

    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(tree))])
    assert _added_path(session.tree(D), path) == added
    outcome = EditTree().act(
        SessionView(session, _node(D, path)),
        action="add",
        model_id=D,
        path=path,
        variable="x",
        metric="range",
    )
    (op,) = outcome.ops
    assert (op.op, op.path) == ("add", op_path)
    session.apply(outcome)
    assert node_or_none(session.tree(D), added)["metric"] == "range"


def test_plus_beside_a_root_leaf_reveals_the_roots_second_child(session):
    from dstf.authoring.builtin.edit_tree import _added_path

    session.stage([PatchOp(D, "add", ROOT, {"metric": "nrmse", "variable": "x"})])
    assert _added_path(session.tree(D), ROOT) == "/metrics/children/1"


async def test_the_plus_tooltip_and_title_say_where_the_criterion_lands(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(THREE))])
    leaf = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, leaf)) as (user, _e, _f):
        assert _tips(_one(user, f"add:{leaf}")) == ["Add a criterion after this one"]
        user.find(marker=f"add:{leaf}").click()
        await user.should_see(marker=f"apply-add:{leaf}")
        assert _one(user, "host-row:title").text == "Add a criterion after range · x"
        await user.should_see("Values come from the data where the metric needs them.")
    async with host(session, EditTree(), _node(D, ROOT)) as (user, _e, _f):
        assert _tips(_one(user, f"add:{ROOT}")) == [
            "Add a criterion at the end of this and"
        ]
        user.find(marker=f"add:{ROOT}").click()
        await user.should_see(marker=f"apply-add:{ROOT}")
        assert _one(user, "host-row:title").text == (
            "Add a criterion at the end of and · 3 criteria"
        )


# -- a combinator's kind is a dropdown in its row (Q3 A) --------------------


async def test_and_to_or_in_the_dropdown_stages_the_kind_alone(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(THREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, errors, _f):
        kind = _one(user, f"kind:{ROOT}")
        assert kind.props.get("aria-label") == "Combinator kind"
        staged = len(session.spec.pending)
        kind.set_value("or")
        await _settled(lambda: session.tree(D)["combinator"] == "or")
        ops = session.spec.pending[staged:]
        assert [(op.op, op.path, op.value) for op in ops] == [
            ("replace", "/metrics/combinator", "or")
        ]
        assert errors == {}


async def test_choosing_k_of_n_opens_the_edit_form_and_stages_nothing_until_apply(
    session,
):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(THREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, errors, _f):
        kind = _one(user, f"kind:{ROOT}")
        staged = len(session.spec.pending)
        kind.set_value("k-of-n")
        await user.should_see(marker=f"apply-edit:{ROOT}")
        assert _one(user, "host-row:title").text == "Edit this combinator"
        assert _labels_shown(user) & FIELDS == {K}
        assert _labelled(user, ui.select, "Combinator") == []
        assert len(session.spec.pending) == staged
        assert kind.value == "and"  # the node's kind until Apply
        user.find(marker=f"apply-edit:{ROOT}").click()
        await _settled(lambda: session.tree(D)["combinator"] == "k-of-n")
        assert session.tree(D)["k"] == 1
        assert errors == {}


async def test_warn_on_a_root_of_three_is_refused_and_the_select_goes_back(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(THREE))])
    async with host(session, EditTree(), _node(D, ROOT)) as (user, errors, _f):
        kind = _one(user, f"kind:{ROOT}")
        staged = len(session.spec.pending)
        kind.set_value("warn")
        await _settled(lambda: "edit-tree" in errors)
        assert "'warn' combinator requires exactly 1 child" in errors["edit-tree"]
        assert kind.value == "and"
        assert len(session.spec.pending) == staged


async def test_the_weighted_threshold_hint_follows_direction(session):
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(NESTED))])
    k_of_n = "/metrics/children/1"
    async with host(session, EditTree(), _node(D, k_of_n)) as (user, _errors, _f):
        _one(user, f"kind:{k_of_n}").set_value("weighted")
        await _settled(lambda: not _none(user, "threshold-hint"))
        assert _one(user, "threshold-hint").text == (
            "Passes when Σ weight × score over the children is below the "
            "threshold (lower is better)."
        )
        (direction,) = _labelled(user, ui.select, "Direction")
        direction.set_value(_by_label(direction, "higher is better"))
        await _settled(lambda: "above" in _one(user, "threshold-hint").text)
        assert _one(user, "threshold-hint").text == (
            "Passes when Σ weight × score over the children is above the "
            "threshold (higher is better)."
        )


def test_a_combinators_count_text_is_its_node_text_without_the_kind():
    from dstf.authoring.tree_text import count_text, node_text

    leaf = {"kind": "leaf", "metric": "nrmse", "variable": "x"}
    node = {"kind": "combinator", "combinator": "and", "children": [leaf] * 3}
    assert (node_text(node), count_text(node)) == ("and · 3 criteria", "3 criteria")
    k = {**node, "combinator": "k-of-n", "k": 2}
    assert count_text(k) == "k=2 · 3 criteria"
    w = {**node, "combinator": "weighted", "threshold": 0.5}
    assert count_text(w) == "threshold 0.5 · 3 criteria"
    # A locked weighted row reads `node_text`: it keeps its threshold.
    assert node_text(w) == "weighted · 3 criteria · threshold 0.5"
    one = {**node, "children": [leaf]}
    assert count_text(one) == "1 criterion"


# -- the add form's picker (U2, Task 3) ------------------------------------------


def _wide_session(tmp_path, extra: int, columns=()):
    """`two_var_session` with ``extra`` more names in A's result file
    (``v0`` ...), untracked, and ``columns`` as result-file names too."""
    import json

    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for i in range(extra):
        payload["variables"][f"v{i}"] = [float(i)] * len(payload["time"])
    for name in columns:
        payload["variables"][name] = [1.0, 2.0, 3.0, 4.0, 5.0]
    path.write_text(json.dumps(payload), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def _with_diagnostics(session, **finals):
    """A's in-memory result carries these diagnostics (a Dymola run's
    `CPUtime` / `EventCounter`); the JSON driver records none."""
    import numpy as np

    from dstf.backends.types import VariableResult

    result = session.project.results[A]
    t = np.asarray(result.variables[0].time)
    result.diagnostics = [
        VariableResult(index=0, time=t, values=np.linspace(0, v, len(t)), name=n)
        for n, v in finals.items()
    ]


def _picker(user, mark):
    (var,) = user.find(marker=f"{mark}:variable").elements
    return var


def _sections(picker) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in picker.shown:
        out.setdefault(row.section, []).append(row.name)
    return out


async def _open_plus(user, variable: str):
    user.find(marker=f"add-for:{variable}").click()
    await user.should_see(marker=f"apply-add:{variable}:variable")
    return _picker(user, f"apply-add:{variable}")


async def test_the_picker_offers_every_result_name_in_sections(tmp_path):
    """Q8: the plot's series, then every other name of the result FILE —
    tracked or not — then the diagnostics the run RECORDED (B4): an
    `EventCounter` column of the file that no run recorded as a diagnostic
    is a variable like any other (a `.final` path would never read it)."""
    session = _wide_session(tmp_path, 3, columns=("CPUtime", "EventCounter"))
    _with_diagnostics(session, CPUtime=0.5)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        assert picker.value == "y"
        assert _sections(picker) == {
            "On this plot": ["y"],
            "Other variables": ["x", "v0", "v1", "v2", "EventCounter"],
            "Diagnostics": ["CPUtime"],
        }


async def test_a_run_that_records_no_diagnostics_has_no_diagnostics_section(tmp_path):
    session = _wide_session(tmp_path, 0, columns=("CPUtime",))
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        assert "Diagnostics" not in _sections(await _open_plus(user, "y"))


async def test_the_picker_filters_by_glob_regex_and_says_a_bad_one(tmp_path):
    session = _wide_session(tmp_path, 12)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        picker.box.needle.set_value("v1*")
        await _settled(lambda: list(picker.options) == ["v1", "v10", "v11"])
        picker._filter("^v(2|3)$", True, False)
        assert list(picker.options) == ["v2", "v3"]
        picker._filter("v(", True, False)
        assert picker.box.why.visible
        assert "not a regular expression" in picker.box.why.text


async def test_the_picker_filters_on_the_label_too(tmp_path):
    """A row reads its label: what a person types is what they read."""
    from test_session import with_expressions

    session = _wide_session(tmp_path, 3)
    with_expressions(session, A, {"x": "pipe.T[3]"})
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        picker._filter("pipe", False, False)
        assert picker.options == {"x": "pipe.T[3]"}


async def test_the_picker_caps_at_200_rows_and_says_so(tmp_path):
    session = _wide_session(tmp_path, 400)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        assert len(picker.shown) == 200
        assert picker.cap.visible
        assert picker.cap.text == "Showing 200 of 402 — narrow the search"


async def test_choosing_a_row_is_what_add_stages(tmp_path):
    session = _wide_session(tmp_path, 3)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, errors, _figure):
        await _open_plus(user, "y")
        user.find(marker="apply-add:y:variable:pick:v1").click()
        assert _picker(user, "apply-add:y").value == "v1"
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(session.tree(A)["children"]) == 3)
        assert session.tree(A)["children"][-1]["variable"] == "v1"
        assert errors == {}


async def test_the_chosen_line_names_a_choice_the_filter_hides(tmp_path):
    """K7: under the list, `Chosen: <label>` — a filter that hides the
    chosen row still shows what Add will stage."""
    from test_session import with_expressions

    session = _wide_session(tmp_path, 3)
    with_expressions(session, A, {"x": "pipe.T[3]"})
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        assert picker.chosen.text == "Chosen: y"
        user.find(marker="apply-add:y:variable:pick:x").click()
        assert picker.chosen.text == "Chosen: pipe.T[3]"
        picker._filter("v2", False, False)
        assert "x" not in picker.options
        assert picker.chosen.text == "Chosen: pipe.T[3]"


def _press(picker, key: str, needle: str = "") -> None:
    """A key in the filter box, as `_KEYS` sends it: with the box's text."""
    picker._key(SimpleNamespace(args={"key": key, "needle": needle}))


async def test_the_pickers_keys_move_and_choose(tmp_path):
    session = _wide_session(tmp_path, 3)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        assert picker.cursor == 0  # the chosen row, y
        _press(picker, "ArrowDown")
        _press(picker, "ArrowDown")
        _press(picker, "Enter")
        assert picker.value == "v0"
        _press(picker, "ArrowUp")
        _press(picker, "Enter")
        assert picker.value == "x"


async def test_a_key_typed_before_the_filter_acts_on_the_list_as_typed(tmp_path):
    """The filter reaches the server 250 ms after the last keystroke; a key
    carries the box's text, so ↓ Enter typed inside that act on the list
    the user sees — and the filter arriving after draws nothing again,
    leaving the cursor where the key put it."""
    session = _wide_session(tmp_path, 3)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        _press(picker, "ArrowDown", "v")
        assert list(picker.options) == ["v0", "v1", "v2"]
        _press(picker, "ArrowDown", "v")
        _press(picker, "Enter", "v")
        assert picker.value == "v1"
        picker._filter("v", False, False)  # the debounced change, late
        assert picker.cursor == 1 and picker.value == "v1"
        assert "dstf-pick-cursor" in picker._drawn["v1"].classes


async def test_the_picker_is_a_combobox_over_a_listbox(tmp_path):
    """WAI-ARIA: the box a combobox controlling the list, the cursor's row
    its active descendant; each option named by its text and, when that
    differs, the name — never the middle cut's two parts."""
    from test_session import with_expressions

    session = _wide_session(tmp_path, 1)
    with_expressions(session, A, {"x": "pipe.T[3]"})
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        box = picker.box.needle.props
        assert picker.list.props["role"] == "listbox"
        assert box["role"] == "combobox"
        assert box["aria-controls"] == picker.list.html_id
        assert "aria-activedescendant" not in box
        _press(picker, "ArrowDown")
        assert box["aria-activedescendant"] == picker._drawn["x"].html_id
        labels = {n: e.props["aria-label"] for n, e in picker._drawn.items()}
        assert labels == {"y": "y", "x": "pipe.T[3] (x)", "v0": "v0"}
        picker._filter("v0", False, False)
        assert "aria-activedescendant" not in box  # its row is gone


async def test_a_clicked_row_is_the_one_cursor(tmp_path):
    """A click moves the cursor with the choice: one row carries the ring,
    and the box's active descendant is that row. A click set the cursor's
    index alone, and the ring stayed on the row the keys had left it on —
    the next ↓ moved from one row while another showed the ring."""
    session = _wide_session(tmp_path, 3)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        _press(picker, "ArrowDown")
        _press(picker, "ArrowDown")
        user.find(marker="apply-add:y:variable:pick:v2").click()
        box = picker.box.needle.props
        assert box.get("aria-activedescendant") == picker._drawn["v2"].html_id
        rings = [n for n, e in picker._drawn.items() if "dstf-pick-cursor" in e.classes]
        assert rings == ["v2"]
        _press(picker, "ArrowDown")
        rings = [n for n, e in picker._drawn.items() if "dstf-pick-cursor" in e.classes]
        assert rings == ["y"]  # from v2, the last row, round to the first


async def test_an_options_accessible_name_says_the_name_once(tmp_path):
    """Two variables whose expressions collide read `a.Q (y)` in the test's
    scope. A row shows the plain label with the muted name beside it, and
    its accessible name is the label and the name once — it said the
    scope's text and the name again, `a.Q (y) (y)`."""
    from test_session import with_expressions

    session = _wide_session(tmp_path, 1)
    with_expressions(session, A, {"x": "a.Q", "y": "a.Q"})
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        labels = {n: e.props["aria-label"] for n, e in picker._drawn.items()}
        assert labels == {"y": "a.Q (y)", "x": "a.Q (x)", "v0": "v0"}, labels
        from dstf.authoring.shell.widgets import MiddleCut

        cuts = [
            e.text for e in picker._drawn["y"].descendants() if isinstance(e, MiddleCut)
        ]
        assert cuts == ["a.Q"], cuts  # the muted `y` beside it names it


async def test_the_pickers_sections_are_named_groups_and_no_match_is_a_status(
    tmp_path,
):
    """WAI-ARIA: a listbox holds options and groups of them. Each section
    is a group named by its title, holding its options; "no variable
    matches" is no option — a status line outside the list."""
    session = _wide_session(tmp_path, 1)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        picker = await _open_plus(user, "y")
        groups = list(picker.list.default_slot.children)
        assert [(g.props.get("role"), g.props.get("aria-label")) for g in groups] == [
            ("group", "On this plot"),
            ("group", "Other variables"),
        ]
        for name, row in picker._drawn.items():
            (holder,) = [g for g in groups if row in g.descendants()]
            assert holder.props["aria-label"] == next(
                r.section for r in picker.shown if r.name == name
            )
        picker._filter("nothing-is-named-this", False, False)
        (none,) = user.find(content="no variable matches").elements
        assert none.props.get("role") == "status" and none.visible
        assert none not in picker.list.descendants()
        picker._filter("", False, False)
        assert not none.visible


async def test_a_simulate_only_test_says_nothing_is_scored(tmp_path):
    """A criterion staged on a simulate-only test is never scored: the add
    form says so, and its Add is grey (A10)."""
    session = _wide_session(tmp_path, 0)
    session.project.test(A).simulate_only = True
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        await _open_plus(user, "y")
        (add,) = user.find(marker="apply-add:y").elements
        assert not add.enabled
        await user.should_see("simulate-only: nothing is scored")


# -- the metric list follows the variable (U2, Task 4) ---------------------------


def _metric_select(user):
    return next(
        e
        for e in user.find(kind=ui.select).elements
        if e.props.get("label") == "Metric"
    )


async def test_a_diagnostic_offers_scalar_only_and_seeds_a_quantity_path(tmp_path):
    """Q2 + K1: a diagnostic is one stored number — the Metric select offers
    what takes a ``quantity`` (scalar), and Add writes its final through a
    quantity path, a count exact. Back on an ordinary variable, every metric
    that scores a time series, and the one chosen there before."""
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, CPUtime=0.37, EventCounter=4.0)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, errors, _figure):
        picker = await _open_plus(user, "y")
        met = _metric_select(user)
        everything = list(met.options)
        assert "scalar" in everything and len(everything) > 1
        assert _sections(picker)["Diagnostics"] == ["CPUtime", "EventCounter"]
        user.find(marker="apply-add:y:variable:pick:EventCounter").click()
        assert list(met.options) == ["scalar"] and met.value == "scalar"
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(session.tree(A)["children"]) == 3)
        assert session.tree(A)["children"][-1] == {
            "metric": "scalar",
            "variable": "EventCounter",
            "quantity": "EventCounter.final",
            "tolerance": 0,
            "tolerance_mode": "abs",
        }
        assert errors == {}
        picker = await _open_plus(user, "y")
        met = _metric_select(user)
        met.set_value("tube")
        user.find(marker="apply-add:y:variable:pick:CPUtime").click()
        assert list(met.options) == ["scalar"]
        user.find(marker="apply-add:y:variable:pick:EventCounter").click()
        # back on an ordinary variable, the metric chosen before the
        # diagnostics, not the list's first
        user.find(marker="apply-add:y:variable:pick:x").click()
        assert list(met.options) == everything and met.value == "tube"


async def test_a_metric_kept_across_a_diagnostic_is_restored_once(tmp_path):
    """The metric kept over a diagnostic comes back ONCE, on the first
    ordinary variable after it; a metric chosen after that is the one kept
    when the choice moves to another ordinary variable, not the old one."""
    session = _wide_session(tmp_path, 1)
    _with_diagnostics(session, CPUtime=0.37)
    sel = Selection(tests=frozenset({A}), variable="y")
    async with host(session, EditTree(), sel) as (user, _errors, _figure):
        await _open_plus(user, "y")
        met = _metric_select(user)
        met.set_value("tube")
        user.find(marker="apply-add:y:variable:pick:CPUtime").click()
        user.find(marker="apply-add:y:variable:pick:x").click()
        assert met.value == "tube"
        met.set_value("points")
        user.find(marker="apply-add:y:variable:pick:v0").click()
        assert met.value == "points"


async def test_cpu_time_is_seeded_relative(tmp_path):
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, CPUtime=1.0)
    view = SessionView(session, Selection(tests=frozenset({A}), variable="y"))
    patch = EditTree().act(
        view, action="add", model_id=A, path=ROOT, variable="CPUtime", metric="scalar"
    )
    (op,) = patch.ops
    assert op.value["combinator"] == "warn"  # CPU time moves with the load
    (leaf,) = op.value["children"]
    assert leaf["quantity"] == "CPUtime.final"
    assert (leaf["tolerance"], leaf["tolerance_mode"]) == (0.1, "rel")


_XY = [{"metric": "nrmse", "variable": "x"}, {"metric": "nrmse", "variable": "y"}]


@pytest.mark.parametrize(
    ("tree", "path", "at", "warned"),
    [
        ({"combinator": "and", "children": _XY}, ROOT, f"{ROOT}/children/-", True),
        ({"combinator": "or", "children": _XY}, ROOT, f"{ROOT}/children/-", False),
        (
            {"combinator": "k-of-n", "k": 1, "children": _XY},
            ROOT,
            f"{ROOT}/children/-",
            False,
        ),
        (
            {"combinator": "and", "children": _XY},
            f"{ROOT}/children/0",
            f"{ROOT}/children/1",
            True,
        ),
        (
            {"combinator": "or", "children": _XY},
            f"{ROOT}/children/0",
            f"{ROOT}/children/1",
            False,
        ),
        (
            {
                "combinator": "and",
                "children": [
                    _XY[0],
                    {"combinator": "k-of-n", "k": 1, "children": _XY},
                ],
            },
            f"{ROOT}/children/1/children/1",
            f"{ROOT}/children/1/children/2",
            False,
        ),
    ],
    ids=["and", "or", "k-of-n", "beside-in-and", "beside-in-or", "beside-in-k-of-n"],
)
async def test_cpu_time_warns_only_where_it_lands_in_an_and(
    tmp_path, tree, path, at, warned
):
    """A `warn` child passes whatever it scores: under an `or` or a `k-of-n`
    it would make its parent pass with nothing else passing. There CPU time
    is added bare, with its 10% — an `and` (the root leaf's too, which
    becomes one) is where a warn only keeps it from failing the test."""
    session = _wide_session(tmp_path, 0)
    session.stage([PatchOp(A, "add", ROOT, copy.deepcopy(tree))])
    _with_diagnostics(session, CPUtime=0.4)
    view = SessionView(session, Selection(tests=frozenset({A}), variable="y"))
    (op,) = (
        EditTree()
        .act(
            view,
            action="add",
            model_id=A,
            path=path,
            variable="CPUtime",
            metric="scalar",
        )
        .ops
    )
    assert op.path == at
    leaf = op.value["children"][0] if warned else op.value
    assert op.value.get("combinator") == ("warn" if warned else None)
    assert leaf["quantity"] == "CPUtime.final"
    assert (leaf["tolerance"], leaf["tolerance_mode"]) == (0.1, "rel")


async def test_cpu_time_beside_a_root_leaf_warns_in_the_and_it_makes(tmp_path):
    session = _wide_session(tmp_path, 0)
    session.stage([PatchOp(A, "add", ROOT, dict(_XY[0]))])
    _with_diagnostics(session, CPUtime=0.4)
    view = SessionView(session, Selection(tests=frozenset({A}), variable="y"))
    (op,) = (
        EditTree()
        .act(
            view,
            action="add",
            model_id=A,
            path=ROOT,
            variable="CPUtime",
            metric="scalar",
        )
        .ops
    )
    assert op.value["combinator"] == "and"
    assert op.value["children"][1]["combinator"] == "warn"


async def test_cpu_time_added_bare_under_an_or_reveals_its_leaf(tmp_path):
    """Added bare, the criterion IS the leaf: the reveal opens it."""
    session = _wide_session(tmp_path, 0)
    session.stage([PatchOp(A, "add", ROOT, {"combinator": "or", "children": _XY})])
    _with_diagnostics(session, CPUtime=0.4)
    sel = Selection(tests=frozenset({A}), variable="y")
    reveals: list[str] = []
    async with host(session, EditTree(), sel, reveals) as (user, errors, _figure):
        await _open_plus(user, "y")
        user.find(marker="apply-add:y:variable:pick:CPUtime").click()
        user.find(marker="apply-add:y").click()
        await _settled(lambda: len(session.tree(A)["children"]) == 3)
        added = session.tree(A)["children"][-1]
        assert "combinator" not in added and added["variable"] == "CPUtime"
        assert reveals == [f"{ROOT}/children/2"] and errors == {}


async def test_a_diagnostic_refuses_a_trajectory_metric(tmp_path):
    """B4: `act` decides "diagnostic" from what the run recorded, and a
    metric that takes no quantity cannot score one."""
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, CPUtime=1.0)
    view = SessionView(session, Selection(tests=frozenset({A}), variable="y"))
    with pytest.raises(ValueError, match="scalar"):
        EditTree().act(
            view,
            action="add",
            model_id=A,
            path=ROOT,
            variable="CPUtime",
            metric="nrmse",
        )


# -- values in effect (U2, Task 5) -----------------------------------------------


def _hints(user) -> dict[str, str]:
    """``{label: hint}`` of every field with a hint line."""
    out: dict[str, str] = {}
    for kind in (ui.number, ui.select, ui.input):
        try:
            elements = user.find(kind=kind).elements
        except AssertionError:
            continue
        out |= {
            str(e.props.get("label")): str(e.props["hint"])
            for e in elements
            if e.props.get("hint")
        }
    return out


async def test_the_editor_says_the_tolerance_in_effect_until_the_mode_moves(session):
    """K3: a leaf without a tolerance of its own is scored with the base
    one, and the editor says which on the field's hint line — never
    written. A switched mode's fields were never applied: no hint."""
    tol = session.comparisons[A].variables[0].tolerance_used.upper
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _):
        await user.should_see("Tolerance")
        assert _hints(user) == {
            "Tolerance": f"in effect: {tol:g} (the project's tolerance)"
        }
        assert len(session.spec.pending) == 0
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        # points has a tolerance field and its seed sets none: the base one
        # would read as "in effect" for a mode that was never scored
        mode.set_value("points")
        await user.should_see("Default tolerance")
        await _settled(lambda: not _hints(user))


async def test_an_own_tolerance_cleared_says_the_base_in_effect(session):
    """K3: the hint names what applies while the field is EMPTY. A leaf
    with its own 0.5 opens with no hint; cleared, the field says the base
    the leaf would be scored with — it said nothing, because the view
    carried the base only for a leaf without its own."""
    base = session.comparisons[A].variables[0].tolerance_used.upper  # none of its own
    session.stage([PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)])
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _):
        await user.should_see("Tolerance")
        assert _hints(user) == {}
        (tol,) = [
            e
            for e in user.find(kind=ui.number).elements
            if e.props.get("label") == "Tolerance"
        ]
        assert tol.value == 0.5
        tol.set_value(None)
        await _settled(
            lambda: (
                _hints(user)
                == {"Tolerance": f"in effect: {base:g} (the project's tolerance)"}
            )
        )


# -- a tolerance's two sides on the criterion's form ------------------------------


def _marked(user, mark: str):
    (element,) = user.find(marker=mark).elements
    return element


def _sides_of(user, field: str = "tolerance"):
    """The lower field, the link toggle and the upper field of ``field``."""
    return tuple(
        _marked(user, f"{field}:{part}") for part in ("lower", "link", "upper")
    )


def _leaf0(session) -> dict:
    return session.tree(A)["children"][0]


async def test_the_editor_opens_on_a_pair_and_stages_each_transition(session):
    """The shape in the file is the link state. Every transition stages
    exactly the value the leaf then holds, as ONE pending replace of the
    leaf; a pair with one side blank stages nothing."""
    pair = {"lower": 0.1, "upper": 0.3}
    leaf = {"metric": "points", "variable": "x", "tolerance": pair}
    session.stage([PatchOp(A, "replace", "/metrics/children/0", leaf)])
    pending = len(session.spec.pending)
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("− Default tolerance")
        await user.should_see("+ Default tolerance")
        lower, link, upper = _sides_of(user)
        assert (lower.value, upper.value) == (0.1, 0.3)
        assert link.props["icon"] == "link_off"
        assert _hints(user) == {}  # a side holds a value: nothing else applies

        def staged():
            assert not errors and len(session.spec.pending) == pending
            return _leaf0(session).get("tolerance")

        upper.set_value(0.5)  # unlinked: its own side
        assert staged() == {"lower": 0.1, "upper": 0.5}
        user.find(marker="tolerance:link").click()  # re-link: the larger side
        assert staged() == 0.5 and (lower.value, upper.value) == (0.5, 0.5)
        lower.set_value(0.2)  # linked: both move
        assert staged() == 0.2 and upper.value == 0.2
        user.find(marker="tolerance:link").click()  # unlink: the number twice
        assert staged() == {"lower": 0.2, "upper": 0.2}
        lower.set_value(None)  # half a pair: nothing staged, the blank marked
        assert staged() == {"lower": 0.2, "upper": 0.2}
        assert lower.error == "required" and upper.error is None
        upper.set_value(None)  # both blank: unset, linked again
        assert staged() is None and "tolerance" not in _leaf0(session)
        assert link.props["icon"] == "link" and lower.error is None


# -- a half pair on the criterion's form outlives the panel it was typed in ----

FORM_PAIR = {"lower": 0.1, "upper": 0.3}
FORM_HALF = {"lower": 0.1, "upper": None}


def _pair_leaf_on_a(session) -> None:
    leaf = {"metric": "points", "variable": "x", "tolerance": dict(FORM_PAIR)}
    session.stage([PatchOp(A, "replace", "/metrics/children/0", leaf)])


def _form_sides(user) -> tuple:
    """The form's tolerance as it shows: each side's value and mark, and
    the toggle's icon."""
    lower, link, upper = _sides_of(user)
    return (lower.value, lower.error, upper.value, upper.error, link.props["icon"])


async def test_a_half_pair_on_the_form_is_blank_in_the_panel_built_after_its_own(
    session,
):
    """The form writes a half pair where it outlives the panel
    (`Panel.kept`), as the tables do: by field, with what the leaf holds
    under it. The panel built next — after a drag on the plot, after a
    table's cell — reads it back: `+` blank and "required", `−` as typed,
    unlinked. Built from the spec alone the blank came back as its old
    value, unmarked. Filled, it is no record any more."""
    _pair_leaf_on_a(session)
    kept: dict = {}
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        _sides_of(user)[2].set_value(None)
        assert kept == {"tolerance": {"base": FORM_PAIR, "shown": FORM_HALF}}
        assert _leaf0(session)["tolerance"] == FORM_PAIR
    pending = list(session.spec.pending)
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        assert _form_sides(user) == (0.1, None, None, "required", "link_off")
        assert session.spec.pending == pending  # reading it back stages nothing
        _sides_of(user)[2].set_value(0.4)
        assert _leaf0(session)["tolerance"] == {"lower": 0.1, "upper": 0.4}
        assert kept == {} and not errors


async def test_a_half_pair_whose_tolerance_moved_in_the_spec_shows_the_specs(session):
    """The record holds what the leaf held under the field. Where that has
    since moved — its side dragged on the plot — the fields show the
    spec's pair, whole, and the record is gone."""
    _pair_leaf_on_a(session)
    kept: dict = {}
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        _sides_of(user)[2].set_value(None)
        assert kept
    dragged = {"lower": 0.1, "upper": 0.5}
    session.stage([PatchOp(A, "replace", "/metrics/children/0/tolerance", dragged)])
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        assert _form_sides(user) == (0.1, None, 0.5, None, "link_off")
        assert kept == {}


async def test_a_half_pair_goes_with_the_mode_it_was_typed_in(session):
    """A mode switched to has fields of its own — nrmse's tolerance is
    another field, though it has the name, and holds as little as the one
    the half pair was typed over: the record is dropped with the switch,
    not left for the next field of that name. Back in the first mode the
    fields are empty, and nothing is marked."""
    leaf = {"metric": "points", "variable": "x"}
    session.stage([PatchOp(A, "replace", "/metrics/children/0", leaf)])
    kept: dict = {}
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        user.find(marker="tolerance:link").click()  # unset, and unlinked
        _sides_of(user)[0].set_value(0.1)
        assert kept == {
            "tolerance": {"base": None, "shown": {"lower": 0.1, "upper": None}}
        }
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("nrmse")
        await user.should_see("Tolerance")
        assert "tolerance" not in _leaf0(session)  # as little as before
        assert kept == {}
        mode.set_value("points")
        await user.should_see("+ Default tolerance")
        assert _form_sides(user) == (None, None, None, None, "link")


async def test_a_pair_linked_again_while_half_is_no_record(session):
    """The toggle is an edit too: linked, both fields take the side that
    is there, the leaf holds that number and nothing is half."""
    _pair_leaf_on_a(session)
    kept: dict = {}
    async with host(session, EditCriteria(), LEAF0, kept=kept) as (user, errors, _):
        await user.should_see("+ Default tolerance")
        _sides_of(user)[2].set_value(None)
        assert kept
        user.find(marker="tolerance:link").click()
        assert kept == {}
        assert _leaf0(session)["tolerance"] == 0.1


async def test_an_unset_two_sided_tolerance_says_what_is_in_effect_and_writes_nothing(
    session,
):
    """A scalar with no tolerance of its own: both fields empty, linked, the
    base under the first — and opening its editor stages nothing."""
    leaf = {"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}}
    session.stage([PatchOp(A, "replace", "/metrics/children/0", leaf)])
    before = [(op.path, op.value) for op in session.spec.pending]
    base = session.comparisons[A].base_tolerance
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("− Tolerance")
        lower, link, upper = _sides_of(user)
        assert (lower.value, upper.value) == (None, None)
        assert link.props["icon"] == "link"
        hint = f"in effect: {base:g} (the project's tolerance)"
        assert _hints(user) == {"− Tolerance": hint}
        assert [(op.path, op.value) for op in session.spec.pending] == before
        upper.set_value(0.05)  # typed in either: the number, and no hint
        assert _leaf0(session)["tolerance"] == 0.05 and lower.value == 0.05
        assert _hints(user) == {} and not errors
        lower.set_value(None)
        assert "tolerance" not in _leaf0(session)
        assert _hints(user) == {"− Tolerance": hint}


async def test_nrmse_keeps_one_tolerance_field(session):
    """nrmse is one ceiling: one field, no toggle."""
    async with host(session, EditCriteria(), LEAF0) as (user, _errors, _):
        await user.should_see("Tolerance")
        labels = [
            str(e.props.get("label"))
            for e in user.find(ui.number).elements
            if "olerance" in str(e.props.get("label"))
        ]
        assert labels == ["Tolerance"]
        for part in ("lower", "upper", "link"):
            with pytest.raises(AssertionError):
                user.find(marker=f"tolerance:{part}")


@pytest.mark.parametrize(
    ("leaf", "field", "labels"),
    [
        (
            {"metric": "event-timing", "variable": "x", "time_tolerance": 0.02},
            "time_tolerance",
            ("early Time tolerance (s)", "late Time tolerance (s)"),
        ),
        (
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_abs": 0.02,
            },
            "tube_abs",
            ("− Tube abs", "+ Tube abs"),
        ),
        (
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "rel",
                "tube_rel": 0.02,
            },
            "tube_rel",
            ("− Tube rel", "+ Tube rel"),
        ),
    ],
)
async def test_every_two_sided_field_of_a_form_is_named_by_its_sides(
    session, leaf, field, labels
):
    session.stage([PatchOp(A, "replace", "/metrics/children/0", leaf)])
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see(labels[0])
        lower, _link, upper = _sides_of(user, field)
        assert (lower.props["label"], upper.props["label"]) == labels
        assert (lower.value, upper.value) == (0.02, 0.02)
        user.find(marker=f"{field}:link").click()
        upper.set_value(0.05)
        assert not errors
        assert _leaf0(session)[field] == {"lower": 0.02, "upper": 0.05}


# -- a quantity added from the Statistics card (U3) -------------------------------

_COUNT_LEAF = {
    "metric": "scalar",
    "quantity": "translation.nonlinear_count",
    "tolerance": 0,
    "tolerance_mode": "abs",
}


def _add_quantity(session, quantity, path=ROOT):
    view = SessionView(session, Selection(tests=frozenset({A})))
    return (
        EditTree().act(view, action="add", model_id=A, path=path, quantity=quantity).ops
    )


@pytest.mark.parametrize("flat", [True, False])
async def test_a_count_lands_at_the_end_of_an_and_root(tmp_path, flat):
    """The `+` of a Statistics row adds at the END of the root (Q6 b): one
    op, `/metrics/children/-`, the count exact and bare."""
    session = _wide_session(tmp_path, 0)
    if not flat:
        session.stage([PatchOp(A, "add", ROOT, {"combinator": "and", "children": _XY})])
    (op,) = _add_quantity(session, "translation.nonlinear_count")
    assert (op.op, op.path) == ("add", f"{ROOT}/children/-")
    assert op.value == _COUNT_LEAF
    session.stage([op])
    assert session.tree(A)["children"][-1] == _COUNT_LEAF
    assert len(session.tree(A)["children"]) == 3


async def test_cpu_time_warns_in_an_and_root_and_goes_bare_in_an_or(tmp_path):
    session = _wide_session(tmp_path, 0)
    session.stage([PatchOp(A, "add", ROOT, {"combinator": "and", "children": _XY})])
    (op,) = _add_quantity(session, "CPUtime")
    assert op.value == {
        "combinator": "warn",
        "children": [
            {
                "metric": "scalar",
                "quantity": "CPUtime",
                "tolerance": 0.1,
                "tolerance_mode": "rel",
            }
        ],
    }
    session.stage([PatchOp(A, "add", ROOT, {"combinator": "or", "children": _XY})])
    (op,) = _add_quantity(session, "CPUtime")
    assert "combinator" not in op.value and op.value["tolerance_mode"] == "rel"


async def test_a_quantity_beside_a_root_leaf_makes_an_and_of_both(tmp_path):
    session = _wide_session(tmp_path, 0)
    session.stage([PatchOp(A, "add", ROOT, dict(_XY[0]))])
    (op,) = _add_quantity(session, "translation.nonlinear_count")
    assert (op.op, op.path) == ("add", ROOT)
    assert op.value == {"combinator": "and", "children": [_XY[0], _COUNT_LEAF]}


async def test_a_diagnostic_final_goes_through_its_seed(tmp_path):
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, EventCounter=4.0)
    (op,) = _add_quantity(session, "EventCounter.final")
    assert op.value == {
        "metric": "scalar",
        "variable": "EventCounter",
        "quantity": "EventCounter.final",
        "tolerance": 0,
        "tolerance_mode": "abs",
    }


async def test_a_diagnostic_max_is_added_on_its_own_path(tmp_path):
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, EventCounter=4.0)
    (op,) = _add_quantity(session, "EventCounter.max")
    assert op.value["quantity"] == "EventCounter.max"


async def test_a_top_level_statistic_naming_a_diagnostic_takes_its_kind(tmp_path):
    """A user-configured diagnostic folded into the statistics: its row's
    `+` reads the run's samples, as the add form's `Wall.final` does — a
    time, warned in an `and` (Corrections A5)."""
    session = _wide_session(tmp_path, 0)
    _with_diagnostics(session, Wall=2.5)  # samples 0 .. 2.5: fractional
    (op,) = _add_quantity(session, "Wall")
    assert op.value["combinator"] == "warn"
    (leaf,) = op.value["children"]
    assert (leaf["quantity"], leaf["tolerance_mode"]) == ("Wall", "rel")
    assert "variable" not in leaf


async def test_a_quantity_and_a_variable_together_raise(tmp_path):
    session = _wide_session(tmp_path, 0)
    view = SessionView(session, Selection(tests=frozenset({A})))
    with pytest.raises(ValueError, match="quantity or a variable"):
        EditTree().act(
            view,
            action="add",
            model_id=A,
            path=ROOT,
            quantity="CPUtime",
            variable="y",
            metric="scalar",
        )


_STATS_LEAF = {
    "metric": "scalar",
    "quantity": "translation.nonlinear_after_manipulation_count",
    "tolerance": 0,
    "tolerance_mode": "abs",
}


def _labels(user) -> list[str]:
    return [
        str(e.props.get("label"))
        for e in user.client.layout.descendants()
        if isinstance(e, (ui.select, ui.number, ui.input, ui.textarea))
    ]


async def test_a_statistics_leafs_editor_has_no_mode_window_or_quantity(session):
    """U3 Task 6: the card's row names the quantity and only `scalar` scores
    it; a stored number has no window. Tolerance stays — and an edit keeps
    the quantity the form does not show (B3): without it the replace
    staged a scalar that reads no statistic."""
    session.stage([PatchOp(A, "add", "/metrics/children/-", dict(_STATS_LEAF))])
    sel = Selection(tests=frozenset({A}), leaf_path="/metrics/children/1")
    async with host(session, EditCriteria(), sel) as (user, errors, _):
        await user.should_see("Tolerance")
        labels = _labels(user)
        assert "mode" not in labels and "Quantity" not in labels
        assert "window start" not in labels and "window end" not in labels
        assert "− Tolerance" in labels and "+ Tolerance" in labels
        assert "against" in labels
        (tol,) = user.find(marker="tolerance:lower").elements
        tol.set_value(1)
        assert not errors
        op = session.spec.pending[-1]
        assert (op.op, op.path) == ("replace", "/metrics/children/1")
        assert op.value["quantity"] == _STATS_LEAF["quantity"]
        assert op.value["tolerance"] == 1


async def test_a_reduce_scalar_keeps_its_mode_window_and_quantity(session):
    leaf = {"metric": "scalar", "variable": "x", "quantity": {"reduce": "max"}}
    session.stage([PatchOp(A, "add", "/metrics/children/-", leaf)])
    sel = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/1")
    async with host(session, EditCriteria(), sel) as (user, _errors, _):
        await user.should_see("Tolerance")
        labels = _labels(user)
        assert "mode" in labels and "window start" in labels and "Quantity" in labels


def test_a_statistics_criterion_is_edited_at_its_own_placement():
    """B2: Edit criteria alone attaches where the Statistics card hosts a
    criterion's editor; Edit on plot, which needs a plot, does not."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    at = {
        c.name
        for c in caps.registered_capabilities()
        if Placement.STATISTIC_CRITERION in c.placements
    }
    assert at == {EditCriteria.name}
    assert Placement.STATISTIC_CRITERION not in EditOnPlot.placements


def test_a_tree_criterion_is_edited_by_edit_criteria_alone():
    """U5 Y4: under a tree row only Edit criteria attaches — Edit on plot
    needs a drawn figure, and the tree editor links to the plot's."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    at = {
        c.name
        for c in caps.registered_capabilities()
        if Placement.TREE_CRITERION in c.placements
    }
    assert at == {EditCriteria.name}
    assert Placement.TREE_CRITERION not in EditOnPlot.placements


# -- one act, one pending edit ------------------------------------------------


def test_each_act_on_a_flat_tests_tree_is_one_pending_edit(session):
    """A (no `metrics` in the file) through the acts that take more than
    one op: each adds ONE to what the header counts, whatever it took."""
    from dstf.authoring.spec_document import edit_count
    from dstf.authoring.tree_move import Dest

    def act(path: str, **inputs) -> tuple[int, int]:
        view = SessionView(session, _node(A, path))
        outcome = EditTree().act(view, model_id=A, path=path, **inputs)
        session.apply(outcome)
        return len(session.spec.pending), edit_count(session.spec.pending)

    assert "metrics" not in session.spec.saved_entry(A)
    # A criterion added: the tree written out under it, and the leaf.
    assert act(ROOT, action="add", variable="x", metric="range") == (2, 1)
    # A criterion wrapped.
    assert act(f"{ROOT}/children/1", action="wrap", kind="or") == (3, 2)
    # That group unwrapped again: the same node written again, where the
    # wrap wrote it — the edit that one was (a value typed over is one).
    assert act(f"{ROOT}/children/1", action="unwrap") == (3, 2)
    # A move: the whole tree, given as one op.
    assert act(f"{ROOT}/children/1", action="move", dest=Dest("at", ROOT, 0)) == (4, 3)
    # The added criterion removed: the entry scores as saved again, and an
    # entry as saved has no pending edit at all.
    assert act(f"{ROOT}/children/0", action="remove") == (0, 0)


# -- move a subtree (U4) ----------------------------------------------------


def _move(session, src, dest, model=A):
    view = SessionView(session, _node(model, src))
    return EditTree().act(view, action="move", model_id=model, path=src, dest=dest)


def test_a_move_on_a_flat_test_stages_one_whole_tree_add(two_var_session):
    """A's tree is flat `and[nrmse x, nrmse y]`: the move supplies /metrics
    itself, so nothing is materialized before it (V1)."""
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    before = s.tree(A)
    patch = _move(s, "/metrics/children/1", Dest("at", ROOT, 0))
    (op,) = patch.ops
    assert (op.op, op.path) == ("add", ROOT)
    assert op.value["children"] == list(reversed(before["children"]))
    s.apply(patch)
    (pending,) = s.spec.pending
    assert (pending.op, pending.path, pending.materializes) == ("add", ROOT, False)
    assert s.tree(A)["children"] == list(reversed(before["children"]))


def test_two_moves_in_a_row_are_one_pending_op(two_var_session):
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    x, y = s.tree(A)["children"]
    s.apply(_move(s, "/metrics/children/1", Dest("at", ROOT, 0)))
    s.apply(_move(s, "/metrics/children/1", Dest("onto", "/metrics/children/0")))
    (pending,) = s.spec.pending
    assert (pending.op, pending.path) == ("add", ROOT)
    assert len(SessionView(s).pending) == 1
    assert s.tree(A)["children"] == [{"combinator": "and", "children": [y, x]}]


def test_a_move_and_the_move_back_leave_nothing_pending(two_var_session):
    """A flat test's tree moved is one whole tree staged; moved back it is
    the tree the entry already scores by — as saved, and no pending edit
    (it stayed "1 unsaved", and Save wrote a `metrics` block that scores
    as the flat entry does)."""
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    before = s.tree(A)
    s.apply(_move(s, "/metrics/children/1", Dest("at", ROOT, 0)))
    assert len(s.spec.pending) == 1
    s.apply(_move(s, "/metrics/children/0", Dest("at", ROOT, None)))
    assert s.spec.pending == []
    assert "metrics" not in s.spec.entry(A)
    assert s.tree(A) == before


def test_the_preview_shows_a_saved_trees_move_as_a_reorder(tmp_path):
    """A's saved entry holds NESTED: the drawer's diff is what moved and
    the k it lowered — the metrics block is not shown as added."""
    import json

    from dstf.authoring.spec_preview import entry_lines
    from dstf.authoring.tree_move import Dest

    cfg, work = _project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = NESTED
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    s.apply(_move(s, "/metrics/children/1/children/1", Dest("at", ROOT, 0)))
    lines = entry_lines(s.spec.preview_base(A), s.spec.entry(A))
    added = [line.text.strip() for line in lines if line.kind == "add"]
    assert '"max_value": 9.0' in added and '"k": 1,' in added, added
    assert not any('"metrics"' in text for text in added)


def test_a_flat_tests_move_previews_as_the_whole_tree_added(two_var_session):
    """The test gains its first tree: the drawer shows the whole metrics
    block added, as a root wrap on a flat test already does (C9)."""
    from dstf.authoring.spec_preview import entry_lines
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    s.apply(_move(s, "/metrics/children/1", Dest("at", ROOT, 0)))
    lines = entry_lines(s.spec.preview_base(A), s.spec.entry(A))
    assert any(line.kind == "add" and '"metrics"' in line.text for line in lines)


def test_a_refused_move_stages_nothing_and_says_why(session):
    from dstf.authoring.tree_move import INTO_WARN, WARN_UNDER, Dest, MoveRefused

    session.stage([PatchOp(A, "add", ROOT, copy.deepcopy(NESTED))])
    before = list(session.spec.pending)
    with pytest.raises(MoveRefused) as e:
        _move(session, "/metrics/children/0", Dest("at", "/metrics/children/2", 0))
    assert str(e.value) == INTO_WARN
    with pytest.raises(MoveRefused) as e:
        _move(session, "/metrics/children/2", Dest("at", "/metrics/children/1", 0))
    assert str(e.value) == WARN_UNDER
    assert session.spec.pending == before


def test_a_move_the_roles_refuse_says_the_validators_sentence(tmp_path):
    """The Session's roles: `om_cross` is A's soft check, so it cannot
    leave its warn — refused before it is staged, in stage's own words."""
    from dstf.authoring.tree_move import Dest, MoveRefused

    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", copy.deepcopy(WARN_LEAF))
    )
    before = list(s.spec.pending)
    with pytest.raises(
        MoveRefused, match="scores against 'om_cross' outside a 'warn', where only"
    ):
        _move(s, "/metrics/children/1/children/0", Dest("at", ROOT, None))
    assert s.spec.pending == before


def test_a_move_that_changes_nothing_is_no_outcome(two_var_session):
    from dstf.authoring.tree_move import Dest

    assert _move(two_var_session, "/metrics/children/1", Dest("at", ROOT, None)) is None


def test_the_planned_move_says_where_it_landed_and_what_changed(session):
    """What a panel reveals and says after the move (V4), read from the
    same plan `act` stages — they cannot disagree."""
    from dstf.authoring.builtin.edit_tree import planned_move
    from dstf.authoring.tree_move import Dest

    session.stage([PatchOp(A, "add", ROOT, copy.deepcopy(NESTED))])
    src = "/metrics/children/2/children/0"
    view = SessionView(session, _node(A, src))
    moved = planned_move(view, A, src, Dest("at", ROOT, 0))
    patch = EditTree().act(
        view, action="move", model_id=A, path=src, dest=Dest("at", ROOT, 0)
    )
    assert patch.ops[0].value == moved.tree
    assert moved.path == "/metrics/children/0"
    assert moved.notes == ("the empty warn was removed",)


def test_an_unknown_action_is_refused_before_its_inputs_are_read(session):
    """No `kind` input: the action's name is what is wrong, not a missing
    key (C10)."""
    view = SessionView(session, _node(A, ROOT))
    with pytest.raises(ValueError, match="unknown action 'swap'"):
        EditTree().act(view, action="swap", model_id=A, path=ROOT)


def test_a_move_after_rescore_stages_its_whole_tree_unmarked(two_var_session):
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    # The tree `rescore` writes out is no edit, beside another or alone:
    # no op of it stays for a move to fold onto, and the move's is its own.
    s.stage([PatchOp(A, "add", "/plots", [{"var": "x", "context": []}])])
    s.rescore(A)
    assert [op.path for op in s.spec.pending] == ["/plots"]
    s.apply(_move(s, "/metrics/children/1", Dest("at", ROOT, 0)))
    _plots, pending = s.spec.pending
    assert (pending.op, pending.path, pending.materializes) == ("add", ROOT, False)


def test_a_move_onto_a_criterion_stages_the_wrap(two_var_session):
    from dstf.authoring.tree_move import Dest

    s = two_var_session
    x, y = s.tree(A)["children"]
    patch = _move(s, "/metrics/children/1", Dest("onto", "/metrics/children/0"))
    (op,) = patch.ops
    assert op.value == {
        "combinator": "and",
        "children": [{"combinator": "and", "children": [x, y]}],
    }


# -- every edit says where each node went (U5, R1 / E1) ----------------------


def _q(*idx: int) -> str:
    return ROOT + "".join(f"/children/{i}" for i in idx)


def _r(top: float) -> dict:
    return {"metric": "range", "variable": "x", "max_value": top}


#: and[r9, k-of-n[and[r8], r7], warn[r5]]: every shape an edit can shift.
SHIFTS = {
    "combinator": "and",
    "children": [
        _r(9.0),
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [{"combinator": "and", "children": [_r(8.0)]}, _r(7.0)],
        },
        {"combinator": "warn", "children": [_r(5.0)]},
    ],
}
#: SHIFTS's node paths, each to itself: an edit that moves no node.
UNMOVED = {
    q: q
    for q in (
        ROOT,
        _q(0),
        _q(1),
        _q(1, 0),
        _q(1, 0, 0),
        _q(1, 1),
        _q(2),
        _q(2, 0),
    )
}


async def _apply(user, marker: str) -> None:
    await user.should_see(marker=marker)
    user.find(marker=marker).click()


async def _remove(user, path):
    user.find(marker=f"remove:{path}").click()


async def _add(user, path):
    user.find(marker=f"add:{path}").click()
    await _apply(user, f"apply-add:{path}")


async def _wrap(user, path):
    user.find(marker=f"wrap:{path}").click()
    await _apply(user, f"apply-wrap:{path}")


async def _kind(value, user, path):
    _one(user, f"kind:{path}").set_value(value)


async def _k(user, path):
    user.find(marker="host-click").click()  # its row opens its fields
    await user.should_see(marker=f"apply-edit:{path}")
    (k,) = _labelled(user, ui.number, K)
    k.set_value(2)
    user.find(marker=f"apply-edit:{path}").click()


def _down(q: str, at: str, by: int = 1) -> str:
    """``q`` with the index of its ancestor-or-self that is a sibling AFTER
    the place ``at`` raised by ``by`` — what an insert at ``at`` does."""
    parent, index = at.rsplit("/children/", 1)
    head = parent + "/children/"
    if not q.startswith(head):
        return q
    i, _, rest = q[len(head) :].partition("/")
    if int(i) < int(index):
        return q
    return f"{head}{int(i) + by}" + (f"/{rest}" if rest else "")


@pytest.mark.parametrize(
    ("path", "do", "paths"),
    [
        pytest.param(
            _q(0),
            _remove,
            {ROOT: ROOT, _q(1): _q(0), _q(1, 0): _q(0, 0), _q(1, 0, 0): _q(0, 0, 0)}
            | {_q(1, 1): _q(0, 1), _q(2): _q(1), _q(2, 0): _q(1, 0)},
            id="remove",
        ),
        pytest.param(
            _q(0),
            _add,
            {q: _down(q, _q(1)) for q in UNMOVED},
            id="add-after-a-criterion",
        ),
        pytest.param(_q(1), _add, UNMOVED, id="add-at-a-groups-end"),
        pytest.param(
            _q(1),
            _wrap,
            UNMOVED
            | {
                _q(1): _q(1, 0),
                _q(1, 0): _q(1, 0, 0),
                _q(1, 0, 0): _q(1, 0, 0, 0),
                _q(1, 1): _q(1, 0, 1),
            },
            id="wrap",
        ),
        pytest.param(
            ROOT,
            _wrap,
            {q: _q(0) + q[len(ROOT) :] for q in UNMOVED},
            id="wrap-the-root",
        ),
        pytest.param(
            _q(1),
            partial(_kind, "unwrap…"),
            {ROOT: ROOT, _q(0): _q(0), _q(1, 0): _q(1), _q(1, 0, 0): _q(1, 0)}
            | {_q(1, 1): _q(2), _q(2): _q(3), _q(2, 0): _q(3, 0)},
            id="unwrap-several",
        ),
        pytest.param(
            _q(2),
            partial(_kind, "unwrap…"),
            {q: q for q in UNMOVED if not q.startswith(_q(2))} | {_q(2, 0): _q(2)},
            id="unwrap-one",
        ),
        pytest.param(_q(1), partial(_kind, "or"), UNMOVED, id="kind"),
        pytest.param(_q(1), _k, UNMOVED, id="fields"),
    ],
)
async def test_every_edit_says_where_each_node_went(session, path, do, paths):
    """R1: an edit that went through tells the page each node it kept, its
    path before -> after (`Panel.edited`) — the siblings an add or a remove
    shifts, a wrapped node pushed down, an unwrapped node's children
    lifted — so what the page keys by a node's path follows it."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(SHIFTS))])
    edits: list[dict[str, str]] = []
    async with host(session, EditTree(), _node(D, path), edits=edits) as (
        user,
        errors,
        _f,
    ):
        staged = copy.deepcopy(session.spec.pending)
        await do(user, path)
        await _settled(lambda: bool(edits) or bool(errors))
        assert errors == {}
        # Staged: a root's wrap folds into the pending `add /metrics`.
        assert session.spec.pending != staged
    assert edits == [paths]


async def test_an_add_beside_the_root_leaf_says_it_went_under_the_new_and(session):
    session.stage([PatchOp(D, "add", ROOT, _r(9.0))])
    edits: list[dict[str, str]] = []
    async with host(session, EditTree(), _node(D, ROOT), edits=edits) as (
        user,
        errors,
        _f,
    ):
        await _add(user, ROOT)
        await _settled(lambda: bool(edits))
        assert errors == {}
    assert edits == [{ROOT: _q(0)}]


async def test_the_plots_plus_says_its_add_moved_nothing(session):
    """The plot's `+` appends to the root: every node stays where it was."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(SHIFTS))])
    edits: list[dict[str, str]] = []
    sel = Selection(tests=frozenset({D}), variable="x")
    async with host(session, EditTree(), sel, edits=edits) as (user, errors, _f):
        user.find(marker="add-for:x").click()
        await _apply(user, "apply-add:x")
        await _settled(lambda: bool(edits))
        assert errors == {}
    assert edits == [UNMOVED]


async def test_a_refused_edit_says_nothing_of_where_nodes_went(session):
    """The warn's one child removed: the validator refuses, nothing moved."""
    session.stage([PatchOp(D, "add", ROOT, copy.deepcopy(SHIFTS))])
    edits: list[dict[str, str]] = []
    leaf = _q(2, 0)
    async with host(session, EditTree(), _node(D, leaf), edits=edits) as (
        user,
        errors,
        _f,
    ):
        await _remove(user, leaf)
        await _settled(lambda: "edit-tree" in errors)
    assert edits == []
