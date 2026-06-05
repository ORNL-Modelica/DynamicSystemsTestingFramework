"""Pre-recorded experiment data loader — the "no simulator" test.

This file contains zero numerical-integration code. The ``simulate()``
function loads pre-recorded (t, x) pairs from a CSV and returns them
verbatim. It exists to prove the backend abstraction is not secretly
shaped around ODE simulation: a "SimulatorRunner" can serve any
source of time-series data, including pure data playback.

The ``stop_time`` argument is respected as a clip window — samples with
``t > stop_time`` are excluded. ``tolerance`` is ignored (there is no
solver tolerance to honor for pre-recorded data).

This is the minimal version: already-aligned CSV with well-defined
``t`` and ``x`` columns. More sophisticated use cases (server fetches,
clock-drift alignment, windowing) are user-space concerns — any of
them can be implemented in a Python test file today. Framework-level
alignment/fitting is tracked as a follow-up in ``docs/ideas.md``.
"""

from __future__ import annotations

import csv
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "constant_experiment.csv"


def simulate(stop_time: float, tolerance: float) -> dict:
    # tolerance is irrelevant for pre-recorded data; kept in the
    # signature to satisfy the framework contract.
    del tolerance

    times: list[float] = []
    values: list[float] = []
    with _DATA_FILE.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = float(row["t"])
            if t > stop_time:
                break
            times.append(t)
            values.append(float(row["x"]))

    return {
        "time": times,
        "variables": {"x": values},
    }
