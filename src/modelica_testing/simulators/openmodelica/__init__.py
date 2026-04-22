"""OpenModelica simulator backend (omc subprocess + .mos scripts).

MVP scope: single-subprocess per test via batch-style .mos driven by the
``omc`` binary. Persistent-worker mode (OMPython / OMCSessionZMQ) and FMU
export (``buildModelFMU``) are deferred follow-ups.

One-time bootstrap (per machine) to install the Modelica Standard Library:

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
"""

# Public re-exports (populated by Task 4 once runner.py lands):
#   from .runner import OpenModelicaConfig, OpenModelicaRunner
# For now, submodules are imported directly by consumers.
