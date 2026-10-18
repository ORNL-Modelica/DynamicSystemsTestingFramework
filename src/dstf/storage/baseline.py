"""The stored-baseline types: what a baseline file holds, as the rest of the
code reads it. Only :mod:`dstf.storage.baseline_codec` turns a file's JSON into
these and back; nothing else indexes the parsed dict.

A primary reference file (``ref_NNNN.json``) is a :class:`ReferenceFile`: its
own fields plus one :class:`Baseline` named ``"primary"``. A soft check
(``soft_checks/ref_NNNN/<name>.json``, D66) and a JSON companion's data file
are a :class:`Baseline` alone.
"""

from dataclasses import dataclass, field

#: Name of a reference file's one built-in baseline.
PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class StoredVariable:
    """One stored trajectory. ``name`` is the variable's name in the result
    file and the only key; ``expression`` is the label beside it
    (see ``VariableResult``). ``values`` are on the baseline's ``time``.

    ``expression`` is ``None`` only for an entry written before the
    identity change (D132) without the key — old data, which
    ``migration_hint`` reads as un-migrated (0 of 2,454 entries in the
    real baselines measured 2026-10-15); every writer writes text."""

    index: int | None
    name: str
    expression: str | None
    values: list[float]


@dataclass(frozen=True)
class DiagnosticSummary:
    """A diagnostic variable stored as a summary, not a trajectory. A field
    is ``None`` when the run held no samples of it."""

    name: str
    final: float | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class Sampling:
    """What accept did to the run's rows (D153): the setting it applied, the
    rows the run had and kept, and the precision the arrays are read back
    through (``"single"`` or ``"double"``)."""

    setting: int | str
    source_points: int
    stored_points: int
    precision: str


@dataclass(frozen=True)
class Baseline:
    """One named set of reference trajectories on one shared time grid."""

    name: str
    time: list[float]
    variables: list[StoredVariable]
    diagnostics: list[DiagnosticSummary] = field(default_factory=list)
    #: Simulation parameters that produced this baseline.
    simulation: dict = field(default_factory=dict)
    #: Statistics captured at accept time (dotted paths, e.g.
    #: ``translation.continuous_time_states``).
    statistics: dict = field(default_factory=dict)
    #: What produced this baseline. A soft check's is the importer's
    #: (``origin``, ``captured_at``, ``notes``, ...); a primary's is empty
    #: until accept writes one.
    provenance: dict = field(default_factory=dict)

    def variable(self, name: str) -> StoredVariable | None:
        """The first stored variable named ``name``, or ``None``."""
        return next((v for v in self.variables if v.name == name), None)

    def names(self) -> list[str]:
        """Every stored variable's name, in file order."""
        return [v.name for v in self.variables]


@dataclass(frozen=True)
class ReferenceFile:
    """A primary reference file: its own fields and its ``"primary"``
    baseline. ``status``, ``date_added`` and ``last_updated`` are ``None`` only
    in files written before they existed (old data)."""

    model_id: str
    test_id: str | None
    status: str | None
    date_added: str | None
    last_updated: str | None
    sampling: Sampling | None
    #: Per-test comparison settings (``tolerance``, ``variable_overrides``);
    #: ``{}`` when the file has none.
    comparison: dict
    baseline: Baseline

    @property
    def active_status(self) -> str:
        """``status``, reading a file without one as ``"active"``."""
        return self.status or "active"
