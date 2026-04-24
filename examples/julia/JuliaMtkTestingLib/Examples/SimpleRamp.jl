# Linear ramp test: x(t) = 2t. Counterpart to ModelicaTestingLib.Examples.SimpleTest.
#
# MTK convention the framework expects: a ``build_mtk_system()`` function
# returning ``(sys, u0, ps)``.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t)
    eqs = [D(x) ~ 2.0]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    return (sys = sys, u0 = [x => 0.0], ps = Float64[])
end
