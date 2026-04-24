"""Linear ramp: x(t) = 2t. scipy ODE counterpart to

* ``ModelicaTestingLib.Examples.SimpleTest`` (Dymola/OpenModelica), and
* ``JuliaMtkTestingLib.Examples.SimpleRamp`` (Julia/MTK).

This is the "real simulation" half of the Python backend showcase — it
exists to prove that any Python ODE solver (scipy, numba-rk4, custom)
can serve as a test source. The companion ``ConstantCsv.py`` is the
"no simulator at all" half that proves the backend abstraction is not
secretly shaped around ODE simulation.
"""
from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp


def simulate(stop_time: float, tolerance: float) -> dict:
    """Integrate dx/dt = 2, x(0) = 0 up to ``stop_time``.

    The framework calls this function once per test run, passing the
    ``stop_time`` and ``tolerance`` values from test_spec.json's
    ``simulation`` block. The returned dict is serialized to JSON by
    the framework's ``run_test.py`` driver.
    """
    sol = solve_ivp(
        fun=lambda t, y: [2.0],
        t_span=(0.0, stop_time),
        y0=[0.0],
        rtol=tolerance,
        atol=tolerance,
        dense_output=False,
        t_eval=np.linspace(0.0, stop_time, 101),
    )
    if not sol.success:
        raise RuntimeError(f"solve_ivp failed: {sol.message}")
    return {
        "time": sol.t.tolist(),
        "variables": {"x": sol.y[0].tolist()},
    }
