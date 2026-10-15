# Julia

The Julia backend runs ModelingToolkit models. Each test is a `.jl` file that
defines `build_mtk_system()`, returning the simplified system, its initial
values and its parameters; `examples/julia/JuliaMtkTestingLib` in the
repository is a library of them.

## Install

Install Julia 1.11 or newer from <https://julialang.org/downloads/> or with
`juliaup`. The library is a Julia project (its `Project.toml` and
`Manifest.toml`) that holds ModelingToolkit and OrdinaryDiffEq; instantiate it
once:

```console
$ julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

dstf runs it two ways: **workers that stay open** (the default), each one
Julia process that compiles ModelingToolkit once and runs test after test; and
**`--batch`**, one Julia process per test. A project's first run compiles
ModelingToolkit and OrdinaryDiffEq, which takes minutes; later runs use the
compiled cache.

A project of Julia tests sets {ref}`source_type <key-testing-source-type>` to
`"julia"` and names each test's file in its entry's `source`:

<!-- snippet: testing -->
```json
{
  "source_type": "julia",
  "source_path": "../..",
  "backends": {
    "Julia": {"executables": ["julia"]}
  }
}
```

## Check

When it is found, `dstf check Julia` prints the version Julia reports and,
for a project with a `Manifest.toml`, whether that version is at least the
one the manifest was resolved with:

```text
Julia
  executable: /home/you/.julia/juliaup/julia-1.11.9+0.x64.linux.gnu/bin/julia
    found by: search: the standard folder /home/you/.julia/juliaup
  version: julia version 1.11.9
  note: a project's first run pays a multi-minute ModelingToolkit / OrdinaryDiffEq compile; later runs use the cache

OK — Julia is ready.
```

When the block names a `julia` that is not there:

```text
Julia
  executable: not found — the configured executables: /opt/julia-1.11/bin/julia does not exist

FAIL — Julia is not ready. Fix one of:
  1. Install Julia from https://julialang.org/downloads/ (or with `juliaup`), or set "executables" in the Julia block of testing.json's "backends"
```

## Settings

The Julia block takes `executables` alone: {ref}`backend-julia`.
