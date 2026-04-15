# FMU examples

This directory demonstrates the FMPy-backed FMU workflow (Phase 2).

## Prebuilt Reference-FMUs

The FMI org publishes prebuilt reference test FMUs as release ZIPs at
[`modelica/Reference-FMUs/releases`](https://github.com/modelica/Reference-FMUs/releases).
We fetch them into a gitignored directory rather than submoduling the source
repo — the source requires CMake + a C compiler to build, which would force a
dev-toolchain dependency on every contributor and CI machine.

Fetch once per clone (or after a version bump):

```bash
uv run python scripts/fetch_reference_fmus.py
```

The script pins a specific release version (see `DEFAULT_VERSION` in the
script), skips re-downloading if the pinned version is already present, and
extracts only the FMI 2.0 + 3.0 FMUs. Output lands at
`examples/fmu/reference-fmus-binaries/` (gitignored).

### What gets extracted

From the release ZIP (~17 MB, filtered to ~4 MB):

- `2.0/*.fmu` — FMI 2.0 FMUs: `BouncingBall`, `Dahlquist`, `Feedthrough`,
  `Resource`, `Stair`, `VanDerPol`.
- `3.0/*.fmu` — FMI 3.0 FMUs: the above plus `Clocks`, `StateSpace`.
- `README.md` + `LICENSE.txt` from the release.

Skipped: FMI 1.0 FMUs (FMPy support is best for 2.0+), and the platform-
specific `fmusim-*` standalone-simulator binaries (unrelated).

### What each FMU demonstrates

- **BouncingBall** — canonical starter; hybrid-ODE with state events.
- **Dahlquist** — stiff linear ODE; tests solver behavior on stiff systems.
- **VanDerPol** — non-linear oscillator; limit-cycle behavior.
- **Feedthrough** — trivial input → output pass-through; tests I/O handling.
- **Stair** — counter-style discrete behaviour.
- **Resource** — FMU with bundled resource files.
- **Clocks** (FMI 3.0 only) — clock-driven behavior new in FMI 3.0.
- **StateSpace** (FMI 3.0 only) — matrix-based linear system; tests vector I/O.

## FMPy dependency

FMPy is an optional extra of this package:

```bash
uv pip install -e ".[fmpy]"
# or bundled with dev extras:
uv pip install -e ".[dev,fmpy]"
```

Tests that exercise the FMPy backend are marked with `@pytest.mark.fmpy` and
skipped automatically if FMPy isn't installed. Tests that require the FMU
fixtures are marked `@pytest.mark.reference_fmus` and skipped if the binaries
haven't been fetched.

## Bumping the pinned version

Edit `DEFAULT_VERSION` in `scripts/fetch_reference_fmus.py`, then run:

```bash
uv run python scripts/fetch_reference_fmus.py --force
```

Commit the script change. The binaries themselves stay gitignored — each
clone refetches on demand.
