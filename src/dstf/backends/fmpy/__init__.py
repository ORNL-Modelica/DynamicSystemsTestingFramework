"""FMPy backend.

Runs prebuilt FMUs through FMPy, using the ``Capability`` +
``DatasetType`` + ``Baseline`` abstractions declared on the ``Backend``
ABC. Importing this module registers ``FmpyBackend``
under the name ``"FMPy"``.
"""

from .backend import FmpyBackend

__all__ = ["FmpyBackend"]
