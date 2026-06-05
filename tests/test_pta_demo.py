"""End-to-end PTA demo (PTA.6).

Wires the bundled `testing.json` recognizer config in
`examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json`
to a real model (`Examples/SimulateOnlyTest.mo`) and asserts the recognized
test carries `simulate_only=True` plus the experiment-annotation values.
"""

from __future__ import annotations

from pathlib import Path

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
