"""Orchestrate test discovery: find test models and merge parameters."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import (
    Config,
    DEFAULT_METHOD,
    DEFAULT_STOP_TIME,
    DEFAULT_TOLERANCE,
)
from .mo_parser import MoParseResult, parse_mo_file


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
    timeout: Optional[int] = None  # Per-test timeout override (seconds)

    # External spec: variable patterns (may include globs like "medium.T*" or "*")
    # These are resolved against actual .mat variable names after simulation.
    variable_patterns: list[str] = field(default_factory=list)

    # Where this test was defined: "unit_tests", "spec", "both"
    source: str = "unit_tests"


def _build_test_model(mo_result: MoParseResult) -> TestModel:
    """Build a TestModel from .mo parse results.

    Applies experiment() annotation values over defaults.
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
        result_file=short_name,
    )

    if exp:
        if exp.stop_time is not None:
            model.stop_time = exp.stop_time
        if exp.tolerance is not None:
            model.tolerance = exp.tolerance
        if exp.method is not None:
            model.method = exp.method
        if exp.number_of_intervals is not None:
            model.number_of_intervals = exp.number_of_intervals
        if exp.output_interval is not None:
            model.output_interval = exp.output_interval

    return model


def discover_tests(config: Config) -> list[TestModel]:
    """Discover all test models from UnitTests blocks and/or external spec.

    Sources (merged by model_id):
    1. UnitTests components in .mo files (experiment annotation for sim params)
    2. External test_spec.json (if configured, overrides experiment annotation)

    When a model appears in both UnitTests and spec, variable patterns
    from the spec are added alongside UnitTests variables, and the source
    is marked as "both". Spec simulation parameters override experiment defaults.
    """
    # Step 1: Discover UnitTests from .mo files
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

        test = _build_test_model(result)
        ut_tests[test.model_id] = test

    # Step 2: Load external test spec (if configured)
    spec_tests: dict[str, TestModel] = {}
    if config.test_spec_file and config.test_spec_file.exists():
        from .spec_parser import parse_test_spec
        for test in parse_test_spec(config.test_spec_file):
            spec_tests[test.model_id] = test

    # Step 3: Merge — union by model_id
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
            # Spec simulation params override experiment annotation (if explicitly set)
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
