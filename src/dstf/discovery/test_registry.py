"""Orchestrate test discovery: find test models and merge parameters."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..config import (
    Config,
    DEFAULT_METHOD,
    DEFAULT_STOP_TIME,
    DEFAULT_TOLERANCE,
)
from .recognizer import RecognizerResult, get_recognizers

if TYPE_CHECKING:
    from ..comparison.tree_spec import SpecNode


@dataclass
class TestModel:
    """A fully resolved test model with all metadata needed for simulation."""

    model_id: str
    source_file: Path
    source_package: str
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

    # Comparison settings (per-test and per-variable overrides)
    comparison_tolerance: Optional[float] = None  # Overrides config.tolerance
    variable_overrides: dict[str, dict] = field(default_factory=dict)
    # variable_overrides format: {"var_name": {"tolerance": 0.1}, ...}

    # Phase 3.2: optional user-authored MetricTree spec from test_spec.json
    # under the "metrics" key. When set, Phase 3.3+ will use this in place
    # of the implicit flat-AND. Parse-only today — no behavior gated on it.
    metric_tree_spec: Optional["SpecNode"] = None

    # PTA.4 — richer-contract fields settable via recognizers. Additive;
    # defaults preserve pre-PTA behavior.
    #
    # simulate_only: when True, the test passes iff the simulation completes
    # without error — no per-variable comparison. Wired end-to-end in PTA.5.
    simulate_only: bool = False
    # requested_fmu_export: placeholder for 4.B (cross-backend verification).
    # Recognizers can set this today; no consumer until 4.B lands.
    requested_fmu_export: bool = False
    # requested_baselines: names of additional baselines to produce for this
    # test (e.g., ["dymola-via-fmpy"]). Placeholder for 4.B.
    requested_baselines: list[str] = field(default_factory=list)

    # Per-field provenance: where the value came from.
    # Keys: "stop_time", "tolerance", "method", "number_of_intervals",
    # "output_interval". Values: "annotation", "test_spec", "default".
    # Populated during recognizer + spec merge so the dashboard's
    # resolution-explainer column can show "stop_time: 10 (annotation)".
    field_sources: dict[str, str] = field(default_factory=dict)

    # Where this test was defined: "unit_tests", "spec", "both"
    source: str = "unit_tests"


def _build_test_model_from_recognizer_results(
    model_id: str,
    results: list[RecognizerResult],
) -> TestModel:
    """Merge per-recognizer results for one model into a TestModel.

    Later results override earlier ones per-field. The bundled recognizer
    registers first; user-provided recognizers (PTA.3) append, so user values
    win on conflicts. Fields no recognizer set fall back to TestModel's
    dataclass defaults.
    """
    parts = model_id.rsplit(".", 1)
    source_package = parts[0] if len(parts) > 1 else ""
    short_name = parts[-1]

    model = TestModel(
        model_id=model_id,
        source_file=Path(""),
        source_package=source_package,
        short_name=short_name,
        n_vars=1,
        result_file=short_name,
    )

    for r in results:
        if r.source_file is not None:
            model.source_file = r.source_file
        if r.n_vars is not None:
            model.n_vars = r.n_vars
        if r.x_expressions:
            model.x_expressions = list(r.x_expressions)
        if r.x_raw:
            model.x_raw = r.x_raw
        if r.x_reference is not None:
            model.x_reference = list(r.x_reference)
        if r.error_expected is not None:
            model.error_expected = r.error_expected
        if r.stop_time is not None:
            model.stop_time = r.stop_time
            model.field_sources["stop_time"] = "annotation"
        if r.tolerance is not None:
            model.tolerance = r.tolerance
            model.field_sources["tolerance"] = "annotation"
        if r.method is not None:
            model.method = r.method
            model.field_sources["method"] = "annotation"
        if r.number_of_intervals is not None:
            model.number_of_intervals = r.number_of_intervals
            model.field_sources["number_of_intervals"] = "annotation"
        if r.output_interval is not None:
            model.output_interval = r.output_interval
            model.field_sources["output_interval"] = "annotation"
        if r.simulate_only is not None:
            model.simulate_only = r.simulate_only
        if r.requested_fmu_export is not None:
            model.requested_fmu_export = r.requested_fmu_export
        if r.requested_baselines is not None:
            model.requested_baselines = list(r.requested_baselines)

    return model


def discover_tests(config: Config) -> list[TestModel]:
    """Discover all test models from registered recognizers and/or external spec.

    Sources (merged by model_id):
    1. Source-file recognizers (PTA.1+) — for ``source_type == "modelica"``,
       walks ``*.mo`` and runs every registered Modelica recognizer. Bundled
       default is the ``UnitTests`` + ``experiment(...)`` recognizer; user-
       provided recognizers (PTA.3) layer on top.
    2. External test_spec.json (if configured, overrides recognizer values).

    When a model appears in both recognizers and spec, variable patterns
    from the spec are added alongside the recognizer-derived variables, and
    the source is marked as "both". Spec simulation parameters override
    recognizer values when explicitly set.
    """
    # Trigger bundled-recognizer registration on first call.
    from . import mo_parser  # noqa: F401

    # Step 1: Run recognizers over source files.
    # Bundled recognizers come from the module-level registry (filtered by
    # config.disabled_bundled); user-provided recognizers from config.recognizers
    # append, so user values win on per-field merge in
    # _build_test_model_from_recognizer_results.
    ut_tests: dict[str, TestModel] = {}
    if config.source_type == "modelica":
        library_dir = config.library_dir
        bundled = [
            r
            for r in get_recognizers("modelica")
            if r.name not in config.disabled_bundled
        ]
        user = [r for r in config.recognizers if "modelica" in r.applies_to]
        recognizers = bundled + user

        per_model: dict[str, list[RecognizerResult]] = {}
        for mo_file in sorted(library_dir.rglob("*.mo")):
            for recognizer in recognizers:
                if not recognizer.applies_to_path(mo_file, library_dir):
                    continue
                result = recognizer.recognize(mo_file)
                if result is not None:
                    per_model.setdefault(result.model_id, []).append(result)

        for model_id, results in per_model.items():
            ut_tests[model_id] = _build_test_model_from_recognizer_results(
                model_id,
                results,
            )

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
            existing = merged[model_id]
            existing.variable_patterns = spec_test.variable_patterns
            existing.source = "both"
            if spec_test.stop_time != DEFAULT_STOP_TIME:
                existing.stop_time = spec_test.stop_time
                existing.field_sources["stop_time"] = "test_spec"
            if spec_test.tolerance != DEFAULT_TOLERANCE:
                existing.tolerance = spec_test.tolerance
                existing.field_sources["tolerance"] = "test_spec"
            if spec_test.method != DEFAULT_METHOD:
                existing.method = spec_test.method
                existing.field_sources["method"] = "test_spec"
            if spec_test.number_of_intervals is not None:
                existing.number_of_intervals = spec_test.number_of_intervals
                existing.field_sources["number_of_intervals"] = "test_spec"
            if spec_test.output_interval is not None:
                existing.output_interval = spec_test.output_interval
                existing.field_sources["output_interval"] = "test_spec"
            if spec_test.comparison_tolerance is not None:
                existing.comparison_tolerance = spec_test.comparison_tolerance
            if spec_test.variable_overrides:
                existing.variable_overrides.update(spec_test.variable_overrides)
            if spec_test.timeout is not None:
                existing.timeout = spec_test.timeout
            if spec_test.metric_tree_spec is not None:
                existing.metric_tree_spec = spec_test.metric_tree_spec
        else:
            for fname in (
                "stop_time",
                "tolerance",
                "method",
                "number_of_intervals",
                "output_interval",
            ):
                spec_test.field_sources.setdefault(fname, "test_spec")
            merged[model_id] = spec_test

    # Sort by model_id for consistent ordering
    tests = sorted(merged.values(), key=lambda t: t.model_id)
    return tests
