"""One structural figure golden per registered metric mode (spec §6).

A golden records a figure's SHAPE — how many traces, of which types, under
which ids, on which axes, how long each array is, which layout keys exist —
and never a value. The geometry tests own the values, so a legitimate number
change (a re-run reference, a retuned tolerance) never asks for a re-bless;
only a change to what the figure layer DRAWS does.

Rewrite the goldens with ``UPDATE_GOLDEN=1 uv run pytest
tests/test_figure_goldens.py`` and read the diff before committing them.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from dstf.comparison.algorithms import (
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_scalar,
    _compare_trajectories,
    _compare_tube,
)
from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
from dstf.comparison.registry import metrics
from dstf.figures.plotly import variable_figure
from dstf.figures.view import build_view

#: Every fixture below samples at this rate, so decimation to
#: ``MAX_SAMPLES`` actually happens and the recorded lengths are stable.
N = 1001
MAX_SAMPLES = 200
LEAF_PATH = "/metrics/children/0"
GOLDEN_DIR = Path(__file__).parent / "golden"


def _stamped(vc, name="x"):
    vc.name = name
    stamp_leaf_path(leaf_from_variable(vc), LEAF_PATH)
    return LEAF_PATH, vc


def _signals(scale: float):
    """A fixed reference and an actual that trails it, scaled by ``scale``."""
    t = np.linspace(0.0, 10.0, N)
    ref = np.sin(t)
    return t, ref, (ref + 0.01) * scale


def _view_nrmse(scale: float = 1.0):
    t, ref, act = _signals(scale)
    vc = _compare_trajectories(t, ref, t, act, tolerance=1e-2)
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


def _view_tube(scale: float = 1.0):
    t, ref, act = _signals(scale)
    vc = _compare_tube(
        t,
        ref,
        t,
        act,
        {
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 2.0, "upper": 0.1, "lower": 0.1},
                {"time": 8.0, "upper": 0.2, "lower": 0.2},
            ],
        },
    )
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


def _view_points(scale: float = 1.0):
    t, ref, act = _signals(scale)
    # One checkpoint the actual misses, one it meets.
    vc = _compare_points(
        t,
        ref,
        t,
        act,
        points=[{"time": 2.0, "tolerance": 0.005}, {"time": 8.0, "tolerance": 0.5}],
    )
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


def _view_range(scale: float = 1.0):
    # Baseline-free: the bounds come from the spec, so the view has no
    # reference and therefore no error series.
    t, _, act = _signals(scale)
    vc = _compare_range(t, act, min_value=-2.0, max_value=1.5)
    return build_view("x", act_time=t, act_values=act, leaves=[_stamped(vc)])


def _view_scalar(scale: float = 1.0):
    t, ref, act = _signals(scale)
    vc = _compare_scalar(
        t,
        ref,
        t,
        act,
        reduction="max",
        tolerance=0.05,
        tolerance_mode="rel",
        min_value=None,
        max_value=None,
    )
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


def _view_event_timing(scale: float = 1.0):
    # Modelica writes an event as two samples at one instant; the actual
    # reproduces it 0.5 ms late, inside a 1 ms window.
    t = np.linspace(0.0, 10.0, N)
    ref_time = np.insert(t, 501, t[500])
    act_time = np.insert(t, 501, [t[500] + 5e-4, t[500] + 5e-4])
    ref_values = np.where(ref_time >= t[500], 1.0, 0.0)
    ref_values[500] = 0.0
    act_values = np.where(act_time >= t[500] + 5e-4, 1.0, 0.0) * scale
    vc = _compare_event_timing(ref_time, act_time, time_tolerance=1e-3)
    return build_view(
        "x",
        act_time=act_time,
        act_values=act_values,
        ref_time=ref_time,
        ref_values=ref_values,
        leaves=[_stamped(vc)],
    )


def _view_dominant_frequency(scale: float = 1.0):
    t = np.linspace(0.0, 10.0, N)
    ref = np.sin(2 * np.pi * 2.0 * t)
    act = ref * scale
    # 2 Hz is in the signal, 5 Hz is not: one matched peak, one missed.
    vc = _compare_dominant_frequency(
        t,
        ref,
        t,
        act,
        peaks=[{"freq": 2.0, "tolerance": 0.05}, {"freq": 5.0, "tolerance": 0.02}],
    )
    return build_view(
        "x",
        act_time=t,
        act_values=act,
        ref_time=t,
        ref_values=ref,
        leaves=[_stamped(vc)],
    )


#: One fixture per registered mode. A new metric without an entry here fails
#: :func:`test_every_registered_mode_has_a_figure_golden`.
VIEWS = {
    "nrmse": _view_nrmse,
    "tube": _view_tube,
    "points": _view_points,
    "range": _view_range,
    "scalar": _view_scalar,
    "event-timing": _view_event_timing,
    "dominant-frequency": _view_dominant_frequency,
}


def _slug(mode: str) -> str:
    return mode.replace("-", "_")


def _golden_path(mode: str) -> Path:
    return GOLDEN_DIR / f"figure_{_slug(mode)}.json"


def _trace_id(meta: dict) -> str | None:
    """A trace's handle: its primitive id, the family of a grouped marker
    trace, or the bound an invisible autorange anchor stands in for."""
    if "id" in meta:
        return meta["id"]
    if "family" in meta:
        return meta["family"]
    if "anchor_for" in meta:
        return "anchor:" + meta["anchor_for"]
    return None


def _structure(fig: dict) -> dict:
    """The figure reduced to what a golden asserts — no numbers from the data."""
    return {
        "traces": [
            {
                "type": tr.get("type"),
                "mode": tr.get("mode"),
                "name": tr.get("name"),
                "id": _trace_id(tr.get("meta") or {}),
                "n": len(tr.get("x") or []),
                "xaxis": tr.get("xaxis"),
                "yaxis": tr.get("yaxis"),
            }
            for tr in fig["data"]
        ],
        "shapes": [
            {
                "type": s.get("type"),
                "name": s.get("name"),
                "xref": s.get("xref"),
                "yref": s.get("yref"),
            }
            for s in fig["layout"]["shapes"]
        ],
        "layout_keys": sorted(fig["layout"]),
    }


def _dump(structure: dict) -> str:
    return json.dumps(structure, indent=2) + "\n"


def _diff(mode: str, want: dict, got: dict) -> str:
    lines = difflib.unified_diff(
        _dump(want).splitlines(),
        _dump(got).splitlines(),
        fromfile=str(_golden_path(mode)),
        tofile=f"variable_figure({mode})",
        lineterm="",
    )
    return (
        f"{mode}: the figure's structure no longer matches its golden.\n"
        "Values are not recorded here, so this is a change to WHAT is drawn.\n"
        "Re-bless with UPDATE_GOLDEN=1 once the change is intended.\n"
        + "\n".join(lines)
    )


@pytest.mark.parametrize("mode", list(VIEWS))
def test_figure_structure_matches_its_golden(mode):
    got = _structure(variable_figure(VIEWS[mode](), max_samples=MAX_SAMPLES))
    path = _golden_path(mode)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(_dump(got), encoding="utf-8", newline="\n")
    assert path.exists(), f"no golden for {mode}; run with UPDATE_GOLDEN=1"
    want = json.loads(path.read_text(encoding="utf-8"))
    assert got == want, _diff(mode, want, got)


@pytest.mark.parametrize("mode", list(VIEWS))
def test_a_changed_value_does_not_change_the_structure(mode):
    """Scaling the actual by two moves every number and several verdicts;
    a golden that needed re-blessing for that would be re-blessed blindly."""
    plain = _structure(variable_figure(VIEWS[mode](), max_samples=MAX_SAMPLES))
    scaled = _structure(
        variable_figure(VIEWS[mode](scale=2.0), max_samples=MAX_SAMPLES)
    )
    assert scaled == plain, _diff(mode, plain, scaled)


def test_every_registered_mode_has_a_figure_golden():
    on_disk = {p.stem[len("figure_") :] for p in GOLDEN_DIR.glob("figure_*.json")}
    registered = {_slug(desc.name) for desc in metrics()}
    assert registered <= on_disk, (
        f"registered modes with no figure golden: {sorted(registered - on_disk)}"
    )
