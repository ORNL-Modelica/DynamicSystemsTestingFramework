"""Simulator backends for running Modelica simulations."""

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
    backend = config.simulator_backend

    # Lazy-import known backends so they register on demand
    if backend not in _REGISTRY:
        _import_builtin_backend(backend)

    cls = _REGISTRY.get(backend)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported simulator backend: {backend} "
            f"(from '{config.simulator}'). Available: {available}"
        )
    return cls(config)


def _import_builtin_backend(name: str) -> None:
    """Import a built-in backend module so it registers itself."""
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
    }
    module = builtins.get(name)
    if module:
        import importlib
        importlib.import_module(module, package=__name__)
