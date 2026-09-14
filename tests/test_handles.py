"""The inverse of each mode's handles (``comparison/handles.py``): a dragged
position back to a raw leaf, and §6's "every handle's id resolves to the spec
path it edits" over the Session."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from dstf.authoring.session import Session
from dstf.authoring.spec_document import PatchOp
from dstf.comparison.geometry import Box, HLine, Marker
from dstf.comparison.handles import HandleEdit, HandleEditError
from dstf.comparison.registry import metric
from test_session import A, D, _project

REF = (np.array([0, 0.25, 0.5, 0.75, 1.0]), np.array([0, 0.25, 0.5, 0.75, 1.0]))

TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.5, "upper": 0.1, "lower": 0.2},
                {"time": 0.9, "upper": 0.1, "lower": 0.1},
            ],
        },
        {"metric": "range", "variable": "x", "min_value": -0.5, "max_value": 2.0},
        {
            "metric": "points",
            "variable": "x",
            "points": [
                {"time": 0.25, "tolerance": 0.05},
                {"time": 0.75, "value": 0.75, "tolerance": 0.05},
            ],
        },
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": "max"},
            "min_value": 0.0,
            "max_value": 2.0,
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "rel",
            "tube_points": [{"time": 0.5, "upper": 0.1, "lower": 0.1}],
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "abs",
            "tube_points": [{"time": 0.0, "upper": 1.5, "lower": -0.5}],
        },
    ],
}

PEAKS = {
    "metric": "dominant-frequency",
    "variable": "x",
    "peaks": [{"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"}],
}

BAND, RANGE, POINTS, SCALAR, REL, ABS = TREE["children"]


def _edit(leaf, local, x=None, y=None, reference=REF):
    """Call the leaf's metric's editor, and check it left the input alone."""
    before = copy.deepcopy(leaf)
    try:
        return metric(leaf["metric"]).edit_handle(
            HandleEdit(leaf=leaf, local=local, x=x, y=y, reference=reference)
        )
    finally:
        assert leaf == before


def _with(leaf, **extra):
    return {**copy.deepcopy(leaf), **extra}


class TestTube:
    def test_band(self):
        new = _edit(BAND, "tube/upper/0", 0.5, 0.8)
        assert new["tube_points"][0] == {
            "time": 0.5,
            "upper": pytest.approx(0.3),
            "lower": 0.2,
        }
        new = _edit(BAND, "tube/lower/1", 0.9, 0.6)
        assert new["tube_points"][1]["lower"] == pytest.approx(0.3)
        assert new["tube_points"][1]["upper"] == 0.1
        # Width measured against ref at the NEW time: ref(0.6) = 0.6.
        new = _edit(BAND, "tube/upper/0", 0.6, 0.8)
        assert new["tube_points"][0]["time"] == 0.6
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)
        new = _edit(BAND, "tube/upper/0", None, 0.8)
        assert new["tube_points"][0]["time"] == 0.5
        assert new["tube_points"][0]["upper"] == pytest.approx(0.3)

    def test_rel(self):
        new = _edit(REL, "tube/upper/0", 0.5, 0.6)
        assert new["tube_points"][0]["upper"] == pytest.approx(0.2)
        with pytest.raises(HandleEditError, match="reference is zero"):
            _edit(REL, "tube/upper/0", 0.0, 0.1)

    def test_abs(self):
        new = _edit(ABS, "tube/lower/0", 0.0, -1.0)
        assert new["tube_points"][0]["lower"] == -1.0
        with pytest.raises(HandleEditError, match="below lower"):
            _edit(ABS, "tube/upper/0", 0.0, -0.6)
        with pytest.raises(HandleEditError, match="above upper"):
            _edit(ABS, "tube/lower/0", 0.0, 1.6)

    def test_abs_missing_side_reads_as_zero(self):
        # The side NOT dragged is the missing one, so its 0.0 is what is read.
        leaf = _with(ABS, tube_points=[{"time": 0.0, "upper": 1.0}])
        with pytest.raises(HandleEditError, match="below lower"):
            _edit(leaf, "tube/upper/0", None, -0.1)
        assert _edit(leaf, "tube/upper/0", None, 0.1)["tube_points"][0]["upper"] == 0.1
        leaf = _with(ABS, tube_points=[{"time": 0.0, "lower": -1.0}])
        with pytest.raises(HandleEditError, match="above upper"):
            _edit(leaf, "tube/lower/0", None, 0.1)
        assert (
            _edit(leaf, "tube/lower/0", None, -0.1)["tube_points"][0]["lower"] == -0.1
        )

    def test_negative_width_refused(self):
        with pytest.raises(HandleEditError, match="negative"):
            _edit(BAND, "tube/upper/0", 0.5, 0.4)
        with pytest.raises(HandleEditError, match="negative"):
            _edit(BAND, "tube/lower/0", 0.5, 0.6)
        with pytest.raises(HandleEditError, match="negative"):
            _edit(REL, "tube/upper/0", 0.5, 0.4)

    def test_time_outside_the_reference_span_refused(self):
        with pytest.raises(HandleEditError, match="outside"):
            _edit(BAND, "tube/upper/0", 1.5, 0.8)
        with pytest.raises(HandleEditError, match="outside"):
            _edit(ABS, "tube/upper/0", -0.1, 1.5)

    def test_window_bounds_the_span_by_its_reference_samples(self):
        leaf = _with(BAND, window={"start": 0.6})
        with pytest.raises(HandleEditError, match="outside"):
            _edit(leaf, "tube/upper/1", 0.55, None)
        # Inside the window but before its first reference sample, 0.75.
        with pytest.raises(HandleEditError, match="outside"):
            _edit(leaf, "tube/upper/1", 0.65, None)
        assert _edit(leaf, "tube/upper/1", 0.8, None)["tube_points"][1]["time"] == 0.8
        leaf = _with(BAND, window={"end": 0.6})
        with pytest.raises(HandleEditError, match="outside"):
            _edit(leaf, "tube/upper/0", 0.55, None)
        assert _edit(leaf, "tube/upper/0", 0.5, None)["tube_points"][0]["time"] == 0.5

    def test_window_holding_no_reference_sample_refused(self):
        leaf = _with(BAND, window={"start": 0.3, "end": 0.4})
        with pytest.raises(HandleEditError, match="no reference sample"):
            _edit(leaf, "tube/upper/0", 0.35, None)

    def test_abs_without_reference_uses_the_windows_own_ends(self):
        # abs needs no reference.
        new = _edit(ABS, "tube/upper/0", 0.7, 2.0, reference=None)
        assert new["tube_points"][0] == {"time": 0.7, "upper": 2.0, "lower": -0.5}
        leaf = _with(ABS, window={"start": 0.5})
        assert (
            _edit(leaf, "tube/upper/0", 0.7, None, reference=None)["tube_points"][0][
                "time"
            ]
            == 0.7
        )
        leaf = _with(ABS, window={"end": 0.5})
        with pytest.raises(HandleEditError, match="outside"):
            _edit(leaf, "tube/upper/0", 0.7, None, reference=None)

    def test_band_and_rel_need_the_primary_reference(self):
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(BAND, "tube/upper/0", 0.5, 0.8, reference=None)
        with pytest.raises(HandleEditError, match="needs a reference"):
            _edit(REL, "tube/upper/0", None, 0.6, reference=None)
        with pytest.raises(HandleEditError, match="'other'"):
            _edit(_with(BAND, against="other"), "tube/upper/0", 0.5, 0.8)
        assert _edit(_with(BAND, against="primary"), "tube/upper/0", 0.5, 0.8)

    def test_min_width_is_written_as_dragged(self):
        leaf = _with(BAND, tube_min_width=0.3)
        new = _edit(leaf, "tube/upper/0", 0.5, 0.6)
        assert new["tube_points"][0]["upper"] == pytest.approx(0.1)

    @pytest.mark.parametrize(
        "local",
        [
            "tube/upper/-1",
            "tube/upper/01",
            "tube/upper/x",
            "tube/upper",
            "tube/band",
            "tube/middle/0",
            "tube/upper/0/extra",
            "range/max",
            "",
        ],
    )
    def test_a_local_it_does_not_name_is_refused(self, local):
        # (0.5, 0.3) is a drag child 0's lower handle takes, so a side the
        # editor fell through to is not refused for some other reason.
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(BAND, local, 0.5, 0.3)

    def test_no_such_point(self):
        with pytest.raises(HandleEditError, match="no entry 7"):
            _edit(BAND, "tube/upper/7", 0.5, 0.8)
        with pytest.raises(HandleEditError, match="no entry 0"):
            _edit(_with(BAND, tube_points=None), "tube/upper/0", 0.5, 0.8)


class TestBounds:
    def test_range(self):
        new = _edit(RANGE, "range/max", 123.0, 1.5)
        assert new == {**RANGE, "max_value": 1.5}
        with pytest.raises(HandleEditError, match="exceed"):
            _edit(RANGE, "range/min", None, 3.0)
        with pytest.raises(HandleEditError, match="exceed"):
            _edit(RANGE, "range/max", None, -1.0)
        only_max = {"metric": "range", "variable": "x", "max_value": 2.0}
        assert _edit(only_max, "range/max", None, -50.0)["max_value"] == -50.0
        only_min = {"metric": "range", "variable": "x", "min_value": 2.0}
        assert _edit(only_min, "range/min", None, 50.0)["min_value"] == 50.0

    def test_scalar(self):
        assert _edit(SCALAR, "scalar/min", None, -1.0)["min_value"] == -1.0
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(SCALAR, "scalar/actual", None, 1.0)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(SCALAR, "scalar/reference", None, 1.0)

    @pytest.mark.parametrize(
        ("leaf", "local"),
        [
            (RANGE, "scalar/min"),
            (SCALAR, "range/max"),
            (RANGE, "range/max/outside"),
            (RANGE, "range"),
            ({"variable": "x", "max_value": 1.0}, "range/max"),
        ],
    )
    def test_a_local_it_does_not_name_is_refused(self, leaf, local):
        with pytest.raises(HandleEditError, match="cannot be moved"):
            metric("range").edit_handle(
                HandleEdit(leaf=leaf, local=local, x=None, y=1.0, reference=REF)
            )


class TestPoints:
    def test_a_baseline_following_point_moves_in_time_only(self):
        new = _edit(POINTS, "points/target/0", 0.3, 0.9)
        assert new["points"][0] == {"time": 0.3, "tolerance": 0.05}

    def test_a_point_with_a_value_moves_in_both_axes(self):
        new = _edit(POINTS, "points/target/1", 0.8, 0.7)
        assert new["points"][1] == {"time": 0.8, "value": 0.7, "tolerance": 0.05}

    def test_a_vertical_drag_of_a_following_point_changes_nothing(self):
        assert _edit(POINTS, "points/target/0", 0.25, 0.9) == POINTS

    def test_refusals(self):
        with pytest.raises(HandleEditError, match="no declared point"):
            _edit(POINTS, "points/target/final", 0.3, 0.9)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(POINTS, "points/box/0", 0.3, 0.9)
        with pytest.raises(HandleEditError, match="no entry 2"):
            _edit(POINTS, "points/target/2", 0.3, 0.9)


class TestPeaks:
    def test_declared(self):
        new = _edit(PEAKS, "peaks/declared/0", 2.0, 99.0)
        assert new["peaks"][0] == {
            "freq": 2.0,
            "tolerance": 0.1,
            "tolerance_mode": "rel",
        }

    def test_refusals(self):
        with pytest.raises(HandleEditError, match="positive"):
            _edit(PEAKS, "peaks/declared/0", 0.0, None)
        with pytest.raises(HandleEditError, match="positive"):
            _edit(PEAKS, "peaks/declared/0", -1.0, None)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(PEAKS, "peaks/window/0", 2.0, None)
        with pytest.raises(HandleEditError, match="cannot be moved"):
            _edit(PEAKS, "peaks/detected/0", 2.0, None)


FAMILIES = [
    (BAND, "tube/upper/0", 0.5, 0.8),
    (BAND, "tube/lower/0", 0.5, 0.2),
    (REL, "tube/upper/0", 0.5, 0.6),
    (ABS, "tube/lower/0", 0.0, -1.0),
    (RANGE, "range/min", None, -1.0),
    (RANGE, "range/max", None, 1.0),
    (SCALAR, "scalar/min", None, -1.0),
    (SCALAR, "scalar/max", None, 1.0),
    (POINTS, "points/target/1", 0.8, 0.7),
    (PEAKS, "peaks/declared/0", 2.0, 1.0),
]


@pytest.mark.parametrize(("leaf", "local", "x", "y"), FAMILIES)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_non_finite_position_is_refused(leaf, local, x, y, bad, axis):
    # Each case is accepted with its finite position first, so the refusal
    # below is the non-finite number's, not some other rule's.
    assert _edit(leaf, local, x, y) != leaf
    pos = {"x": x, "y": y, axis: bad}
    with pytest.raises(HandleEditError, match="not a finite number"):
        _edit(leaf, local, pos["x"], pos["y"])


def test_the_builtins_that_have_editors():
    assert metric("nrmse").edit_handle is None
    assert metric("event-timing").edit_handle is None
    for name in ("tube", "range", "scalar", "points", "dominant-frequency"):
        assert metric(name).edit_handle is not None, name


# -- §6: every handle's id resolves to the spec path it edits -----------------


#: The claim under test: the address in the leaf each handle family edits.
ADDRESS = {
    "tube/upper": "/tube_points/{i}",
    "tube/lower": "/tube_points/{i}",
    "range/min": "/min_value",
    "scalar/min": "/min_value",
    "range/max": "/max_value",
    "scalar/max": "/max_value",
    "points/target": "/points/{i}",
    "peaks/declared": "/peaks/{i}",
}


def _address(local: str) -> tuple[str, str] | None:
    """The family and the address a local names, per ``ADDRESS``."""
    for family, template in ADDRESS.items():
        if local == family:
            return family, template
        if local.startswith(family + "/"):
            rest = local[len(family) + 1 :]
            if rest.isdigit():
                return family, template.format(i=rest)
    return None


def _diff(old, new, at: str = ""):
    """Every JSON Pointer at which ``old`` and ``new`` differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            here = f"{at}/{key}"
            if key not in old or key not in new:
                yield here
            else:
                yield from _diff(old[key], new[key], here)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            if i >= len(old) or i >= len(new):
                yield f"{at}/{i}"
            else:
                yield from _diff(old[i], new[i], f"{at}/{i}")
    elif old != new or type(old) is not type(new):
        yield at


def _walk(tree: dict, leaf_path: str) -> dict:
    """The raw node at ``leaf_path`` (a pointer rooted at ``/metrics``)."""
    assert leaf_path == "/metrics" or leaf_path.startswith("/metrics/")
    node = tree
    for part in leaf_path.split("/")[2:]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


@pytest.fixture
def session(tmp_path) -> Session:
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def test_every_handle_id_resolves_to_the_spec_path_it_edits(session):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    session.stage([PatchOp(A, "add", "/metrics", copy.deepcopy(PEAKS))])
    edited: set[str] = set()
    seen_handles = 0
    for model_id in (D, A):
        view = session.view(model_id, "x")
        tree = session.tree(model_id)
        reference = (
            (view.ref_time, view.ref_values) if view.ref_time is not None else None
        )
        for leaf_view in view.leaves:
            raw = _walk(tree, leaf_view.path)
            editor = metric(leaf_view.metric).edit_handle
            for prim in leaf_view.geometry:
                if prim.kind != "handle":
                    continue
                seen_handles += 1
                assert prim.id.startswith(leaf_view.path + "/"), prim.id
                local = prim.id[len(leaf_view.path) + 1 :]
                if isinstance(prim, Marker):
                    x, y = prim.x + 0.01, prim.y + 0.01
                elif isinstance(prim, HLine):
                    x, y = None, prim.value + 0.01
                elif isinstance(prim, Box):
                    x, y = None, prim.y0
                else:
                    pytest.fail(f"unexpected handle primitive {prim!r}")
                assert editor is not None, leaf_view.metric
                try:
                    new = editor(
                        HandleEdit(leaf=raw, local=local, x=x, y=y, reference=reference)
                    )
                except HandleEditError:
                    continue
                named = _address(local)
                assert named is not None, f"{prim.id} edited a leaf with no address"
                family, address = named
                changed = list(_diff(raw, new))
                assert changed, prim.id
                for pointer in changed:
                    assert pointer == address or pointer.startswith(address + "/"), (
                        prim.id,
                        pointer,
                    )
                edited.add(family)
    assert seen_handles
    # A family that only ever raised would make the claim vacuous.
    assert edited == set(ADDRESS)


def _marker(session, model_id, handle_id):
    view = session.view(model_id, "x")
    (prim,) = [p for leaf in view.leaves for p in leaf.geometry if p.id == handle_id]
    return prim


@pytest.mark.parametrize(
    ("child", "local", "x", "y"),
    [
        (0, "tube/upper/0", 0.5, 0.8),
        (4, "tube/upper/0", 0.5, 0.6),
        (1, "range/max", None, 1.5),
    ],
)
def test_staging_the_edit_puts_the_handle_where_it_was_dragged(
    session, child, local, x, y
):
    session.stage([PatchOp(D, "add", "/metrics", copy.deepcopy(TREE))])
    leaf = session.tree(D)["children"][child]
    new = _edit(leaf, local, x, y)
    session.stage([PatchOp(D, "replace", f"/metrics/children/{child}", new)])
    prim = _marker(session, D, f"/metrics/children/{child}/{local}")
    landed = prim.value if isinstance(prim, HLine) else prim.y
    assert landed == pytest.approx(y)
