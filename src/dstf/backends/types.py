"""Value types every backend speaks, and the variable-pattern resolver.

Stdlib + numpy only: this module imports nothing from ``dstf``. That is what
keeps the package's import graph acyclic — :mod:`dstf.storage` and
:mod:`dstf.comparison` can name these types without a cycle back through the
backends, and a backend module can import them before the registry in
:mod:`dstf.backends` has finished loading.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend capabilities + dataset types (Phase 1 abstraction)
# ---------------------------------------------------------------------------
# See docs/vision.md and docs/extensibility.md. Each backend declares what it
# supports; framework features gate on these rather than on backend class.


class Capability(str, Enum):
    """Declared abilities of a backend."""

    #: Backend can hold a loaded model in memory across multiple tests.
    PERSISTENT_WORKERS = "persistent-workers"
    #: Backend exposes a non-interactive script-driven fallback.
    BATCH_FALLBACK = "batch-fallback"
    #: Backend can export a test artefact as an FMU (enables cross-backend verification).
    FMU_EXPORT = "fmu-export"


class DatasetType(str, Enum):
    """Typed categories of result data a backend may produce.

    Every backend currently emits ``TIME_SERIES``. ``SCALARS`` is scorable
    already — the ``scalar`` metric accepts it — so a scalars-only backend
    would work today; the remaining types (events, spectra, distributions)
    are reserved for metrics that don't exist yet.
    """

    TIME_SERIES = "time-series"
    SCALARS = "scalars"
    EVENTS = "events"
    SPECTRUM = "spectrum"
    DISTRIBUTION = "distribution"


# ---------------------------------------------------------------------------
# Common result types (shared across all backends)
# ---------------------------------------------------------------------------


@dataclass
class VariableResult:
    """Time series for a single tracked variable."""

    index: int  # 1-based
    time: np.ndarray
    values: np.ndarray
    name: str = ""  # Variable name (e.g., "pipe.T[1]" or "unitTests.x[1]")


@dataclass
class TestResult:
    """Results from a single test simulation."""

    model_id: str
    success: bool
    variables: list[VariableResult] = field(default_factory=list)
    diagnostics: list[VariableResult] = field(default_factory=list)
    error_message: str | None = None
    statistics: dict | None = None


#: Appended to every timed-out result's ``error_message``, so the escape hatch
#: travels with the failure to wherever it is read — the dispatcher's progress
#: line, the console report, the dashboard detail. ``--timeout`` only moves the
#: global default; one genuinely slow model wants the per-test override, and a
#: user who has just hit a timeout is exactly who needs to be told that.
TIMEOUT_OVERRIDE_HINT = (
    "raise it per test with simulation.timeout in test_spec.json, "
    "or globally with --timeout"
)


def timeout_message(what: str) -> str:
    """``"<what> (<hint>)"`` — the one phrasing for a timeout failure."""
    return f"{what} ({TIMEOUT_OVERRIDE_HINT})"


@dataclass
class TestRunResult:
    """Result of running a single test."""

    model_id: str
    test_key: str
    success: bool
    elapsed: float = 0.0
    error_message: str | None = None
    timed_out: bool = False
    statistics: dict | None = None
    # Phase breakdown (captured by the persistent backend; None in batch mode)
    translation_wall: float | None = None
    sim_wall: float | None = None
    # Set by persistent workers when the test run left the worker process
    # dead (we called close()). The dispatch loop uses this to decide
    # whether the next restart should count against the budget — workers
    # killed for benign reasons (timeout, post-timeout disk-rescue) get a
    # fresh start without ticking the bound. Independent of `timed_out`
    # and `success`: a disk-rescued test can have success=True AND
    # worker_killed=True (the .mat was written just before the watchdog
    # killed Dymola).
    worker_killed: bool = False


# Variables to exclude when resolving "*" or glob patterns (Dymola internals, etc.)
_EXCLUDE_PREFIXES = ("der(", "$", "_")
_EXCLUDE_NAMES = {"time"}


def _pattern_to_regex(pattern: str):
    """Convert a simple glob pattern to a compiled regex.

    Only * (match any) and ? (match one) are wildcards.
    All other characters (including Modelica's [] for array indices,
    parentheses, dots) are treated as literals.
    """
    import re as _re

    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(_re.escape(ch))
    return _re.compile("".join(parts))


def resolve_variable_patterns(
    patterns: list[str],
    available_vars: list[str],
) -> list[str]:
    """Resolve variable patterns against available variable names from simulation.

    Pattern types:
    - Explicit name: "pipe.T[1]" — must exist exactly
    - Glob: "medium.T*" — fnmatch against available names
    - Wildcard: "*" — all non-internal variables
    - Empty list: [] — no variables (simulate-only mode)

    Returns sorted list of resolved variable names.
    """
    if not patterns:
        return []

    resolved = set()
    for pattern in patterns:
        if pattern == "*":
            # All non-internal variables
            for var in available_vars:
                if var in _EXCLUDE_NAMES:
                    continue
                if any(var.startswith(p) for p in _EXCLUDE_PREFIXES):
                    continue
                resolved.add(var)
        elif "*" in pattern or "?" in pattern:
            # Glob pattern — only * and ? are wildcards, everything else
            # (including [] from Modelica array indices) is literal
            regex = _pattern_to_regex(pattern)
            matched = [v for v in available_vars if regex.fullmatch(v)]
            if not matched:
                logger.warning("Pattern '%s' matched no variables", pattern)
            resolved.update(matched)
        else:
            # Explicit name
            if pattern in available_vars:
                resolved.add(pattern)
            else:
                logger.warning("Variable '%s' not found in results", pattern)

    return sorted(resolved)
