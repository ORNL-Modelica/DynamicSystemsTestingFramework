"""End-to-end PTA demo (PTA.6).

Wires the bundled `testing.json` recognizer config in
`examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json`
to a real model (`Examples/SimulateOnlyTest.mo`) and asserts the recognized
test carries `simulate_only=True` plus the experiment-annotation values.
"""

from __future__ import annotations

import json
from pathlib import Path

from dstf.comparison.registry import combinator_names, metric_names
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests

PROJECT_ROOT = Path(__file__).parent.parent
TESTING_JSON = (
    PROJECT_ROOT
    / "examples"
    / "modelica"
    / "ModelicaTestingLib"
    / "Resources"
    / "ReferenceResults"
    / "testing.json"
)
TEST_SPEC_JSON = TESTING_JSON.parent / "test_spec.json"


def test_demo_recognizer_finds_simulate_only_model():
    config = Config(config_file=str(TESTING_JSON))
    # The demo recognizer is loaded from testing.json
    assert any(
        r.name == "demo:icons-example-as-simulate-only" for r in config.recognizers
    )

    tests = discover_tests(config)
    by_id = {t.model_id: t for t in tests}

    sim_only = by_id.get("ModelicaTestingLib.Examples.SimulateOnlyTest")
    assert sim_only is not None, (
        "demo recognizer should pick up SimulateOnlyTest via its "
        "'extends *Icons.Example' clause"
    )
    assert sim_only.simulate_only is True
    # experiment-annotation source extracted these:
    assert sim_only.stop_time == 5
    assert sim_only.tolerance == 1e-4


def test_bundled_unit_tests_still_discovered():
    """Demo recognizer is additive — bundled UnitTests-based tests stay."""
    config = Config(config_file=str(TESTING_JSON))
    tests = discover_tests(config)
    ids = {t.model_id for t in tests}
    assert "ModelicaTestingLib.Examples.SimpleTest" in ids
    assert "ModelicaTestingLib.Examples.EventTest" in ids


def test_simulate_only_model_does_not_trigger_bundled():
    """SimulateOnlyTest has no UnitTests component, so bundled returns None
    on it; only the demo recognizer fires."""
    config = Config(config_file=str(TESTING_JSON))
    tests = discover_tests(config)
    by_id = {t.model_id: t for t in tests}
    sim_only = by_id["ModelicaTestingLib.Examples.SimulateOnlyTest"]
    # n_vars stays at TestModel default since neither bundled (no match)
    # nor demo recognizer (no n_vars field) sets it.
    assert sim_only.n_vars == 1
    assert sim_only.x_expressions == []


# ---------------------------------------------------------------------------
# Demo spec coverage — every metric and every combinator runs in the demo
# ---------------------------------------------------------------------------


def _demo_spec_usage() -> tuple[set[str], set[str]]:
    """Return the (metrics, combinators) the bundled demo spec exercises."""
    spec = json.loads(TEST_SPEC_JSON.read_text(encoding="utf-8"))
    metrics: set[str] = set()
    combinators: set[str] = set()

    def walk(node: dict) -> None:
        if "combinator" in node:
            combinators.add(node["combinator"])
            for child in node.get("children", []):
                walk(child)
            return
        metrics.add(node["metric"])

    for entry in spec["tests"]:
        if "metrics" in entry:
            walk(entry["metrics"])
        overrides = entry.get("comparison", {}).get("variable_overrides", {})
        for override in overrides.values():
            metrics.add(override.get("mode", "nrmse"))
    return metrics, combinators


def test_demo_spec_exercises_every_metric_and_combinator():
    """The bundled demo is the framework's own end-to-end coverage: a metric
    or combinator nobody runs there is one nobody runs at all (D103)."""
    metrics, combinators = _demo_spec_usage()
    assert metric_names() - metrics == set()
    assert combinator_names() - combinators == set()


def test_demo_simple_test_carries_the_scalar_leaf():
    """SimpleTest's tree is and[k-of-n(...), scalar] — the k-of-n coverage
    stays, and the scalar leaf reduces the first tracked series, which the
    result file names ``unitTests.x[1]`` (the model writes ``x`` for it, and
    that is the label, not the key)."""
    spec = json.loads(TEST_SPEC_JSON.read_text(encoding="utf-8"))
    entry = next(t for t in spec["tests"] if t["model"].endswith("Examples.SimpleTest"))
    root = entry["metrics"]
    assert root["combinator"] == "and"
    kinds = [c.get("combinator") or c.get("metric") for c in root["children"]]
    assert kinds == ["k-of-n", "scalar"]
    scalar = root["children"][1]
    assert scalar["variable"] == "unitTests.x[1]"
    assert scalar["quantity"] == {"reduce": "final"}
