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

from .algorithms import SCALAR_REDUCTIONS
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
from .registry import DEFAULT_METRIC
from .registry import metric as registry_metric
from .registry import metrics as registered_metrics
from .sides import Sides, parse_sides

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ComparisonMode(ABC):
    """Strategy interface for variable comparison."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in config and VariableComparison.mode."""
        ...

    @abstractmethod
    def compare(
        self,
        ref_time: np.ndarray,
        ref_values: np.ndarray,
        act_time: np.ndarray,
        act_values: np.ndarray,
    ) -> VariableComparison:
        """Compare actual vs reference and return metrics."""
        ...

    def is_baseline_free(self) -> bool:
        """Does this mode + its current config score without a reference?

        Default ``False`` — most modes (NRMSE, tube, final-only) compare
        against a saved baseline. Subclasses that score purely against
        config-declared bounds or structures (range; event-timing with a
        declared ``events`` list; dominant-frequency with declared
        ``peaks``) override this to return ``True``. Used by the
        comparator to decide whether a test with no stored baseline can
        still produce meaningful pass/fail — otherwise the whole test
        collapses to the NO_REF state (idea #59 / D83).
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
                "Pass iff NRMSE = RMSE / signal_range stays below this value. "
                "Range-normalized so the same tolerance works across variables "
                "with different magnitudes."
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
                "How the tube width is specified. "
                "'band' = constant offset in signal units (uses tube_abs); "
                "'rel' = fraction of |reference| (uses tube_rel); "
                "'abs' = literal y-axis bounds — requires tube_points "
                "with explicit upper/lower."
            ),
        },
    )
    tube_abs: Sides = field(
        default=Sides(0.0, 0.0),
        metadata={
            "label": "Tube abs",
            "help": "Absolute offset from reference (used when width_mode='band').",
        },
    )
    tube_rel: Sides = field(
        default=Sides(0.0, 0.0),
        metadata={
            "label": "Tube rel",
            "help": "Fractional offset from |reference| (e.g. 0.02 = 2%).",
        },
    )
    tube_min_width: float = field(
        default=0.0,
        metadata={
            "label": "Min width",
            "help": "Floor for the tube width — useful near zero-crossings.",
        },
    )
    tube_points: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Control points",
            "help": (
                "Time-varying tube defined by control points. Edit on the "
                "authoring tool's Edit on plot tab: drag a handle, or edit "
                "the table."
            ),
            # Row keys as ``_interpolate_tube_widths`` reads them: ``time``
            # indexed, each width ``.get`` with a 0 default. A row with
            # neither reads 0/0 and pinches the tube there; the reader
            # refuses only a list in which NO row has a width.
            "columns": (("time", "float"), ("upper", "float?"), ("lower", "float?")),
        },
    )
    tube_interpolation: Literal["linear", "constant"] = field(
        default="linear",
        metadata={
            "label": "Interpolation",
            "help": "How tube bounds interpolate between control points.",
        },
    )

    def to_dict(self) -> dict:
        """Convert back to the flat dict format consumed by _compare_tube."""
        d: dict = {}
        if self.tube_width_mode is not None:
            d["tube_width_mode"] = self.tube_width_mode
        for key in ("tube_abs", "tube_rel"):
            # A config built with a number holds the number: both sides.
            sides = parse_sides(getattr(self, key), key)
            if sides.upper:
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
                "Optional list of (time, value, tolerance, "
                "tolerance_mode, time_tolerance) checkpoints. When None "
                "or empty, the mode falls back to final-value comparison "
                "with the global ``tolerance``. Edit on the authoring "
                "tool's Edit on plot tab: drag a handle, or edit the table."
            ),
            # As ``_compare_points`` reads them, all with ``.get``: a blank
            # ``time`` is the documented trace-end sentinel (``time: null``),
            # a blank ``value`` follows the baseline.
            "columns": (
                ("time", "float?"),
                ("value", "float?"),
                ("tolerance", "float?"),
                ("tolerance_mode", ("abs", "rel")),
                ("time_tolerance", "float?"),
            ),
        },
    )
    tolerance: Sides = field(
        default=Sides(1e-4, 1e-4),
        metadata={
            "label": "Default tolerance",
            "help": (
                "Default per-point y-tolerance when not specified inside "
                "a point dict. Also the tolerance for the implicit final-"
                "value check when ``points`` is empty."
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
                "Signal must never drop below this value. Leave blank to "
                "skip — only the upper bound will apply."
            ),
        },
    )
    max_value: float | None = field(
        default=None,
        metadata={
            "label": "Upper bound (optional)",
            "help": (
                "Signal must never exceed this value. Leave blank to skip — "
                "only the lower bound will apply."
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
                "Default max time-shift between paired reference/actual "
                "events. Per-event overrides in the declared ``events`` "
                "list take precedence when present."
            ),
        },
    )
    count_must_match: bool = field(
        default=True,
        metadata={
            "label": "Event counts must match",
            "help": (
                "If checked, reference and actual must fire the same number "
                "of events. Unchecked allows pairs-that-exist comparisons "
                "even when extra/missing events appear."
            ),
        },
    )
    events: list[dict] | None = field(
        default=None,
        metadata={
            "label": "Declared events",
            "help": (
                "Declared reference-side events. Each entry has a ``time`` "
                "(seconds) and an optional ``tolerance`` (seconds; falls "
                "back to the leaf's ``time_tolerance`` if omitted). When "
                "None, events are auto-detected from duplicate-time samples "
                "in ``ref_time``. Edit the table on the authoring tool's "
                "Edit on plot tab."
            ),
            "columns": (("time", "float"), ("tolerance", "float?")),
        },
    )


@dataclass(frozen=True)
class DominantFrequencyConfig:
    """Configuration for dominant-frequency declared-peaks comparison (D75).

    Each entry in ``peaks`` is a dict:
        {"freq": float, "tolerance": float, "tolerance_mode": "rel"|"abs"}

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
                "Declared peaks to track. Each entry has a frequency, a "
                "tolerance, and a tolerance mode ('rel' = fractional, "
                "'abs' = Hz). Edit on the authoring tool's Edit on plot "
                "tab: drag a handle, or edit the table."
            ),
            # ``freq`` stays required although ``_compare_dominant_frequency``
            # reads it with ``.get``: a peak without a frequency is
            # meaningless. The reader defaults ``tolerance_mode`` to "rel".
            "columns": (
                ("freq", "float"),
                ("tolerance", "float?"),
                ("tolerance_mode", ("rel", "abs")),
            ),
        },
    )


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
                "Which number to score: a reduction of the leaf's variable "
                '({"reduce": "max"|"min"|"mean"|"sample_mean"|"final"|'
                "\"integral\"} — 'mean' is time-weighted, i.e. the integral "
                "over the time span, so it doesn't move with the output grid, "
                "while 'sample_mean' is the arithmetic mean of the samples), "
                'or a dotted statistics / diagnostics path ("CPUtime.final", '
                '"translation.scalar_unknowns"). Edited as raw JSON.'
            ),
        },
    )
    tolerance: Sides = field(
        default=Sides(1e-4, 1e-4),
        metadata={
            "label": "Tolerance",
            "help": (
                "Pass iff the deviation from the baseline scalar stays below "
                "this value (absolute or relative — see tolerance mode). "
                "Ignored when bounds are declared."
            ),
        },
    )
    tolerance_mode: Literal["abs", "rel"] = field(
        default="abs",
        metadata={
            "label": "Tolerance mode",
            "help": (
                "'abs' = |actual - reference| <= tolerance; 'rel' = "
                "|actual - reference| <= tolerance * |reference|, falling "
                "back to the absolute test when the reference is 0."
            ),
        },
    )
    min_value: float | None = field(
        default=None,
        metadata={
            "label": "Lower bound (optional)",
            "help": (
                "Declaring a bound switches the leaf to a baseline-free "
                "check: the scalar must not fall below this value."
            ),
        },
    )
    max_value: float | None = field(
        default=None,
        metadata={
            "label": "Upper bound (optional)",
            "help": (
                "Declaring a bound switches the leaf to a baseline-free "
                "check: the scalar must not exceed this value."
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
            raise ValueError(
                "RangeConfig requires at least one of min_value / max_value"
            )
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
            "ScalarConfig requires a 'quantity': a dotted statistics path or "
            '{"reduce": "max"|"min"|"mean"|"sample_mean"|"final"|"integral"}'
        )
    if not isinstance(quantity, str):
        if not isinstance(quantity, dict):
            raise ValueError(
                f"ScalarConfig quantity must be a string path or an object, "
                f"got {type(quantity).__name__}"
            )
        shapes = [k for k in ("reduce", "expr") if k in quantity]
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


#: The fields whose scorer still reads ONE number (it takes ``.upper``), per
#: mode: its leaf keys, and its ``(list field, row key)`` pairs. Until a
#: mode's scorer applies the two sides separately, :func:`resolve_mode`
#: refuses an unequal pair under a leaf key and ANY pair under a row key (a
#: row is read with ``float(...)``) — so a pair is never scored as its
#: upper side. Each scoring task of step E1 deletes its mode's entry; the
#: last deletes the table.
_NOT_SCORED_YET: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "points": (
        ("tolerance",),
        (("points", "tolerance"), ("points", "time_tolerance")),
    ),
    "scalar": (("tolerance",), ()),
    "tube": (("tube_abs", "tube_rel"), ()),
    "event-timing": (("time_tolerance",), (("events", "tolerance"),)),
    "dominant-frequency": ((), (("peaks", "tolerance"),)),
}


def _refuse_unscored_pairs(
    mode_name: str, var_override: dict, tolerance: Sides
) -> None:
    leaf_keys, row_keys = _NOT_SCORED_YET.get(mode_name, ((), ()))
    for key in leaf_keys:
        if key == "tolerance":
            sides = tolerance  # resolved: the leaf's own, else the base
        elif key in var_override:
            sides = parse_sides(var_override[key], key)
        else:
            continue
        if not sides.equal:
            raise ValueError("two-sided tolerance is not scored yet")
    for field_name, key in row_keys:
        rows = var_override.get(field_name)
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get(key), dict):
                raise ValueError("two-sided tolerance is not scored yet")


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
    mode_name = var_override.get("mode", "") or (
        "points" if default_points else DEFAULT_METRIC
    )

    desc = registry_metric(mode_name)
    if desc is None:
        # An unrecognized mode must be loud. Silently defaulting to NRMSE
        # changed the scoring algorithm on a typo ("Tube" vs "tube") with
        # no diagnostic (review 2026-07-06).
        valid = ", ".join(d.name for d in registered_metrics())
        raise ValueError(f"unknown comparison mode {mode_name!r}; valid: {valid}")

    # A number (a caller with the base in hand) is both sides.
    tolerance = parse_sides(tolerance, "tolerance")
    _refuse_unscored_pairs(mode_name, var_override, tolerance)
    return desc.build(var_override, tolerance)
