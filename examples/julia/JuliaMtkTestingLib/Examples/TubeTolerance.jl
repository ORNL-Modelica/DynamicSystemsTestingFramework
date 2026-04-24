# Tube tolerance test: x(t) is an exponentially-decaying oscillation.
# Counterpart to ModelicaTestingLib.Examples.TubeToleranceTest. Spec
# wraps the reference trajectory in a ±5% rel-mode tube; self-regression
# trivially passes since act = ref.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t) v(t)
    @parameters ω ζ
    eqs = [
        D(x) ~ v,
        D(v) ~ -2 * ζ * ω * v - ω^2 * x,
    ]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    # Under-damped oscillator: ω = 2π rad/s (1 Hz), ζ = 0.1 (10% damping).
    two_pi = 6.283185307179586
    return (
        sys = sys,
        u0 = [x => 1.0, v => 0.0],
        ps = [ω => two_pi, ζ => 0.1],
    )
end
