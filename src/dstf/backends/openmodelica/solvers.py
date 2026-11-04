"""The solvers OpenModelica runs, by the name its ``simulate()`` takes.

Its own module so the script builder (:mod:`.mos_generator`) and the backend
class (:meth:`OpenModelicaBackend.solver
<dstf.backends.openmodelica.backend.OpenModelicaBackend.solver>`) ask the same
table without the builder importing the backend.
"""

from __future__ import annotations

from ...discovery.test_registry import TestModel
from ..types import Solver

#: The solver OpenModelica runs when it is asked for none — and for a name it
#: does not know.
DEFAULT = "dassl"

#: The names ``simulate(method=...)`` runs, as OpenModelica spells them.
#: OpenModelica 1.27.1's own table: ``SOLVER_METHOD_NAME`` in
#: ``/usr/include/omc/c/util/simulation_options.c`` (line 1258 of that
#: release). Left out of it: ``symSolver`` and ``symSolverSsc``, which run
#: only on a model translated with the ``--symSolver`` compiler flag, and
#: ``optimization``, a solver for dynamic optimization, not simulation. A
#: name not here makes omc stop before simulating (1.27.1: ``unrecognized
#: option -s esdirk45a``).
KNOWN = ("dassl", "ida", "cvode", "gbode", "euler", "rungekutta", "qss")


def solver(test: TestModel) -> Solver:
    """The solver OpenModelica runs *test* with: the test's ``method`` when
    OpenModelica knows it, whatever its case (Dymola's ``Dassl`` is
    ``dassl``); else :data:`DEFAULT`, with the name the test asked for. One
    rule wherever the name came from — a ``__Dymola_Algorithm`` annotation or
    ``test_spec.json``. A test that names none runs the default."""
    if test.method is None:
        return Solver(DEFAULT, None)
    name = test.method.lower()
    if name in KNOWN:
        return Solver(name, None)
    return Solver(DEFAULT, test.method)
