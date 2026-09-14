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
    "BackendUnavailableError",
    "VariableResult",
    "TestResult",
    "TestRunResult",
    "BatchManifest",
    "resolve_variable_patterns",
    "get_backend",
    "get_backend_class",
    "select_backend",
    "dataset_kind_for",
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
    contexts that don't want to pay instantiation cost or raise the
    unavailable-backend ``RuntimeError`` an optional dependency's absence
    signals (FmpyBackend.__init__ imports fmpy).
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


class BackendUnavailableError(RuntimeError):
    """The configured backend cannot be built on this machine.

    Not a bug: an unregistered backend name, a missing executable, or a
    persistent variant whose dependency probe failed. Callers decide what
    to do about it — the CLI prints the message and exits 1
    (:func:`cli.run_cmds._get_backend`), the authoring tool shows it on the
    job that asked for the run, and ``Session.open`` turns it into a
    ``ConfigError`` so ``dstf author`` refuses the project with one line.
    """


def select_backend(config, *, persistent: bool) -> Backend:
    """Instantiate the backend for *config*, persistent variant when asked.

    When *persistent* is True and the backend **declares**
    :attr:`Capability.PERSISTENT_WORKERS`, pre-flight the variant's
    dependency probe and instantiate it. When the probe raises
    :class:`RuntimeError`, raise :class:`BackendUnavailableError` naming
    both fixes (install the dep, or re-run with ``--batch``) — no silent
    fallback.

    The declaration is what gates, not the presence of the hook: a backend
    that ships an experimental ``persistent_backend_cls`` it isn't ready to
    have driven says so by leaving the flag off, and lands on the batch
    path. Registration-time validation (:func:`_validate_capabilities`)
    guarantees the converse — flag implies a real override — so a declaring
    backend always has a class to return.

    Backend-agnostic: no string matching on backend names. New persistent
    backends plug in by declaring the capability and overriding
    ``persistent_backend_cls`` + ``preflight`` on their backend classes.

    This function itself reads ``config.backend`` only to phrase the
    persistent-failure message; :func:`get_backend_class`, which it calls
    first, requires the attribute.
    """
    try:
        batch_cls = get_backend_class(config)
    except ValueError as exc:
        # An unregistered backend name is a configuration problem, not a
        # bug; same class of failure as a missing executable.
        raise BackendUnavailableError(str(exc)) from exc
    if not persistent or Capability.PERSISTENT_WORKERS not in batch_cls.capabilities:
        # Batch-only backend (FMPy, Python today). Persistent flag is a
        # no-op rather than an error so a single CLI works across mixed
        # backends in a multi-library config.
        return _instantiate(batch_cls, config)
    pcls = batch_cls.persistent_backend_cls()
    if pcls is None:
        # Unreachable for a validated backend (the flag implies the
        # override); kept as the type narrowing for the call below.
        return _instantiate(batch_cls, config)
    try:
        pcls.preflight(config)
    except RuntimeError as exc:
        raise BackendUnavailableError(
            f"Persistent-worker {config.backend} unavailable.\n"
            f"\n"
            f"{exc}\n"
            f"\n"
            f"Or re-run with `--batch` to use the per-test subprocess "
            f"backend instead."
        ) from exc
    return _instantiate(pcls, config)


def _instantiate(cls: type[Backend], config) -> Backend:
    """``cls(config)``, with a missing-executable ``RuntimeError`` translated.

    The ``try`` wraps exactly the constructor call: backends raise
    ``RuntimeError`` when the tool they drive isn't installed
    (``python/backend.py``, ``julia/backend.py``), which is an unavailable
    backend, while any other exception is a genuine bug and keeps its
    traceback.
    """
    try:
        return cls(config)
    except RuntimeError as exc:
        raise BackendUnavailableError(str(exc)) from exc


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


def dataset_kind_for(backend):
    """The single :class:`DatasetType` *backend* declares, else the default.

    ``produced_datasets`` is the backend's declaration of what shape its
    results carry; the MetricTree evaluator refuses a leaf whose metric
    can't score that shape. When a backend declares exactly one kind we can
    say authoritatively which it is, so we thread it. A backend declaring
    several can't be resolved from the class alone — the kind would have to
    come off each result — so we fall back to ``TIME_SERIES``, which is what
    the evaluator assumes anyway. All five builtins declare exactly
    ``{TIME_SERIES}``, so this is behaviour-neutral today; it exists so the
    first backend to declare something else is scored against the right
    metric set without a second wiring pass.
    """
    from .types import DatasetType

    kinds = type(backend).produced_datasets
    if len(kinds) == 1:
        return next(iter(kinds))
    return DatasetType.TIME_SERIES
