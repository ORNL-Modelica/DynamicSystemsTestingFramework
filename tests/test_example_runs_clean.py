"""The shipped Modelica example runs clean: every test passes its own
baseline and says nothing else.

`MetricTreeTest` tracks two variables and its tree scored one, so every run
of the example — a green one too — ended with a `Structural Warnings` block
naming `unitTests.x[2]`. The warning was true, and a demo is no place for a
standing one: the tree holds that variable to a bound now.

Without a simulator: each stored baseline of each shipped partition is
scored as its own run, through the store the run reads (soft checks
included). The Windows partition is here too — it is the one no Linux
machine can run, and a bound added to the tree has to hold on what it
stores."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_one
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests
from dstf.reporting.status import PASS, classify
from dstf.storage.reference_store import ReferenceStore

EXAMPLE = Path(__file__).parent.parent / "examples" / "modelica" / "ModelicaTestingLib"
REFS = EXAMPLE / "Resources" / "ReferenceResults"
TREE = "ModelicaTestingLib.Examples.MetricTreeTest"
PARTITIONS = ["Dymola/linux", "Dymola/windows", "OpenModelica/linux"]


def _as_its_own_run(reference: dict) -> TestResult:
    time = np.asarray(reference["time"], dtype=float)
    return TestResult(
        reference["model_id"],
        True,
        variables=[
            VariableResult(
                v["index"],
                time,
                np.asarray(v["values"], dtype=float),
                name=v["name"],
                expression=v.get("expression") or "",
            )
            for v in reference["variables"]
        ],
    )


@pytest.mark.parametrize("partition", PARTITIONS)
def test_every_shipped_baseline_passes_its_own_criteria_and_warns_of_nothing(
    partition,
):
    backend, os_name = partition.split("/")
    config = Config(
        config_file=REFS / "testing.json", backend_name=backend, os_name=os_name
    )
    store = ReferenceStore(config)
    said = {}
    for test in discover_tests(config):
        reference = store.get_reference(test.model_id)
        if reference is None:
            continue  # simulate-only, or a model this partition was not run for
        comp = compare_one(test, _as_its_own_run(reference), store)
        said[test.model_id] = (classify(comp), [w.text for w in comp.warnings])
    assert TREE in said and len(said) >= 11
    assert {model: s for model, s in said.items() if s != (PASS, [])} == {}


def test_the_tree_scores_every_variable_the_model_tracks():
    """The entry itself: the two UnitTests variables, each named by a
    leaf, and the downstream lag held to the bound it can never pass."""
    spec = json.loads((REFS / "test_spec.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == TREE]
    children = entry["metrics"]["children"]
    assert children[-1] == {
        "metric": "range",
        "variable": "unitTests.x[2]",
        "max_value": 1.0,
    }
    config = Config(config_file=REFS / "testing.json", backend_name="OpenModelica")
    (test,) = [t for t in discover_tests(config) if t.model_id == TREE]
    from dstf.comparison.tree_spec import collect_variables
    from dstf.discovery.unit_tests import unit_test_variables

    tracked = {v.name for v in unit_test_variables(test)}
    assert tracked == {"unitTests.x[1]", "unitTests.x[2]"}
    assert set(collect_variables(test.metric_tree_spec)) == tracked
