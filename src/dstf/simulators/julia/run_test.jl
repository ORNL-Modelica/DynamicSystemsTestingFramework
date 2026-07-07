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

# review 2026-07-06 (finding 75): the failure JSON is hand-built (JSON3 may
# not have loaded when the failure happens). Julia's repr()-style escaping
# is NOT JSON (`\$` is invalid) — real error detail was lost to parse
# errors. Minimal manual JSON string escaper instead.
function json_escape(s::AbstractString)
    buf = IOBuffer()
    for c in s
        if c == '\\'
            write(buf, "\\\\")
        elseif c == '"'
            write(buf, "\\\"")
        elseif c == '\n'
            write(buf, "\\n")
        elseif c == '\r'
            write(buf, "\\r")
        elseif c == '\t'
            write(buf, "\\t")
        elseif c < ' '
            write(buf, "\\u" * string(UInt32(c), base=16, pad=4))
        else
            write(buf, c)
        end
    end
    return String(take!(buf))
end

function write_failure(msg)
    open(RESULT_PATH, "w") do io
        write(io, string("{\"success\": false, \"error\": \"",
                         json_escape(string(msg)),
                         "\", \"time\": [], \"variables\": {}}"))
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

    # review 2026-07-06 (finding 4): a diverged / unstable / maxiters-
    # truncated solve must not be written as success — with --accept the
    # truncated trajectory would become the regression baseline. Check the
    # retcode AND that integration actually reached stop_time.
    if !OrdinaryDiffEq.SciMLBase.successful_retcode(sol)
        error("solver did not succeed: retcode=" * string(sol.retcode))
    end
    if isempty(sol.t) || abs(sol.t[end] - STOP_TIME) > 1e-6 * max(abs(STOP_TIME), 1.0)
        t_end = isempty(sol.t) ? "none" : string(sol.t[end])
        error("solve stopped at t=" * t_end * " before stop_time=" *
              string(STOP_TIME) * " (retcode=" * string(sol.retcode) * ")")
    end

    # Collect unknowns (state variables) AND observables (algebraic
    # expressions like `y ~ a*x1 + b*x2` that structural_simplify
    # promotes out of the unknowns list but are still materializable
    # from the solution via sol[expr]). Users authoring MTK tests
    # typically expect both to be accessible.
    unk = ModelingToolkit.unknowns(nt.sys)
    obs_eqs = ModelingToolkit.observed(nt.sys)
    variables = Dict{String, Vector{Float64}}()
    for u in unk
        name = string(u)
        stripped = endswith(name, "(t)") ? name[1:end-3] : name
        variables[stripped] = Float64.(sol[u])
    end
    for eq in obs_eqs
        lhs = eq.lhs
        name = string(lhs)
        stripped = endswith(name, "(t)") ? name[1:end-3] : name
        # Skip if already captured as an unknown (shouldn't happen but
        # defensive) or if the name is a helper symbol MTK introduced.
        if haskey(variables, stripped) || startswith(stripped, "ˍ")
            continue
        end
        try
            variables[stripped] = Float64.(sol[lhs])
        catch
            # Some observables can't be evaluated (e.g., dependent on
            # parameters only); skip silently rather than fail the test.
        end
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
