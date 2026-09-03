"""Registration of the built-in metrics and combinators.

Imported for its side effect by the package ``__init__`` — the same shape
``simulators`` uses for its backends. Each ``build`` here reproduces the
per-mode construction that used to live in ``modes.resolve_mode``'s
if/elif chain, so it is the single place that knows how an override dict
turns into a configured mode.
"""

from __future__ import annotations

from ..simulators.types import DatasetType
from .metric_tree import (
    AndCombinator,
    KOfNCombinator,
    OrCombinator,
    WarnCombinator,
    WeightedCombinator,
)
from .modes import (
    _TUBE_KEYS,
    DominantFrequencyConfig,
    DominantFrequencyMode,
    EventTimingConfig,
    EventTimingMode,
    NrmseConfig,
    NrmseMode,
    PointsConfig,
    PointsMode,
    RangeConfig,
    RangeMode,
    ScalarConfig,
    ScalarMode,
    TubeConfig,
    TubeMode,
)
from .registry import (
    DEFAULT_METRIC,
    CombinatorDescriptor,
    MetricDescriptor,
    metric,
    register_combinator,
    register_metric,
)


def _build_nrmse(override: dict, tolerance: float) -> NrmseMode:
    return NrmseMode(NrmseConfig(tolerance=tolerance))


def _build_tube(override: dict, tolerance: float) -> TubeMode:
    tube_kwargs = {k: override[k] for k in _TUBE_KEYS if k in override}
    return TubeMode(TubeConfig(**tube_kwargs))


def _build_points(override: dict, tolerance: float) -> PointsMode:
    return PointsMode(
        PointsConfig(
            points=override.get("points"),
            tolerance=tolerance,
        )
    )


def _build_range(override: dict, tolerance: float) -> RangeMode:
    # ``min_value`` / ``max_value`` only — the names the ``RangeConfig``
    # dataclass, the auto-derived UI and MetricTree leaf params all use.
    return RangeMode(
        RangeConfig(
            min_value=override.get("min_value"),
            max_value=override.get("max_value"),
        )
    )


def _build_event_timing(override: dict, tolerance: float) -> EventTimingMode:
    return EventTimingMode(
        EventTimingConfig(
            time_tolerance=override.get("time_tolerance", 1e-3),
            count_must_match=override.get("count_must_match", True),
            events=override.get("events"),
        )
    )


def _build_dominant_frequency(
    override: dict, tolerance: float
) -> DominantFrequencyMode:
    return DominantFrequencyMode(
        DominantFrequencyConfig(
            peaks=override.get("peaks"),
        )
    )


def _build_scalar(override: dict, tolerance: float) -> ScalarMode:
    return ScalarMode(
        ScalarConfig(
            quantity=override.get("quantity"),
            tolerance=tolerance,
            tolerance_mode=override.get("tolerance_mode", "abs"),
            min_value=override.get("min_value"),
            max_value=override.get("max_value"),
        )
    )


def register_builtins() -> None:
    """Register the seven built-in metrics and five combinators.

    Registration order is the canonical presentation order (schema export
    ``$defs``, the reporter's mode-schema map, the "valid: ..." list in
    :func:`~.modes.resolve_mode`'s error message).

    Idempotent: ``importlib.reload(dstf.comparison)`` re-executes the
    package init, and a second registration pass must not trip the
    duplicate-name guard (B1 review finding 3).
    """
    if metric(DEFAULT_METRIC) is not None:
        return
    register_metric(
        MetricDescriptor(
            name="nrmse",
            config_cls=NrmseConfig,
            build=_build_nrmse,
            score_direction="lower",
        )
    )
    register_metric(
        MetricDescriptor(
            name="tube",
            config_cls=TubeConfig,
            build=_build_tube,
            # Tube's score is the fraction of samples inside the envelope.
            score_direction="higher",
        )
    )
    register_metric(
        MetricDescriptor(
            name="points",
            config_cls=PointsConfig,
            build=_build_points,
            score_direction="lower",
        )
    )
    register_metric(
        MetricDescriptor(
            name="range",
            config_cls=RangeConfig,
            build=_build_range,
            score_direction="lower",
        )
    )
    register_metric(
        MetricDescriptor(
            name="event-timing",
            config_cls=EventTimingConfig,
            build=_build_event_timing,
            score_direction="lower",
        )
    )
    register_metric(
        MetricDescriptor(
            name="dominant-frequency",
            config_cls=DominantFrequencyConfig,
            build=_build_dominant_frequency,
            score_direction="lower",
        )
    )
    register_metric(
        MetricDescriptor(
            name="scalar",
            config_cls=ScalarConfig,
            build=_build_scalar,
            # The one metric that scores a number rather than a trajectory:
            # it works on a scalars-only dataset as well as on a time series
            # it reduces itself.
            accepted_datasets=frozenset({DatasetType.TIME_SERIES, DatasetType.SCALARS}),
            score_direction="lower",
            # The only builtin whose ``quantity`` names its data source.
            accepts_quantity=True,
        )
    )

    register_combinator(
        CombinatorDescriptor(name="and", build=lambda spec: AndCombinator())
    )
    register_combinator(
        CombinatorDescriptor(name="or", build=lambda spec: OrCombinator())
    )
    register_combinator(
        CombinatorDescriptor(name="k-of-n", build=lambda spec: KOfNCombinator(spec.k))
    )
    register_combinator(
        CombinatorDescriptor(name="warn", build=lambda spec: WarnCombinator())
    )
    register_combinator(
        CombinatorDescriptor(
            name="weighted",
            build=lambda spec: WeightedCombinator(
                spec.weights, spec.threshold, spec.direction
            ),
        )
    )
