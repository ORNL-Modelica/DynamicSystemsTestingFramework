"""Pluggable test-recognition system.

A ``Recognizer`` inspects a source file (a Modelica ``.mo``, an FMU, a Julia
script, ...) and emits a ``RecognizerResult`` describing a test it found.
Discovery runs every registered recognizer that applies to the configured
``source_type`` and merges results by ``model_id``. This lets users layer
their own test-tagging conventions on top of (or in place of) the bundled
defaults — a library that can't adopt the bundled ``UnitTests`` component
provides its own recognizer (PTA.2 onward: via a JSON map) instead.

PTA.1 wires the registry; only the bundled recognizer registers today, so
behavior is identical to the pre-PTA hardcoded path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RecognizerResult:
    """One recognizer's contribution toward a ``TestModel`` for one source file.

    Fields left ``None`` / empty are not contributed by this recognizer; the
    framework defaults fill them in (or another recognizer overrides, when
    multiple recognizers emit for the same ``model_id``).

    The shape today mirrors what the bundled Modelica recognizer extracts.
    PTA.4 broadens this with runtime-behavior fields (``simulate_only``,
    ``requested_fmu_export``, ...).
    """

    model_id: str
    source_file: Path | None = None
    # Variables-to-track contribution (was UnitTestInfo)
    n_vars: int | None = None
    x_expressions: list[str] = field(default_factory=list)
    x_raw: str = ""
    x_reference: list[float] | None = None
    error_expected: float | None = None
    # Simulation-parameters contribution (was ExperimentInfo)
    stop_time: float | None = None
    tolerance: float | None = None
    method: str | None = None
    number_of_intervals: int | None = None
    output_interval: float | None = None
    # PTA.4 — richer-contract runtime-behavior fields. Recognizers can set
    # these; TestModel carries them through. Consumers land in PTA.5 (for
    # simulate_only) and 4.B (for requested_fmu_export / requested_baselines).
    simulate_only: bool | None = None
    requested_fmu_export: bool | None = None
    requested_baselines: list[str] | None = None


class Recognizer(ABC):
    """Base class for source-file recognizers."""

    #: Unique recognizer name; used in diagnostics and as a registry key.
    name: str = "<unnamed>"
    #: ``Config.source_type`` values this recognizer applies to.
    applies_to: frozenset[str] = frozenset()

    @abstractmethod
    def recognize(self, source_file: Path) -> RecognizerResult | None:
        """Inspect a source file. Return a result if a test is recognized, else None."""
        ...

    def applies_to_path(self, source_file: Path, base: Path) -> bool:
        """Optional pre-filter: return False to skip the file before recognize().

        Default: True (no filtering). Subclasses can override to honor a
        per-recognizer paths_include / paths_exclude config (PTA-follow.1).
        """
        return True


# Registration-ordered list. The bundled recognizer registers first on import;
# user-provided recognizers (PTA.3) append, and discovery's per-model merge
# applies later writers as overrides — bundled defaults, user values win.
_REGISTRY: list[Recognizer] = []


def register(recognizer: Recognizer) -> None:
    """Add a recognizer to the registry (idempotent on identity)."""
    if recognizer not in _REGISTRY:
        _REGISTRY.append(recognizer)


def get_recognizers(source_type: str) -> list[Recognizer]:
    """Return registered recognizers that apply to ``source_type``."""
    return [r for r in _REGISTRY if source_type in r.applies_to]


def _reset_for_tests() -> None:
    """Clear the registry — only for unit tests that need a clean slate."""
    _REGISTRY.clear()
