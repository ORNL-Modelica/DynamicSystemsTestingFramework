"""Registration of the built-in metrics and combinators.

Imported for its side effect by the package ``__init__`` — the same shape
``backends`` uses for its backends. Each ``build`` here reproduces the
per-mode construction that used to live in ``modes.resolve_mode``'s
if/elif chain, so it is the single place that knows how an override dict
turns into a configured mode.
"""

from __future__ import annotations

from ..backends.types import DatasetType
from .handles import (
    edit_events_handle,
    edit_peaks_handle,
    edit_points_handle,
    edit_range_handle,
    edit_scalar_handle,
    edit_tube_handle,
    place_events_row,
    place_peaks_row,
    place_points_row,
    place_tube_row,
    tube_row_noun,
)
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
from .seeds import seed_dominant_frequency, seed_range, seed_scalar, seed_tube
from .sides import Sides, parse_number, parse_sides
from .summaries import (
    summarize_dominant_frequency,
    summarize_event_timing,
    summarize_nrmse,
    summarize_points,
    summarize_range,
    summarize_scalar,
    summarize_tube,
)


def _build_nrmse(override: dict, tolerance: Sides) -> NrmseMode:
    if not tolerance.equal:
        raise ValueError("nrmse has one ceiling: tolerance must be a number")
    return NrmseMode(NrmseConfig(tolerance=tolerance.upper))


def _build_tube(override: dict, tolerance: Sides) -> TubeMode:
    tube_kwargs = {k: override[k] for k in _TUBE_KEYS if k in override}
    for key in ("tube_abs", "tube_rel"):
        if key not in tube_kwargs:
            continue
        if tube_kwargs[key] is None:
            # ``null`` is "no width", as it was when a width was a number
            # (a falsy one was left out): absent, not a config error.
            del tube_kwargs[key]
        else:
            tube_kwargs[key] = parse_sides(tube_kwargs[key], key)
    # The floor has no sides: one number, read here so that anything else
    # is a ``ValueError`` where ``resolve_mode``'s callers catch it — a
    # pair or a string went on to the scorer, which compared it with zero
    # and raised a ``TypeError`` nothing caught. ``null`` is "no floor".
    if tube_kwargs.get("tube_min_width") is None:
        tube_kwargs.pop("tube_min_width", None)
    else:
        tube_kwargs["tube_min_width"] = parse_number(
            tube_kwargs["tube_min_width"], "tube_min_width"
        )
    return TubeMode(TubeConfig(**tube_kwargs))


def _build_points(override: dict, tolerance: Sides) -> PointsMode:
    return PointsMode(
        PointsConfig(
            points=override.get("points"),
            tolerance=tolerance,
        )
    )


def _bound(override: dict, key: str) -> float | None:
    """A ``min_value`` / ``max_value`` as it is written: one number, or
    none (absent, or ``null``). Checked here, as a tube's floor is, so that
    anything else is a ``ValueError`` where ``resolve_mode``'s callers catch
    it: a string, or an integer too large for a float, went on to the
    scorer, which subtracted it from the samples and raised what nothing
    caught."""
    value = override.get(key)
    if value is not None:
        parse_number(value, key)
    return value


def _build_range(override: dict, tolerance: Sides) -> RangeMode:
    # ``min_value`` / ``max_value`` only — the names the ``RangeConfig``
    # dataclass, the auto-derived UI and MetricTree leaf params all use.
    return RangeMode(
        RangeConfig(
            min_value=_bound(override, "min_value"),
            max_value=_bound(override, "max_value"),
        )
    )


def _build_event_timing(override: dict, tolerance: Sides) -> EventTimingMode:
    return EventTimingMode(
        EventTimingConfig(
            time_tolerance=parse_sides(
                override.get("time_tolerance", 1e-3), "time_tolerance"
            ),
            count_must_match=override.get("count_must_match", True),
            events=override.get("events"),
        )
    )


def _build_dominant_frequency(
    override: dict, tolerance: Sides
) -> DominantFrequencyMode:
    return DominantFrequencyMode(
        DominantFrequencyConfig(
            peaks=override.get("peaks"),
        )
    )


def _build_scalar(override: dict, tolerance: Sides) -> ScalarMode:
    return ScalarMode(
        ScalarConfig(
            quantity=override.get("quantity"),
            tolerance=tolerance,
            tolerance_mode=override.get("tolerance_mode", "abs"),
            min_value=_bound(override, "min_value"),
            max_value=_bound(override, "max_value"),
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
            description=(
                "Compares the whole trajectory with the baseline: the "
                "root-mean-square error, divided by the range of the baseline, must "
                "stay below the tolerance. The error is taken piece by piece "
                "between events, so a jump is not smeared across its neighbours. "
                "The default mode, and the one to start with for a signal you "
                "expect to reproduce closely."
            ),
            config_cls=NrmseConfig,
            build=_build_nrmse,
            score_direction="lower",
            summarize=summarize_nrmse,
        )
    )
    register_metric(
        MetricDescriptor(
            name="tube",
            description=(
                "Draws an envelope around the baseline — a band of fixed width, a "
                "width relative to the baseline, or edges you give over time — and "
                "passes when every point of the run lies inside it. Use it when you "
                "can say how far a signal may wander, and where."
            ),
            config_cls=TubeConfig,
            build=_build_tube,
            # Tube's score is the fraction of samples inside the envelope.
            score_direction="higher",
            summarize=summarize_tube,
            edit_handle=edit_tube_handle,
            seed=seed_tube,
            place_row=place_tube_row,
            row_noun=tube_row_noun,
        )
    )
    register_metric(
        MetricDescriptor(
            name="points",
            description=(
                "Checks the run at the times you declare: each checkpoint has a "
                "time, a value (or the baseline's value at that time) and a "
                "tolerance in value and in time. With no checkpoints it compares "
                "the final value with the baseline's. Use it for set points, "
                "settled values and anything that matters at an instant."
            ),
            config_cls=PointsConfig,
            build=_build_points,
            score_direction="lower",
            summarize=summarize_points,
            edit_handle=edit_points_handle,
            place_row=place_points_row,
            row_noun=lambda _leaf: "a point",
        )
    )
    register_metric(
        MetricDescriptor(
            name="range",
            description=(
                "Checks that every point of the run lies between a lower and an "
                "upper bound, either of which may be left out. It reads no "
                "baseline. Use it for physical limits: a temperature that must stay "
                "below a limit, a level that must stay positive."
            ),
            config_cls=RangeConfig,
            build=_build_range,
            score_direction="lower",
            summarize=summarize_range,
            edit_handle=edit_range_handle,
            drawn_by_default=False,
            seed=seed_range,
        )
    )
    register_metric(
        MetricDescriptor(
            name="event-timing",
            description=(
                "Compares when events happen: the instants at which the result "
                "holds two points at the same time, which is how a Modelica tool "
                "records an event. Each event of the run must fall within the time "
                "tolerance of its baseline event, or of an event you declare. Use "
                "it for the timing of switches, valves and relays."
            ),
            config_cls=EventTimingConfig,
            build=_build_event_timing,
            score_direction="lower",
            summarize=summarize_event_timing,
            edit_handle=edit_events_handle,
            place_row=place_events_row,
            row_noun=lambda _leaf: "an event",
        )
    )
    register_metric(
        MetricDescriptor(
            name="dominant-frequency",
            description=(
                "Compares spectra: each frequency you declare must appear as a peak "
                "of the run's spectrum within its tolerance. With no declared peaks "
                "it fails and reports the baseline's strongest peaks to choose "
                "from. Use it for oscillations whose frequency matters more than "
                "their phase."
            ),
            config_cls=DominantFrequencyConfig,
            build=_build_dominant_frequency,
            score_direction="lower",
            summarize=summarize_dominant_frequency,
            edit_handle=edit_peaks_handle,
            seed=seed_dominant_frequency,
            place_row=place_peaks_row,
            row_noun=lambda _leaf: "a peak",
        )
    )
    register_metric(
        MetricDescriptor(
            name="scalar",
            description=(
                "Scores one number: a reduction of the variable (its maximum, "
                "minimum, mean, final value or integral) or a statistic of the run, "
                "against the same number from the baseline or between bounds you "
                "give. Use it for an overshoot, a total, a settled value or a run "
                "statistic such as CPU time."
            ),
            config_cls=ScalarConfig,
            build=_build_scalar,
            # The one metric that scores a number rather than a trajectory:
            # it works on a scalars-only dataset as well as on a time series
            # it reduces itself.
            accepted_datasets=frozenset({DatasetType.TIME_SERIES, DatasetType.SCALARS}),
            score_direction="lower",
            # The only builtin whose ``quantity`` names its data source.
            accepts_quantity=True,
            summarize=summarize_scalar,
            edit_handle=edit_scalar_handle,
            drawn_by_default=False,
            seed=seed_scalar,
        )
    )

    register_combinator(
        CombinatorDescriptor(
            name="and",
            description=(
                "Passes when every child passes. A test with no metrics tree is "
                "scored as an and of its tracked variables."
            ),
            build=lambda spec: AndCombinator(),
        )
    )
    register_combinator(
        CombinatorDescriptor(
            name="or",
            description="Passes when at least one child passes.",
            build=lambda spec: OrCombinator(),
        )
    )
    register_combinator(
        CombinatorDescriptor(
            name="k-of-n",
            description="Passes when at least k of its children pass.",
            build=lambda spec: KOfNCombinator(spec.k),
        )
    )
    register_combinator(
        CombinatorDescriptor(
            name="warn",
            description=(
                "Holds one child, whose failure is reported as a warning and does "
                "not fail the test. A criterion scored against a soft check must "
                "sit under one."
            ),
            build=lambda spec: WarnCombinator(),
        )
    )
    register_combinator(
        CombinatorDescriptor(
            name="weighted",
            description=(
                "Adds up its children's scores, each times its weight, and passes "
                'when the sum is below the threshold (direction "less", for scores '
                'that are errors) or above it ("greater", for scores that are '
                "coverage)."
            ),
            build=lambda spec: WeightedCombinator(
                spec.weights, spec.threshold, spec.direction
            ),
        )
    )
