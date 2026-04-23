# Framework-generated Julia driver for a single test.
#
# Invoked by JuliaRunner as:
#   julia --project=<examples_project> run_test.jl <user_file> <stop_time> <tol> <result_path>
#
# The user file must export `build_mtk_system()` returning a NamedTuple with:
#   sys :: ODESystem   — structurally simplified
#   u0  :: Vector      — initial conditions (pairs of Symbol => value)
#   ps  :: Vector      — parameter values    (pairs of Symbol => value)
#
# Result file is JSON:
#   { "success": bool,
#     "time": [...],
#     "variables": {"name": [...], ...},
#     "error": "..." (only if success == false) }
#
# The wrapper intentionally catches exceptions so the framework always
# gets a structured result (success=false + error) rather than a
# process-level crash.

using Printf

const USER_FILE   = ARGS[1]
const STOP_TIME   = parse(Float64, ARGS[2])
const TOLERANCE   = parse(Float64, ARGS[3])
const RESULT_PATH = ARGS[4]

function write_failure(msg)
    open(RESULT_PATH, "w") do io
        write(io, string("{\"success\": false, \"error\": ",
                         Base.repr(string(msg)),
                         ", \"time\": [], \"variables\": {}}"))
    end
end

try
    import ModelingToolkit
    import OrdinaryDiffEq
    import JSON3

    # Load the user file. It should export `build_mtk_system()`.
    include(USER_FILE)
    isdefined(@__MODULE__, :build_mtk_system) || error(
        "user file $(USER_FILE) must export build_mtk_system()")

    nt = build_mtk_system()
    haskey(nt, :sys) || error("build_mtk_system() must return NamedTuple with :sys")

    u0 = haskey(nt, :u0) ? nt.u0 : []
    ps = haskey(nt, :ps) ? nt.ps : []

    prob = ModelingToolkit.ODEProblem(nt.sys, u0, (0.0, STOP_TIME), ps)
    sol = OrdinaryDiffEq.solve(prob, OrdinaryDiffEq.Tsit5(); reltol=TOLERANCE)

    # Collect unknowns and their trajectories. Names are printable symbols
    # (e.g. "x(t)"); we strip the trailing "(t)" for reporter parity with
    # Modelica variable naming.
    unk = ModelingToolkit.unknowns(nt.sys)
    variables = Dict{String, Vector{Float64}}()
    for u in unk
        name = string(u)
        # "x(t)" → "x"
        stripped = endswith(name, "(t)") ? name[1:end-3] : name
        variables[stripped] = Float64.(sol[u])
    end

    payload = Dict(
        "success" => true,
        "time" => Float64.(sol.t),
        "variables" => variables,
    )
    open(RESULT_PATH, "w") do io
        JSON3.write(io, payload)
    end

    @printf "OK: %d variables, %d time points\n" length(variables) length(sol.t)
catch e
    buf = IOBuffer()
    showerror(buf, e, catch_backtrace())
    msg = String(take!(buf))
    @printf stderr "FAIL: %s\n" msg
    try
        write_failure(msg)
    catch
        # If even writing the failure JSON fails, at least exit nonzero.
        exit(2)
    end
    exit(1)
end
