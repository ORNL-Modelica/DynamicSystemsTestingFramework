# Constant test: x(t) = 2.5 for all t. Counterpart to
# ModelicaTestingLib.Examples.ConstantTest.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t)
    eqs = [D(x) ~ 0.0]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    return (sys = sys, u0 = [x => 2.5], ps = Float64[])
end
