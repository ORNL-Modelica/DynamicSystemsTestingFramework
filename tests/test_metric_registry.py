"""Metric / combinator registries — the Metric-layer extension seam.

The point of these tests is the third-party path: a metric registered from
outside the package must parse, evaluate, and render controls without any
edit to ``modes.py`` / ``tree_spec.py`` / ``tree_eval.py`` /
``schema_export.py``. ``test_toy_metric_end_to_end`` is the load-bearing
one — comment out its ``register_metric`` call and it fails at parse.
"""

from __future__ import annotations

import dataclasses
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
            # The evaluator stamps the leaf's variable over this; a mode
            # builds its comparison before it knows which one it scored.
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

    def test_every_builtin_config_is_frozen(self):
        for desc in registry.metrics():
            config = desc.config_cls()
            first = dataclasses.fields(config)[0].name
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(config, first, None)

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
        from dstf.comparison.mode_schema import derive_schema
        from dstf.comparison.registry import metric

        register_metric(_final_slope_descriptor())

        desc = metric("final-slope")
        assert desc is not None
        assert desc.config_cls is FinalSlopeConfig
        schema = derive_schema(desc.config_cls, mode="final-slope")
        assert [f.name for f in schema.fields] == ["max_slope"]
        assert schema.fields[0].type == "float"
        assert schema.fields[0].label == "Max final slope"

    def test_registered_metric_reaches_the_exported_json_schema(self):
        from dstf.reporting.schema_export import build_schema

        register_metric(_final_slope_descriptor())
        schema = build_schema()
        assert "final-slope" in schema["$defs"]["leaf"]["properties"]["metric"]["enum"]
        assert "max_slope" in schema["$defs"]["mode_final_slope"]["properties"]


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
        # The label is the variable — the plot pairs by it — and the
        # diagnostic names both kinds: what the metric takes and what it
        # was handed.
        assert result.label == "x"
        error = result.diagnostics["variable"].diagnostics["error"]
        assert "spectrum" in error
        assert "time-series" in error

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
        vc = result.diagnostics["variable"]
        # the dataset gate fired, not missing-var
        assert "spectrum" in vc.diagnostics["error"]
        assert vc.mode == "final-slope"


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


def test_a_metric_says_whether_its_drawing_shows_by_default():
    # A bound across the whole plot (range, scalar) starts hidden on the Test
    # page when it is a closed criterion that is not failing; every other
    # builtin draws until its swatch says otherwise.
    assert {d.name: d.drawn_by_default for d in registry.metrics()} == {
        "nrmse": True,
        "tube": True,
        "points": True,
        "range": False,
        "event-timing": True,
        "dominant-frequency": True,
        "scalar": False,
    }
    # A third-party metric registered without the field draws by default.
    assert _final_slope_descriptor().drawn_by_default is True


class TestABuilderTakesSides:
    """`MetricDescriptor.build(override, tolerance)`: the resolved tolerance
    arrives as `Sides`, and `resolve_mode` makes one of a number."""

    def test_resolve_mode_hands_the_builder_sides(self):
        from dstf.comparison.sides import Sides

        seen = []

        def build(override, tolerance):
            seen.append(tolerance)
            return registry.metric("nrmse").build(override, tolerance)

        register_metric(
            MetricDescriptor(name="spy", config_cls=FinalSlopeConfig, build=build)
        )
        resolve_mode({"mode": "spy"}, 0.25)
        resolve_mode({"mode": "spy"}, Sides(0.25, 0.25))
        assert seen == [Sides(0.25, 0.25), Sides(0.25, 0.25)]

    def test_the_builtins_keep_the_sides(self):
        from dstf.comparison.sides import Sides

        build = lambda name, override=None: registry.metric(name).build(  # noqa: E731
            override or {}, Sides(0.25, 0.25)
        )
        assert build("points").config.tolerance == Sides(0.25, 0.25)
        assert build("scalar", {"quantity": "a.b"}).config.tolerance == Sides(
            0.25, 0.25
        )
        assert build("event-timing").config.time_tolerance == Sides(1e-3, 1e-3)
        assert build("event-timing", {"time_tolerance": 2}).config.time_tolerance == (
            Sides(2.0, 2.0)
        )
        tube = build("tube", {"tube_width_mode": "band", "tube_abs": 0.5}).config
        assert (tube.tube_abs, tube.tube_rel) == (Sides(0.5, 0.5), Sides(0.0, 0.0))
        # nrmse is one ceiling: a float, and unequal sides are refused.
        nrmse = build("nrmse").config.tolerance
        assert nrmse == 0.25 and isinstance(nrmse, float)
        with pytest.raises(
            ValueError, match="^nrmse has one ceiling: tolerance must be a number$"
        ):
            registry.metric("nrmse").build({}, Sides(0.1, 0.2))

    def test_a_mode_built_from_a_config_holding_a_number_scores(self):
        from dstf.comparison.modes import (
            EventTimingConfig,
            EventTimingMode,
            PointsConfig,
            PointsMode,
            ScalarConfig,
            ScalarMode,
            TubeConfig,
            TubeMode,
        )

        t = np.linspace(0.0, 1.0, 11)
        for mode in (
            PointsMode(PointsConfig(tolerance=0.5)),
            ScalarMode(ScalarConfig(quantity={"reduce": "max"}, tolerance=0.5)),
            EventTimingMode(EventTimingConfig(time_tolerance=0.5)),
            TubeMode(TubeConfig(tube_width_mode="band", tube_abs=0.5)),
            PointsMode(PointsConfig()),
            ScalarMode(ScalarConfig(quantity={"reduce": "max"})),
            EventTimingMode(EventTimingConfig()),
        ):
            assert mode.compare(t, t, t, t + 0.25).passed is (
                mode.config != PointsConfig()
                and mode.config != ScalarConfig(quantity={"reduce": "max"})
            )
