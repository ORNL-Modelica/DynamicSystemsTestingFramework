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
from typing import Optional

import numpy as np

from .comparator import (
    VariableComparison,
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
    tolerance: float = 1e-4


@dataclass(frozen=True)
class TubeConfig:
    """Configuration for tube comparison.

    Mirrors the fields previously scattered as arbitrary keys in the
    variable_overrides dict, but with types enforced.
    """
    tube_width_mode: Optional[str] = None  # "band" | "rel" | "absolute" | None (legacy)
    tube_abs: float = 0.0
    tube_rel: float = 0.0
    tube_min_width: float = 0.0
    tube_points: Optional[list[dict]] = None
    tube_interpolation: str = "linear"

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
    tolerance: float = 1e-4


@dataclass(frozen=True)
class RangeConfig:
    """Configuration for range (bounds-check) comparison.

    At least one of ``min_value`` or ``max_value`` must be set. Bounds
    are declared in the spec itself — reference data is not consulted.
    """
    min_value: Optional[float] = None
    max_value: Optional[float] = None


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
        # Accept "min" / "max" from the user-facing spec and map to the
        # internal min_value / max_value (which don't shadow Python builtins).
        return RangeMode(RangeConfig(
            min_value=var_override.get("min"),
            max_value=var_override.get("max"),
        ))

    if mode_name == "final_only" or (not mode_name and default_final_only):
        return FinalOnlyMode(FinalOnlyConfig(tolerance=tolerance))

    # Default: NRMSE
    return NrmseMode(NrmseConfig(tolerance=tolerance))
