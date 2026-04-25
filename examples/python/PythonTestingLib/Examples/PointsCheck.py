"""Sinusoid sampled at declared checkpoints. scipy ODE counterpart to

* ``ModelicaTestingLib.Examples.PointsCheckTest`` (Dymola/OpenModelica), and
* ``JuliaMtkTestingLib.Examples.PointsCheck`` (Julia/MTK).

Exercises the framework's ``points`` comparison mode (D84) — declared
checkpoints with mixed abs / rel y-tolerance and an x-tolerance box on
the zero crossing. The signal is the same x(t) = sin(t) integrated
from a harmonic oscillator (``x'' + x = 0``, x(0) = 0, v(0) = 1) so
the numerical content matches the Modelica + Julia counterparts.
"""
from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp


def simulate(stop_time: float, tolerance: float) -> dict:
    """Integrate the harmonic oscillator x'' + x = 0 up to ``stop_time``."""
    def rhs(_t: float, y: np.ndarray) -> list[float]:
        x, v = y
        return [v, -x]

    sol = solve_ivp(
        fun=rhs,
        t_span=(0.0, stop_time),
        y0=[0.0, 1.0],
        rtol=tolerance,
        atol=tolerance,
        dense_output=False,
        t_eval=np.linspace(0.0, stop_time, 501),
    )
    if not sol.success:
        raise RuntimeError(f"solve_ivp failed: {sol.message}")
    return {
        "time": sol.t.tolist(),
        "variables": {"x": sol.y[0].tolist()},
    }
