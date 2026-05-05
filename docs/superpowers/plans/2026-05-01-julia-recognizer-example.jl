# =============================================================================
# DSTF Julia recognizer — illustrative example (not real code)
# =============================================================================
# This file shows what a USER would write under the proposed convention from
# `2026-05-01-julia-recognizer.md`. It is a teaching artifact — it does NOT
# live under examples/julia/, is NOT picked up by DSTF discovery, and the
# recognizer that would parse it doesn't exist yet.
#
# Read top to bottom. Each section is annotated with:
#   [USER WRITES]    — what the human author types
#   [RECOGNIZER]     — what the proposed BundledJuliaRecognizer extracts
# =============================================================================


# -----------------------------------------------------------------------------
# Section 1: the DSTF marker comment block (the new piece)
# -----------------------------------------------------------------------------
# [USER WRITES] — A leading comment block. Single line for MVP; multi-line
# `# DSTF:` continuation can come later. Free-form `# ...` comments above or
# below it are ignored (only the line(s) starting with `# DSTF:` matter).
#
# Damped harmonic oscillator: ẍ + 2ζω·ẋ + ω²·x = 0. With ζ=0.1, ω=2π,
# x(0)=1, ẋ(0)=0 we expect a decaying sinusoid; final value should be near
# zero by t=10s.

# DSTF: stop_time=10.0, tolerance=1e-6, variables=["x", "v"]

# [RECOGNIZER] — parses the line above, extracts:
#   stop_time      = 10.0
#   tolerance      = 1e-6
#   x_expressions  = ["x", "v"]    (the variables to track / score against baselines)
#   x_raw          = '"x", "v"'
#   n_vars         = 2
#
# Anything not in the marker is left None on the RecognizerResult; the
# framework defaults fill it in (e.g. tolerance defaults to config.tolerance
# = 1e-4 if no marker tolerance is set).


# -----------------------------------------------------------------------------
# Section 2: the build_*() sentinel function (existing DSTF convention)
# -----------------------------------------------------------------------------
# [USER WRITES] — exactly the shape today's `JuliaMtkTestingLib/Examples/*.jl`
# files use. Returns a NamedTuple `(sys, u0, ps)` that the Julia worker
# (`run_persistent.jl`) plugs into ODEProblem + solve.

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
    two_pi = 6.283185307179586
    return (
        sys = sys,
        u0  = [x => 1.0, v => 0.0],
        ps  = [ω => two_pi, ζ => 0.1],
    )
end

# [RECOGNIZER] — finds `function build_mtk_system(` and uses it as the
# sentinel meaning "this .jl file is a DSTF-eligible test." Without this
# function, the recognizer returns None for the file even if a `# DSTF:`
# marker is present — the marker alone isn't enough; you need a build
# function for the worker to actually call.
#
# The recognizer does NOT execute the function. It only confirms the
# function exists. Variable names, stop_time, and tolerance come from the
# marker comment, NOT from introspecting the ODESystem.


# -----------------------------------------------------------------------------
# Section 3: model_id derivation (no user action needed)
# -----------------------------------------------------------------------------
# [RECOGNIZER] — derives `model_id` from filesystem layout. If this file is
# at `JuliaMtkTestingLib/Examples/DampedOscillator.jl` and the project root
# (the directory holding `Project.toml`) is `JuliaMtkTestingLib/`, the
# composed model_id is:
#
#     JuliaMtkTestingLib.Examples.DampedOscillator
#
# Mirrors how the Modelica recognizer composes `within` + class name into
# `MyLib.Examples.SimpleTest` (see mo_parser.py:_extract_within).


# -----------------------------------------------------------------------------
# Section 4: what RecognizerResult ends up with
# -----------------------------------------------------------------------------
# Putting Sections 1-3 together, the recognizer emits:
#
#   RecognizerResult(
#       model_id      = "JuliaMtkTestingLib.Examples.DampedOscillator",
#       source_file   = Path(".../DampedOscillator.jl"),
#       n_vars        = 2,
#       x_expressions = ["x", "v"],
#       x_raw         = '"x", "v"',
#       stop_time     = 10.0,
#       tolerance     = 1e-6,
#       # method, number_of_intervals, output_interval, x_reference,
#       # error_expected, simulate_only, requested_fmu_export,
#       # requested_baselines all None — left to defaults / spec.
#   )
#
# This is the SAME RecognizerResult shape the Modelica recognizer fills.
# Downstream (test_registry.py) doesn't care what backend produced it.


# -----------------------------------------------------------------------------
# Section 5: merge with test_spec.json
# -----------------------------------------------------------------------------
# If the user ALSO has an entry in test_spec.json:
#
#     {
#       "model": "JuliaMtkTestingLib.Examples.DampedOscillator",
#       "simulation": {"stop_time": 20.0, "tolerance": 1e-8},
#       "comparison": {"tolerance": 0.005}
#     }
#
# the spec wins: stop_time=20.0, tolerance=1e-8 override the marker's
# 10.0 / 1e-6. This matches existing behavior in discovery/test_registry.py
# where JSON-recognizer results merge after bundled-recognizer results
# (later writers win on a per-model_id basis).
#
# Practical implication: drop a `.jl` file in the right directory + a
# marker comment, and discovery picks it up zero-config. Add a test_spec
# entry only if you want to override sim/comparison/metric-tree settings.


# -----------------------------------------------------------------------------
# Section 6: contrast with idiomatic MTK / SciML test style
# -----------------------------------------------------------------------------
# This is what the SAME oscillator regression test would look like written
# in the dominant SciML pattern (see MTKStandardLibrary's test/Blocks/sources.jl):
#
#     @testset "DampedOscillator" begin
#         @variables x(t) v(t)
#         @parameters ω ζ
#         eqs = [D(x) ~ v, D(v) ~ -2 * ζ * ω * v - ω^2 * x]
#         @named sys = ODESystem(eqs, t)
#         sys = structural_simplify(sys)
#         prob = ODEProblem(sys, [x => 1.0, v => 0.0], (0.0, 10.0),
#                           [ω => 2π, ζ => 0.1])
#         sol = solve(prob, Tsit5())
#         @test sol.retcode == ReturnCode.Success
#         @test abs(sol[x][end]) < 0.1                # hand-written literal
#         @test abs(sol[v][end]) < 0.5                # hand-written literal
#     end
#
# Differences:
#
#   * MTK style asserts via hand-written numeric literals inline. Each new
#     test = bump the literal by hand if the model changes.
#   * MTK style has NO baseline-file concept. Regression-vs-prior-run is
#     not a thing in the SciML test corpus.
#   * MTK style uses `@testset "name" begin ... end`. DSTF could parse the
#     name string but cannot extract `stop_time`, `variables`, or
#     `tolerance` from inside the testset body without running the code —
#     the (0.0, 10.0) tuple is positional inside ODEProblem(), not labeled.
#
# DSTF's value-add is: the `# DSTF:` marker carries the regression metadata
# explicitly, the build_mtk_system() function lets the worker drive the
# system without forcing the user to write `@test` literals, and the
# baseline (stored under Resources/ReferenceResults/Julia/<os>/) handles
# the "what did the trajectory look like last time?" question that MTK
# tests don't answer.


# -----------------------------------------------------------------------------
# Section 7: multi-test-per-file (Phase 2, sketch only)
# -----------------------------------------------------------------------------
# Out of scope for the MVP, but the parser hooks should leave room. The
# proposed shape:
#
#     # DSTF[oscillator]: stop_time=10.0, variables=["x", "v"]
#     function build_oscillator()
#         ...
#         return (sys = sys, u0 = ..., ps = ...)
#     end
#
#     # DSTF[critically_damped]: stop_time=20.0, variables=["x"]
#     function build_critically_damped()
#         ...
#     end
#
# Recognizer emits two RecognizerResults:
#   JuliaMtkTestingLib.Examples.DampedOscillator.oscillator
#   JuliaMtkTestingLib.Examples.DampedOscillator.critically_damped
#
# Worker dispatches by `function_name` field on TestModel; today's worker
# always calls `build_mtk_system` so the multi-test path needs a small
# extension to `run_persistent.jl`'s `handle_request`.
