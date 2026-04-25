# Points check test: x(t) = sin(t) sampled at declared checkpoints.
# Counterpart to ModelicaTestingLib.Examples.PointsCheckTest. Exercises
# the framework's "points" leaf — declared checkpoints with mixed
# abs / rel y-tolerance and an x-tolerance box on the zero crossing.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t) v(t)
    eqs = [
        D(x) ~ v,
        D(v) ~ -x,
    ]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    # x(0) = 0, v(0) = 1 → x(t) = sin(t).
    return (sys = sys, u0 = [x => 0.0, v => 1.0], ps = Float64[])
end
