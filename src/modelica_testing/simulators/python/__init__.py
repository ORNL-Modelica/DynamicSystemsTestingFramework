"""Python subprocess runner (D80).

Mirrors the Julia D77 pattern: framework-shipped driver script loads the
user's ``.py`` file, calls ``simulate(stop_time, tolerance)``, and writes
a JSON result. Primary motivation is validating that the backend
abstraction is truly language/simulator-agnostic — see the ConstantCsv
example under ``examples/python/PythonTestingLib/`` which uses zero ODE
machinery.
"""

from . import runner  # noqa: F401  (import triggers @register side effect)
