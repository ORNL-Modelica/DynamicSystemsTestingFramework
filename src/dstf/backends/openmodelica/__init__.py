"""OpenModelica simulator backend.

Two modes:

- Persistent-worker (default CLI path): long-lived OMCSessionZMQ per worker
  via OMPython (:mod:`.persistent_backend`). Cuts wall-time for repeated-test
  runs since MSL and library loads are amortized across the whole batch.
- Batch fallback (``--batch``): one ``omc`` subprocess per test driven by a
  generated ``simulate.mos`` (:mod:`.backend`). Used automatically when
  OMPython is unavailable.

FMU export (``buildModelFMU``) is still a deferred follow-up.

One-time bootstrap (per machine) to install the Modelica Standard Library:

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
"""

from .backend import OpenModelicaBackend, OpenModelicaConfig

__all__ = ["OpenModelicaConfig", "OpenModelicaBackend"]
