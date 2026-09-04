"""Entry-point plug-in loading — the seam third-party packages register through.

Every extension registry in the framework (``backends.register``,
``discovery.recognizer.register``, ``comparison.registry.register_metric`` /
``register_combinator``) is populated by *importing* the module that calls it.
Builtins are imported by their package ``__init__``; a third-party plug-in
lives in a package we never import, so it needs a hook. That hook is a single
entry-point group:

.. code-block:: toml

    # in the plug-in package's pyproject.toml
    [project.entry-points."dstf.plugins"]
    acme_backend = "acme_dstf.backend"          # module: import side effects register
    acme_metrics = "acme_dstf.metrics:register" # callable: invoked with no args

One group, not one per layer: a plug-in package routinely ships a backend
*and* the metrics that score it, and splitting the declaration by layer only
makes the author guess which group a given registration belongs to.

A plug-in that fails to import or call is a **hard error**
(:class:`~dstf.config.ConfigError`). A metric that silently failed to register
does not disappear quietly — it changes scoring semantics, either by failing
spec parse with a confusing "unknown metric" or by falling back to a default.
Fail at startup, naming the entry point.
"""

from __future__ import annotations

import importlib.metadata
from types import ModuleType

#: The single entry-point group third-party packages advertise plug-ins under.
PLUGIN_GROUP = "dstf.plugins"

# None until the first successful load. Loading is idempotent: the CLI calls
# it once per process, but a library user may call it again from anywhere.
_loaded: list[str] | None = None
_completed: set[str] = set()


def load_plugins() -> list[str]:
    """Import every ``dstf.plugins`` entry point, returning the names loaded.

    Each entry point's ``.load()`` result is either a **module** (the import
    itself performed the registrations) or a **callable** (invoked with no
    arguments). Anything else is an error.

    Idempotent — the second and later calls return the same names without
    re-importing. Raises :class:`~dstf.config.ConfigError` naming the failing
    entry point, chaining the original exception.
    """
    global _loaded
    if _loaded is not None:
        return list(_loaded)

    # Per-entry-point completion tracking, not one all-or-nothing flag: a
    # library user who catches the ConfigError from a broken plug-in and
    # retries must not re-invoke the HEALTHY plug-ins that already ran —
    # their re-registration would raise the registry's duplicate error and
    # blame the wrong plug-in (B2 review finding 4).
    names: list[str] = []
    for ep in importlib.metadata.entry_points(group=PLUGIN_GROUP):
        if ep.name in _completed:
            names.append(ep.name)
            continue
        try:
            obj = ep.load()
            if isinstance(obj, ModuleType):
                pass  # import side effects did the registration
            elif isinstance(obj, type):
                # A class is callable, but instantiating it with no args is
                # never what a plug-in author meant (B2 review finding 8).
                raise TypeError(
                    f"resolved to class {obj.__name__}; point the entry "
                    f"point at a module or a no-argument callable"
                )
            elif callable(obj):
                obj()
            else:
                raise TypeError(
                    f"resolved to {type(obj).__name__}, expected a module or a callable"
                )
        except Exception as exc:
            # Lazy — config imports enough of the package that a module-level
            # import here would risk a cycle, and this is the cold path.
            from .config import ConfigError

            raise ConfigError(
                f"Failed to load DSTF plug-in '{ep.name}' "
                f"({PLUGIN_GROUP} = {ep.value!r}): {exc}"
            ) from exc
        _completed.add(ep.name)
        names.append(ep.name)

    _loaded = names
    return list(names)


def _reset_for_tests() -> None:
    """Forget that plug-ins were loaded — unit tests only."""
    global _loaded
    _completed.clear()
    _loaded = None
