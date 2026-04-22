"""Simulator backends. Concrete today: Dymola, FMPy, OpenModelica. Pluggable via ``@register``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    SimulatorRunner,
    VariableResult,
    TestResult,
    TestRunResult,
    BatchManifest,
    resolve_variable_patterns,
)

if TYPE_CHECKING:
    from ..config import Config

__all__ = [
    "SimulatorRunner",
    "VariableResult",
    "TestResult",
    "TestRunResult",
    "BatchManifest",
    "resolve_variable_patterns",
    "get_runner",
    "get_runner_class",
]


# ---------------------------------------------------------------------------
# Simulator registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[SimulatorRunner]] = {}


def register(name: str):
    """Class decorator that registers a SimulatorRunner subclass."""
    def decorator(cls: type[SimulatorRunner]) -> type[SimulatorRunner]:
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_runner(config: "Config") -> SimulatorRunner:
    """Instantiate the simulator backend specified in *config*.

    Backends self-register via the ``@register`` decorator.  Importing the
    backend module triggers registration.
    """
    return get_runner_class(config)(config)


def get_runner_class(config: "Config") -> type[SimulatorRunner]:
    """Return the runner *class* for the config's backend, without instantiating.

    Useful for reading class-level attributes (e.g. ``artifact_files``) from
    contexts that don't want to pay instantiation cost or trigger optional-
    dependency import errors (FmpyRunner.__init__ imports fmpy).
    """
    backend = config.simulator_backend
    if backend not in _REGISTRY:
        _import_builtin_backend(backend)
    cls = _REGISTRY.get(backend)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported simulator backend: {backend} "
            f"(from '{config.simulator}'). Available: {available}"
        )
    return cls


def _import_builtin_backend(name: str) -> None:
    """Import a built-in backend module so it registers itself."""
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
        "OpenModelica": ".openmodelica",
    }
    module = builtins.get(name)
    if module:
        import importlib
        importlib.import_module(module, package=__name__)
