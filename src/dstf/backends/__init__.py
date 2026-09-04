"""Backends. Concrete today: Dymola, FMPy, OpenModelica, Julia, Python. Pluggable via ``@register``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..storage.batch_manifest import BatchManifest
from .base import Backend
from .types import (
    Capability,
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
)

if TYPE_CHECKING:
    from ..config import Config

__all__ = [
    "Backend",
    "VariableResult",
    "TestResult",
    "TestRunResult",
    "BatchManifest",
    "resolve_variable_patterns",
    "get_backend",
    "get_backend_class",
]


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[Backend]] = {}


def _validate_capabilities(cls: type[Backend], name: str) -> None:
    """Assert declared :class:`Capability` flags have matching method overrides.

    A capability declaration is a promise the framework can rely on. Without
    this check, a backend can claim ``FMU_EXPORT`` and not override
    :meth:`export_fmu` — the framework gates on the flag, calls the method,
    and gets the base ``NotImplementedError`` at runtime instead of at
    registration. We catch this drift at import time so the typo or
    copy-paste-and-forget can't survive past `pytest --collect-only`.

    Every capability has a load-bearing method, and all three are checked:

    - ``FMU_EXPORT`` → ``export_fmu`` must be overridden.
    - ``BATCH_FALLBACK`` → either ``run_tests`` or ``run_single_test`` must
      be overridden (a backend can ship its own batched orchestration *or*
      use the default per-test loop).
    - ``PERSISTENT_WORKERS`` → ``persistent_backend_cls`` must be overridden
      to return a non-None class.

    Only ``@register``-decorated classes are checked. Persistent variants
    are reached via ``persistent_backend_cls``, not the registry, so their
    declarations are covered by tests instead (see
    ``tests/test_capability_gating.py::TestFmuExportGate``).
    """
    caps = cls.capabilities
    if Capability.FMU_EXPORT in caps and cls.export_fmu is Backend.export_fmu:
        raise TypeError(
            f"@register('{name}'): {cls.__name__} declares "
            f"Capability.FMU_EXPORT but does not override export_fmu(). "
            f"Either implement the method or drop the capability flag."
        )
    if Capability.BATCH_FALLBACK in caps and (
        cls.run_tests is Backend.run_tests
        and cls.run_single_test is Backend.run_single_test
    ):
        raise TypeError(
            f"@register('{name}'): {cls.__name__} declares "
            f"Capability.BATCH_FALLBACK but overrides neither "
            f"run_tests() nor run_single_test(). Implement one or "
            f"drop the capability flag."
        )
    if (
        Capability.PERSISTENT_WORKERS in caps
        # review 2026-07-06 (finding 28): classmethods must be compared via
        # __func__ — `cls.persistent_backend_cls is Backend.
        # persistent_backend_cls` compares per-access bound-method wrappers
        # and is always False, which left this check permanently inert.
        and cls.persistent_backend_cls.__func__
        is Backend.persistent_backend_cls.__func__
    ):
        raise TypeError(
            f"@register('{name}'): {cls.__name__} declares "
            f"Capability.PERSISTENT_WORKERS but does not override "
            f"persistent_backend_cls(). Return your persistent-backend "
            f"class (lazy-imported) or drop the capability flag."
        )


def register(name: str):
    """Class decorator that registers a Backend subclass.

    Validates capability honesty at decoration time — see
    :func:`_validate_capabilities`. A typo or stale flag becomes an
    ``ImportError`` (via ``TypeError``) at module-import time rather
    than a confusing ``NotImplementedError`` mid-run.
    """

    def decorator(cls: type[Backend]) -> type[Backend]:
        _validate_capabilities(cls, name)
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_backend(config: Config) -> Backend:
    """Instantiate the backend specified in *config*.

    Backends self-register via the ``@register`` decorator.  Importing the
    backend module triggers registration.
    """
    return get_backend_class(config)(config)


def get_backend_class(config: Config) -> type[Backend]:
    """Return the backend *class* for the config's backend, without instantiating.

    Useful for reading class-level attributes (e.g. ``artifact_files``) from
    contexts that don't want to pay instantiation cost or trigger optional-
    dependency import errors (FmpyBackend.__init__ imports fmpy).
    """
    backend = config.backend
    if backend not in _REGISTRY:
        _import_builtin_backend(backend)
    cls = _REGISTRY.get(backend)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported backend: {backend} "
            f"(from '{config.backend_name}'). Available: {available}"
        )
    return cls


def _import_builtin_backend(name: str) -> None:
    """Import a built-in backend module so it registers itself."""
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
        "OpenModelica": ".openmodelica",
        "Julia": ".julia",
        "Python": ".python",
    }
    module = builtins.get(name)
    if module:
        import importlib

        importlib.import_module(module, package=__name__)
