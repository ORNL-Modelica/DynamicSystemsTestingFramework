# Metric tree test: two independent variables, x and y, each with its own
# scoring leaf under an AND combinator with y wrapped in warn.
# Counterpart to ModelicaTestingLib.Examples.MetricTreeTest. The spec
# authors the explicit tree — this model just emits two smooth signals.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t) y(t)
    eqs = [
        D(x) ~ 1.0,        # x(t) = t
        D(y) ~ -0.5 * y,   # y(t) = y0 * exp(-0.5 t)
    ]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    return (sys = sys, u0 = [x => 0.0, y => 1.0], ps = Float64[])
end
