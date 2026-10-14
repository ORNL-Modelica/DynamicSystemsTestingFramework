"""Comparison mode strategies — pluggable algorithms for comparing variables.

Each mode implements a single `compare()` method that takes reference and actual
time series and returns a VariableComparison.  Mode-specific configuration is
encapsulated in a typed dataclass rather than an untyped dict.

Modes:
    NrmseMode  — normalized RMSE with piecewise event handling (default)
    TubeMode   — tolerance tube envelope around reference trajectory
    PointsMode — compare values at declared time points (or the final value
                 when no points are declared)
    ScalarMode — score one number: a series reduction or a stored statistic
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .algorithms import (
    SCALAR_REDUCTIONS,
    _event_tolerances,
    _peak_tolerance,
    _point_tolerances,
)
from .comparator import (
    VariableComparison,
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_points,
    _compare_range,
    _compare_scalar,
    _compare_trajectories,
    _compare_tube,
)
from .registry import DEFAULT_METRIC, DEFAULT_POINTS_METRIC
from .registry import metric as registry_metric
from .registry import metrics as registered_metrics
from .sides import Sides, parse_sides

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ComparisonMode(ABC):
    """A way of scoring one variable of a run against its baseline: the
    strategy behind a criterion's ``metric`` (a ``variable_overrides`` entry's
    ``mode``).

    A mode plug-in subclasses this, gives it a frozen dataclass of settings,
    and registers both with :func:`~dstf.comparison.registry.register_metric`.
    The dataclass's fields are the keys a criterion of the mode may hold: a key
    that is not a field is refused where the spec is read, and each field's
    ``metadata`` is what the reference page, the published JSON schema and the
    authoring tool's forms show of it (see
    :func:`~dstf.comparison.mode_schema.derive_schema`).

    The framework builds a mode for each criterion it scores, through the
    descriptor's ``build``, and calls :meth:`compare` once, on the part of the
    run the criterion's window keeps.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The name the mode is registered under (``"tube"``).

        Returns:
            The name; the same as its descriptor's.
        """
        ...

    @abstractmethod
    def compare(
        self,
        ref_time: np.ndarray,
        ref_values: np.ndarray,
        act_time: np.ndarray,
        act_values: np.ndarray,
    ) -> VariableComparison:
        """Score a run's variable against the baseline's.

        Args:
            ref_time: The baseline's sample times, in seconds, rising; equal
                neighbours mark an event.
            ref_values: The baseline's values, one per time.
            act_time: The run's sample times, in seconds.
            act_values: The run's values, one per time.

        Returns:
            The verdict, with ``passed``, the score and what the report shows. A
            criterion that cannot be scored — no samples in its window — is a
            comparison with ``passed=False`` and the reason in
            ``diagnostics["error"]``, not an exception.
        """
        ...

    def is_baseline_free(self) -> bool:
        """Whether this mode, as configured, scores without a baseline.

        A test with no stored baseline is still scored when every criterion it has
        is baseline-free; otherwise it waits for one. The default is False.

        Returns:
            True when the configuration alone says what passes (declared
            bounds, declared values).
        """
        return False


# ---------------------------------------------------------------------------
# Mode configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NrmseConfig:
    """Configuration for NRMSE comparison."""

    tolerance: float = field(
        default=1e-4,
        metadata={
            "label": "Tolerance",
            "help": (
                "The ceiling: the criterion passes when the root-mean-square error, "
                "divided by the range of the baseline, is below it. Dividing by the "
                "range lets one number serve variables of any size; a baseline that "
                "is constant is divided by its magnitude instead. One number."
            ),
        },
    )


@dataclass(frozen=True)
class TubeConfig:
    """Configuration for tube comparison.

    Mirrors the fields previously scattered as arbitrary keys in the
    variable_overrides dict, but with types enforced. The tightened
    `Literal[...]` choices feed the auto-derived UI schema
    (comparison/mode_schema.py).
    """

    tube_width_mode: Literal["band", "rel", "abs"] | None = field(
        default=None,
        metadata={
            "label": "Width mode",
            "help": (
                'How the tube\'s width is given; required. "band": a width in the '
                'variable\'s units, from tube_abs or the rows of tube_points. "rel": '
                "a fraction of the baseline's magnitude, from tube_rel or the rows. "
                '"abs": the rows of tube_points are the tube\'s edges themselves.'
            ),
        },
    )
    tube_abs: Sides = field(
        default=Sides(0.0, 0.0),
        metadata={
            "label": "Tube abs",
            "help": (
                'In "band" mode with no tube_points: how far above and below the '
                "baseline the run may be, in the variable's units. One number for "
                "both sides, or a lower/upper pair."
            ),
        },
    )
    tube_rel: Sides = field(
        default=Sides(0.0, 0.0),
        metadata={
            "label": "Tube rel",
            "help": (
                'In "rel" mode with no tube_points: how far above and below the '
                "baseline the run may be, as a fraction of the baseline's magnitude "
                "(0.02 is 2 %). One number for both sides, or a lower/upper pair."
            ),
        },
    )
    tube_min_width: float = field(
        default=0.0,
        metadata={
            "label": "Min width",
            "help": (
                "The narrowest the tube may be on either side, in the variable's "
                "units, so that a relative tube stays open where the baseline "
                'crosses zero. Not used in "abs" mode.'
            ),
        },
    )
    tube_points: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Control points",
            "help": (
                "A tube that changes over time: one row per time, with the width "
                'above (upper) and below (lower) the baseline there — or, in "abs" '
                "mode, the edges. Before the first row and after the last, the "
                "nearest row holds."
            ),
            # Row keys as ``_interpolate_tube_widths`` reads them: ``time``
            # indexed, each width ``.get`` with a 0 default. A row with
            # neither reads 0/0 and pinches the tube there; the reader
            # refuses only a list in which NO row has a width.
            "columns": (("time", "float"), ("upper", "float?"), ("lower", "float?")),
            # What each column of a row holds, for the person writing it.
            "column_help": {
                "time": "The row's time, in seconds.",
                "upper": (
                    'The width above the baseline there, or in "abs" mode the upper '
                    "edge; none is 0."
                ),
                "lower": (
                    'The width below the baseline there, or in "abs" mode the lower '
                    "edge; none is 0."
                ),
            },
        },
    )
    tube_interpolation: Literal["linear", "constant"] = field(
        default="linear",
        metadata={
            "label": "Interpolation",
            "help": (
                'Between two rows of tube_points: "linear" joins them with a '
                'straight line, "constant" holds each row until the next.'
            ),
        },
    )

    def to_dict(self) -> dict:
        """Convert back to the flat dict format consumed by _compare_tube."""
        d: dict = {}
        if self.tube_width_mode is not None:
            d["tube_width_mode"] = self.tube_width_mode
        for key in ("tube_abs", "tube_rel"):
            # A config built with a number holds the number: both sides. A
            # dataclass is always truthy, so "no width" is said: zero on
            # both sides — one zero side is a width.
            sides = parse_sides(getattr(self, key), key)
            if sides != Sides(0.0, 0.0):
                d[key] = sides
        if self.tube_min_width:
            d["tube_min_width"] = self.tube_min_width
        if self.tube_points is not None:
            d["tube_points"] = self.tube_points
            d["tube_interpolation"] = self.tube_interpolation
        return d


@dataclass(frozen=True)
class PointsConfig:
    """Configuration for point-based comparison.

    When ``points`` is None or [], the mode behaves exactly like the
    former final-only: checks ``act[-1]`` vs ``ref[-1]`` with
    ``tolerance`` as absolute delta. When ``points`` is a non-empty
    list, each entry is a declared checkpoint with optional explicit
    target value, per-point tolerance, and per-point time-tolerance.
    See docs/superpowers/specs/2026-04-24-points-mode-design.md.
    """

    points: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Declared points",
            "help": (
                "The checkpoints, one row each. A row passes when the run comes "
                "within its tolerance of its value somewhere within its time "
                "tolerance. With no rows, the run's final value is compared with "
                "the baseline's."
            ),
            # As ``_compare_points`` reads them, all with ``.get``: a blank
            # ``time`` is the documented trace-end sentinel (``time: null``),
            # a blank ``value`` follows the baseline. A ``sides?`` cell is a
            # number or a lower/upper pair; a time tolerance's two sides are
            # how much earlier and how much later.
            "columns": (
                ("time", "float?"),
                ("value", "float?"),
                ("tolerance", "sides?"),
                ("tolerance_mode", ("abs", "rel")),
                ("time_tolerance", "sides?", ("before", "after")),
            ),
            # What each column of a row holds, for the person writing it.
            "column_help": {
                "time": (
                    "The checkpoint's time, in seconds; none is the end of the run."
                ),
                "value": (
                    "The value expected there; none is the baseline's value at that "
                    "time."
                ),
                "tolerance": (
                    "How far the run may be from the value: one number for both "
                    "sides, or a lower/upper pair; none is the field tolerance."
                ),
                "tolerance_mode": (
                    '"abs": the tolerance is in the variable\'s units; "rel": a '
                    'fraction of the value\'s magnitude. None is "abs".'
                ),
                "time_tolerance": (
                    "How much earlier and later than time the value may be met, in "
                    "seconds: one number for both, or a before/after pair; none is "
                    "0."
                ),
            },
        },
    )
    tolerance: Sides = field(
        default=Sides(1e-4, 1e-4),
        metadata={
            "label": "Default tolerance",
            "help": (
                "The tolerance of a row that gives none, and of the final-value "
                "check with no rows: one number for both sides, or a lower/upper "
                "pair, in the variable's units."
            ),
        },
    )


@dataclass(frozen=True)
class RangeConfig:
    """Configuration for range (bounds-check) comparison.

    At least one of ``min_value`` or ``max_value`` must be set. Bounds
    are declared in the spec itself — reference data is not consulted.
    """

    min_value: float | None = field(
        default=None,
        metadata={
            "label": "Lower bound (optional)",
            "help": (
                "The lowest value the variable may take anywhere in the run. Leave "
                "it out for no lower bound; at least one of the two bounds is "
                "required."
            ),
        },
    )
    max_value: float | None = field(
        default=None,
        metadata={
            "label": "Upper bound (optional)",
            "help": (
                "The highest value the variable may take anywhere in the run. Leave "
                "it out for no upper bound; at least one of the two bounds is "
                "required."
            ),
        },
    )


@dataclass(frozen=True)
class EventTimingConfig:
    """Configuration for event-timing comparison (4.C.1).

    When ``events`` is None (default), both reference and actual event
    instants are auto-detected from duplicate-time samples (Modelica
    convention) and paired by index. When ``events`` is provided, the
    declared list becomes the authoritative reference-side event set —
    the actual signal is still auto-detected, and declared events claim
    distinct actual events by optimal interval matching (windows sorted
    by right endpoint, smallest unclaimed actual wins — see
    ``algorithms._compare_event_timing``); an unclaimed declared event
    fails. Mirrors the dominant-frequency declared-peaks semantics (D75).
    """

    time_tolerance: Sides = field(
        default=Sides(1e-3, 1e-3),
        metadata={
            "label": "Time tolerance (s)",
            "help": (
                "How much earlier or later than its baseline event an event of the "
                "run may happen, in seconds: one number for both, or an early/late "
                "pair. A declared event's own tolerance wins over it."
            ),
            "side_labels": ("early", "late"),
        },
    )
    count_must_match: bool = field(
        default=True,
        metadata={
            "label": "Event counts must match",
            "help": (
                "Whether the run must have as many events as the baseline, or as "
                "are declared. Off, only the events that pair up are compared."
            ),
        },
    )
    events: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Declared events",
            "help": (
                "Events you declare in place of the baseline's, one row each. Each "
                "must be met by its own event of the run. With no rows, the "
                "baseline's events are found and paired with the run's in order."
            ),
            "columns": (
                ("time", "float"),
                ("tolerance", "sides?", ("early", "late")),
            ),
            # What each column of a row holds, for the person writing it.
            "column_help": {
                "time": "The event's time, in seconds.",
                "tolerance": (
                    "How much earlier and later the run's event may be, in seconds: "
                    "one number for both, or an early/late pair; none is the field "
                    "time_tolerance."
                ),
            },
        },
    )


@dataclass(frozen=True)
class DominantFrequencyConfig:
    """Configuration for dominant-frequency declared-peaks comparison (D75).

    Each entry in ``peaks`` is a dict:
        {"freq": float, "tolerance": float | {"lower": float, "upper": float},
         "tolerance_mode": "rel"|"abs"}

    The algorithm finds the strongest local maximum in the *actual*
    spectrum within each declared peak's tolerance window; the leaf
    passes iff every declared peak has such a match. A leaf with no
    declared peaks fails and reports the reference spectrum's top-N
    peaks as the ``detected_reference_peaks_hz`` diagnostic, which is
    what an author declares the list from.
    """

    peaks: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Peaks",
            "help": (
                "The frequencies the run's spectrum must show, one row each. A row "
                "passes when the spectrum has a peak within its tolerance. With no "
                "rows the criterion fails and reports the baseline's strongest "
                "peaks."
            ),
            # ``freq`` stays required although ``_compare_dominant_frequency``
            # reads it with ``.get``: a peak without a frequency is
            # meaningless. The reader defaults ``tolerance_mode`` to "rel".
            "columns": (
                ("freq", "float"),
                ("tolerance", "sides?"),
                ("tolerance_mode", ("rel", "abs")),
            ),
            # What each column of a row holds, for the person writing it.
            "column_help": {
                "freq": "The frequency, in hertz.",
                "tolerance": (
                    "How far the run's peak may be from it: one number for both "
                    "sides, or a lower/upper pair; none is 0.01 on each side."
                ),
                "tolerance_mode": (
                    '"rel": the tolerance is a fraction of freq; "abs": it is in '
                    'hertz. None is "rel".'
                ),
            },
        },
    )


#: The keys a ``quantity`` OBJECT may hold — one of them — on a metric whose
#: config declares the field (``ScalarConfig``), each with what it says.
#: ``expr`` is reserved: named here so it is refused as "not implemented",
#: not as a misspelling.
QUANTITY_KEYS: dict[str, str] = {
    "reduce": (
        'How the variable becomes one number: "max", "min", "mean" (weighted '
        'by time), "sample_mean", "final" or "integral".'
    ),
    "expr": "Reserved for an expression; not implemented, and refused.",
}


@dataclass(frozen=True)
class ScalarConfig:
    """Configuration for scalar comparison — one number, not a trajectory.

    ``quantity`` says *which* number, in one of two v1 shapes:

    * ``{"reduce": "max"|"min"|"mean"|"sample_mean"|"final"|"integral"}`` —
      reduce the leaf's tracked ``variable`` (after the leaf window) on both
      sides. ``integral`` is the trapezoid rule over the windowed series and
      ``mean`` is that integral over the time span — time-weighted, so a
      finer output grid doesn't move the score; ``sample_mean`` is the plain
      arithmetic mean of the samples, which does move with the grid.
    * ``"<dotted.path>"`` — a stored scalar: a dotted lookup into the run's
      ``statistics``, falling back to a diagnostics summary field
      (``"CPUtime.final"``). Resolved by
      :func:`~.tree_eval.resolve_scalar_path`; the leaf window does not
      apply because a stored scalar is not a time series.

    The object shape is what keeps the field extensible: a later
    ``{"expr": ...}`` quantity slots in as a third shape without breaking
    either of the above. It is deliberately NOT implemented in v1 — there
    is no expression evaluator here.

    Declaring ``min_value`` and/or ``max_value`` switches the leaf to the
    baseline-free bounds form; otherwise the scalar is compared against the
    same quantity taken from the baseline.
    """

    quantity: str | dict | None = field(
        default=None,
        metadata={
            "label": "Quantity",
            "help": (
                'The number scored: a reduction of the leaf\'s variable, {"reduce": '
                '"max"} or "min", "mean" (weighted by time), "sample_mean" (of the '
                'samples, so it moves with the output grid), "final" or "integral"; '
                "or the dotted path of a statistic of the run, such as "
                '"CPUtime.final". A leaf with a path needs no variable.'
            ),
        },
    )
    tolerance: Sides = field(
        default=Sides(1e-4, 1e-4),
        metadata={
            "label": "Tolerance",
            "help": (
                "How far the number may be from the same number of the baseline: "
                "one number for both sides, or a lower/upper pair, read as "
                "tolerance_mode says. Not used when a bound is given."
            ),
        },
    )
    tolerance_mode: Literal["abs", "rel"] = field(
        default="abs",
        metadata={
            "label": "Tolerance mode",
            "help": (
                '"abs": the tolerance is in the number\'s units. "rel": it is a '
                "fraction of the baseline's number — in units where that number is "
                "0."
            ),
        },
    )
    min_value: float | None = field(
        default=None,
        metadata={
            "label": "Lower bound (optional)",
            "help": (
                "The lowest the number may be. A bound replaces the comparison with "
                "the baseline, so the criterion needs none."
            ),
        },
    )
    max_value: float | None = field(
        default=None,
        metadata={
            "label": "Upper bound (optional)",
            "help": (
                "The highest the number may be. A bound replaces the comparison "
                "with the baseline, so the criterion needs none."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------


class NrmseMode(ComparisonMode):
    """Piecewise NRMSE comparison with event boundary handling."""

    def __init__(self, config: NrmseConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "nrmse"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_trajectories(
            ref_time,
            ref_values,
            act_time,
            act_values,
            self.config.tolerance,
        )


class TubeMode(ComparisonMode):
    """Tolerance tube envelope comparison."""

    def __init__(self, config: TubeConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "tube"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_tube(
            ref_time,
            ref_values,
            act_time,
            act_values,
            self.config.to_dict(),
        )


class PointsMode(ComparisonMode):
    """Compare actual vs reference at user-declared time points.

    When ``config.points`` is None or empty, falls back to the legacy
    final-value-only check (act[-1] vs ref[-1] with config.tolerance).
    """

    def __init__(self, config: PointsConfig):
        self.config = config
        # A config built with a number (a seed's, a test's) is both sides.
        self.tolerance = parse_sides(config.tolerance, "tolerance")
        # The rows' tolerances are read here as well as in ``compare``: a
        # malformed one must be a ``ValueError`` where ``resolve_mode``'s
        # callers catch it — out of ``compare`` the flat path catches nothing.
        _point_tolerances(config.points, self.tolerance)

    @property
    def name(self) -> str:
        return "points"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_points(
            ref_time,
            ref_values,
            act_time,
            act_values,
            points=self.config.points,
            tolerance=self.tolerance,
        )

    def is_baseline_free(self) -> bool:
        # Baseline-free iff non-empty points list AND every point
        # has an explicit ``value``. Empty list → implicit final
        # comparison reads ref → not baseline-free. Mixed (some with
        # value, some without) is also not baseline-free; users
        # commit to all-or-nothing.
        pts = self.config.points
        if not pts:
            return False
        return all(p.get("value") is not None for p in pts)


class EventTimingMode(ComparisonMode):
    """Compare event instants (duplicate-time markers) between signals."""

    def __init__(self, config: EventTimingConfig):
        self.config = config
        self.time_tolerance = parse_sides(config.time_tolerance, "time_tolerance")
        # As ``PointsMode`` reads its rows: here, where a ``ValueError`` is
        # the leaf's config error on both scoring paths.
        _event_tolerances(config.events, self.time_tolerance)

    @property
    def name(self) -> str:
        return "event-timing"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_event_timing(
            ref_time,
            act_time,
            time_tolerance=self.time_tolerance,
            count_must_match=self.config.count_must_match,
            declared_events=self.config.events,
        )

    def is_baseline_free(self) -> bool:
        # Declared-events path (D82): the user-authored list replaces the
        # auto-detected reference event set, so the leaf scores using only
        # ``act_time``. Auto-detect path still needs the reference.
        return self.config.events is not None


class DominantFrequencyMode(ComparisonMode):
    """Compare the dominant frequency of two signals (FFT peak)."""

    def __init__(self, config: DominantFrequencyConfig):
        self.config = config
        # As ``PointsMode`` reads its rows: here, where a ``ValueError`` is
        # the leaf's config error on both scoring paths.
        for i, declared in enumerate(config.peaks or []):
            _peak_tolerance(declared, i)

    @property
    def name(self) -> str:
        return "dominant-frequency"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_dominant_frequency(
            ref_time,
            ref_values,
            act_time,
            act_values,
            peaks=self.config.peaks,
        )

    def is_baseline_free(self) -> bool:
        # Declared-peaks path (D75): pass/fail is decided by match-in-window
        # against the actual spectrum; the reference spectrum is only used
        # for the reporter's overlay. No declared peaks → the mode already
        # fails loudly, and a reference would be needed anyway.
        return self.config.peaks is not None


class RangeMode(ComparisonMode):
    """Per-point bounds check (min <= actual <= max).

    First leaf type that doesn't consume the reference trajectory — the
    bounds come from the spec. Validates the MetricTree leaf contract
    beyond NRMSE/tube/final-only.
    """

    def __init__(self, config: RangeConfig):
        if config.min_value is None and config.max_value is None:
            raise ValueError("needs at least one of min_value / max_value")
        self.config = config

    @property
    def name(self) -> str:
        return "range"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_range(
            act_time,
            act_values,
            self.config.min_value,
            self.config.max_value,
        )

    def is_baseline_free(self) -> bool:
        # Bounds live in the leaf's own config; reference trajectories are
        # never consulted.
        return True


class ScalarMode(ComparisonMode):
    """Score a single number against a baseline scalar or declared bounds.

    The reduction applies to whatever arrays the evaluator supplies: a
    windowed trajectory for the ``{"reduce": ...}`` quantity, or the
    1-sample arrays it builds for a stats-path quantity (which is why the
    reduction defaults to ``"final"``).
    """

    def __init__(self, config: ScalarConfig):
        _validate_scalar_config(config)
        self.config = config
        self.tolerance = parse_sides(config.tolerance, "tolerance")
        quantity = config.quantity
        self.reduction = quantity["reduce"] if isinstance(quantity, dict) else "final"

    @property
    def name(self) -> str:
        return "scalar"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_scalar(
            ref_time,
            ref_values,
            act_time,
            act_values,
            reduction=self.reduction,
            tolerance=self.tolerance,
            tolerance_mode=self.config.tolerance_mode,
            min_value=self.config.min_value,
            max_value=self.config.max_value,
        )

    def is_baseline_free(self) -> bool:
        # Declared bounds are scored against the actual scalar alone; the
        # baseline form needs the same quantity from a reference.
        return self.config.min_value is not None or self.config.max_value is not None


def _validate_scalar_config(config: ScalarConfig) -> None:
    """Reject a scalar leaf that can't be scored, loudly and at build time.

    Same error type the other modes raise (``ValueError`` from
    ``RangeMode.__init__``), so ``resolve_mode``'s callers keep one
    failure path.
    """
    quantity = config.quantity
    if quantity is None or quantity == "":
        raise ValueError(
            "needs a 'quantity': a dotted statistics path or "
            '{"reduce": "max"|"min"|"mean"|"sample_mean"|"final"|"integral"}'
        )
    if not isinstance(quantity, str):
        if not isinstance(quantity, dict):
            raise ValueError(
                f"ScalarConfig quantity must be a string path or an object, "
                f"got {type(quantity).__name__}"
            )
        shapes = [k for k in QUANTITY_KEYS if k in quantity]
        if len(shapes) != 1:
            raise ValueError(
                f"ScalarConfig quantity object must declare exactly one of "
                f"'reduce' / 'expr'; got {sorted(quantity)}"
            )
        if "expr" in quantity:
            raise ValueError(
                "ScalarConfig quantity {'expr': ...} is not implemented — v1 "
                "takes a reduction or a statistics path"
            )
        if quantity["reduce"] not in SCALAR_REDUCTIONS:
            raise ValueError(
                f"ScalarConfig quantity reduce must be one of "
                f"{list(SCALAR_REDUCTIONS)}; got {quantity['reduce']!r}"
            )
    if config.tolerance_mode not in ("abs", "rel"):
        raise ValueError(
            f"ScalarConfig tolerance_mode must be 'abs' or 'rel', "
            f"got {config.tolerance_mode!r}"
        )
    if (
        config.min_value is not None
        and config.max_value is not None
        and config.min_value >= config.max_value
    ):
        raise ValueError(
            f"ScalarConfig min_value ({config.min_value}) must be below "
            f"max_value ({config.max_value})"
        )


# ---------------------------------------------------------------------------
# Factory: resolve override dict → ComparisonMode
# ---------------------------------------------------------------------------

#: Keys :func:`registry` builders lift out of a flat override dict into
#: :class:`TubeConfig`. Lives here next to the dataclass it mirrors.
_TUBE_KEYS = frozenset(
    {
        "tube_width_mode",
        "tube_abs",
        "tube_rel",
        "tube_min_width",
        "tube_points",
        "tube_interpolation",
    }
)


def override_metric(var_override: dict, default_points: bool = False) -> str:
    """The name of the metric a flat override is scored by: its ``mode``,
    else the default — ``points`` under ``default_points``, ``nrmse``
    otherwise. Whatever was written, registered or not: :func:`resolve_mode`
    refuses a name nothing is registered under."""
    return var_override.get("mode", "") or (
        DEFAULT_POINTS_METRIC if default_points else DEFAULT_METRIC
    )


def resolve_mode(
    var_override: dict,
    tolerance: Sides | float,
    default_points: bool = False,
) -> ComparisonMode:
    """Build the appropriate ComparisonMode from a per-variable override dict.

    Resolution order:
    1. Explicit ``mode`` key in override → use that mode.
    2. If no explicit mode and ``default_points`` is True → PointsMode
       with points=None (implicit final-value check).
    3. Otherwise → NrmseMode (legacy default).

    The mode names and their construction come from
    :mod:`dstf.comparison.registry`, so a third-party metric registered via
    ``register_metric`` resolves here without touching this function.
    """
    mode_name = override_metric(var_override, default_points)
    desc = registry_metric(mode_name)
    if desc is None:
        # An unrecognized mode must be loud. Silently defaulting to NRMSE
        # changed the scoring algorithm on a typo ("Tube" vs "tube") with
        # no diagnostic (review 2026-07-06).
        valid = ", ".join(d.name for d in registered_metrics())
        raise ValueError(f"unknown comparison mode {mode_name!r}; valid: {valid}")

    # A number (a caller with the base in hand) is both sides.
    tolerance = parse_sides(tolerance, "tolerance")
    return desc.build(var_override, tolerance)
