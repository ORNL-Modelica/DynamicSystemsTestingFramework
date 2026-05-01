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

> Fill this in during the research phase of the next session before committing to a design.

### MTK conventions

_(to be filled in)_

### Dyad conventions

_(to be filled in)_

### Test.jl introspection

_(to be filled in)_

### Decision

_(to be filled in)_

---

## Implementation tasks (pending research)

> Concrete tasks emerge after research. Sketch:
>
> 1. Add `BundledJuliaRecognizer` to `src/dstf/discovery/julia_parser.py` (mirroring `mo_parser.py`'s shape).
> 2. Register for `source_type="julia"` at module import.
> 3. Wire into `test_registry.discover_tests()` — should be automatic via the `_REGISTRY` walk if `applies_to` is set correctly.
> 4. Add `tests/test_julia_recognizer.py`.
> 5. Update `docs/extensibility.md` + `docs/usage.md`.
> 6. D-log entry.
> 7. Smoke test: run `JuliaMtkTestingLib` end-to-end with test_spec.json renamed; expect identical results.

---

## Starter prompt (to paste into the next session)

> Continuing DSTF at HEAD `921aeb0`. Today's task: build a Julia recognizer for auto-discovering `.jl` tests, complementing (not competing with) the standard MTK/Dyad testing framework that Julia users already use. Plan + research checklist at `docs/superpowers/plans/2026-05-01-julia-recognizer.md`.
>
> Two-phase approach: (1) research the Julia/MTK/Dyad test ecosystem (~½ session) before committing to a design; (2) implement after the research checklist is filled in. The plan doc lists 5 design options pre-emptively but the right answer depends on what the research finds about Test.jl introspection, MTK testset conventions, and whether Dyad ships a regression-test framework.
>
> Goal of phase 1: fill in the "Research findings" section of the plan doc with concrete repo links + code excerpts so phase 2 can pick the right option from A/B/C/D/E without re-litigating the design mid-implementation.
