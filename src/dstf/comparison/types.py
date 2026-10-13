"""Comparison-result dataclasses + shared constants.

Lives separately from :mod:`.comparator` (orchestration) and
:mod:`.algorithms` (per-mode math) so both can import the result types
without an import cycle. Re-exported from :mod:`.comparator` for
historical-import compatibility, but new code should prefer
``from dstf.comparison.types import VariableComparison``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .geometry import Primitive
from .sides import Sides, parse_sides

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
    # The tolerance applied for this variable, per side (a number is both).
    tolerance_used: Sides = Sides(0.0, 0.0)
    #: The metric this criterion was scored by — or, a config-error
    #: sentinel's, could not be. THE one source of a criterion's metric name:
    #: the console and JUnit blocks, the tree view and `verdicts.json` read
    #: it and none substitutes a default. ``""`` only for a flat override
    #: naming a mode nothing is registered under.
    mode: str = "nrmse"
    # Tube-specific metrics (populated when mode="tube")
    tube_points_inside: float | None = None  # Fraction of points inside tube (0-1)
    tube_worst_violation: float | None = None  # Largest violation (absolute)
    tube_worst_violation_time: float | None = None  # Time of worst violation
    # Open-ended structured extras — future metrics attach here instead of
    # growing this dataclass (event-timing, spectral, domain-specific scores).
    diagnostics: dict = field(default_factory=dict)
    #: The baseline this criterion READ — ``"primary"``, a soft check's name
    #: — or ``None`` for one that reads none: a ``range``, a criterion with
    #: declared events / peaks / values, one whose config could not be
    #: built. Stamped by the comparator and the tree evaluator from the mode
    #: they built (``is_baseline_free``); THE one source of the baseline a
    #: surface names beside a criterion (the console's and JUnit's ``·
    #: against primary``, the tree view's ``against``), on both paths.
    against: str | None = None
    # The scored variable's expression (see ``VariableResult.expression``): a
    # label, never a key. Stamped by the comparator and the tree evaluator
    # from the result variable the leaf scored; "" when there is none.
    expression: str = ""
    # What the mode scored against, as drawable data (see geometry.py).
    # Ids are local until the evaluator prefixes them with the leaf path.
    geometry: tuple[Primitive, ...] = ()

    def __post_init__(self) -> None:
        # A number handed in (a fixture, a third-party metric) is both sides.
        self.tolerance_used = parse_sides(self.tolerance_used, "tolerance_used")


@dataclass(frozen=True)
class ScoreSummary:
    """One leaf's verdict in words: the pill text and the criterion sentence.

    Produced per mode by :mod:`.summaries` and dispatched through the
    metric descriptor, so the report's tree view and the terminal review
    loop read the same mode-aware text.
    """

    display: str
    criterion: str
    score: float | None = None
    score_label: str = ""
    #: The mode's own key/value pairs behind the verdict — the largest error
    #: and where, the finals, the worst violation, the failed checkpoints.
    #: Each mode names only quantities that mean something for it: the
    #: shared numeric fields are overloaded on some modes (a range leaf's
    #: ``nrmse`` holds its worst violation), so no consumer prints them
    #: under a generic label.
    details: tuple[tuple[str, str], ...] = ()


@dataclass
class StructuralWarning:
    """A warning beside a test's verdict, in one of two forms.

    A CHANGE between the baseline and this run: ``field`` with
    ``reference_value`` and ``current_value`` — ``Event count: 3 -> 5``.

    A STATEMENT about this run alone: ``field`` and ``statement`` —
    ``Tracked but not scored by any criterion: unitTests.x[2]``. It has no
    baseline side; laid out as a change (``1 scored by the tree ->
    unitTests.x[2]``) it read as one between two runs, and backwards.

    A surface prints :attr:`text`, or asks :attr:`is_change` before it
    lays the two values out in columns.
    """

    field: str
    reference_value: str = ""
    current_value: str = ""
    statement: str = ""

    @property
    def is_change(self) -> bool:
        return not self.statement

    @property
    def text(self) -> str:
        if self.statement:
            return f"{self.field}: {self.statement}"
        return f"{self.field}: {self.reference_value} -> {self.current_value}"


@dataclass
class TestComparison:
    """Comparison result for a full test model."""

    model_id: str
    passed: bool
    test_id: str | None = None  # ref file ID (e.g., "0001")
    variables: list[VariableComparison] = field(default_factory=list)
    warnings: list[StructuralWarning] = field(default_factory=list)
    error_message: str | None = None
    sim_success: bool = True
    has_reference: bool = True
    # PTA.5 — this test's pass criterion is "the simulation ran"; no
    # baseline is consulted and ``variables`` is empty by construction.
    # Carried on the comparison (not just on the TestModel) so every
    # report surface can classify it from the comparison alone —
    # reporting.status is the one classifier, and this is the input it
    # used to be missing (D117).
    simulate_only: bool = False
    #: False when there is no result to compare (D145): the last run never
    #: ran this test, or never finished it. ``reporting.status`` reads it
    #: first — NOT RUN, which is not a failure. ``sim_success`` is False
    #: beside it: nothing succeeded.
    ran: bool = True
    # Phase 3.1: MetricTree root for this test. Today populated as the
    # implicit flat-AND over per-variable comparisons (matches `passed`
    # exactly). Phase 3.2+ replaces with user-authored trees from
    # test_spec.json. None when no comparison ran (sim failure, no baseline).
    metric_tree: MetricResult | None = None
    #: Where the tolerance a leaf without its own was scored with came from:
    #: ``"test"`` (the spec's ``comparison.tolerance``), ``"reference"`` (the
    #: baseline JSON's) or ``"project"`` (``Config.tolerance``: the CLI's,
    #: else testing.json's, else 1e-4 — the comparator is handed one number
    #: and cannot tell those apart). None where nothing was scored.
    tolerance_source: str | None = None
    #: That base tolerance: what a leaf without its own is scored with.
    base_tolerance: float | None = None

    @property
    def evaluated(self) -> bool:
        """True when a comparison actually ran for this test.

        Distinguishes a baseline-free or simulate-only test (which has a
        metric tree / variables and a real verdict despite
        ``has_reference=False``) from the NO_REF short-circuit where
        nothing was scored. Report surfaces must classify ``not passed``
        as FAILED whenever ``evaluated`` is true — review 2026-07-06,
        finding 5 (failing baseline-free tests exited 0 as "NO_REF").
        """
        return self.metric_tree is not None or bool(self.variables)
