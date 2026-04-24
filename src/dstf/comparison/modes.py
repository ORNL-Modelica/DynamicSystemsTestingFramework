"""Comparison mode strategies — pluggable algorithms for comparing variables.

Each mode implements a single `compare()` method that takes reference and actual
time series and returns a VariableComparison.  Mode-specific configuration is
encapsulated in a typed dataclass rather than an untyped dict.

Modes:
    NrmseMode     — normalized RMSE with piecewise event handling (default)
    TubeMode      — tolerance tube envelope around reference trajectory
    FinalOnlyMode — compare only the final value of each variable
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from .comparator import (
    VariableComparison,
    _compare_dominant_frequency,
    _compare_event_timing,
    _compare_final_values,
    _compare_range,
    _compare_trajectories,
    _compare_tube,
)


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
    `Literal[...]` choices feed the reporter's auto-derived UI
    (reporting/ui/mode_controls.py).
    """
    tube_width_mode: Optional[Literal["band", "rel", "absolute"]] = field(
        default=None,
        metadata={
            "label": "Width mode",
            "help": (
                "How the tube width is specified. "
                "'rel' = fraction of |reference|; "
                "'band' = offset in signal units (max(abs, rel*|ref|)); "
                "'absolute' = literal y-axis bounds (not an offset). "
                "Leave unset to use the rich tube editor's point-level config."
            ),
        },
    )
    tube_abs: float = field(
        default=0.0,
        metadata={
            "label": "Tube abs",
            "help": "Absolute offset from reference (used when width_mode='band').",
        },
    )
    tube_rel: float = field(
        default=0.0,
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
    tube_points: Optional[list[dict]] = field(
        default=None,
        metadata={
            "label": "Control points",
            "help": (
                "Time-varying tube defined by control points. Edit via the "
                "rich tube editor below the plot."
            ),
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
        if self.tube_abs:
            d["tube_abs"] = self.tube_abs
        if self.tube_rel:
            d["tube_rel"] = self.tube_rel
        if self.tube_min_width:
            d["tube_min_width"] = self.tube_min_width
        if self.tube_points is not None:
            d["tube_points"] = self.tube_points
            d["tube_interpolation"] = self.tube_interpolation
        return d


@dataclass(frozen=True)
class FinalOnlyConfig:
    """Configuration for final-value comparison."""
    tolerance: float = field(
        default=1e-4,
        metadata={
            "label": "Tolerance",
            "help": (
                "Pass iff |actual_final - reference_final| is below this value. "
                "Only the last sample is compared; trajectory shape is ignored."
            ),
        },
    )


@dataclass(frozen=True)
class RangeConfig:
    """Configuration for range (bounds-check) comparison.

    At least one of ``min_value`` or ``max_value`` must be set. Bounds
    are declared in the spec itself — reference data is not consulted.
    """
    min_value: Optional[float] = field(
        default=None,
        metadata={
            "label": "Lower bound (optional)",
            "help": (
                "Signal must never drop below this value. Leave blank to "
                "skip — only the upper bound will apply."
            ),
        },
    )
    max_value: Optional[float] = field(
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
    the actual signal is still auto-detected, but each declared event
    must find a nearest actual event within its own tolerance window.
    Mirrors the dominant-frequency declared-peaks semantics (D75).
    """
    time_tolerance: float = field(
        default=1e-3,
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
    events: Optional[list[dict]] = field(
        default=None,
        metadata={
            "label": "Declared events",
            "help": (
                "Declared reference-side events. Each entry has a ``time`` "
                "(seconds) and an optional ``tolerance`` (seconds; falls "
                "back to the leaf's ``time_tolerance`` if omitted). When "
                "None, events are auto-detected from duplicate-time samples "
                "in ``ref_time``. Authored via the table editor in the "
                "interactive HTML reporter."
            ),
        },
    )


@dataclass(frozen=True)
class DominantFrequencyConfig:
    """Configuration for dominant-frequency declared-peaks comparison (D75).

    Each entry in ``peaks`` is a dict:
        {"freq": float, "tolerance": float, "tolerance_mode": "rel"|"abs"}

    The algorithm finds the strongest local maximum in the *actual*
    spectrum within each declared peak's tolerance window; the leaf
    passes iff every declared peak has such a match. The reporter
    surfaces a table editor + a "Detect peaks from reference" button
    that bootstraps the list from the reference spectrum's top-N peaks.
    """
    peaks: Optional[list[dict]] = field(
        default=None,
        metadata={
            "label": "Peaks",
            "help": (
                "Declared peaks to track. Each entry has a frequency, a "
                "tolerance, and a tolerance mode ('rel' = fractional, "
                "'abs' = Hz). Authored via the table editor; use 'Detect "
                "peaks from reference' on a fresh test to seed values from "
                "the reference spectrum's top peaks."
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
            ref_time, ref_values, act_time, act_values, self.config.tolerance,
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
            ref_time, ref_values, act_time, act_values, self.config.to_dict(),
        )


class FinalOnlyMode(ComparisonMode):
    """Compare only final values."""

    def __init__(self, config: FinalOnlyConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "final_only"

    def compare(self, ref_time, ref_values, act_time, act_values):
        ref_final = float(ref_values[-1]) if len(ref_values) > 0 else 0.0
        act_final = float(act_values[-1]) if len(act_values) > 0 else 0.0
        return _compare_final_values(ref_final, act_final, self.config.tolerance)


class EventTimingMode(ComparisonMode):
    """Compare event instants (duplicate-time markers) between signals."""

    def __init__(self, config: EventTimingConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "event-timing"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_event_timing(
            ref_time, act_time,
            time_tolerance=self.config.time_tolerance,
            count_must_match=self.config.count_must_match,
            declared_events=self.config.events,
        )


class DominantFrequencyMode(ComparisonMode):
    """Compare the dominant frequency of two signals (FFT peak)."""

    def __init__(self, config: DominantFrequencyConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "dominant-frequency"

    def compare(self, ref_time, ref_values, act_time, act_values):
        return _compare_dominant_frequency(
            ref_time, ref_values, act_time, act_values,
            peaks=self.config.peaks,
        )


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
            act_time, act_values,
            self.config.min_value, self.config.max_value,
        )


# ---------------------------------------------------------------------------
# Factory: resolve override dict → ComparisonMode
# ---------------------------------------------------------------------------

_TUBE_KEYS = frozenset({
    "tube_width_mode", "tube_abs", "tube_rel",
    "tube_min_width", "tube_points", "tube_interpolation",
})


def resolve_mode(
    var_override: dict,
    tolerance: float,
    default_final_only: bool = False,
) -> ComparisonMode:
    """Build the appropriate ComparisonMode from a per-variable override dict.

    Resolution order:
    1. Explicit ``mode`` key in override → use that mode.
    2. If no explicit mode and ``default_final_only`` is True → FinalOnlyMode
       (but only when mode is not explicitly set to something else).
    3. Otherwise → NrmseMode.

    This fixes the previous bug where ``config.final_only`` could override
    an explicit ``mode: "tube"`` setting.
    """
    mode_name = var_override.get("mode", "")

    if mode_name == "tube":
        tube_kwargs = {k: var_override[k] for k in _TUBE_KEYS if k in var_override}
        return TubeMode(TubeConfig(**tube_kwargs))

    if mode_name == "range":
        # Accept both the canonical ``min_value`` / ``max_value`` (matches
        # the ``RangeConfig`` dataclass field, the auto-derive UI, and
        # MetricTree leaf params) and the shorthand ``min`` / ``max`` kept
        # for compatibility with early-phase specs. The canonical form wins
        # if both are present.
        return RangeMode(RangeConfig(
            min_value=var_override.get("min_value", var_override.get("min")),
            max_value=var_override.get("max_value", var_override.get("max")),
        ))

    if mode_name == "event-timing":
        return EventTimingMode(EventTimingConfig(
            time_tolerance=var_override.get("time_tolerance", 1e-3),
            count_must_match=var_override.get("count_must_match", True),
        ))

    if mode_name == "dominant-frequency":
        return DominantFrequencyMode(DominantFrequencyConfig(
            peaks=var_override.get("peaks"),
        ))

    if mode_name == "final_only" or (not mode_name and default_final_only):
        return FinalOnlyMode(FinalOnlyConfig(tolerance=tolerance))

    # Default: NRMSE
    return NrmseMode(NrmseConfig(tolerance=tolerance))
