"""Comparison-result dataclasses + shared constants.

Lives separately from :mod:`.comparator` (orchestration) and
:mod:`.algorithms` (per-mode math) so both can import the result types
without an import cycle. Re-exported from :mod:`.comparator` for
historical-import compatibility, but new code should prefer
``from dstf.comparison.types import VariableComparison``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Annotation-only — runtime import is inside compare_test() in
    # comparator.py to break the comparator <-> metric_tree cycle.
    from .metric_tree import MetricResult


DEFAULT_TOLERANCE = 1e-4

# Machine-epsilon guard — signals with range below this are treated as
# constant by the NRMSE path.
_EPS = 100 * np.finfo(np.float64).eps


@dataclass
class VariableComparison:
    """Comparison result for a single tracked variable.

    Conforms to the ``MetricResult`` contract in docs/extensibility.md:
    carries a ``passed`` flag, a numeric score (``nrmse`` for NRMSE mode,
    ``tube_points_inside`` for tube mode), and a structured ``diagnostics``
    bag for metric-specific extras (e.g. event-timing deltas, spectral
    peaks) that future metrics may attach without widening the schema.
    """
    index: int
    name: str
    passed: bool
    nrmse: float  # Normalized RMSE (pass/fail metric)
    rmse: float  # Raw RMSE
    signal_range: float  # max(ref) - min(ref), for context
    max_abs_error: float  # Largest absolute deviation
    max_abs_error_time: float  # Time at which it occurs
    reference_final: float
    actual_final: float
    is_constant: bool = False  # True if reference signal has zero range
    tolerance_used: float = 0.0  # The tolerance threshold applied for this variable
    mode: str = "nrmse"  # Comparison mode: "nrmse" or "tube"
    # Tube-specific metrics (populated when mode="tube")
    tube_points_inside: Optional[float] = None  # Fraction of points inside tube (0-1)
    tube_worst_violation: Optional[float] = None  # Largest violation (absolute)
    tube_worst_violation_time: Optional[float] = None  # Time of worst violation
    # Open-ended structured extras — future metrics attach here instead of
    # growing this dataclass (event-timing, spectral, domain-specific scores).
    diagnostics: dict = field(default_factory=dict)


@dataclass
class StructuralWarning:
    """Warning about structural changes between reference and current run."""
    field: str
    reference_value: str
    current_value: str


@dataclass
class TestComparison:
    """Comparison result for a full test model."""
    model_id: str
    passed: bool
    test_id: Optional[str] = None  # ref file ID (e.g., "0001")
    variables: list[VariableComparison] = field(default_factory=list)
    warnings: list[StructuralWarning] = field(default_factory=list)
    error_message: Optional[str] = None
    sim_success: bool = True
    has_reference: bool = True
    # Phase 3.1: MetricTree root for this test. Today populated as the
    # implicit flat-AND over per-variable comparisons (matches `passed`
    # exactly). Phase 3.2+ replaces with user-authored trees from
    # test_spec.json. None when no comparison ran (sim failure, no baseline).
    metric_tree: Optional["MetricResult"] = None
