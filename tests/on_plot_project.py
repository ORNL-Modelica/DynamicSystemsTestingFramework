"""A fixture project for the gestures on a plot (D149): `_two_var_project`
with a run and a baseline that HAVE something to settle on — an oscillation
with two spectral peaks and two events — and a tree holding every metric a
gesture reads. Not a test module (no `test_` prefix); the unit tier and the
browser tier both build it."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.reference_store import ReferenceStore
from test_session import A, _two_var_project

#: The run's events: a time sampled twice, as a Modelica result marks one.
EVENTS = (0.4, 0.8)
#: The two frequencies x oscillates at, Hz: its spectrum's two peaks.
PEAKS = (3.0, 8.0)

POINTS = "/metrics/children/0"
EVENT_TIMING = "/metrics/children/1"
FREQUENCY = "/metrics/children/2"
TUBE = "/metrics/children/3"
RANGE = "/metrics/children/4"
NRMSE = "/metrics/children/5"
SCALAR = "/metrics/children/6"
CONSTANT_TUBE = "/metrics/children/7"

TREE = {
    "combinator": "and",
    "children": [
        {
            "metric": "points",
            "variable": "x",
            "tolerance": 0.2,
            "points": [
                {"time": 0.25, "value": -1.0, "tolerance": 0.3, "time_tolerance": 0.05},
                {"time": 0.6},
            ],
        },
        {
            "metric": "event-timing",
            "variable": "x",
            "time_tolerance": 0.03,
            "events": [{"time": 0.4, "tolerance": 0.05}],
        },
        {
            "metric": "dominant-frequency",
            "variable": "x",
            "peaks": [{"freq": 3.0, "tolerance": 0.2, "tolerance_mode": "rel"}],
        },
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.1, "upper": 0.2, "lower": 0.2},
                {"time": 0.9, "upper": 0.6, "lower": 0.4},
            ],
        },
        {"metric": "range", "variable": "x", "min_value": -2.0, "max_value": 2.0},
        {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
        {
            "metric": "scalar",
            "variable": "x",
            "quantity": {"reduce": "max"},
            "tolerance": 0.5,
        },
        {
            "metric": "tube",
            "variable": "y",
            "tube_width_mode": "band",
            "tube_abs": 0.3,
        },
    ],
}


def wave(t: float) -> float:
    """x: 3 Hz, and 8 Hz at four tenths of it."""
    return math.sin(2 * math.pi * PEAKS[0] * t) + 0.4 * math.sin(
        2 * math.pi * PEAKS[1] * t
    )


def times(n: int = 200) -> list[float]:
    """0..1 in ``n`` steps, each of `EVENTS` sampled twice."""
    grid = [i / n for i in range(n + 1)]
    return sorted(grid + list(EVENTS))


def wave_project(tmp_path: Path, tree: dict | None = None) -> tuple[Path, Path]:
    """A scored by ``tree`` (`TREE` unless said) over that data: the run's x
    is `wave`, its baseline 0.95 of it, so a checkpoint that follows the
    baseline and one that takes the run's value are told apart."""
    cfg, work = _two_var_project(tmp_path)
    t = times()
    (work / "test_0001" / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "time": t,
                "variables": {"x": [wave(v) for v in t], "y": [2 * v for v in t]},
            }
        ),
        encoding="utf-8",
    )
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["metrics"] = TREE if tree is None else tree
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    tt = np.asarray(t)
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(A, variable_patterns=["x", "y"]),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    time=tt,
                    values=np.asarray([0.95 * wave(v) for v in t]),
                    name="x",
                ),
                VariableResult(index=2, time=tt, values=2.1 * tt, name="y"),
            ],
        ),
    )
    return cfg, work
