"""Orchestrate test discovery: find test models, merge parameters, generate mos file."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import (
    Config,
    DEFAULT_METHOD,
    DEFAULT_NUMBER_OF_INTERVALS,
    DEFAULT_STOP_TIME,
    DEFAULT_TOLERANCE,
)
from .mo_parser import MoParseResult, parse_mo_file
from .mos_parser import SimParams, parse_mos_file


@dataclass
class TestModel:
    """A fully resolved test model with all metadata needed for simulation."""
    model_id: str
    mo_file: Path
    package_path: str
    short_name: str
    n_vars: int
    x_expressions: list[str] = field(default_factory=list)
    x_raw: str = ""
    x_reference: Optional[list[float]] = None
    error_expected: float = 1e-6
    stop_time: float = DEFAULT_STOP_TIME
    tolerance: float = DEFAULT_TOLERANCE
    method: str = DEFAULT_METHOD
    number_of_intervals: Optional[int] = None
    output_interval: Optional[float] = None
    result_file: str = ""
    in_mos: bool = False  # Whether this test appears in runAll_Dymola.mos

    # External spec: variable patterns (may include globs like "medium.T*" or "*")
    # These are resolved against actual .mat variable names after simulation.
    variable_patterns: list[str] = field(default_factory=list)

    # Where this test was defined: "unit_tests", "spec", "both"
    source: str = "unit_tests"


def _merge_params(
    mo_result: MoParseResult, sim_params: Optional[SimParams]
) -> TestModel:
    """Merge .mo parse results with .mos simulation params.

    .mos params take priority over experiment() annotations.
    """
    parts = mo_result.model_id.rsplit(".", 1)
    package_path = parts[0] if len(parts) > 1 else ""
    short_name = parts[-1]

    ut = mo_result.unit_test
    exp = mo_result.experiment

    model = TestModel(
        model_id=mo_result.model_id,
        mo_file=mo_result.mo_file,
        package_path=package_path,
        short_name=short_name,
        n_vars=ut.n if ut else 1,
        x_expressions=ut.x_expressions if ut else [],
        x_raw=ut.x_raw if ut else "",
        x_reference=ut.x_reference if ut else None,
        error_expected=ut.error_expected if ut else 1e-6,
    )

    # Layer 1: Dymola defaults (already set in dataclass)
    # Layer 2: experiment() annotation
    if exp:
        if exp.stop_time is not None:
            model.stop_time = exp.stop_time
        if exp.tolerance is not None:
            model.tolerance = exp.tolerance
        if exp.method is not None:
            model.method = exp.method
        if exp.number_of_intervals is not None:
            model.number_of_intervals = exp.number_of_intervals

    # Layer 3: runAll_Dymola.mos (highest priority)
    if sim_params:
        model.in_mos = True
        if sim_params.stop_time is not None:
            model.stop_time = sim_params.stop_time
        if sim_params.tolerance is not None:
            model.tolerance = sim_params.tolerance
        if sim_params.method is not None:
            model.method = sim_params.method
        if sim_params.number_of_intervals is not None:
            model.number_of_intervals = sim_params.number_of_intervals
        if sim_params.output_interval is not None:
            model.output_interval = sim_params.output_interval
        if sim_params.result_file is not None:
            model.result_file = sim_params.result_file

    if not model.result_file:
        model.result_file = short_name

    return model


def discover_tests(config: Config) -> list[TestModel]:
    """Discover all test models from UnitTests blocks and/or external spec.

    Sources (merged by model_id):
    1. UnitTests components in .mo files
    2. External test_spec.json (if configured)
    3. runAll_Dymola.mos for simulation parameter overrides

    When a model appears in both UnitTests and spec, variable patterns
    from the spec are added alongside UnitTests variables, and the source
    is marked as "both". Spec simulation parameters override UnitTests defaults.
    """
    # Step 1: Parse .mos file for simulation parameter overrides
    mos_params: dict[str, SimParams] = {}
    if config.mos_file.exists():
        mos_params = parse_mos_file(config.mos_file)

    # Step 2: Discover UnitTests from .mo files
    ut_tests: dict[str, TestModel] = {}
    library_dir = config.library_dir

    for mo_file in sorted(library_dir.rglob("*.mo")):
        try:
            content = mo_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "UnitTests" not in content:
            continue

        result = parse_mo_file(mo_file)
        if result is None:
            continue

        sim = mos_params.get(result.model_id)
        test = _merge_params(result, sim)
        ut_tests[test.model_id] = test

    # Step 3: Load external test spec (if configured)
    spec_tests: dict[str, TestModel] = {}
    if config.test_spec_file and config.test_spec_file.exists():
        from .spec_parser import parse_test_spec
        for test in parse_test_spec(config.test_spec_file):
            # Apply .mos overrides to spec tests too
            sim = mos_params.get(test.model_id)
            if sim:
                test.in_mos = True
                if sim.stop_time is not None:
                    test.stop_time = sim.stop_time
                if sim.tolerance is not None:
                    test.tolerance = sim.tolerance
                if sim.method is not None:
                    test.method = sim.method
                if sim.number_of_intervals is not None:
                    test.number_of_intervals = sim.number_of_intervals
                if sim.output_interval is not None:
                    test.output_interval = sim.output_interval
                if sim.result_file is not None:
                    test.result_file = sim.result_file
            spec_tests[test.model_id] = test

    # Step 4: Merge — union by model_id
    merged: dict[str, TestModel] = {}

    # Start with UnitTests
    for model_id, test in ut_tests.items():
        merged[model_id] = test

    # Merge spec tests
    for model_id, spec_test in spec_tests.items():
        if model_id in merged:
            # Both sources — merge variable patterns, spec params override
            existing = merged[model_id]
            existing.variable_patterns = spec_test.variable_patterns
            existing.source = "both"
            # Spec simulation params override UnitTests (if explicitly set in spec)
            if spec_test.stop_time != DEFAULT_STOP_TIME:
                existing.stop_time = spec_test.stop_time
            if spec_test.tolerance != DEFAULT_TOLERANCE:
                existing.tolerance = spec_test.tolerance
            if spec_test.method != DEFAULT_METHOD:
                existing.method = spec_test.method
            if spec_test.number_of_intervals is not None:
                existing.number_of_intervals = spec_test.number_of_intervals
            if spec_test.output_interval is not None:
                existing.output_interval = spec_test.output_interval
        else:
            # Spec-only test
            merged[model_id] = spec_test

    # Sort by model_id for consistent ordering
    tests = sorted(merged.values(), key=lambda t: t.model_id)
    return tests


def generate_mos_file(tests: list[TestModel], output_path: Path) -> None:
    """Regenerate runAll_Dymola.mos from discovered tests."""
    lines = []
    for test in tests:
        parts = [f'"{test.model_id}"']

        if test.stop_time != DEFAULT_STOP_TIME:
            # Format nicely: use int if whole number, else float
            if test.stop_time == int(test.stop_time):
                parts.append(f"stopTime={int(test.stop_time)}")
            else:
                parts.append(f"stopTime={test.stop_time}")

        if test.number_of_intervals is not None:
            parts.append(f"numberOfIntervals={test.number_of_intervals}")

        if test.output_interval is not None:
            parts.append(f"outputInterval={test.output_interval}")

        if test.method != DEFAULT_METHOD:
            parts.append(f'method="{test.method}"')

        parts.append(f"tolerance={test.tolerance:.0e}")
        parts.append(f'resultFile="{test.result_file}"')

        line = "simulateModel(" + ",".join(parts) + ");"
        lines.append(line)

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
