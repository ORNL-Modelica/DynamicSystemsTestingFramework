"""Locate and load OMPython's :class:`OMCSessionZMQ`.

OMPython is a regular pip package — unlike Dymola's shipped-in-install-tree
interface, it's just a PyPI install away. The loader exists for symmetry
with :mod:`backends.dymola.interface_loader` and so the persistent-worker
backend can fail with a useful install hint when OMPython is missing
(instead of an opaque :exc:`ImportError` deep in the worker thread).
"""

from __future__ import annotations

from typing import Any

_INSTALL_HINT = (
    "OMPython is not installed. The persistent-worker OpenModelica\n"
    "backend needs it to drive omc over ZMQ. Install the optional extra:\n"
    "\n"
    "    source checkout:  uv sync --extra om --locked --inexact\n"
    "    installed tool:   reinstall with the [om] extra"
)


_cached_class: Any = None


def load_omc_session() -> Any:
    """Return :class:`OMPython.OMCSessionZMQ`. Cached after first call.

    Raises :class:`RuntimeError` with an install hint on missing package.
    Deferred import so batch-mode runs don't pay the price.
    """
    global _cached_class
    if _cached_class is not None:
        return _cached_class
    try:
        from OMPython import OMCSessionZMQ  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    _cached_class = OMCSessionZMQ
    return OMCSessionZMQ


def describe_om_session() -> dict:
    """Diagnostic info: is OMPython installed, what version. No raises."""
    info: dict = {
        "ompython_installed": False,
        "version": None,
        "import_ok": False,
        "error": None,
    }
    try:
        import OMPython  # type: ignore[import-not-found]

        info["ompython_installed"] = True
        info["version"] = getattr(OMPython, "__version__", "unknown")
        from OMPython import (
            OMCSessionZMQ,  # type: ignore[import-not-found]  # noqa: F401
        )

        info["import_ok"] = True
    except Exception as exc:  # pragma: no cover — diagnostic helper
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info
