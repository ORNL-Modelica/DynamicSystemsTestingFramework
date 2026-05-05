# Julia Recognizer — Research + Design + Implementation Prompt

> **Status**: research-first prompt for a future session. Unlike the prior dashboard-unification plan, the design is not pre-baked. The first half of the session is research (Julia/MTK/Dyad test conventions); the second half is design + implementation only after research lands.

---

## Goal

Build a Julia recognizer for DSTF that auto-discovers tests from `.jl` files, **complementing** (not competing with) the standard testing framework that ModelingToolkit and Dyad users already use. Today every Julia test must be hand-listed in `test_spec.json`; the recognizer should let users drop a `.jl` file in the right directory and have it picked up automatically — the same ergonomics the bundled Modelica `UnitTests` recognizer provides for `.mo` files.

**Non-goal**: forcing users to adopt DSTF-specific Julia syntax. If a user's test file already works with `Test.jl` / `@testset` / whatever MTK or Dyad's convention is, DSTF should integrate with that, not duplicate it.

---

## Why this is research-first

Three things are unknown that determine the design:

1. **What's MTK's standard test convention?** ModelingToolkit's repo and ModelingToolkitStandardLibrary's repo both use `Test.jl` testsets, but the testset shape varies (some compare to analytical solutions, some check structural properties, some run integrators and assert on final values). There may also be a `RegressionTests.jl`–style helper package or convention among MTK users.
2. **What does Dyad use?** Dyad is JuliaSim's modeling language. JuliaHub may ship a regression-test framework with Dyad that we should slot under, not parallel to.
3. **Is there a way to detect a "regression-eligible" Julia test without DSTF-specific markers?** If MTK tests follow a convention like `function build_system() ... end` + `runtests.jl` invoking it, the recognizer might key off those without any DSTF metadata at all.

The session must answer these before committing to a parser shape — otherwise we'll ship a bundled recognizer that users immediately have to override.

---

## Research checklist (first half of session)

- [ ] **MTK test conventions**. WebFetch the README + test/ directory of these representative repos:
  - `SciML/ModelingToolkit.jl` — main MTK repo
  - `SciML/ModelingToolkitStandardLibrary.jl` — domain libraries (electrical, thermal, etc.)
  - `SciML/ModelingToolkitNeuralNets.jl`
  - Any "regression test" packages in the SciML org

  For each: what's the testset shape? What gets asserted (numerical comparison, structural properties, integration success)? Is there a baseline-file convention?

- [ ] **Dyad test conventions**. Dyad is at https://help.juliahub.com/dyad/. Find their testing documentation + sample Dyad models with tests. Note any framework-specific patterns.

- [ ] **`Test.jl` ecosystem**. Specifically:
  - `Test.@testset` introspection — can we walk testsets without running them? (`Test.collect_test_results` etc.)
  - `ReferenceTests.jl` — does this exist? If so, how do users specify references?
  - Doctest / regression patterns commonly used.

- [ ] **The current DSTF Julia convention** is at `examples/julia/JuliaMtkTestingLib/Examples/*.jl`:
  ```julia
  function build_mtk_system()
      @variables x(t)
      eqs = [D(x) ~ 2.0]
      @named sys = ODESystem(eqs, t)
      sys = structural_simplify(sys)
      return (sys = sys, u0 = [x => 0.0], ps = Float64[])
  end
  ```
  Each test exports `build_mtk_system()` returning `(sys, u0, ps)`. The runner (`simulators/julia/persistent_runner.py`) calls this. **Audit how widely this convention is shared with MTK best practice** — if it's a DSTF-only invention, the recognizer's design should pivot toward whichever convention MTK users actually follow.

- [ ] **JuliaSim / JuliaHub testing**. Do they ship a regression-test framework alongside MTK? Is there a JuliaSim "testing" package that DSTF can sit underneath?

**Output of research phase**: a 1-2 page summary in this doc updating the "Research findings" section below, with concrete repo links + code excerpts. Then proceed to design.

---

## Design questions (after research)

Based on what research finds, pick among:

### Option A — Marker comment
Bundled recognizer scans for `# DSTF: variables=["x", "y"], stop_time=5.0` comments. Pros: zero Julia deps, dead-simple parser, fast. Cons: DSTF-specific syntax users must learn; comments rot independent of code.

### Option B — Function-name convention (current DSTF)
Recognizer detects `function build_mtk_system()` (or whatever MTK convention winds up being); harvests model_id from filename, sim params from a sibling metadata file or annotations. Pros: leverages current shape; no new syntax. Cons: rigid — every test must follow the convention; hard to layer onto existing MTK code without refactoring.

### Option C — Test.jl testset detection
Recognizer parses `@testset "name" begin ... end` blocks. For each testset, looks for an MTK/Dyad system construction pattern inside (e.g., `ODEProblem(...)` or `solve(...)`); extracts variables to track from `@test` assertions. Pros: idiomatic Julia; users keep their existing tests unchanged; DSTF acts as overlay. Cons: parser complexity; may need actual Julia subprocess for AST introspection rather than regex.

### Option D — DSTFRegression.jl wrapper package
Ship a tiny Julia package that exports `@regression_test name="..." variables=[...] system=... stop_time=...`; user sprinkles it in their test files; recognizer detects the macro. Pros: idiomatic, types checked at Julia-side; users opt in cleanly. Cons: shipping a Julia package alongside the Python package is a release-engineering burden; DSTF currently has zero Julia-side artifacts.

### Option E — Hybrid (multi-recognizer)
Register multiple recognizers for `source_type="julia"`: one for marker comments (Option A, fastest), one for `build_mtk_system()` convention (Option B, current state), one for Test.jl testsets (Option C, for power users). Discovery merges by model_id. Pros: meets users wherever they are; no forced migration. Cons: more code surface; potential for double-discovery confusion.

**Recommendation pre-research**: Option C if the research finds Test.jl testset introspection is feasible without spawning a Julia subprocess at discovery time. Option E if multiple equally-valid conventions exist in the wild. Option A as a fallback if everything else is too brittle.

---

## Constraints (apply regardless of option)

- **Discovery must be fast**. The Modelica recognizer parses `.mo` files via regex in milliseconds. If the Julia recognizer needs to spawn a `julia` subprocess to introspect, that's fine for *test execution* (we already pay it via the persistent worker) but **not for discovery**. Cap discovery latency at ~50ms per file.
- **Don't break existing test_spec.json**. Hand-listed tests must still work after the recognizer ships — if a model is both auto-discovered and listed in test_spec.json, the spec wins (consistent with existing Modelica + JSON recognizer merge behavior in `discovery/test_registry.py`).
- **No forced migration of `JuliaMtkTestingLib`**. The example library should still work after the recognizer ships, ideally without test_spec.json edits — it should be auto-discoverable.
- **Match the bundled-recognizer pattern**. The recognizer should subclass `Recognizer` (in `src/dstf/discovery/recognizer.py`), declare `applies_to = frozenset({"julia"})`, register at module-import time, and emit `RecognizerResult` with the same fields the Modelica recognizer fills in (see `discovery/mo_parser.py:300` for the bundled Modelica example).

---

## Acceptance criteria

- [ ] **Discovery works without test_spec.json**: `dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json discover` finds at least the 8 existing tests when test_spec.json is moved aside (or the recognizer takes precedence on the same models).
- [ ] **Existing tests still pass**: full pytest suite + `JuliaMtkTestingLib` smoke test still produces the same verdicts.
- [ ] **Tests for the recognizer**: `tests/test_julia_recognizer.py` exercises:
  - basic recognition of a representative `.jl` file
  - merge with test_spec.json (spec overrides recognizer values)
  - graceful skip for `.jl` files that don't match the convention
  - performance: 100-file discovery completes in < 5s
- [ ] **Documentation**: `docs/extensibility.md` gets a "Julia recognizer" section explaining the convention; `docs/usage.md` gets a "Discovering Julia tests" example.
- [ ] **D-entry**: `docs/decisions.md` gets a D92 (or whatever's next) entry recording the convention chosen + why over alternatives.

---

## Research findings

> Filled in 2026-05-01 by three parallel research agents (MTK/SciML, Dyad/JuliaSim, Test.jl ecosystem) plus a local audit of `examples/julia/JuliaMtkTestingLib`.

### Current DSTF convention (audit)

Each `.jl` example exports a single `build_mtk_system()` returning `(sys, u0, ps)`. The runner does `include(user_file)` then `Base.invokelatest(build_mtk_system)` (`src/dstf/simulators/julia/run_persistent.jl:34-46`). The `.jl` file carries **zero metadata** — model_id, stop_time, tolerance, and variables-to-track all live in `test_spec.json`. Variables collected = `unknowns(sys) ∪ observed(sys)` (everything MTK exposes). Compared to the Modelica recognizer (`mo_parser.py:300`), which extracts `UnitTests(n, x, errorExpected)` + `experiment(StopTime, Tolerance)` directly from the source, the Julia path has no extraction surface — the function name identifies the file as test-eligible, but nothing else can be inferred from the source alone.

### MTK / SciML conventions

**One dominant pattern** across SciML/ModelingToolkit.jl, SciML/ModelingToolkitStandardLibrary.jl, SciML/ModelingToolkitNeuralNets.jl, and SciML/OrdinaryDiffEq.jl:

```
test/runtests.jl  →  @safetestset "label" begin include("file.jl") end
                ↓
test/<area>/<file>.jl  →  @testset "name" begin
                            @named src = Constant(k = 2); ...; sys = mtkcompile(iosys)
                            prob = ODEProblem(sys, ..., (0.0, 10.0))
                            sol = solve(prob, Rodas4())
                            @test sol.retcode == Success
                            @test sol[src.output.u][end] ≈ 2 atol = 1.0e-3
                          end
```
([MTKStdLib runtests.jl](https://github.com/SciML/ModelingToolkitStandardLibrary.jl/blob/main/test/runtests.jl), [Blocks/sources.jl](https://github.com/SciML/ModelingToolkitStandardLibrary.jl/blob/main/test/Blocks/sources.jl))

System construction is **inline inside the `@testset`** — there is no `build_X()` helper-function convention. Helper functions exist only for synthetic ground-truth (e.g. `lotka_true()` in MTKNeuralNets), not the system-under-test.

**Assertions** (in prevalence order):
1. `@test sol.retcode == Success` (often the only assertion).
2. Scalar `≈` against hand-written literals at endpoints/indices: `@test sol[s.mass.flange.v][end] ≈ -0.1*10 atol=1.0e-3`.
3. Structural checks: `@test length(equations(sys)) == 2`, `@test_call`, `@test_skip`.

**Baseline files: zero.** No CSV/JLD2/JSON/MAT reference data lives alongside any SciML test directory. OrdinaryDiffEq's `test/regression/` checks tolerance-vs-analytic-solution, not snapshot diffs ([ode_dense_tests.jl](https://github.com/SciML/OrdinaryDiffEq.jl/blob/master/test/regression/ode_dense_tests.jl)). No `@regression_test` macro exists in any SciML repo. The only third-party regression package is [RegressionTests.jl](https://juliapackages.com/p/regressiontests) (general-purpose A/B benchmarking, not used by SciML).

**Implication for DSTF:** the `build_mtk_system()` convention is a **DSTF-only invention** — MTK practice doesn't follow it, and there is no MTK-blessed convention for DSTF to inherit. DSTF would be introducing snapshot/baseline regression to a community whose status quo is "solver returned Success and the endpoint matches a hand-written number."

### Dyad / JuliaSim

Dyad has its **own** first-class regression-testing framework, integrated into the Dyad compiler. Tests are declared as metadata blocks **inside `.dyad` source files**:

```
metadata {"Dyad": {"tests": {"case1": {"stop": 10, "expect": {"initial": {"T": 320}}}}}}
```
([Dyad Syntax manual](https://help.juliahub.com/dyad/stable/manual/syntax.html), [Getting Started](https://help.juliahub.com/dyad/dev/tutorials/getting-started.html))

The Dyad compiler auto-generates a Julia `@testset` from that metadata; users run via `Pkg.test`. Per the [JuliaCon 2025 talk](https://pretalx.com/juliacon-2025/talk/WYUASV/) the compiler is "capable of automatically creating reference trajectories for regression testing." The on-disk format/path for those reference trajectories is **not publicly documented** — likely internal to the Dyad compiler. JuliaSim's umbrella product no longer ships a separate testing framework; Dyad is the DSL front-end as of Dec 2025 ([Dyad 2.0 announcement](https://juliahub.com/blog/december-2025-newsletter)).

Real `.dyad` examples: [DyadLang/TranslatedComponents](https://github.com/DyadLang/TranslatedComponents) (Modelica Std Lib ported via LLM), [DyadModelOptimizer.jl/dyad](https://github.com/DyadLang/DyadModelOptimizer.jl/tree/main/dyad).

**Implication for DSTF:** Dyad's testing model (metadata-in-source + Pkg.test execution + compiler-managed baselines) is incompatible with DSTF's external-harness model. Don't try to integrate today. But the metadata blocks are parseable JSON and could become a *secondary* recognizer later (translate `tests.<case>.stop` → `stop_time`, `expect.initial` → points-mode leaf at t=0, `expect.signals` → variables-to-track). Treat Dyad as a Phase-2 recognizer, not the primary path.

### Test.jl introspection

**Key discovery: [TestItems.jl / TestItemRunner.jl](https://juliapackages.com/p/testitemrunner)** uses `@testitem` instead of `@testset`, with explicitly **syntactic discovery** ("test item detection is based on syntactic analysis, with no code from your package being run"). Used by VS Code's Julia extension. If users adopt TestItemRunner, DSTF gets static discovery for free — no tree-sitter, no Julia subprocess.

Standard `@testset` (stdlib Test.jl) has **no static introspection API** — `DefaultTestSet` is built as a side effect of `runtests`. Four syntactic forms (`begin`, `for`, `let`, `call`) and Julia's overloaded `end` keyword (closes `function`/`if`/`for`/`while`/`let`/`module`/`do`/`a[end]`) make Python regex viable for **enumerating testset names** (~85% confidence on well-formed code) but **unreliable for delimiting bodies** (~50%, breaks on `let`-form, triple-quoted descriptions, nested `end`-closing constructs, custom testset types).

**ReferenceTests.jl** ([repo](https://github.com/JuliaTesting/ReferenceTests.jl)) — `@test_reference "filename" expr`, files in `test/references/`. Format-by-extension (.txt/.png/.sha256). Could surface alongside DSTF as a *companion* baseline role, but it's unit-test-grade scalar/image fixtures, not time-series with tolerance trees — wrong shape for DSTF's primary baselines.

**ReTest.jl / InlineTest.jl** — registers testsets at module-load, still requires Julia subprocess. **JuliaSyntax.jl / CSTParser.jl** — no Python bindings. **[tree-sitter-julia](https://github.com/tree-sitter/tree-sitter-julia)** via [py-tree-sitter](https://tree-sitter.github.io/py-tree-sitter/) is the only Python-resident option for proper Julia AST parsing — feasible but heavy for what we need.

**Implication for DSTF:** parsing real-world MTK `@testset` blocks isn't useful even if we could do it cleanly — the bodies contain inline `ODEProblem(..., (0.0, 10.0))` and hand-written `@test` literals, none of which translate to DSTF's `(variables, stop_time, tolerance)` triple without DSTF-specific markers anyway. Static `@testset`-name enumeration is regex-tractable but a thin payoff. `@testitem` is structurally aligned with what we need (static discovery + named scope) and worth supporting as a first-class signal if a library opts in.

### Decision

**Recommendation: Option A (marker comment) as the primary recognizer, layered with two future hooks.**

The pre-research recommendation was Option C (`@testset` parsing). Research kills it: real MTK `@testset` bodies have no extractable simulation metadata — the community style is "construct system inline, assert on hand-written numeric literals." Even if we parsed `@testset` boundaries via tree-sitter, the testsets don't carry the data DSTF needs.

The pivot: since **MTK has no convention DSTF can inherit, DSTF must define one**. The question becomes: marker comment, helper-function name, macro, or Julia-side package?

| Option | Verdict | Why |
|---|---|---|
| A — marker comment (`# DSTF: stop_time=..., variables=[...]`) | **Adopt as primary** | Zero Julia deps, parses in milliseconds, works regardless of how the user structures the rest of the file, easy to layer on existing MTK code without refactoring. |
| B — `build_mtk_system()` function-name | **Keep as fallback** | The current convention works; recognizer can detect `function build_mtk_system(` to identify the file as test-eligible *and* read the marker comment for params. One-test-per-file is a real limitation; address by allowing multiple `build_*()` functions per file each preceded by its own DSTF marker. |
| C — `@testset` parsing | **Reject** | Body extraction needs tree-sitter-julia; payoff is thin since MTK testsets carry no DSTF-relevant metadata anyway. |
| D — `DSTFRegression.jl` Julia package | **Reject for MVP** | Release-engineering burden; DSTF currently ships zero Julia-side artifacts. Reconsider in Phase 2 if users want type-checked metadata. |
| E — multi-recognizer hybrid (A + B + future Dyad) | **Adopt as final shape** | Same code skeleton as the Modelica/JSON recognizer merge in `discovery/test_registry.py`. Marker-comment recognizer is primary; current `build_mtk_system()` shape stays supported via the same recognizer reading the function definition; future Dyad metadata recognizer slots in alongside. |

**Concrete shape for the bundled Julia recognizer:**

1. Parse leading-comment block at the top of the `.jl` file for `# DSTF: key=value, key=[...]` lines (similar to the Python-recognizer convention or `pyproject.toml` `[tool.dstf]` block — pick one and document).
2. Detect at least one `function build_mtk_system(` (or `function build_<name>()` for multi-test-per-file) to confirm test-eligibility.
3. Derive `model_id` from filename + parent-package name (`Examples/Constant.jl` in `JuliaMtkTestingLib` → `JuliaMtkTestingLib.Examples.Constant`, mirroring the Modelica `within` + class-name composition).
4. Emit `RecognizerResult` with the same fields the Modelica recognizer fills (`model_id`, `source_file`, `n_vars`, `x_expressions`, `stop_time`, `tolerance`).

**Future hooks (post-MVP):**
- A `BundledDyadRecognizer` for `.dyad` files that parses `metadata.Dyad.tests.*` blocks. Translates `stop` → `stop_time`, `expect.initial` → points-mode leaf at t=0, `expect.signals` → variables-to-track.
- An optional `@testitem` recognizer if users adopt TestItemRunner — pure-syntactic, regex-tractable since `@testitem` doesn't have the `end`-overloading footguns of `@testset`.
- Surface ReferenceTests.jl files (under `test/references/`) as a companion baseline role — plot-only overlay, doesn't replace primary baseline.

**Existing examples:** `JuliaMtkTestingLib/Examples/*.jl` keeps working without edits — recognizer treats the bare `build_mtk_system()` as a sentinel and pulls sim params from the existing `test_spec.json` (since the merge order is "spec wins over recognizer," the migration is zero-risk). To opt **into** marker-comment-driven discovery without test_spec.json, users add `# DSTF: stop_time=10.0, variables=["x"], tolerance=1e-6` at the top of their file.

---

## Implementation tasks (post-research)

Concrete after research. Order:

1. **`src/dstf/discovery/julia_parser.py`** (new file, mirrors `mo_parser.py`):
   - `_strip_julia_literals()` — strip `#`-line comments, `#= ... =#` block comments, `"..."`/`"""..."""` strings to avoid false positives in docstrings/prose.
   - `_parse_dstf_marker(text) -> Optional[DstfMarker]` — extract leading-comment block of the form `# DSTF: stop_time=10.0, tolerance=1e-6, variables=["x", "v"]`. Single-line variant required for MVP; multi-line `# DSTF:` continuation block can land later.
   - `_extract_module_name(path, project_root) -> str` — derive `model_id` from filename + project structure: `JuliaMtkTestingLib/Examples/Constant.jl` (with `Project.toml` at `JuliaMtkTestingLib/`) → `JuliaMtkTestingLib.Examples.Constant`.
   - `_has_build_function(text) -> bool` — confirm at least one `function build_mtk_system(` or `function build_<name>()` exists (sentinel for "this `.jl` is a DSTF-eligible test").
   - `parse_jl_file(path) -> Optional[JuliaParseResult]` — orchestrates above; returns `None` if no `build_*()` function found.
   - `BundledJuliaRecognizer(Recognizer)`:
     - `applies_to = frozenset({"julia"})`
     - Translates `JuliaParseResult` → `RecognizerResult` with the same fields the Modelica recognizer fills.
   - `register(BundledJuliaRecognizer())` at module import.

2. **`src/dstf/discovery/__init__.py`** — import `julia_parser` so the registration side-effect runs (mirrors how `mo_parser` is currently imported).

3. **`src/dstf/discovery/test_registry.py`** — verify the existing `discover_tests()` walk finds `.jl` files when `source_type="julia"`. Today it scans `.mo`; extend the file-pattern selector or add a `source_type → glob` map. Likely a 5-line change.

4. **`tests/test_julia_recognizer.py`** (new file):
   - Recognizes a representative `.jl` file with a `# DSTF: ...` marker.
   - Recognizes a `build_mtk_system()`-only file with **no** marker (current convention) → emits result with `model_id`+`source_file` only; sim params filled from `test_spec.json` later.
   - Skips a `.jl` file with no `build_*()` function.
   - Skips when the marker keyword appears only inside a string/docstring.
   - Merge with `test_spec.json` — spec wins over recognizer.
   - Performance: 100 synthetic `.jl` files discovered in < 5s.

5. **`examples/julia/JuliaMtkTestingLib/Examples/*.jl`** — leave existing files **unchanged** for the regression smoke test. Optionally add one new file (`MarkerExample.jl`) demonstrating the `# DSTF:` syntax for documentation.

6. **`examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/test_spec.json`** — rename to `test_spec.json.bak` for the smoke test, confirm `dstf discover` still finds the 8 tests via the recognizer alone, then restore.

7. **Documentation:**
   - `docs/extensibility.md` — new "Julia recognizer" section under §3 (Persistent-worker contract is §3 currently — add as §4 or interleave).
   - `docs/usage.md` — "Discovering Julia tests" example with the marker-comment syntax.
   - `docs/decisions.md` — D92 (or whatever's next): "Julia recognizer convention — marker comment + build_*() sentinel; rejected `@testset` parsing because real MTK testsets carry no DSTF-relevant metadata; deferred Dyad recognizer to Phase 2."

8. **Smoke test (manual):** `uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json discover` with `test_spec.json` renamed; expect 8 tests found by the recognizer alone, with sim params either marker-derived or default-filled.

**Out of scope for this PR (Phase 2 candidates):**
- `BundledDyadRecognizer` for `.dyad` metadata blocks.
- `@testitem` recognizer for TestItemRunner adopters.
- ReferenceTests.jl companion-baseline integration.
- Multi-test-per-file via multiple `build_<name>()` + paired marker comments (the parser hooks should leave room, but full multi-test isn't required for MVP).

---

## Starter prompt (to paste into the next session)

> Continuing DSTF at HEAD `921aeb0`. Today's task: build a Julia recognizer for auto-discovering `.jl` tests, complementing (not competing with) the standard MTK/Dyad testing framework that Julia users already use. Plan + research checklist at `docs/superpowers/plans/2026-05-01-julia-recognizer.md`.
>
> Two-phase approach: (1) research the Julia/MTK/Dyad test ecosystem (~½ session) before committing to a design; (2) implement after the research checklist is filled in. The plan doc lists 5 design options pre-emptively but the right answer depends on what the research finds about Test.jl introspection, MTK testset conventions, and whether Dyad ships a regression-test framework.
>
> Goal of phase 1: fill in the "Research findings" section of the plan doc with concrete repo links + code excerpts so phase 2 can pick the right option from A/B/C/D/E without re-litigating the design mid-implementation.
