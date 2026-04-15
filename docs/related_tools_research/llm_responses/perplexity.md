Here is a practical map of open-source testing options for time-dependent simulations, with emphasis on **regression testing, numerical tolerances, and CI-friendly workflows** across Modelica, FMI/FMU, Julia/SciML, and adjacent simulation ecosystems. The strongest open-source patterns today are Python- or Julia-based harnesses that compare reference trajectories against new runs, while FMI validation is better served by dedicated checkers than by generic test frameworks. [modelica.github](https://modelica.github.io/fmi-guides/main/fmi-guide/)

## Tool landscape

### FMU/FMI validation tools
- **FMPy** — Python FMU simulator/validator for FMI 1.0/2.0/3.0, with CLI, GUI, Jupyter notebook generation, and plotting; active on GitHub. [github](https://github.com/CATIA-Systems/FMPy)
- **FMU standard validation page** — not a tool itself, but the FMI project’s curated list points to FMPy and other validators as recommended checks. [fmi-standard](https://fmi-standard.org/validation/)
- **VDMCheck / FMI-VDM-Model** — formal validation of FMUs against FMI 2/3 schemas and semantics; useful for compliance checks rather than numerical regression. [github](https://github.com/INTO-CPS-Association/FMI-VDM-Model)
- **FMU-Check** — static checker mentioned in the FMI guide, good for metadata/schema issues but not trajectory regression. [modelica.github](https://modelica.github.io/fmi-guides/main/fmi-guide/)

### Modelica testing frameworks
- **MoPyRegtest** — lightweight Python regression framework for Modelica models using `unittest`, OpenModelica, and CSV reference outputs; CI-oriented and explicit about GitHub Actions usage. [github](https://github.com/pstelzig/MoPyRegtest)
- **OpenModelicaLibraryTesting** — repository and GitHub Action for automated Modelica library regression tests, including simulation and verification against stored reference files. [github](https://github.com/OpenModelica/openmodelica-library-testing-action)
- **openmodelica-library-testing-action** — the GitHub Action wrapper around OpenModelica library testing, designed for CI pipelines. [github](https://github.com/OpenModelica/openmodelica-library-testing-action)
- **Modelica Standard Library regression-testing workflow** — the MSL wiki describes automated steps such as pedantic checks, loading libraries, and regression testing as part of the ecosystem workflow. [github](https://github.com/modelica/ModelicaStandardLibrary/wiki/Regression-testing-of-Modelica-and-ModelicaTest)
- **Model Testing Toolkit / Optimica Testing Toolkit** — tool-agnostic Modelica/FMI cross-tool testing framework described by Modelon; more industry-oriented and less obviously open-source from the public material, but important conceptually for cross-simulator validation. [modelon](https://modelon.com/blog/regression-testing-as-an-enabler-for-excellence-in-model-development/)

### Julia / SciML testing approaches
- **DifferentialEquations.jl / SciML ecosystem** — the core solver ecosystem; testing is commonly done through package test suites, reference problems, and solver-specific assertions rather than a dedicated “regression testing app”. [github](https://github.com/SciML/DiffEqProblemLibrary.jl)
- **DiffEqProblemLibrary.jl** — a library of canonical problems explicitly used for examples and testing of differential equation solvers. [github](https://github.com/SciML/DiffEqProblemLibrary.jl)
- **DiffEqCallbacks.jl** — not a testing framework, but important for event/callback behavior in simulation workflows, which matters when validating event handling. [github](https://github.com/SciML/DiffEqCallbacks.jl)
- **FMI.jl** — Julia package for FMU simulation and integration into SciML-style workflows; useful for cross-validating native Julia models against FMUs. [juliapackages](https://juliapackages.com/p/fmi)

### General-purpose simulation testing patterns
- **PySimulator** — referenced in Modelica/FMI regression literature as a promising open environment for tool comparison and regression workflows. [ep.liu](https://ep.liu.se/ecp/118/072/ecp15118671.pdf)
- **Python + pytest/unittest + pandas/numpy** — the most common practical harness pattern for comparing trajectories, metrics, and tolerances around external simulators, especially for FMU and Modelica workflows. [github](https://github.com/CATIA-Systems/FMPy)
- **GitHub Actions-based pipelines** — repeatedly emphasized in Modelica tooling repos as the preferred CI integration path. [github](https://github.com/pstelzig/MoPyRegtest)

## Deep comparison

| Tool / approach | Time-series comparison | Tolerance handling | Event validation | Cross-simulator validation | CI/CD integration | Setup ease | Scale for large models |
|---|---|---|---|---|---|---|---|
| FMPy  [github](https://github.com/CATIA-Systems/FMPy) | Good for generating trajectories and comparing results in Python | Manual or scripted; not a dedicated regression DSL | Possible via outputs and event indicators, but not a specialized event-testing harness | Strong for FMU-native comparison; can compare FMUs from different tools | Good via CLI/Python | Moderate | Good for medium-sized FMUs |
| MoPyRegtest  [github](https://github.com/pstelzig/MoPyRegtest) | Yes, via CSV reference result comparison | Yes, via test logic you define in Python | Not a primary feature | Yes, if the underlying toolchain supports it | Strong; designed for GitHub Actions | Moderate, OpenModelica required | Best for library-scale tests, not huge industrial suites |
| OpenModelicaLibraryTesting / action  [github](https://github.com/OpenModelica/openmodelica-library-testing-action) | Yes, reference-file verification is core | Yes, comparison-based verification is built in | Limited in public docs; focused on simulation/verification outputs | Good for Modelica tool/library comparison | Very strong, GitHub Actions ready | Moderate | Good for many-library regression suites |
| VDMCheck  [github](https://github.com/INTO-CPS-Association/FMI-VDM-Model) | No | No | No | Indirectly, by validating FMU semantics before simulation | Usable in automation, but mainly compliance | Moderate | Excellent for static validation at scale |
| FMI guide toolchain (FMPy + checkers)  [modelica.github](https://modelica.github.io/fmi-guides/main/fmi-guide/) | Yes, via FMPy simulation runs | Yes, user-defined | Some structural checks via validators | Yes | Good | Moderate | Good |
| SciML / DifferentialEquations.jl tests  [github](https://github.com/SciML/DiffEqProblemLibrary.jl) | Yes, via solver output assertions | Native Julia test tolerances can be very precise | Strong via callbacks/event assertions | Possible with FMI.jl or custom harnesses | Strong through Julia CI | Moderate | Excellent for large solver test suites |
| DiffEqProblemLibrary.jl  [github](https://github.com/SciML/DiffEqProblemLibrary.jl) | Indirectly, as benchmark/reference problems | Through tests written around the problems | Depends on problem type | Indirectly | Good in package CI | Easy | Good as a benchmark set, not a full framework |
| FMI.jl  [juliapackages](https://juliapackages.com/p/fmi) | Yes | User-defined in Julia tests | Possible through FMU event behavior and solver callbacks | Strong for FMU/native hybrid comparisons | Strong in Julia CI | Moderate | Good |

## How tests are usually structured

The dominant pattern is **golden trajectory regression**: run a model, sample outputs, and compare against stored reference CSV or MAT files using absolute/relative tolerances. A second pattern is **property-based testing**, where you check invariants instead of exact traces, such as conservation, boundedness, monotonicity, or event-order expectations; this is especially attractive for chaotic or stiff systems where exact trajectories drift quickly. A third pattern is **scenario-based testing**, where one model is executed across many parameter sets or inputs and the outputs are compared to prior baselines or acceptance envelopes. [github](https://github.com/SciML/DiffEqCallbacks.jl)

## Ecosystem-specific notes

### FMI/FMU
For FMI, the most useful split is between **compliance validation** and **simulation regression**. VDMCheck and FMU-Check help catch standard violations early, while FMPy is more useful when you want to actually simulate an FMU and compare outputs across versions or toolchains. The FMI guide explicitly recommends using multiple validators because they catch different classes of problems. [fmi-standard](https://fmi-standard.org/validation/)

### Modelica
For Modelica, the strongest open-source regression story is currently **OpenModelica + Python harnesses**. MoPyRegtest is intentionally lightweight and specifically calls out reproducibility checks, CI, and OpenModelica integration. OpenModelicaLibraryTesting and its GitHub Action go further by packaging library regression workflows for automated CI and verification against reference files. [github](https://github.com/OpenModelica/OpenModelicaLibraryTesting)

### Julia / SciML
In SciML, testing is generally **native to the package test suite** rather than outsourced to a separate framework. The ecosystem leans on `Test`, canonical problem libraries, solver callbacks, and comparison to known solutions or stored outputs. FMI.jl adds a bridge for FMU-based regression and cross-validation, which is useful when you want to compare a Julia-native model with a co-simulation artifact. [youtube](https://www.youtube.com/watch?v=Z45tGWP_4YQ)

## Gaps in the ecosystem

The biggest gap is the lack of a **standard, language-agnostic regression framework** for dynamic simulation outputs. Most teams assemble their own harnesses using Python, Julia, or shell scripts, then bolt on tolerance rules and artifact management. Another gap is first-class support for **event-focused validation** and **stochastic reproducibility testing**; these are usually implemented ad hoc inside test code rather than by dedicated tooling. [ep.liu](https://ep.liu.se/ecp/118/072/ecp15118671.pdf)

A second pain point is that **cross-simulator validation** remains fragmented. FMI gives a common exchange format, but matching tolerances, solver settings, and event handling across tools is still difficult, especially when comparing native Modelica, FMUs, and Simulink-like workflows. Proprietary ecosystems still dominate polished user experience for large industrial model regression, while open-source tools offer better transparency but require more glue code. [ep.liu](https://ep.liu.se/ecp/118/074/ecp15118687.pdf)

## Recommended patterns

1. **Use dual-layer testing**: run FMI/Modelica compliance checks first, then run numerical regression on reference trajectories. [github](https://github.com/INTO-CPS-Association/FMI-VDM-Model)
2. **Store reference outputs with metadata**: include solver, tolerances, step size, platform, and tool version alongside the baseline CSV/MAT files so you can distinguish true regressions from numerical drift. [github](https://github.com/pstelzig/MoPyRegtest)
3. **Prefer tolerance bands over exact equality**: compare time-series with absolute and relative tolerances, and consider windowed metrics for stiff or event-heavy models. [github](https://github.com/OpenModelica/openmodelica-library-testing-action)
4. **Separate event assertions from waveform assertions**: test event count, event timing windows, and post-event state transitions independently from continuous trajectories. [github](https://github.com/SciML/DiffEqCallbacks.jl)
5. **Automate in CI**: OpenModelica actions, Python `unittest`/`pytest`, and Julia package CI all support headless regression runs on GitHub Actions. [github](https://github.com/pstelzig/MoPyRegtest)
6. **Cross-validate on FMUs when portability matters**: simulate the same FMU in FMPy, OpenModelica-based tooling, or Julia FMI tooling to expose toolchain sensitivity. [juliapackages](https://juliapackages.com/p/fmi)

## Most useful GitHub repos
- **CATIA-Systems/FMPy** — FMU simulation and validation in Python. [github](https://github.com/CATIA-Systems/FMPy)
- **pstelzig/MoPyRegtest** — Modelica regression testing framework in Python. [github](https://github.com/pstelzig/MoPyRegtest)
- **OpenModelica/OpenModelicaLibraryTesting** — Modelica library regression tests and CI workflow. [github](https://github.com/OpenModelica/OpenModelicaLibraryTesting)
- **OpenModelica/openmodelica-library-testing-action** — GitHub Action for model/library regression tests. [github](https://github.com/OpenModelica/openmodelica-library-testing-action)
- **SciML/DiffEqProblemLibrary.jl** — reference problems for testing differential equation solvers. [github](https://github.com/SciML/DiffEqProblemLibrary.jl)
- **SciML/OrdinaryDiffEq.jl** — core solver package with tests and examples useful as patterns. [github](https://github.com/SciML/OrdinaryDiffEq.jl)
- **INTO-CPS-Association/FMI-VDM-Model** — FMU validation via VDMCheck. [github](https://github.com/INTO-CPS-Association/FMI-VDM-Model)
- **fmi-tools/FMI2-VDM-Model** — earlier FMI 2/3 validation tooling lineage. [github](https://github.com/fmi-tools/FMI2-VDM-Model)

A concise practical recommendation is: **use VDMCheck/FMU-Check for FMU validity, FMPy for simulation and trajectory capture, MoPyRegtest or OpenModelicaLibraryTesting for Modelica regression, and SciML’s native testing plus FMI.jl for Julia-native workflows**. [juliapackages](https://juliapackages.com/p/fmi)