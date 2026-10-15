"""A backend plug-in for dstf, whole: exponential decay, simulated in-process.

Each test is the trajectory ``y(t) = exp(-rate * t)`` up to its stop time.
``rate`` is this backend's one setting, written in the ``Decay`` block of
``testing.json``'s ``backends``. The package's ``pyproject.toml`` names this
module under the ``dstf.plugins`` entry-point group, so that installing the
package is all it takes: ``dstf`` imports the module, and ``@register`` adds
the backend.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from dstf.backends import Backend, register
from dstf.backends.locate import EXECUTABLE, SEARCH, Found, Located, Rule
from dstf.backends.types import (
    Capability,
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
)
from dstf.config_shape import Setting
from dstf.discovery.test_registry import TestModel

#: The file a run writes into its test's directory, and the read gets back.
RESULT_FILE = "result.json"


def simulate(stop_time: float, rate: float, intervals: int) -> dict[str, list]:
    """The model: ``y`` sampled on ``intervals + 1`` points from 0 to
    ``stop_time``."""
    t = np.linspace(0.0, stop_time, intervals + 1)
    return {"time": t.tolist(), "y": np.exp(-rate * t).tolist()}


@register("Decay")
class DecayBackend(Backend):
    """Runs a ``source_type: "decay"`` project one test at a time."""

    capabilities = frozenset({Capability.BATCH_FALLBACK})
    source_types = frozenset({"decay"})
    settings = {
        **Backend.settings,
        "rate": Setting(
            {"type": "number", "exclusiveMinimum": 0},
            1.0,
            "How fast y decays, in 1/s.",
        ),
    }

    @classmethod
    def locate(cls, settings: Mapping[str, Any]) -> Located:
        """Nothing to install: the model runs in the interpreter that runs
        dstf, which is therefore what is found."""
        rule = Rule(SEARCH, "the running interpreter")
        return Located((Found(EXECUTABLE, sys.executable, rule),))

    def run_single_test(
        self, test: TestModel, test_key: str, index: int, total: int
    ) -> TestRunResult:
        began = time.monotonic()
        rate = self.settings_of(self.config)["rate"]
        intervals = test.number_of_intervals or 100
        directory = self.config.work_dir / test_key
        directory.mkdir(parents=True, exist_ok=True)
        result = simulate(float(test.stop_time), float(rate), intervals)
        (directory / RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=time.monotonic() - began,
        )

    def read_result(
        self, test: TestModel, test_key: str, run_result: TestRunResult | None
    ) -> TestResult:
        path = self.config.work_dir / test_key / RESULT_FILE
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return TestResult(test.model_id, False, error_message=f"{path}: {exc}")
        t = np.asarray(result.pop("time"), dtype=float)
        names = resolve_variable_patterns(test.variable_patterns, list(result))
        return TestResult(
            test.model_id,
            True,
            variables=[
                VariableResult(
                    index=i, time=t, values=np.asarray(result[name]), name=name
                )
                for i, name in enumerate(names, start=1)
            ],
        )
