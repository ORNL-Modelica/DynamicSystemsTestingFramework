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
from dstf.comparison.tree_eval import flatten_evaluation, leaf_comparisons_by_path
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests
from dstf.reporting.status import PASS, classify
from dstf.storage.baseline import ReferenceFile
from dstf.storage.reference_store import ReferenceStore

EXAMPLE = Path(__file__).parent.parent / "examples" / "modelica" / "ModelicaTestingLib"
REFS = EXAMPLE / "Resources" / "ReferenceResults"
TREE = "ModelicaTestingLib.Examples.MetricTreeTest"
SHOWCASE = "ModelicaTestingLib.Examples.ThermostatBuildingTest"
PARTITIONS = ["Dymola/linux", "Dymola/windows", "OpenModelica/linux"]


def _as_its_own_run(reference: ReferenceFile) -> TestResult:
    time = np.asarray(reference.baseline.time, dtype=float)
    return TestResult(
        reference.model_id,
        True,
        variables=[
            VariableResult(
                v.index,
                time,
                np.asarray(v.values, dtype=float),
                name=v.name,
                expression=v.expression or "",
            )
            for v in reference.baseline.variables
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
    assert TREE in said and SHOWCASE in said and len(said) >= 12
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


@pytest.mark.parametrize("partition", PARTITIONS)
def test_the_showcase_scores_what_the_criteria_guide_says(partition):
    """The numbers docs/guides/criteria.md quotes for ThermostatBuildingTest
    ("An example with every metric"), read from each shipped baseline scored
    as its own run: what the run scores, not the spec's literals (a tube's
    width, a band's edges). A re-baseline or a spec edit that moves one of
    these numbers fails here, and the guide's paragraph is the one to rewrite
    with it."""
    backend, os_name = partition.split("/")
    config = Config(
        config_file=REFS / "testing.json", backend_name=backend, os_name=os_name
    )
    store = ReferenceStore(config)
    (test,) = [t for t in discover_tests(config) if t.model_id == SHOWCASE]
    comp = compare_one(test, _as_its_own_run(store.get_reference(SHOWCASE)), store)
    assert (classify(comp), [w.text for w in comp.warnings]) == (PASS, [])

    # "17 / 19 criteria passed"
    assert (sum(v.passed for v in comp.variables), len(comp.variables)) == (17, 19)

    # Rooms 2 and 4 miss their late band by about 0.047 °C; both criteria
    # sit in the `weighted` group, which totals 0.023 against 0.5 and passes.
    spec = json.loads((REFS / "test_spec.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == SHOWCASE]
    evaluated = flatten_evaluation(comp.metric_tree)
    failed = {}
    for path, vc in leaf_comparisons_by_path(comp.metric_tree).items():
        if vc.passed:
            continue
        group = path.rsplit("/children/", 1)[0]
        node = entry
        for step in group.split("/")[1:]:
            node = node[int(step)] if step.isdigit() else node[step]
        failed[(vc.mode, vc.name)] = (node.get("combinator"), group, vc.nrmse)
    assert {k: v[0] for k, v in failed.items()} == {
        ("range", "T[2]"): "weighted",
        ("range", "T[4]"): "weighted",
    }
    assert failed[("range", "T[2]")][2] == pytest.approx(0.047, abs=5e-4)
    assert failed[("range", "T[4]")][2] == pytest.approx(0.046, abs=5e-4)
    weighted = evaluated[failed[("range", "T[2]")][1]]
    assert weighted["passed"] is True
    assert weighted["weighted_sum"] == pytest.approx(0.0234, abs=1e-4)
    assert weighted["threshold"] == 0.5

    by_mode = {v.mode: v for v in comp.variables}
    # "120 of them between 9000 s and 21500 s"
    events = by_mode["event-timing"].diagnostics
    assert (events["ref_event_count"], events["act_event_count"]) == (120, 120)
    # Two declared peaks, the heating cycle and the fan, both found.
    peaks = by_mode["dominant-frequency"].diagnostics["paired_peaks"]
    assert [p["declared_hz"] for p in peaks] == [0.00056, 0.002381]
    assert all(p["passed"] for p in peaks)
    assert [p["matched_hz"] for p in peaks] == [
        pytest.approx(5.60e-4, rel=2e-3),
        pytest.approx(2.40e-3, rel=2e-3),
    ]
