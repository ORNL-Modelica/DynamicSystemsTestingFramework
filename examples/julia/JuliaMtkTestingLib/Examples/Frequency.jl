# 1 Hz sinusoid via a second-order oscillator: ẍ + ω² x = 0 with ω = 2π.
# Counterpart to ModelicaTestingLib.Examples.FrequencyTest. Exercises the
# framework's dominant-frequency declared-peaks leaf on a Julia/MTK model.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x(t) v(t)
    @parameters ω
    eqs = [
        D(x) ~ v,
        D(v) ~ -ω^2 * x,
    ]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    # x(0) = 0, v(0) = ω so x(t) = sin(ωt). With ω = 2π → 1 Hz.
    two_pi = 6.283185307179586
    return (
        sys = sys,
        u0 = [x => 0.0, v => two_pi],
        ps = [ω => two_pi],
    )
end
