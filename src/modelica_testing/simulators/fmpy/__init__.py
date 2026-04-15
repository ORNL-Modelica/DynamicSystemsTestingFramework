"""FMPy-backed simulator backend.

Phase 2 adds FMPy as the second backend — the first that exercises the
``Capability`` + ``DatasetType`` + ``Baseline`` abstractions declared on the
``SimulatorRunner`` ABC. Importing this module registers ``FmpyRunner``
under the name ``"FMPy"``.
"""

from .runner import FmpyRunner

__all__ = ["FmpyRunner"]
