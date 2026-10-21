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
    """What a recognizer found in one source file: one test, and the parts of
    it the file says.

    A field left None or empty says nothing, and the test takes it from another
    recognizer or the defaults. Results for the same model are merged field by
    field, in the order the recognizers were registered: a later one wins.

    Attributes:
        model_id: The test's model, by its full name.
        source_file: The file it was found in.
        n_vars: How many ``UnitTests`` variables the test has.
        unit_tests_instance: The name of the component whose ``x[i]`` those
            variables are.
        x_expressions: What the model wrote for each of them.
        x_reference: Reference values written in the model, where there are.
        stop_time: The simulation's stop time, in seconds.
        tolerance: The solver's tolerance.
        method: The solver.
        number_of_intervals: The number of output intervals.
        output_interval: The time between output points, in seconds.
        simulate_only: The test passes when its simulation completes; nothing
            is scored.
        requested_baselines: Extra baselines to produce for the test.
    """

    model_id: str
    source_file: Path | None = None
    # Variables-to-track contribution (was UnitTestInfo)
    n_vars: int | None = None
    # Instance name of the component whose ``x[i]`` the variables are: the
    # result file names them ``<instance>.x[i]``. Contributed by a recognizer
    # that takes those variables out of a matched component — their count
    # (``n_vars``) or their expressions, either one; a recognizer that
    # contributes neither leaves it None and the conventional default stands.
    unit_tests_instance: str | None = None
    x_expressions: list[str] = field(default_factory=list)
    x_reference: list[float] | None = None
    # Simulation-parameters contribution (was ExperimentInfo)
    stop_time: float | None = None
    tolerance: float | None = None
    method: str | None = None
    number_of_intervals: int | None = None
    output_interval: float | None = None
    # PTA.4 — richer-contract runtime-behavior fields. Recognizers can set
    # these; TestModel carries them through. Consumed by PTA.5 (simulate_only)
    # and 4.B's cross-backend chain (requested_baselines).
    simulate_only: bool | None = None
    requested_baselines: list[str] | None = None


class Recognizer(ABC):
    """A rule that finds tests in a library's source files.

    Discovery runs every registered recognizer that applies to the project's
    ``source_type`` over each source file, and merges what they find by model.
    A recognizer plug-in subclasses this and adds an instance with
    :func:`register`. A project can declare recognizers of its own in
    ``testing.json`` instead, with no Python: see the recognizers reference.

    Attributes:
        name: The recognizer's name, unique; what messages and
            ``disable_bundled`` name it by.
        applies_to: The ``source_type`` values it runs for.
    """

    #: Unique recognizer name; used in diagnostics and as a registry key.
    name: str = "<unnamed>"
    #: ``Config.source_type`` values this recognizer applies to.
    applies_to: frozenset[str] = frozenset()

    @abstractmethod
    def recognize(self, source_file: Path) -> RecognizerResult | None:
        """Look at one source file for a test.

        Called once per file of the library, so it should be cheap on a file that
        holds none.

        Args:
            source_file: The file.

        Returns:
            The test the file holds, or None.
        """
        ...

    def applies_to_path(self, source_file: Path, base: Path) -> bool:
        """Whether to look at a file at all, before :meth:`recognize`.

        Args:
            source_file: The file.
            base: The library's directory, which the file's path is relative to.

        Returns:
            False to skip the file. The default looks at every file.
        """
        return True


# Registration-ordered list. The bundled recognizer registers first on import;
# user-provided recognizers (PTA.3) append, and discovery's per-model merge
# applies later writers as overrides — bundled defaults, user values win.
_REGISTRY: list[Recognizer] = []


def register(recognizer: Recognizer) -> None:
    """Add a recognizer, after those already registered.

    Args:
        recognizer: The recognizer. Adding the same object twice adds it once.
    """
    if recognizer not in _REGISTRY:
        _REGISTRY.append(recognizer)


def get_recognizers(source_type: str) -> list[Recognizer]:
    """Return registered recognizers that apply to ``source_type``."""
    return [r for r in _REGISTRY if source_type in r.applies_to]


def _reset_for_tests() -> None:
    """Clear the registry — only for unit tests that need a clean slate."""
    _REGISTRY.clear()
