"""The `Baseline` value type shared by the store and its role modules.

A baseline is one named set of reference trajectories: a ref file's
``"primary"`` (synthesized from the flat on-disk fields by
:func:`~dstf.storage.reference_store._extract_baselines`) or one soft_check
read from ``soft_checks/ref_NNNN/<name>.json`` (D66). It lives in its own
module so both :mod:`dstf.storage.reference_store` and
:mod:`dstf.storage.roles` can depend on it without depending on each other.
"""

from dataclasses import dataclass, field

#: Name of a reference file's one built-in baseline.
PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class Baseline:
    """A single named baseline: a ref file's primary, or one soft_check.

    See docs/architecture.md "Multiple named baselines (D66)".
    """

    #: Baseline name (e.g. ``"primary"``, ``"experiment"``, ``"analytical"``).
    name: str
    #: Shared time vector (all variables in this baseline share one time grid).
    time: list[float]
    #: Variable entries: ``[{"index", "name", "values"}, ...]``.
    variables: list[dict]
    #: Diagnostic variable entries (same shape as ``variables``).
    diagnostics: list[dict] = field(default_factory=list)
    #: Simulation parameters that produced this baseline.
    simulation: dict = field(default_factory=dict)
    #: Comparison configuration (tolerance, variable_overrides).
    comparison: dict = field(default_factory=dict)
    #: Statistics captured at baseline-acceptance time.
    statistics: dict = field(default_factory=dict)
    #: Provenance metadata: origin, captured_at, backend, os, notes,
    #: citation, plus arbitrary user metadata. For a ref file's primary this
    #: is synthesized from ``date_added`` / ``last_updated``.
    provenance: dict = field(default_factory=dict)
