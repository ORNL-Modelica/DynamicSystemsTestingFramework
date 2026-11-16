"""OpenModelica backend.

Two modes:

- Persistent-worker (default CLI path): long-lived OMCSessionZMQ per worker
  via OMPython (:mod:`.persistent_backend`). Cuts wall-time for repeated-test
  runs since MSL and library loads are amortized across the whole batch.
- Batch fallback (``--batch``): one ``omc`` subprocess per test driven by a
  generated ``simulate.mos`` (:mod:`.backend`). It needs no OMPython;
  without OMPython the default path refuses and names ``--batch``.

Neither mode exports FMUs (``buildModelFMU``).

One-time bootstrap (per machine) to install the Modelica Standard Library:

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
"""

from .backend import OpenModelicaBackend, OpenModelicaConfig

__all__ = ["OpenModelicaConfig", "OpenModelicaBackend"]
