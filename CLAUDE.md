# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TRANSFORM (TRANsient Simulation Framework Of Reconfigurable Models) is a Modelica library for modeling thermal hydraulic energy systems and multi-physics systems, primarily targeting nuclear reactor applications. It requires **Dymola** as the simulation tool and uses **Modelica Standard Library 4.0.0**.

## Running Tests

All unit tests are run via the Dymola script:
```
runAll_Dymola.mos
```
This file contains `simulateModel(...)` calls for each test case. There is no command-line test runner — tests are executed within Dymola. Each call specifies the model path, solver method, tolerance, and result file name.

To run a single test, execute the corresponding `simulateModel(...)` line in Dymola, e.g.:
```modelica
simulateModel("TRANSFORM.Fluid.Examples.NaturalCirculation", stopTime=1000, numberOfIntervals=1000, method="Esdirk45a", tolerance=1e-4, resultFile="NaturalCirculation");
```

## Architecture

### Package Organization

All source code is under `TRANSFORM/`. Major packages:

- **Fluid** — Largest package. Pipes, volumes, valves, pumps, turbines, sensors, boundary conditions. Subpackage `ClosureRelations/` provides pluggable models for heat transfer, pressure loss, geometry, mass transfer, void fraction, and pump characteristics.
- **HeatAndMassTransfer** — Discretized wall/volume heat conduction models.
- **HeatExchangers** — Shell-and-tube, distributed, LMTD/NTU heat exchanger models.
- **Nuclear** — Reactor kinetics (point kinetics), fuel models, core subchannels, dose calculations.
- **Media** — Custom fluid properties (molten salts, liquid metals, organics) and solid properties (alloys, ceramics, insulation). Organized into `Fluids/` and `Solids/` subdirectories.
- **Examples** — Complete plant models: PWR (Westinghouse), SMR (IRIS), MSR, SFR, sCO2 cycles, CIET facility.
- **Controls, Blocks, Electrical, Mechanics** — Supporting component packages.
- **Math, Units, Types, Utilities, Icons** — Library infrastructure.

### Key Design Patterns

1. **Reconfigurable composition via `replaceable`**: The core extensibility mechanism. Geometry, closure relations (heat transfer correlations, pressure loss models), and media packages are all swappable via `redeclare`. Example: `GenericDistributed_HX` has `replaceable model Geometry`, `replaceable model FlowModel_shell`, `replaceable package Medium_tube`.

2. **Discretized 1D volumes**: Pipes and heat exchangers are arrays of `nV` control volumes with `linspace_1D`-initialized start values for pressure, temperature, enthalpy, mass fractions, and trace substances.

3. **`outer TRANSFORM.Fluid.SystemTF`**: Global system object (analogous to `Modelica.Fluid.System`) accessed via `inner`/`outer` for default system-level parameters.

4. **`Summary` sub-records**: Models expose a `summary` record for post-processing convenience (effective temperatures, max temperatures, normalized positions).

5. **Initialization convention**: Start values are organized into `tab="Initialization"` parameter dialog groups.

### Modelica File Conventions

- Each package has a `package.mo` (package definition) and `package.order` (ordering of sub-components).
- The `within` clause at the top of each `.mo` file specifies its parent package path.
- Global imports in the top-level `package.mo`: `SI` (from `Modelica.Units.SI`), `SIadd` (from `TRANSFORM.Units`), `pi`, `Math`.
- Base/partial classes live in `BaseClasses/` subdirectories within each package.
