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
    "backend_class",
    "backend_family",
    "backend_names",
    "select_backend",
    "source_types",
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

    Every capability has a load-bearing method, and all four are checked:

    - ``FMU_EXPORT`` → ``export_fmu`` must be overridden.
    - ``BATCH_FALLBACK`` → either ``run_tests`` or ``run_single_test`` must
      be overridden (a backend can ship its own batched orchestration *or*
      use the default per-test loop).
    - ``PERSISTENT_WORKERS`` → ``persistent_backend_cls`` must be overridden
      to return a non-None class.
    - ``LIVE_RESULTS`` → ``live_result_path`` must be overridden.
    - ``CANCEL`` → ``cancel_test`` must be overridden (a persistent variant's
      cancel is its worker's, and is covered by tests like its other flags).

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
    if Capability.CANCEL in caps and cls.cancel_test is Backend.cancel_test:
        raise TypeError(
            f"@register('{name}'): {cls.__name__} declares "
            f"Capability.CANCEL but does not override cancel_test(). "
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
    if (
        Capability.LIVE_RESULTS in caps
        and cls.live_result_path.__func__ is Backend.live_result_path.__func__
    ):
        raise TypeError(
            f"@register('{name}'): {cls.__name__} declares "
            f"Capability.LIVE_RESULTS but does not override "
            f"live_result_path(). Return the file that grows during a run "
            f"or drop the capability flag."
        )


def register(name: str):
    """Register a backend class under a name: the class decorator a backend
    plug-in is declared with.

    The name is what ``testing.json``'s ``backend`` and ``backends`` say, and
    what ``--backend`` takes. An entry named ``"<name> 2025"`` is this
    backend's too.

    Args:
        name: The backend's name.

    Returns:
        The decorator, which returns the class unchanged.

    Raises:
        TypeError: At decoration, when the class declares a
            :class:`~dstf.backends.types.Capability` without overriding the
            method it needs.
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
    cls = backend_class(config.backend)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unsupported backend: {config.backend} "
            f"(from '{config.backend_name}'). Available: {available}"
        )
    return cls


def backend_class(name: str) -> type[Backend] | None:
    """The backend registered under ``name`` — a built-in is imported on
    first use — or None."""
    if name not in _REGISTRY:
        _import_builtin_backend(name)
    return _REGISTRY.get(name)


def backend_names() -> list[str]:
    """Every backend there is a class for: the built-ins, then what a
    plug-in or a caller registered, in registration order. The order
    ``dstf check`` lists them in, and the one a project that names no
    backend is searched in (``Backend.source_types``)."""
    return [*_BUILTINS, *(name for name in _REGISTRY if name not in _BUILTINS)]


def source_types() -> list[str]:
    """Every ``source_type`` a project may say: the ones a registered
    backend runs (``Backend.source_types``), in the backends' order — the
    built-ins' ``modelica``, ``fmu``, ``julia``, ``python``, then a
    plug-in's. ``Config`` refuses any other, and the published schema
    lists these."""
    found: dict[str, None] = {}
    for name in backend_names():
        cls = backend_class(name)
        if cls is not None:
            found.update(dict.fromkeys(sorted(cls.source_types)))
    return list(found)


def backend_family(backend_name: str) -> str:
    """The backend a named entry is of: ``"Dymola 2025"`` is a ``"Dymola"``,
    ``"OpenModelica 1.23"`` an ``"OpenModelica"`` — the registered name it
    starts with, the longest where two do. A name that starts with none is
    its own (and no backend, as :func:`get_backend_class` then says)."""
    for name in sorted(backend_names(), key=len, reverse=True):
        if backend_name.startswith(name):
            return name
    return backend_name


class BackendUnavailableError(RuntimeError):
    """The project's backend cannot be built on this machine: no backend of
    that name is registered, its program was not found, or what it needs is not
    installed. ``str()`` says which, in words true wherever it is shown — the
    command line and `dstf author`'s Queue alike.

    ``batch_runs``: only the variant whose workers stay alive is refused;
    the backend's per-test script mode would run. A caller that offers that
    mode says so in its own words (the CLI's ``--batch``).
    """

    def __init__(self, message: str, *, batch_runs: bool = False):
        super().__init__(message)
        self.batch_runs = batch_runs


def select_backend(config, *, persistent: bool, to_run: bool = False) -> Backend:
    """Build the backend a project runs with.

    Args:
        config: The project's configuration.
        persistent: Build the variant that keeps the simulator open between
            tests, where the backend has one; ``--batch`` is False.
        to_run: The backend will simulate, not only read results: one that was
            not found on this machine is refused.

    Returns:
        The backend.

    Raises:
        BackendUnavailableError: The backend is not registered, not found, or
            cannot be used here; the message says why and what to do.
    """
    try:
        batch_cls = get_backend_class(config)
    except ValueError as exc:
        # An unregistered backend name is a configuration problem, not a
        # bug; same class of failure as a missing executable.
        raise BackendUnavailableError(str(exc)) from exc
    if to_run:
        try:
            batch_cls.require_found(config)
        except RuntimeError as exc:
            raise BackendUnavailableError(str(exc)) from exc
    if not persistent or Capability.PERSISTENT_WORKERS not in batch_cls.capabilities:
        # Batch-only backend (FMPy, Python today). Persistent flag is a
        # no-op rather than an error so a single CLI works across mixed
        # backends in a multi-library config. A `--batch` WRITTEN for such
        # a backend is another matter, and the CLI's to refuse
        # (`cli.run_cmds._refuse_batch_without_workers`).
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
            f"Persistent-worker {config.backend} unavailable.\n\n{exc}",
            batch_runs=True,
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


#: The built-in backends and the module each registers itself from. Their
#: order is the order :func:`backend_names` lists them in.
_BUILTINS = {
    "Dymola": ".dymola",
    "OpenModelica": ".openmodelica",
    "FMPy": ".fmpy",
    "Julia": ".julia",
    "Python": ".python",
}


def _import_builtin_backend(name: str) -> None:
    """Import a built-in backend module so it registers itself."""
    module = _BUILTINS.get(name)
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
