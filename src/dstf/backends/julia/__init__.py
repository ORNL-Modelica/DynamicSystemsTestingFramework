"""Julia / ModelingToolkit backend (D77).

Fourth :class:`Backend` alongside Dymola, FMPy, and OpenModelica.
Invokes a framework-supplied Julia driver script (``run_test.jl``) via
subprocess; the driver loads the user's ``.jl`` file (which exports
``build_mtk_system()``), simulates the MTK system via ``OrdinaryDiffEq``,
and writes a JSON result file the backend reads back.

That subprocess-per-test path is the batch lane, reached with ``--batch``.
The default is :class:`~.persistent_backend.PersistentJuliaBackend` (D78),
which keeps long-lived stdin-driven Julia workers alive so the package-load
and JIT cost is paid once per worker instead of once per test. Startup cost
is front-loaded either way: Julia's first-run precompile of MTK +
OrdinaryDiffEq is minutes; subsequent runs are seconds. Contrast Dymola / OM
which paid the library-load cost on EVERY test in batch mode.

Not yet supported:
  * Dyad (compiles to MTK — likely works via the same path, not tested).
  * FMU export from MTK (``ModelingToolkit.generate_fmu``).
  * Per-test ``start_values`` overrides (user authors inside the ``.jl``).
"""

from __future__ import annotations

from .backend import JuliaBackend, JuliaConfig  # noqa: F401

__all__ = ["JuliaBackend", "JuliaConfig"]
