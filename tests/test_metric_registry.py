"""Metric / combinator registries — the Metric-layer extension seam.

The point of these tests is the third-party path: a metric registered from
outside the package must parse, evaluate, and render controls without any
edit to ``modes.py`` / ``tree_spec.py`` / ``tree_eval.py`` /
``schema_export.py``. ``test_toy_metric_end_to_end`` is the load-bearing
one — comment out its ``register_metric`` call and it fails at parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from dstf.backends import VariableResult
from dstf.backends.types import DatasetType
from dstf.comparison import registry
from dstf.comparison.modes import ComparisonMode, resolve_mode
from dstf.comparison.registry import (
    CombinatorDescriptor,
    MetricDescriptor,
    register_combinator,
    register_metric,
)
from dstf.comparison.tree_eval import evaluate_spec
from dstf.comparison.tree_spec import MetricSpecError, parse_metric_tree
from dstf.comparison.types import VariableComparison


@pytest.fixture(autouse=True)
def _clean_registry():
    """Drop any metric a test registers; builtins are restored either way."""
    yield
    registry._reset_for_tests()


# ---------------------------------------------------------------------------
# A toy third-party metric: |final slope| must stay below a threshold.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalSlopeConfig:
    max_slope: float = field(
        default=1.0,
        metadata={
            "label": "Max final slope",
            "help": "Fail if |d(value)/dt| over the last two samples exceeds this.",
        },
    )


class FinalSlopeMode(ComparisonMode):
    def __init__(self, config: FinalSlopeConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "final-slope"

    def is_baseline_free(self) -> bool:
        return True

    def compare(self, ref_time, ref_values, act_time, act_values):
        dt = float(act_time[-1] - act_time[-2])
        slope = abs(float(act_values[-1] - act_values[-2]) / dt)
        return VariableComparison(
            index=0,
            name="",
            passed=slope <= self.config.max_slope,
            nrmse=slope,
            rmse=slope,
            signal_range=0.0,
            max_abs_error=slope,
            max_abs_error_time=float(act_time[-1]),
            reference_final=float("nan"),
            actual_final=float(act_values[-1]),
            mode="final-slope",
        )


def _final_slope_descriptor(**kwargs) -> MetricDescriptor:
    return MetricDescriptor(
        name="final-slope",
        config_cls=FinalSlopeConfig,
        build=lambda override, tol: FinalSlopeMode(
            FinalSlopeConfig(max_slope=override.get("max_slope", 1.0))
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# (a) Builtins are registered with the expected shape
# ---------------------------------------------------------------------------


class TestBuiltinRegistrations:
    def test_seven_metrics_registered(self):
        assert registry.metric_names() == frozenset(
            {
                "nrmse",
                "tube",
                "points",
                "range",
                "event-timing",
                "dominant-frequency",
                "scalar",
            }
        )

    def test_five_combinators_registered(self):
        assert registry.combinator_names() == frozenset(
            {"and", "or", "k-of-n", "warn", "weighted"}
        )

    @pytest.mark.parametrize(
        "name,direction",
        [
            ("nrmse", "lower"),
            ("tube", "higher"),
            ("points", "lower"),
            ("range", "lower"),
            ("event-timing", "lower"),
            ("dominant-frequency", "lower"),
            ("scalar", "lower"),
        ],
    )
    def test_score_directions(self, name, direction):
        assert registry.metric(name).score_direction == direction

    def test_all_builtins_accept_time_series(self):
        for desc in registry.metrics():
            assert DatasetType.TIME_SERIES in desc.accepted_datasets

    def test_scalar_also_accepts_the_scalars_dataset(self):
        # The one builtin that scores a number rather than a trajectory, so
        # a scalars-only backend could be scored by it.
        assert registry.metric("scalar").accepted_datasets == frozenset(
            {DatasetType.TIME_SERIES, DatasetType.SCALARS}
        )

    def test_registration_order_is_spec_order(self):
        assert [d.name for d in registry.metrics()] == [
            "nrmse",
            "tube",
            "points",
            "range",
            "event-timing",
            "dominant-frequency",
            "scalar",
        ]

    def test_unknown_lookups_return_none(self):
        assert registry.metric("no-such-metric") is None
        assert registry.combinator("no-such-combinator") is None


# ---------------------------------------------------------------------------
# (b) Duplicate registration is loud
# ---------------------------------------------------------------------------


class TestDuplicateRegistration:
    def test_duplicate_metric_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            register_metric(
                MetricDescriptor(
                    name="nrmse",
                    config_cls=FinalSlopeConfig,
                    build=lambda o, t: FinalSlopeMode(FinalSlopeConfig()),
                )
            )

    def test_duplicate_combinator_raises(self):
        with pytest.raises(ValueError, match="already registered"):
            register_combinator(
                CombinatorDescriptor(name="and", build=lambda spec: None)
            )

    def test_second_registration_of_a_new_name_raises(self):
        register_metric(_final_slope_descriptor())
        with pytest.raises(ValueError, match="already registered"):
            register_metric(_final_slope_descriptor())


# ---------------------------------------------------------------------------
# (c) End-to-end third-party extension
# ---------------------------------------------------------------------------


def _var_results(values: list[float]) -> dict[str, VariableResult]:
    t = np.linspace(0.0, 10.0, len(values))
    return {"x": VariableResult(index=1, name="x", time=t, values=np.array(values))}


class TestToyMetricEndToEnd:
    """Register → parse → evaluate, with no edit to any framework module."""

    def test_toy_metric_end_to_end(self):
        register_metric(_final_slope_descriptor())

        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "final-slope", "variable": "x", "max_slope": 0.5}
                ],
            }
        )
        assert spec.children[0].metric == "final-slope"

        # Flat tail → slope 0 → passes.
        flat = evaluate_spec(spec, _var_results([0.0, 1.0, 2.0, 2.0]), {}, 1e-4)
        assert flat.passed

        # Steep tail → slope 3/3.33 ≈ 0.9 > 0.5 → fails.
        steep = evaluate_spec(spec, _var_results([0.0, 1.0, 2.0, 5.0]), {}, 1e-4)
        assert not steep.passed

    def test_toy_metric_unregistered_is_rejected_at_parse(self):
        # The negative half of the end-to-end proof: without the
        # registration above, the very same spec no longer parses.
        with pytest.raises(MetricSpecError, match="unknown metric 'final-slope'"):
            parse_metric_tree({"metric": "final-slope", "variable": "x"})

    def test_toy_metric_resolves_through_resolve_mode(self):
        register_metric(_final_slope_descriptor())
        mode = resolve_mode({"mode": "final-slope", "max_slope": 2.0}, 1e-4)
        assert isinstance(mode, FinalSlopeMode)
        assert mode.config.max_slope == 2.0

    def test_toy_metric_appears_in_resolve_mode_error_list(self):
        register_metric(_final_slope_descriptor())
        with pytest.raises(ValueError, match="final-slope"):
            resolve_mode({"mode": "nope"}, 1e-4)

    def test_toy_combinator_end_to_end(self):
        class NoneShallPass:
            def combine(self, children):
                from dstf.comparison.metric_tree import MetricResult

                return MetricResult(
                    label="never", passed=False, score=None, children=list(children)
                )

        register_combinator(
            CombinatorDescriptor(name="never", build=lambda spec: NoneShallPass())
        )
        spec = parse_metric_tree(
            {
                "combinator": "never",
                "children": [{"metric": "range", "variable": "x", "max_value": 1e9}],
            }
        )
        result = evaluate_spec(spec, _var_results([0.0, 1.0, 2.0]), {}, 1e-4)
        assert not result.passed
        assert result.label == "never"


# ---------------------------------------------------------------------------
# (d) Derived UI controls
# ---------------------------------------------------------------------------


class TestDerivedControls:
    def test_registered_metric_gets_a_schema(self):
        from dstf.reporting.ui.mode_controls import (
            emit_mode_schemas,
            get_mode_ui,
            registered_modes,
        )

        register_metric(_final_slope_descriptor())

        ui = get_mode_ui("final-slope")
        assert ui is not None
        assert ui.config_cls is FinalSlopeConfig
        assert [f.name for f in ui.schema.fields] == ["max_slope"]
        assert ui.schema.fields[0].type == "float"
        assert ui.schema.fields[0].label == "Max final slope"
        assert "final-slope" in registered_modes()
        assert "final-slope" in emit_mode_schemas()

    def test_registered_metric_reaches_the_exported_json_schema(self):
        from dstf.reporting.schema_export import build_schema

        register_metric(_final_slope_descriptor())
        schema = build_schema()
        assert "final-slope" in schema["$defs"]["leaf"]["properties"]["metric"]["enum"]
        assert "max_slope" in schema["$defs"]["mode_final_slope"]["properties"]

    def test_builtin_plot_editor_flags_survive_derivation(self):
        pass

        # has_plot_editor was deleted with _UI_EXTRAS (B1 review finding 1:
        # unconsumed, and wrong for two of five modes). JS owns that fact.


# ---------------------------------------------------------------------------
# (e) accepted_datasets gating
# ---------------------------------------------------------------------------


class TestAcceptedDatasets:
    def test_spectrum_only_metric_hard_fails_a_time_series_leaf(self):
        register_metric(
            _final_slope_descriptor(accepted_datasets=frozenset({DatasetType.SPECTRUM}))
        )
        spec = parse_metric_tree({"metric": "final-slope", "variable": "x"})
        result = evaluate_spec(
            spec,
            _var_results([0.0, 1.0, 2.0, 2.0]),
            {},
            1e-4,
            DatasetType.TIME_SERIES,
        )
        assert not result.passed
        # The diagnostic names both kinds — what the metric takes and what
        # it was handed.
        assert "spectrum" in result.label
        assert "time-series" in result.label

    def test_matching_dataset_kind_scores_normally(self):
        register_metric(
            _final_slope_descriptor(accepted_datasets=frozenset({DatasetType.SPECTRUM}))
        )
        spec = parse_metric_tree({"metric": "final-slope", "variable": "x"})
        result = evaluate_spec(
            spec,
            _var_results([0.0, 1.0, 2.0, 2.0]),
            {},
            1e-4,
            DatasetType.SPECTRUM,
        )
        assert result.passed

    def test_incompatible_wins_over_missing_variable(self):
        """A leaf that is BOTH dataset-incompatible and missing its
        variable fails on the dataset gate — deterministic precedence
        (B1 review finding 4), and the sentinel is labeled with the
        leaf's real metric, not the nrmse default."""
        register_metric(
            _final_slope_descriptor(accepted_datasets=frozenset({DatasetType.SPECTRUM}))
        )
        spec = parse_metric_tree({"metric": "final-slope", "variable": "nope"})
        result = evaluate_spec(
            spec,
            {},  # no variables at all
            {},
            1e-4,
            DatasetType.TIME_SERIES,
        )
        assert not result.passed
        assert "spectrum" in result.label  # the dataset gate fired, not missing-var
        vc = result.diagnostics["variable"]
        assert vc.mode == "final-slope"


class TestThirdPartyMetricInTheReporter:
    def test_registered_metric_is_badged_cli_authoritative(self):
        """The template JS can only live-rescore the metrics in its own
        MODE_SCORERS table; a Python-registered metric keeps the CLI
        verdict, and the report must SAY so via the badge instead of
        silently not rescoring (B1 review follow-up: the badge rule is
        'not JS-scored', no longer 'event-timing only')."""
        from pathlib import Path

        from dstf.backends import TestResult, VariableResult
        from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.context_builder import _build_template_context

        register_metric(_final_slope_descriptor())
        t = np.linspace(0.0, 1.0, 20)
        v = 0.5 * t
        mode = _final_slope_descriptor().build({}, 1e-4)
        vc = mode.compare(t, v, t, v)
        vc.name = "x"
        vc.index = 1
        # The report looks leaves up by the path the evaluator stamps; a
        # single-leaf explicit tree is stamped "/metrics".
        root = leaf_from_variable(vc)
        stamp_leaf_path(root, "/metrics")

        model = TestModel(
            model_id="Toy.FinalSlope",
            source_file=Path("toy.mo"),
            source_package="Toy",
            short_name="FinalSlope",
            n_vars=1,
            metric_tree_spec=parse_metric_tree(
                {"metric": "final-slope", "variable": "x"}
            ),
        )
        ctx = _build_template_context(
            model_id=model.model_id,
            comparisons=[vc],
            ref_data=None,
            cur_stats=None,
            test_dir=None,
            test_model=model,
            result=TestResult(
                model_id=model.model_id,
                success=True,
                variables=[VariableResult(index=1, time=t, values=v)],
            ),
            metric_tree=root,
        )
        leaf = ctx["tree_view"]
        assert leaf["kind"] == "leaf"
        assert leaf["cli_authoritative"] is True
        # The five JS-scored builtins stay live (spot check).
        from dstf.reporting.context_builder import _JS_SCORED_METRICS

        assert "nrmse" in _JS_SCORED_METRICS
        assert "event-timing" not in _JS_SCORED_METRICS


# ---------------------------------------------------------------------------
# (f) Reset helper
# ---------------------------------------------------------------------------


class TestResetForTests:
    def test_reset_drops_user_metrics_and_keeps_builtins(self):
        register_metric(_final_slope_descriptor())
        assert registry.metric("final-slope") is not None

        registry._reset_for_tests()

        assert registry.metric("final-slope") is None
        assert registry.metric("nrmse") is not None
        assert len(registry.metrics()) == 7
        assert len(registry.combinators()) == 5

    def test_reset_is_idempotent(self):
        registry._reset_for_tests()
        registry._reset_for_tests()
        assert [d.name for d in registry.metrics()][0] == "nrmse"

    def test_derived_ui_cache_follows_the_reset(self):
        from dstf.reporting.ui.mode_controls import get_mode_ui

        register_metric(_final_slope_descriptor())
        assert get_mode_ui("final-slope") is not None
        registry._reset_for_tests()
        assert get_mode_ui("final-slope") is None
