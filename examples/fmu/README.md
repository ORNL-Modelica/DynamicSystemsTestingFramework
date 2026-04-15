# FMU examples

This directory demonstrates the FMPy-backed FMU workflow (Phase 2).

## Reference-FMUs submodule

The FMI org publishes a small, stable corpus of reference test FMUs at
[`modelica/Reference-FMUs`](https://github.com/modelica/Reference-FMUs). We use
them as zero-cost, license-clean fixtures for both the unit-test suite and the
`examples/fmu/` demonstrations.

The submodule lives at `examples/fmu/reference-fmus/`. Set it up once after cloning:

```bash
git submodule update --init examples/fmu/reference-fmus
```

Or, if you cloned without `--recurse-submodules`:

```bash
git submodule init
git submodule update
```

## What's inside (once the submodule is initialized)

The Reference-FMUs repo ships pre-built FMI 2.0 and FMI 3.0 FMUs for:

- **BouncingBall** — canonical starter; hybrid-ODE with state events.
- **Dahlquist** — stiff linear ODE; tests solver behavior on stiff systems.
- **VanDerPol** — non-linear oscillator; tests handling of limit cycles.
- **Feedthrough** — trivial input → output pass-through; tests I/O handling.
- **Stair** — counter-style discrete behaviour.
- **LinearTransform** — matrix-vector operation; tests vector I/O.
- **Resource** — FMU with bundled resource files.

Each FMU has known analytical or reference trajectories, which means our tests
can validate **correctness** (does the simulation match a known-good trace?)
not just **consistency** (does today's run match yesterday's?).

## FMPy dependency

FMPy is an optional extra of this package. Install with:

```bash
uv pip install -e ".[fmpy]"
# or bundled with dev extras:
uv pip install -e ".[dev,fmpy]"
```

Tests that exercise the FMPy backend are marked with `@pytest.mark.fmpy` and
skipped automatically if FMPy isn't installed. Tests that require the FMU
fixtures are marked `@pytest.mark.reference_fmus` and skipped if the submodule
isn't initialized.
