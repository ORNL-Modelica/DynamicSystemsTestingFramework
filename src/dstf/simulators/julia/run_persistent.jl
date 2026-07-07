# Framework-generated Julia driver for the PERSISTENT worker path (D78).
#
# A long-lived Julia process reads JSON-per-line test requests from stdin
# and writes JSON-per-line responses to stdout. Package load (MTK, ODE,
# JSON3) happens ONCE at startup; per-test cost becomes just
# ``include(user_file)`` + ``structural_simplify`` + ``solve``.
#
# Request:
#   {"user_file": "...", "stop_time": 10.0, "tolerance": 1e-6,
#    "result_path": "...", "test_key": "..."}
# Response:
#   {"test_key": "...", "status": "ok"|"fail", "elapsed": 12.3, "error": "..."}
#
# Control messages:
#   {"cmd": "quit"}  → exits cleanly.
#   {"cmd": "ready"} → emits a ready pulse (used for startup handshake).
#
# Re-inclusion: ``include`` re-evaluates the user file at module scope,
# redefining ``build_mtk_system``. ``Base.invokelatest`` forces the
# generation boundary so the freshly-redefined function is called rather
# than an older compiled version the worker might still have bound.

using Printf
using JSON3
using ModelingToolkit
using OrdinaryDiffEq

# Signal readiness to the dispatcher. The parent can use this as a hand-
# shake marker indicating `using` has completed and the worker is ready
# to accept test requests.
println(stdout, JSON3.write(Dict("event" => "ready", "pid" => getpid())))
flush(stdout)

function handle_request(req)
    user_file = req["user_file"]
    stop_time = Float64(req["stop_time"])
    tol = Float64(req["tolerance"])
    result_path = req["result_path"]
    test_key = get(req, "test_key", "")

    t_start = time()
    try
        # review 2026-07-06 (finding 27): in this long-lived process a user
        # file that fails to (re)define build_mtk_system would silently reuse
        # the PREVIOUS test's definition. Poison the binding with a sentinel
        # method BEFORE every include — if the file doesn't replace it, the
        # invokelatest below raises this error instead of simulating the
        # wrong model (covers bad-file-after-good-file, not just the first).
        Main.eval(:(build_mtk_system() = error(
            "user file did not define build_mtk_system() — stale-definition guard"
        )))
        include(user_file)
        nt = Base.invokelatest(build_mtk_system)
        prob = ModelingToolkit.ODEProblem(nt.sys, nt.u0, (0.0, stop_time), nt.ps)
        sol = OrdinaryDiffEq.solve(prob, OrdinaryDiffEq.Tsit5(); reltol=tol)

        # review 2026-07-06 (finding 4): mirror run_test.jl — a diverged /
        # truncated solve must not be reported (or baselined) as success.
        if !OrdinaryDiffEq.SciMLBase.successful_retcode(sol)
            error("solver did not succeed: retcode=" * string(sol.retcode))
        end
        if isempty(sol.t) || abs(sol.t[end] - stop_time) > 1e-6 * max(abs(stop_time), 1.0)
            t_end = isempty(sol.t) ? "none" : string(sol.t[end])
            error("solve stopped at t=" * t_end * " before stop_time=" *
                  string(stop_time) * " (retcode=" * string(sol.retcode) * ")")
        end

        # Collect unknowns AND observables — see run_test.jl for rationale.
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
            if haskey(variables, stripped) || startswith(stripped, "ˍ")
                continue
            end
            try
                variables[stripped] = Float64.(sol[lhs])
            catch
            end
        end

        payload = Dict(
            "success" => true,
            "time" => Float64.(sol.t),
            "variables" => variables,
        )
        open(result_path, "w") do io
            JSON3.write(io, payload)
        end

        elapsed = time() - t_start
        println(stdout, JSON3.write(Dict(
            "test_key" => test_key,
            "status" => "ok",
            "elapsed" => elapsed,
            "n_vars" => length(variables),
            "n_time" => length(sol.t),
        )))
        flush(stdout)
    catch e
        elapsed = time() - t_start
        buf = IOBuffer()
        showerror(buf, e, catch_backtrace())
        msg = String(take!(buf))
        # Best-effort failure payload so the runner's read_result path still
        # gets a structured response.
        try
            open(result_path, "w") do io
                JSON3.write(io, Dict(
                    "success" => false,
                    "error" => msg,
                    "time" => Float64[],
                    "variables" => Dict{String, Vector{Float64}}(),
                ))
            end
        catch
        end
        println(stdout, JSON3.write(Dict(
            "test_key" => test_key,
            "status" => "fail",
            "elapsed" => elapsed,
            "error" => msg,
        )))
        flush(stdout)
    end
end

# Main loop.
while !eof(stdin)
    line = try
        readline(stdin)
    catch e
        break
    end
    isempty(line) && continue
    req = try
        JSON3.read(line)
    catch e
        println(stderr, "Failed to parse request line: ", line)
        continue
    end
    # Control messages first.
    cmd = get(req, "cmd", "")
    if cmd == "quit"
        println(stdout, JSON3.write(Dict("event" => "quitting")))
        flush(stdout)
        break
    elseif cmd == "ready"
        println(stdout, JSON3.write(Dict("event" => "ready", "pid" => getpid())))
        flush(stdout)
        continue
    end
    # Test request.
    if !haskey(req, "user_file")
        println(stderr, "Ignoring request with no user_file: ", line)
        continue
    end
    handle_request(req)
end
