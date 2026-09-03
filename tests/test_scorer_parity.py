"""Python <-> JS scorer parity tests.

The HTML report's live-edit UX (Phase 6 reporter-as-IDE) requires the
browser to recompute pass/fail as the user drags tube widths, edits
tolerances, etc. — without a server round-trip. So ``interactive.js``
re-implements the simple-math scorers in JS (``MODE_SCORERS`` table).
The vision doc (vision.md:106) calls this a deliberate split: simple
algorithms (``nrmse`` / ``tube`` / ``range`` / ``points``) get JS
recompute; ``event-timing`` is CLI-authoritative; ``dominant-frequency``
gets a live scorer via the ported power-of-2 FFT.

These tests catch *drift* between the Python and JS implementations.
Each fixture builds a ref/act trajectory pair, runs the actual Python
``_compare_*`` function for the authoritative verdict, then evaluates
``MODE_SCORERS`` from the JS side and asserts both sides reach the same
verdict.

Two JS execution paths (review 2026-07-06: the parity suite previously
only ran under Playwright, so environments without browsers never
executed it at all):

* **Node** (``test_js_scorers_agree_with_python_node``) — loads
  ``templates/scorers.js`` ALONE into a bare ``node`` process and calls
  ``MODE_SCORERS`` directly. That fragment loads DOM-free (its pill updater touches the DOM only at call time) (it only reads
  ``window.MT_REPORT`` at load), so the parity path needs no document
  stub — which is the point of keeping the scorers in their own file.
  Skipped when ``node`` is not on PATH.
* **Playwright** (``test_js_scorers_agree_with_python``) — renders the
  full interactive.html and evaluates in Chromium. Skipped when
  Playwright isn't installed.

To extend: add a row to ``_PARITY_CASES`` (mode, expected verdict,
params, trajectory-builder key, optional window). The same row drives
the Python truth and both JS verdicts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dstf.reporting.report_suite import interactive_js_source, write_report_assets

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "dstf" / "reporting" / "templates"
)
# The scorer parity path loads this fragment on its own; the two
# state/trust node tests below exercise editors + export and need the whole
# concatenated bundle (``interactive_js_source()``).
_SCORERS_JS = _TEMPLATE_DIR / "scorers.js"

_NODE = shutil.which("node")


# ---------------------------------------------------------------------------
# Fixture trajectories — small synthetic signals with known scores
# ---------------------------------------------------------------------------


def _linspace(n: int = 50) -> np.ndarray:
    return np.linspace(0.0, 1.0, n)


def _traj(rt: np.ndarray, rv: np.ndarray, at: np.ndarray, av: np.ndarray) -> dict:
    return {
        "ref_time": rt.tolist(),
        "ref_values": rv.tolist(),
        "act_time": at.tolist(),
        "act_values": av.tolist(),
    }


def _ramp(offset: float) -> dict:
    t = _linspace()
    ref = 0.5 + 0.3 * t
    return _traj(t, ref, t, ref + offset)


def _sine_tube(offset: float) -> dict:
    # ref = sine shifted +1.5; |ref| in [1.1, 1.9] keeps rel tubes meaningful.
    t = _linspace()
    ref = 1.5 + 0.4 * np.sin(2 * np.pi * t)
    return _traj(t, ref, t, ref + offset)


def _range_signal(inside: bool) -> dict:
    t = _linspace()
    if inside:
        act = 0.5 + 0.1 * np.sin(2 * np.pi * t)  # within [0, 1]
    else:
        act = -0.5 + 2.0 * t  # exits [0, 1] at both ends
    return _traj(t, act.copy(), t, act)


def _const_final(matching: bool) -> dict:
    t = _linspace()
    ref = 0.5 * np.ones_like(t)
    if matching:
        act = ref + 1e-4
    else:
        act = ref.copy()
        act[-1] = 1.5
    return _traj(t, ref, t, act)


def _const_large() -> dict:
    # Large-magnitude constant: Python normalizes by |ref| when the range
    # collapses (review 2026-07-06 finding 42) — rmse=1000 on a 3.7e10
    # constant is a 2.7e-8 relative error and must PASS at tol 1e-4.
    t = _linspace()
    ref = 3.7e10 * np.ones_like(t)
    return _traj(t, ref, t, ref + 1000.0)


def _const_off_by_one() -> dict:
    t = _linspace()
    ref = 5.0 * np.ones_like(t)
    return _traj(t, ref, t, ref + 1.0)


def _event_jump() -> dict:
    # Duplicate-time jump 1 → 0 at t=0.5 in BOTH ref and act (identical
    # arrays). Piecewise event segmentation scores this as zero error;
    # naive interpolation across the discontinuity inflates the NRMSE
    # (review 2026-07-06 finding 42).
    t1 = np.linspace(0.0, 0.5, 26)
    t2 = np.linspace(0.5, 1.0, 26)
    t = np.concatenate([t1, t2])
    v = np.concatenate([np.ones(26), np.zeros(26)])
    return _traj(t, v, t.copy(), v.copy())


def _step_offset() -> dict:
    # act = ref + 0.3 for t >= 0.45; discriminates linear vs constant
    # tube-width interpolation between control points at t=0 (w=0.05)
    # and t=0.6 (w=0.5).
    t = _linspace()
    ref = 1.5 + 0.4 * np.sin(2 * np.pi * t)
    act = ref + 0.3 * (t >= 0.45)
    return _traj(t, ref, t, act)


def _short_ref_long_act() -> dict:
    # ref covers [0, 1]; act covers [0, 2] — a ref-relative checkpoint at
    # t=1.5 lies outside the reference range and must FAIL, not clamp.
    tr = _linspace()
    ta = np.linspace(0.0, 2.0, 100)
    ref = 0.5 + 0.3 * tr
    act = 0.5 + 0.3 * ta
    return _traj(tr, ref, ta, act)


def _empty_act() -> dict:
    t = _linspace()
    ref = 0.5 + 0.3 * t
    return _traj(t, ref, np.array([]), np.array([]))


def _tone(freqs_amps: list[tuple[float, float]]) -> dict:
    # Sum of tones over a 1-second window with 256 samples — power-of-2
    # resample grids are then bit-identical on both sides.
    t = np.linspace(0.0, 1.0, 256)
    act = np.zeros_like(t)
    for f, a in freqs_amps:
        act = act + a * np.sin(2.0 * np.pi * f * t)
    ref = act.copy()
    return _traj(t, ref, t, act)


_TRAJ_BUILDERS = {
    "ramp_identical": lambda: _ramp(0.0),
    "ramp_small": lambda: _ramp(1e-4),
    "ramp_large": lambda: _ramp(5e-2),
    "sine_off_002": lambda: _sine_tube(0.02),
    "sine_off_0052": lambda: _sine_tube(0.052),
    "sine_off_02": lambda: _sine_tube(0.2),
    "sine_off_005": lambda: _sine_tube(0.05),
    "sine_off_05": lambda: _sine_tube(0.5),
    "range_inside": lambda: _range_signal(True),
    "range_outside": lambda: _range_signal(False),
    "const_final_match": lambda: _const_final(True),
    "const_final_miss": lambda: _const_final(False),
    "const_large": _const_large,
    "const_off_by_one": _const_off_by_one,
    "event_jump": _event_jump,
    "step_offset": _step_offset,
    "short_ref_long_act": _short_ref_long_act,
    "empty_act": _empty_act,
    "tone5": lambda: _tone([(5.0, 1.0)]),
    "tone5_12": lambda: _tone([(5.0, 1.0), (12.0, 0.5)]),
    "tone5_20_tiny": lambda: _tone([(5.0, 1.0), (20.0, 0.002)]),
    "tone5_20_strong": lambda: _tone([(5.0, 1.0), (20.0, 0.3)]),
}


# ---------------------------------------------------------------------------
# Test cases — (id, mode, expected verdict, params, traj, window)
# ---------------------------------------------------------------------------

# Each row generates one leaf in the rendered report. The Python
# authoritative verdict is computed by running the matching ``_compare_*``
# function from ``comparison/algorithms.py`` against the fixture trajectory
# (window pre-sliced the same way tree_eval does); the JS verdict comes
# from evaluating ``MODE_SCORERS[mode](leaf)``. Verdicts must match — and
# the Python verdict itself is asserted against ``verdict`` to catch
# fixture rot.

_TUBE_REL_PTS = [
    {"time": 0.0, "upper": 0.05, "lower": 0.05},
    {"time": 1.0, "upper": 0.05, "lower": 0.05},
]
_TUBE_BAND_PTS = [
    {"time": 0.0, "upper": 0.1, "lower": 0.1},
    {"time": 1.0, "upper": 0.1, "lower": 0.1},
]
_TUBE_STEP_PTS = [
    {"time": 0.0, "upper": 0.05, "lower": 0.05},
    {"time": 0.6, "upper": 0.5, "lower": 0.5},
]

_PARITY_CASES: list[dict[str, Any]] = [
    # ---- original coverage --------------------------------------------
    {
        "id": "nrmse-pass",
        "mode": "nrmse",
        "verdict": "pass",
        "params": {"tolerance": 1e-2},
        "traj": "ramp_small",
    },
    {
        "id": "nrmse-fail",
        "mode": "nrmse",
        "verdict": "fail",
        "params": {"tolerance": 1e-3},
        "traj": "ramp_large",
    },
    {
        "id": "tube-rel-scalar-pass",
        "mode": "tube",
        "verdict": "pass",
        "params": {
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
            "tube_abs": 0,
            "tube_min_width": 0,
        },
        "traj": "sine_off_002",
    },
    {
        "id": "tube-rel-scalar-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
            "tube_abs": 0,
            "tube_min_width": 0,
        },
        "traj": "sine_off_05",
    },
    {
        "id": "range-pass",
        "mode": "range",
        "verdict": "pass",
        "params": {"min_value": 0.0, "max_value": 1.0},
        "traj": "range_inside",
    },
    {
        "id": "range-fail",
        "mode": "range",
        "verdict": "fail",
        "params": {"min_value": 0.0, "max_value": 1.0},
        "traj": "range_outside",
    },
    {
        # D107: no bounds is a config error, not "unbounded → everything
        # passes". Python raises from RangeMode.__init__; the JS scorer
        # returns false rather than passing a leaf that never scored.
        "id": "range-no-bounds-fail",
        "mode": "range",
        "verdict": "fail",
        "params": {},
        "traj": "range_inside",
    },
    {
        "id": "points-final-pass",
        "mode": "points",
        "verdict": "pass",
        "params": {"tolerance": 1e-3, "points": []},
        "traj": "const_final_match",
    },
    {
        "id": "points-final-fail",
        "mode": "points",
        "verdict": "fail",
        "params": {"tolerance": 1e-3, "points": []},
        "traj": "const_final_miss",
    },
    {
        "id": "domfreq-pass",
        "mode": "dominant-frequency",
        "verdict": "pass",
        "params": {"peaks": [{"freq": 5.0, "tolerance": 0.5, "tolerance_mode": "abs"}]},
        "traj": "tone5",
    },
    {
        "id": "domfreq-fail",
        "mode": "dominant-frequency",
        "verdict": "fail",
        "params": {
            "peaks": [{"freq": 12.0, "tolerance": 0.5, "tolerance_mode": "abs"}]
        },
        "traj": "tone5",
    },
    # ---- review 2026-07-06 finding 42: constant-signal normalization +
    # ---- event segmentation --------------------------------------------
    {
        "id": "nrmse-const-large-pass",
        "mode": "nrmse",
        "verdict": "pass",
        "params": {"tolerance": 1e-4},
        "traj": "const_large",
    },
    {
        "id": "nrmse-const-fail",
        "mode": "nrmse",
        "verdict": "fail",
        "params": {"tolerance": 1e-4},
        "traj": "const_off_by_one",
    },
    {
        "id": "nrmse-event-jump-pass",
        "mode": "nrmse",
        "verdict": "pass",
        "params": {"tolerance": 1e-6},
        "traj": "event_jump",
    },
    # ---- review 2026-07-06 finding 1/6 parity: empty windows FAIL ------
    {
        "id": "nrmse-empty-window-fail",
        "mode": "nrmse",
        "verdict": "fail",
        "params": {"tolerance": 1e-2},
        "traj": "ramp_small",
        "window": {"start": 2.0, "end": 3.0},
    },
    {
        "id": "nrmse-window-pass",
        "mode": "nrmse",
        "verdict": "pass",
        "params": {"tolerance": 1e-2},
        "traj": "ramp_small",
        "window": {"start": 0.2, "end": 0.8},
    },
    {
        "id": "tube-empty-window-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {"tube_width_mode": "rel", "tube_rel": 0.05},
        "traj": "sine_off_002",
        "window": {"start": 2.0, "end": 3.0},
    },
    {
        "id": "range-empty-window-fail",
        "mode": "range",
        "verdict": "fail",
        "params": {"min_value": 0.0, "max_value": 1.0},
        "traj": "range_inside",
        "window": {"start": 5.0, "end": 6.0},
    },
    # ---- review 2026-07-06 finding 40: tube_points × width mode ×
    # ---- interpolation ---------------------------------------------------
    # rel-mode points: width 0.05·|ref| >= 0.055 everywhere, so offset
    # 0.052 passes — the pre-fix JS scored control points as band offsets
    # (width 0.05 < 0.052 → false FAIL).
    {
        "id": "tube-points-rel-pass",
        "mode": "tube",
        "verdict": "pass",
        "params": {"tube_width_mode": "rel", "tube_points": _TUBE_REL_PTS},
        "traj": "sine_off_0052",
    },
    {
        "id": "tube-points-rel-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {"tube_width_mode": "rel", "tube_points": _TUBE_REL_PTS},
        "traj": "sine_off_02",
    },
    {
        "id": "tube-points-band-pass",
        "mode": "tube",
        "verdict": "pass",
        "params": {"tube_width_mode": "band", "tube_points": _TUBE_BAND_PTS},
        "traj": "sine_off_005",
    },
    {
        "id": "tube-points-band-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {"tube_width_mode": "band", "tube_points": _TUBE_BAND_PTS},
        "traj": "sine_off_02",
    },
    # abs-mode points: literal y-bounds. act in [1.12, 1.92]: inside
    # [0.5, 2.5] → pass; above [0.0, 1.0] → fail (the pre-fix JS treated
    # the bounds as band offsets → false PASS).
    {
        "id": "tube-points-abs-pass",
        "mode": "tube",
        "verdict": "pass",
        "params": {
            "tube_width_mode": "abs",
            "tube_points": [
                {"time": 0.0, "upper": 2.5, "lower": 0.5},
                {"time": 1.0, "upper": 2.5, "lower": 0.5},
            ],
        },
        "traj": "sine_off_002",
    },
    {
        "id": "tube-points-abs-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {
            "tube_width_mode": "abs",
            "tube_points": [
                {"time": 0.0, "upper": 1.0, "lower": 0.0},
                {"time": 1.0, "upper": 1.0, "lower": 0.0},
            ],
        },
        "traj": "sine_off_002",
    },
    # Interpolation: linear widths ramp up early enough to admit the step
    # offset; stepwise (constant) holds 0.05 until t=0.6 and fails.
    {
        "id": "tube-interp-linear-pass",
        "mode": "tube",
        "verdict": "pass",
        "params": {
            "tube_width_mode": "band",
            "tube_points": _TUBE_STEP_PTS,
            "tube_interpolation": "linear",
        },
        "traj": "step_offset",
    },
    {
        "id": "tube-interp-constant-fail",
        "mode": "tube",
        "verdict": "fail",
        "params": {
            "tube_width_mode": "band",
            "tube_points": _TUBE_STEP_PTS,
            "tube_interpolation": "constant",
        },
        "traj": "step_offset",
    },
    # ---- review 2026-07-06 finding 43: points-mode hard-fail paths -----
    {
        "id": "points-clipped-fail",
        "mode": "points",
        "verdict": "fail",
        "params": {
            "tolerance": 0.5,
            "points": [{"time": 5.0, "value": 0.6, "tolerance": 0.5}],
        },
        "traj": "ramp_small",
    },
    {
        "id": "points-outside-ref-fail",
        "mode": "points",
        "verdict": "fail",
        "params": {"tolerance": 0.5, "points": [{"time": 1.5, "tolerance": 0.5}]},
        "traj": "short_ref_long_act",
    },
    {
        "id": "points-empty-actual-fail",
        "mode": "points",
        "verdict": "fail",
        "params": {
            "tolerance": 0.5,
            "points": [{"time": 0.5, "value": 0.65, "tolerance": 0.5}],
        },
        "traj": "empty_act",
    },
    {
        "id": "points-multi-pass",
        "mode": "points",
        "verdict": "pass",
        "params": {
            "tolerance": 0.1,
            "points": [
                {"time": 0.3, "tolerance": 0.1},
                {"time": 0.7, "tolerance": 0.1},
            ],
        },
        "traj": "ramp_small",
    },
    # ---- review 2026-07-06 finding 44: claiming + amplitude floor ------
    # Two declared peaks, one actual resonance: the second declared peak
    # cannot claim the already-claimed actual peak → FAIL.
    {
        "id": "domfreq-claiming-fail",
        "mode": "dominant-frequency",
        "verdict": "fail",
        "params": {
            "peaks": [
                {"freq": 5.0, "tolerance": 0.5, "tolerance_mode": "abs"},
                {"freq": 5.2, "tolerance": 0.5, "tolerance_mode": "abs"},
            ]
        },
        "traj": "tone5",
    },
    # Two real resonances, two declared peaks: claiming must not break the
    # good path.
    {
        "id": "domfreq-two-peaks-pass",
        "mode": "dominant-frequency",
        "verdict": "pass",
        "params": {
            "peaks": [
                {"freq": 5.0, "tolerance": 0.6, "tolerance_mode": "abs"},
                {"freq": 12.0, "tolerance": 0.6, "tolerance_mode": "abs"},
            ]
        },
        "traj": "tone5_12",
    },
    # A 0.2%-amplitude blip is below the 1% noise floor → FAIL; the same
    # peak at 30% amplitude passes.
    {
        "id": "domfreq-amp-floor-fail",
        "mode": "dominant-frequency",
        "verdict": "fail",
        "params": {
            "peaks": [
                {"freq": 20.0, "tolerance": 1.0, "tolerance_mode": "abs"},
            ]
        },
        "traj": "tone5_20_tiny",
    },
    {
        "id": "domfreq-amp-floor-pass",
        "mode": "dominant-frequency",
        "verdict": "pass",
        "params": {
            "peaks": [
                {"freq": 20.0, "tolerance": 1.0, "tolerance_mode": "abs"},
            ]
        },
        "traj": "tone5_20_strong",
    },
    # review 2026-07-06 finding 76b: omitted tolerance defaults to 0.01 on
    # BOTH sides (Python declared.get("tolerance", 0.01)); the JS default
    # used to be 0, failing every default-tolerance peak.
    {
        "id": "domfreq-default-tol-pass",
        "mode": "dominant-frequency",
        "verdict": "pass",
        "params": {"peaks": [{"freq": 5.0, "tolerance_mode": "rel"}]},
        "traj": "tone5",
    },
    # ---- explicit tolerance of 0 --------------------------------------
    # 0 is the strictest possible tolerance, and Python honors it: the
    # pass rule is a strict ``nrmse < tolerance`` / ``delta < tolerance``,
    # so even identical signals FAIL. ``tolerance_used`` carries the
    # pre-edit tolerance the CLI scored with — exactly what a user sees
    # after typing 0 into the reporter — so a JS helper that treats 0 as
    # "unset" would silently fall back to it and pass.
    {
        "id": "nrmse-zero-tolerance-fail",
        "mode": "nrmse",
        "verdict": "fail",
        "params": {"tolerance": 0},
        "tolerance_used": 1e-2,
        "traj": "ramp_identical",
    },
    {
        "id": "points-final-zero-tolerance-fail",
        "mode": "points",
        "verdict": "fail",
        "params": {"tolerance": 0, "points": []},
        "tolerance_used": 1e-2,
        "traj": "ramp_identical",
    },
    # A NEGATIVE tolerance is authoring nonsense, but the exported patch
    # carries it raw and the CLI fails everything with it — so the live
    # pill must too. A JS guard that clamps or falls back on negatives
    # (review of the zero-tolerance fix) would show green here.
    {
        "id": "nrmse-negative-tolerance-fail",
        "mode": "nrmse",
        "verdict": "fail",
        "params": {"tolerance": -0.01},
        "tolerance_used": 1e-2,
        "traj": "ramp_identical",
    },
]


def _trajectory_for(case: dict) -> dict:
    return _TRAJ_BUILDERS[case["traj"]]()


# ---------------------------------------------------------------------------
# Python authoritative scoring — call the actual comparator functions
# ---------------------------------------------------------------------------


def _slice_window_like_tree_eval(
    t: np.ndarray, v: np.ndarray, window: dict | None
) -> tuple[np.ndarray, np.ndarray]:
    """Mirror tree_eval._slice_window: inclusive [start, end]; a window
    that excludes everything yields empty arrays (the mode then fails)."""
    if not window or len(t) == 0:
        return t, v
    mask = np.ones(len(t), dtype=bool)
    if window.get("start") is not None:
        mask &= t >= window["start"]
    if window.get("end") is not None:
        mask &= t <= window["end"]
    if not mask.any():
        return t[:0], v[:0]
    return t[mask], v[mask]


def _python_verdict(case: dict) -> bool:
    """Run the Python ``_compare_*`` function for *case* and return the
    pass/fail boolean. Mirrors the path the CLI takes (window slicing
    included); if drift exists, this is the side users get when they run
    ``dstf run``.
    """
    from dstf.comparison import comparator as cmp

    mode = case["mode"]
    params = case["params"]
    window = case.get("window")
    traj = _trajectory_for(case)
    rt = np.array(traj["ref_time"])
    rv = np.array(traj["ref_values"])
    at = np.array(traj["act_time"])
    av = np.array(traj["act_values"])
    rt, rv = _slice_window_like_tree_eval(rt, rv, window)
    at, av = _slice_window_like_tree_eval(at, av, window)

    if mode == "nrmse":
        result = cmp._compare_trajectories(rt, rv, at, av, params["tolerance"])
        return bool(result.passed)
    if mode == "tube":
        result = cmp._compare_tube(rt, rv, at, av, params)
        return bool(result.passed)
    if mode == "range":
        # Through RangeMode, not _compare_range directly: the "no bounds
        # declared" guard lives in the mode's constructor, and a leaf that
        # never built is a FAIL on both sides (D107).
        from dstf.comparison.modes import RangeConfig, RangeMode

        try:
            mode_obj = RangeMode(
                RangeConfig(
                    min_value=params.get("min_value"),
                    max_value=params.get("max_value"),
                )
            )
        except ValueError as exc:
            # Only the opted-in no-bounds case may raise here. A future
            # construction-time raise on a BOUNDED case would otherwise be
            # silently read as "both sides fail" — i.e. false parity.
            assert "at least one of min_value / max_value" in str(exc), (
                f"{case['id']}: unexpected raise from RangeMode: {exc}"
            )
            return False
        return bool(mode_obj.compare(rt, rv, at, av).passed)
    if mode == "points":
        result = cmp._compare_points(
            rt,
            rv,
            at,
            av,
            points=params.get("points") or [],
            tolerance=params["tolerance"],
        )
        return bool(result.passed)
    if mode == "dominant-frequency":
        result = cmp._compare_dominant_frequency(
            rt,
            rv,
            at,
            av,
            peaks=params.get("peaks") or [],
        )
        return bool(result.passed)
    raise ValueError(f"unknown mode: {mode}")


def test_python_verdicts_match_declared_expectations():
    """Fixture-rot guard: the declared ``verdict`` in each case must match
    what the Python comparator actually computes — otherwise a parity
    "agreement" could be two implementations agreeing on the wrong thing.
    """
    mismatches = []
    for case in _PARITY_CASES:
        py = _python_verdict(case)
        expected = case["verdict"] == "pass"
        if py != expected:
            mismatches.append(
                f"{case['id']}: declared {case['verdict']}, Python computed "
                f"{'pass' if py else 'fail'}"
            )
    assert not mismatches, "Fixture verdicts drifted:\n  " + "\n  ".join(mismatches)


# ---------------------------------------------------------------------------
# Shared fixture context (drives both the Node and Playwright paths)
# ---------------------------------------------------------------------------


def _build_leaf(idx: int, case: dict, expected: bool) -> dict:
    """Synthesize a leaf dict shaped like what the reporter writes into
    ``window.MT_REPORT.TREE_VIEW.children[i]``. Only the fields the JS
    ``MODE_SCORERS`` actually read need to be accurate.
    """
    var = f"v{idx}"
    return {
        "kind": "leaf",
        "path": f"/metrics/children/{idx}",
        "metric": case["mode"],
        "variable": var,
        "params": dict(case["params"]),
        "against": "primary",
        "window": dict(case.get("window") or {}),
        "children": [],
        # Field below set to the Python verdict — JS recompute should
        # arrive at the same answer despite this being merely a hint;
        # we explicitly do NOT use ``leaf.passed`` as a fallback in the
        # parity assertion.
        "passed": expected,
        "score": 0.0,
        "label": var,
        "name": var,
        "mode_effective": case["mode"],
        "nrmse": 0.0,
        "rmse": 0.0,
        "signal_range": 1.0,
        "max_abs_error": 0.0,
        "max_abs_error_time": 0.0,
        "reference_final": 0.0,
        "actual_final": 0.0,
        "is_constant": False,
        # The CLI-computed tolerance the leaf was scored with. Cases may
        # override it to differ from the live param (see the
        # zero-tolerance rows) so a JS fallback to it is detectable.
        "tolerance_used": case.get(
            "tolerance_used", case["params"].get("tolerance", 1e-4)
        ),
        "score_display": "",
        "criterion": "",
        "tube_points_inside": None,
        "tube_worst_violation": None,
        "tube_worst_violation_time": None,
        "mode_values": dict(case["params"]),
        "mode_controls_html": "",
        "window_controls_html": "",
        "window_values": {},
        "cli_authoritative": False,
    }


def _build_fixture_context() -> tuple[dict, list[bool]]:
    """Render context + the list of Python-authoritative verdicts (one
    per leaf, in tree order). The verdicts are returned alongside so the
    test can ground-truth the JS side against them.
    """
    leaves = []
    expected_verdicts = []
    variables_by_name: dict[str, dict] = {}

    for idx, case in enumerate(_PARITY_CASES):
        verdict = _python_verdict(case)
        expected_verdicts.append(verdict)
        leaves.append(_build_leaf(idx, case, verdict))
        var = f"v{idx}"
        variables_by_name[var] = {
            "name": var,
            "trajectory": _trajectory_for(case),
            "overlays": [],
            "leaf_paths": [f"/metrics/children/{idx}"],
        }

    tree = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": all(expected_verdicts),
        "label": f"and[{len(leaves)}]",
        "children": leaves,
    }

    context = {
        "model_id": "Fixture.ScorerParity",
        "n_passed": sum(1 for v in expected_verdicts if v),
        "sim_failed": False,
        "last_run_at": 0,
        "last_run_str": "",
        "warnings": [],
        "key_stats": {},
        "ref_info": [],
        "sim_params": [],
        "statistics_sections": [],
        "diagnostic_summaries": [],
        "artifacts": [],
        "trajectories": [v["trajectory"] for v in variables_by_name.values()],
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": variables_by_name,
        "mode_schemas": {},
        "overlay_rows": [],
    }
    return context, expected_verdicts


_WALK_LEAVES_JS = """
    const out = [];
    (function walk(node) {
        if (!node) return;
        if (node.kind === 'leaf') {
            const fn = MODE_SCORERS[node.metric];
            const verdict = fn ? !!fn(node) : null;
            out.push({path: node.path, metric: node.metric, jsVerdict: verdict});
        } else if (Array.isArray(node.children)) {
            for (const c of node.children) walk(c);
        }
    })(TREE_VIEW);
"""


def _assert_verdicts_agree(js_verdicts: list[dict], expected: list[bool]) -> None:
    assert len(js_verdicts) == len(expected), (
        f"Tree walk returned {len(js_verdicts)} leaves, expected {len(expected)}. "
        f"Got: {js_verdicts}"
    )
    disagreements = []
    for case, py_verdict, js_entry in zip(_PARITY_CASES, expected, js_verdicts):
        if js_entry["jsVerdict"] is None:
            disagreements.append(
                f"{case['id']} ({case['mode']}): no JS scorer registered "
                f"(MODE_SCORERS['{case['mode']}'] is undefined)"
            )
        elif js_entry["jsVerdict"] != py_verdict:
            disagreements.append(
                f"{case['id']} ({case['mode']}): "
                f"Python={py_verdict}, JS={js_entry['jsVerdict']} "
                f"— params={case['params']}"
            )
    assert not disagreements, "Python <-> JS scorer drift detected:\n  " + "\n  ".join(
        disagreements
    )


# ---------------------------------------------------------------------------
# Node runner — no browser needed
# ---------------------------------------------------------------------------

# scorers.js touches no DOM at load — it reads window.MT_REPORT and builds
# leafState — so the parity runner hands it a window and nothing else. Keeping
# that stub this small is the standing check that the scoring math stays
# browser-independent: if it ever needs `document`, the scorers grew a UI
# dependency and belong on the other side of the seam.
_SCORERS_PRELUDE_TEMPLATE = """
globalThis.window = { MT_REPORT: %s };
"""

# Full-bundle shim: the state / trust runners below load the concatenated
# interactive.js, which registers a DOMContentLoaded handler and builds
# editor controls, so those need a document object too.
_NODE_PRELUDE_TEMPLATE = """
globalThis.window = { MT_REPORT: %s };
globalThis.document = {
  addEventListener() {},
  removeEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getElementById() { return null; },
  createElement() {
    return {
      style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      appendChild() {}, addEventListener() {}, setAttribute() {}, remove() {},
    };
  },
  body: null,
};
"""

_NODE_EPILOGUE = f"""
;(() => {{
{_WALK_LEAVES_JS}
    console.log(JSON.stringify(out));
}})();
"""


@pytest.mark.skipif(_NODE is None, reason="node executable not on PATH")
def test_js_scorers_agree_with_python_node(tmp_path):
    """Same parity assertion as the Playwright test, executed via a bare
    ``node`` subprocess so the parity suite runs on browserless CI too."""
    context, expected = _build_fixture_context()
    mt_report = {
        "MODEL_ID": context["model_id"],
        "TREE_VIEW": context["tree_view"],
        "VARIABLES_BY_NAME": context["variables_by_name"],
        "VARIABLE_ORDER": list(context["variables_by_name"].keys()),
        "MODE_SCHEMAS": {},
        "DIAG_TRAJECTORIES": [],
        "NB_TRAJECTORIES": [],
    }
    script = (
        _SCORERS_PRELUDE_TEMPLATE % json.dumps(mt_report)
        + _SCORERS_JS.read_text(encoding="utf-8")
        + _NODE_EPILOGUE
    )
    script_path = tmp_path / "parity_runner.js"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(script_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"node failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    last_line = proc.stdout.strip().splitlines()[-1]
    js_verdicts = json.loads(last_line)
    _assert_verdicts_agree(js_verdicts, expected)


def _case_index(case_id: str) -> int:
    for i, case in enumerate(_PARITY_CASES):
        if case["id"] == case_id:
            return i
    raise KeyError(case_id)


@pytest.mark.skipif(_NODE is None, reason="node executable not on PATH")
def test_js_state_behaviors_node(tmp_path):
    """Node-driven checks for JS state fixes with no scorer counterpart:

    * review 2026-07-06 finding 47 — ``_detectEvents`` groups CONSECUTIVE
      duplicate samples into one event.
    * finding 38 — leafState deep-copies nested arrays, so an in-place
      tube-point edit shows up in ``buildPatchData`` and the reset button
      restores the original array (and the diff goes quiet again).
    * finding 76a — an explicitly cleared window does not resurrect from
      the pristine node on structural export (``nodeToSpec``).
    """
    context, _expected = _build_fixture_context()
    mt_report = {
        "MODEL_ID": context["model_id"],
        "TREE_VIEW": context["tree_view"],
        "VARIABLES_BY_NAME": context["variables_by_name"],
        "VARIABLE_ORDER": list(context["variables_by_name"].keys()),
        "MODE_SCHEMAS": {},
        "DIAG_TRAJECTORIES": [],
        "NB_TRAJECTORIES": [],
    }
    tube_path = f"/metrics/children/{_case_index('tube-points-rel-pass')}"
    win_path = f"/metrics/children/{_case_index('nrmse-window-pass')}"
    epilogue = """
;(() => {
  const results = {};
  results.detectEvents = _detectEvents([0, 1, 1, 1, 2, 3, 3, 4]);

  const tubePath = %(tube_path)s;
  const st = leafState[tubePath];
  st.params.tube_points[0].upper = 99.0;   // in-place edit, as the editor does
  results.tubeOps = buildPatchData().patch
    .filter(op => op.path.startsWith(tubePath));
  resetLeafToOriginal(tubePath);
  results.afterResetUpper = leafState[tubePath].params.tube_points[0].upper;
  results.opsAfterReset = buildPatchData().patch
    .filter(op => op.path.startsWith(tubePath)).length;

  const winPath = %(win_path)s;
  leafState[winPath].window = {};          // explicit clear
  results.clearedWindowSpec = nodeToSpec(findLeaf(TREE_VIEW, winPath));

  console.log(JSON.stringify(results));
})();
""" % {"tube_path": json.dumps(tube_path), "win_path": json.dumps(win_path)}
    script = (
        _NODE_PRELUDE_TEMPLATE % json.dumps(mt_report)
        + interactive_js_source()
        + epilogue
    )
    script_path = tmp_path / "state_runner.js"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(script_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"node failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    results = json.loads(proc.stdout.strip().splitlines()[-1])

    # finding 47: [0,1,1,1,2,3,3,4] has two events (t=1 ×3 dups, t=3 ×2).
    assert results["detectEvents"] == [1, 3]

    # finding 38: the in-place edit produced exactly one tube_points op...
    assert [op["path"] for op in results["tubeOps"]] == [f"{tube_path}/tube_points"]
    assert results["tubeOps"][0]["value"][0]["upper"] == 99.0
    # ...and reset restored the pristine value + a quiet diff.
    assert results["afterResetUpper"] == 0.05
    assert results["opsAfterReset"] == 0

    # finding 76a: cleared window exports with NO window key.
    assert "window" not in results["clearedWindowSpec"]


# ---------------------------------------------------------------------------
# Reporter-trust fixtures — browser table / band / export must agree with
# what ``dstf run`` would say. Each block below has a Python counterpart
# asserted in the same test.
# ---------------------------------------------------------------------------

# Declared A is wide (t=1.0 ±0.5) and covers BOTH actual events; declared B
# is narrow (t=1.3 ±0.05) and covers only the first. Greedy nearest-unclaimed
# gives A the 1.25 event and leaves B unmatched; the optimal right-endpoint
# rule Python uses gives B the 1.25 event and A the 1.4 one, matching both.
_STEALING_DECLARED = [
    {"time": 1.0, "tolerance": 0.5},
    {"time": 1.3, "tolerance": 0.05},
]
# Modelica-style duplicate-time events at t=1.25 and t=1.4.
_STEALING_ACT_TIME = [0.0, 0.5, 1.25, 1.25, 1.3, 1.4, 1.4, 2.0]


def _trust_fixture_context() -> dict:
    """MT_REPORT for the reporter-trust checks (Fixes 2, 4, 6)."""
    ev_traj = {
        "ref_time": list(_STEALING_ACT_TIME),
        "ref_values": [0.0] * len(_STEALING_ACT_TIME),
        "act_time": list(_STEALING_ACT_TIME),
        "act_values": [0.0] * len(_STEALING_ACT_TIME),
    }
    # ref constant 1.0, act constant 1.1 — outside a zero-width 'rel' tube,
    # inside a 0.2 band. Which one the report DRAWS must be the one it scores.
    t = np.linspace(0.0, 1.0, 20)
    tube_traj = _traj(t, np.ones_like(t), t, np.ones_like(t) + 0.1)
    flat_traj = _ramp(0.0)

    leaves = [
        {
            "kind": "leaf",
            "path": "/metrics/children/0",
            "metric": "event-timing",
            "variable": "ev",
            "params": {"events": _STEALING_DECLARED, "time_tolerance": 1e-3},
            "mode_values": {"events": _STEALING_DECLARED, "time_tolerance": 1e-3},
            "window": {},
            "children": [],
            "name": "ev",
            "mode_effective": "event-timing",
            "cli_authoritative": True,
        },
        {
            "kind": "leaf",
            "path": "/metrics/children/1",
            "metric": "tube",
            "variable": "tb",
            # tube_width_mode deliberately UNSET.
            "params": {"tube_abs": 0.2},
            "mode_values": {"tube_abs": 0.2, "tube_rel": 0.0},
            "window": {},
            "children": [],
            "name": "tb",
            "mode_effective": "tube",
            "cli_authoritative": False,
        },
        {
            # Flat-override leaf: the spec authored NO tolerance; the CLI's
            # inherited config.tolerance arrives only via mode_values.
            "kind": "leaf",
            "path": "/metrics/children/2",
            "metric": "nrmse",
            "variable": "inherited",
            "params": {},
            "mode_values": {"tolerance": 1e-4},
            "window": {},
            "children": [],
            "name": "inherited",
            "mode_effective": "nrmse",
            "cli_authoritative": False,
        },
        {
            # Spec-authored tolerance — must round-trip verbatim.
            "kind": "leaf",
            "path": "/metrics/children/3",
            "metric": "nrmse",
            "variable": "authored",
            "params": {"tolerance": 0.01},
            "mode_values": {"tolerance": 0.01},
            "window": {},
            "children": [],
            "name": "authored",
            "mode_effective": "nrmse",
            "cli_authoritative": False,
        },
    ]
    variables = {
        "ev": {"name": "ev", "trajectory": ev_traj, "overlays": []},
        "tb": {"name": "tb", "trajectory": tube_traj, "overlays": []},
        "inherited": {"name": "inherited", "trajectory": flat_traj, "overlays": []},
        "authored": {"name": "authored", "trajectory": flat_traj, "overlays": []},
    }
    return {
        "MODEL_ID": "Fixture.ReporterTrust",
        "TREE_VIEW": {
            "kind": "combinator",
            "combinator": "and",
            "path": "/metrics",
            "children": leaves,
        },
        "VARIABLES_BY_NAME": variables,
        "VARIABLE_ORDER": list(variables.keys()),
        "MODE_SCHEMAS": {},
        "DIAG_TRAJECTORIES": [],
        "NB_TRAJECTORIES": [],
    }


_TRUST_EPILOGUE = """
;(() => {
  const results = {};

  // --- event-timing live match table (Fix 2) ---
  const evLeaf = findLeaf(TREE_VIEW, '/metrics/children/0');
  results.evMatches = MODE_PLOT_EDITORS['event-timing']._evaluateLiveMatches(evLeaf);

  // --- tube: drawn band vs scored band with tube_width_mode unset (Fix 4) ---
  const tbLeaf = findLeaf(TREE_VIEW, '/metrics/children/1');
  const tbTraj = VARIABLES_BY_NAME['tb'].trajectory;
  const contrib = MODE_PLOT_CONTRIBUTIONS['tube'](tbLeaf, tbTraj);
  const n = tbTraj.ref_time.length;
  const y = contrib.traces[0].y;
  const upper = y.slice(0, n);
  const lower = y.slice(n).reverse();
  results.insideDrawnBand = tbTraj.act_values.every(
    (v, i) => v >= lower[i] && v <= upper[i]);
  results.tubeScorerVerdict = MODE_SCORERS['tube'](tbLeaf);

  // --- structural export: inherited vs authored params (Fix 6) ---
  results.inheritedSpec = nodeToSpec(findLeaf(WORKING_TREE, '/metrics/children/2'));
  results.authoredSpec = nodeToSpec(findLeaf(WORKING_TREE, '/metrics/children/3'));
  leafState['/metrics/children/2'].params.tolerance = 0.5;   // user edit
  results.editedSpec = nodeToSpec(findLeaf(WORKING_TREE, '/metrics/children/2'));

  // --- passthrough JSON textarea: malformed input never reaches params (Fix 5) ---
  const ta = {
    type: 'textarea', value: '[{"time": 2.0}]',
    dataset: { passthrough: 'true' }, style: {},
    classList: { toggle() {} },
  };
  onLeafFieldChange(evLeaf, 'events', ta);
  results.eventsAfterGood = leafState['/metrics/children/0'].params.events;
  ta.value = '[{"time": 2.0';                 // malformed
  onLeafFieldChange(evLeaf, 'events', ta);
  results.eventsAfterBad = leafState['/metrics/children/0'].params.events;
  results.badTitle = ta.title;
  results.badBorder = ta.style.borderColor;
  results.badExport = JSON.stringify(buildPatchData());
  ta.value = '';                              // clearing still works
  onLeafFieldChange(evLeaf, 'events', ta);
  results.eventsAfterClear = leafState['/metrics/children/0'].params.events;

  // --- Shift+click add survives sub-pixel jitter (Fix 3) ---
  // Recording DOM stub: 1 data unit == 100 screen px, so a 1 px wobble is a
  // 0.01 data-unit change — far above the data-space epsilon this replaced.
  const handlers = {};
  document.addEventListener = (name, fn) => { handlers[name] = fn; };
  const axis = { p2d: px => px / 100, d2p: d => d * 100 };
  const plotEl = {
    _fullLayout: { xaxis: axis, yaxis: axis },
    querySelector: () => ({ getBoundingClientRect: () => ({ left: 0, top: 0 }) }),
    addEventListener: (name, fn) => { handlers['el:' + name] = fn; },
    removeEventListener: () => {},
  };
  let added = 0;
  createPointPlotEditor({
    getAnchors: () => [],
    onClickAdd: () => { added++; },
    onDragStep: () => {},
  }).attach(tbLeaf, plotEl, () => {});
  const noop = () => {};
  const down = (x, y) => handlers['el:mousedown']({
    shiftKey: true, button: 0, clientX: x, clientY: y,
    stopPropagation: noop, stopImmediatePropagation: noop, preventDefault: noop,
  });
  const move = (x, y) => handlers['mousemove']({ clientX: x, clientY: y, preventDefault: noop });

  down(100, 100); move(101, 100); handlers['mouseup']();     // 1 px jitter
  results.addsAfterJitter = added;
  down(100, 100); move(160, 140); handlers['mouseup']();     // real 72 px drag
  results.addsAfterDrag = added;

  console.log(JSON.stringify(results));
})();
"""


@pytest.mark.skipif(_NODE is None, reason="node executable not on PATH")
def test_js_reporter_trust_fixes_node(tmp_path):
    """Browser-side artifacts must agree with what ``dstf run`` reports.

    * event-timing's live Match column pairs declared/actual events with the
      same optimal interval matching the Python scorer uses (a greedy rule
      showed rows red that the CLI scores green).
    * a tube leaf with ``tube_width_mode`` unset DRAWS the band it SCORES.
    * a structural export never freezes an inherited config.tolerance into a
      leaf, but keeps spec-authored and user-edited params.
    """
    mt_report = _trust_fixture_context()
    script = (
        _NODE_PRELUDE_TEMPLATE % json.dumps(mt_report)
        + interactive_js_source()
        + _TRUST_EPILOGUE
    )
    script_path = tmp_path / "trust_runner.js"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(script_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"node failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
    )
    results = json.loads(proc.stdout.strip().splitlines()[-1])

    # --- Fix 2: the JS Match column must reproduce Python's pairing ---
    from dstf.comparison.algorithms import _compare_event_timing

    py = _compare_event_timing(
        np.array(_STEALING_ACT_TIME),
        np.array(_STEALING_ACT_TIME),
        time_tolerance=1e-3,
        count_must_match=True,
        declared_events=_STEALING_DECLARED,
    )
    assert py.passed, "fixture must be a PASS Python-side (both events paired)"
    assert [m["matched"] for m in results["evMatches"]] == [True, True], (
        f"live Match column disagrees with the Python pairing: {results['evMatches']}"
    )
    # A takes 1.4 and B takes 1.25 under the right-endpoint rule.
    assert results["evMatches"][0]["at"] == 1.4
    assert results["evMatches"][1]["at"] == 1.25

    # --- Fix 4: unset tube_width_mode — drawn band == scored band ---
    assert results["tubeScorerVerdict"] == results["insideDrawnBand"], (
        "tube plot and tube scorer default tube_width_mode differently"
    )

    # --- Fix 6: structural export omits inherited-only params ---
    assert "tolerance" not in results["inheritedSpec"], (
        "structural export froze the inherited config.tolerance into the leaf"
    )
    assert results["inheritedSpec"]["metric"] == "nrmse"
    assert results["authoredSpec"]["tolerance"] == 0.01
    assert results["editedSpec"]["tolerance"] == 0.5

    # --- Fix 5: malformed passthrough JSON is refused, not stored/exported ---
    assert results["eventsAfterGood"] == [{"time": 2.0}]
    assert results["eventsAfterBad"] == [{"time": 2.0}], (
        "malformed JSON overwrote the last-good passthrough value"
    )
    assert results["badTitle"], "invalid textarea carries no explanatory title"
    assert results["badBorder"] == "#d32f2f"
    assert '[{"time": 2.0' not in results["badExport"], (
        "raw unparsed JSON string leaked into the exported patch"
    )
    # Clearing the field is still a real edit → null.
    assert results["eventsAfterClear"] is None

    # --- Fix 3: a 1 px wobble still counts as a click; a real drag doesn't ---
    assert results["addsAfterJitter"] == 1, (
        "Shift+click add cancelled by sub-pixel mouse drift"
    )
    assert results["addsAfterDrag"] == 1, "a real drag must not add a point"


# ---------------------------------------------------------------------------
# Playwright runner — renders the real interactive.html
# ---------------------------------------------------------------------------


def _render_report(tmp_path: Path) -> tuple[Path, list[bool]]:
    from jinja2 import Environment, FileSystemLoader

    context, expected = _build_fixture_context()
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**context)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    write_report_assets(tmp_path)
    return html_path, expected


# playwright_browser lives in conftest.py — see its docstring for why it
# must not be defined here or imported between test modules (D97).


@pytest.fixture
def parity_page(tmp_path, playwright_browser) -> Iterator[tuple[Any, list[bool]]]:
    html_path, expected = _render_report(tmp_path)
    context = playwright_browser.new_context()
    page = context.new_page()
    page.on(
        "pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False)
    )
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")
    yield page, expected
    context.close()


@pytest.mark.playwright
def test_js_scorers_agree_with_python(parity_page):
    """For every fixture leaf, the JS ``MODE_SCORERS[mode](leaf)`` verdict
    must match the Python ``_compare_<mode>`` verdict on the same data.

    Failure here indicates drift between the two implementations. The
    failure message lists every disagreeing leaf so a single run surfaces
    the full extent of the drift, not just the first case.
    """
    page, expected = parity_page

    js_verdicts = page.evaluate("() => {" + _WALK_LEAVES_JS + " return out; }")
    _assert_verdicts_agree(js_verdicts, expected)
