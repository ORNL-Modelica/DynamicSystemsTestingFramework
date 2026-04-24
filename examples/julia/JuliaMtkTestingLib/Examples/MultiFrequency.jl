# Multi-frequency test: composite y = 3 sin(2πt) + 2 sin(6πt) + sin(14πt).
# Counterpart to ModelicaTestingLib.Examples.MultiFrequencyTest. Three
# independent oscillators whose sum y exercises the framework's
# declared-peaks dominant-frequency mode with n=3.

using ModelingToolkit
using ModelingToolkit: t_nounits as t, D_nounits as D

function build_mtk_system()
    @variables x1(t) v1(t) x2(t) v2(t) x3(t) v3(t) y(t)
    @parameters ω1 ω2 ω3 a1 a2 a3
    eqs = [
        D(x1) ~ v1,  D(v1) ~ -ω1^2 * x1,
        D(x2) ~ v2,  D(v2) ~ -ω2^2 * x2,
        D(x3) ~ v3,  D(v3) ~ -ω3^2 * x3,
        y ~ a1 * x1 + a2 * x2 + a3 * x3,
    ]
    @named sys = ODESystem(eqs, t)
    sys = structural_simplify(sys)
    # Authored frequencies: 1, 3, 7 Hz; amplitudes 3, 2, 1 (distinct so
    # the amplitude-rank peak filter is unambiguous — see D74 notes).
    # Initial conditions chosen so each x_i(t) = sin(ω_i t):
    # x(0)=0, v(0)=ω gives x(t) = sin(ωt).
    two_pi = 6.283185307179586
    return (
        sys = sys,
        u0 = [
            x1 => 0.0, v1 => 1.0 * two_pi,
            x2 => 0.0, v2 => 3.0 * two_pi,
            x3 => 0.0, v3 => 7.0 * two_pi,
        ],
        ps = [
            ω1 => 1.0 * two_pi, ω2 => 3.0 * two_pi, ω3 => 7.0 * two_pi,
            a1 => 3.0, a2 => 2.0, a3 => 1.0,
        ],
    )
end
