# Range check test: x(t) = sin(t) stays within [-1.05, 1.05]. Counterpart to
# ModelicaTestingLib.Examples.RangeCheckTest. Exercises the framework's
# "range" leaf (bounds-check, no reference data needed for scoring).

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
    # x(0) = 0, v(0) = 1 → x(t) = sin(t), peaks at ±1. The spec's
    # range = [-1.05, 1.05] leaves a 5% cushion for numerical noise.
    return (sys = sys, u0 = [x => 0.0, v => 1.0], ps = Float64[])
end
