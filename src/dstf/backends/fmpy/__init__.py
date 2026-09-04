"""FMPy-backed simulator backend.

Phase 2 adds FMPy as the second backend — the first that exercises the
``Capability`` + ``DatasetType`` + ``Baseline`` abstractions declared on the
``Backend`` ABC. Importing this module registers ``FmpyBackend``
under the name ``"FMPy"``.
"""

from .backend import FmpyBackend

__all__ = ["FmpyBackend"]
