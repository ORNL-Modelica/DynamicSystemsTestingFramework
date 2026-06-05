"""Julia / ModelingToolkit backend (D77).

Fourth :class:`SimulatorRunner` alongside Dymola, FMPy, and OpenModelica.
Invokes a framework-supplied Julia driver script (``run_test.jl``) via
subprocess; the driver loads the user's ``.jl`` file (which exports
``build_mtk_system()``), simulates the MTK system via ``OrdinaryDiffEq``,
and writes a JSON result file the runner reads back.

The runner is deliberately batch-only for the MVP — each test spawns
one Julia subprocess. Persistent workers (via ``JuliaCall`` or a long-
lived Julia process over stdin) are a later enhancement once the
batch path is proven. Startup cost is front-loaded: Julia's first-run
precompile of MTK + OrdinaryDiffEq is minutes; subsequent runs are
seconds. Contrast Dymola / OM which paid the library-load cost on
EVERY test in batch mode.

Not yet supported:
  * Dyad (compiles to MTK — likely works via the same path, not tested).
  * FMU export from MTK (``ModelingToolkit.generate_fmu``).
  * Persistent workers.
  * Per-test ``start_values`` overrides (user authors inside the ``.jl``).
"""

from __future__ import annotations

from .runner import JuliaRunner, JuliaConfig  # noqa: F401

__all__ = ["JuliaRunner", "JuliaConfig"]
