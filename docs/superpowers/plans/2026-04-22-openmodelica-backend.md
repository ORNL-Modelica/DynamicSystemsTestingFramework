# OpenModelica Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third `SimulatorRunner` backend (`OpenModelicaRunner`) that runs `examples/modelica/ModelicaTestingLib/` end-to-end on Linux via `omc` subprocess, producing fresh baselines under `Resources/ReferenceResults/OpenModelica/linux/`.

**Architecture:** `omc` subprocess driven by generated `.mos` scripts (analogous to Dymola's batch fallback, not persistent workers). `OpenModelicaRunner` subclasses `SimulatorRunner`, registered as `"OpenModelica"` via the existing `@register` decorator. Result reading shares the DSresult MAT reader (hoisted from `simulators/dymola/` to `simulators/common/`). Per-phase timings parsed from the `SimulationResult` record printed to omc stdout inside sentinel-bounded `<<<MT_PHASE_TIMINGS>>> ... <<<MT_PHASE_TIMINGS_END>>>` blocks.

**Tech Stack:** Python ≥ 3.10, `subprocess`, `numpy`, `pytest`, existing project deps. External binary: `omc 1.26.3` (installed at `/usr/bin/omc`). No new pip deps.

**Design spec:** `docs/superpowers/specs/2026-04-22-openmodelica-backend-design.md` (commit `101d0a8`).

---

## File structure

### New files
- `src/modelica_testing/simulators/common/__init__.py` — namespace marker
- `src/modelica_testing/simulators/common/mat_reader.py` — hoisted from `dymola/`
- `src/modelica_testing/simulators/openmodelica/__init__.py` — re-exports
- `src/modelica_testing/simulators/openmodelica/mos_generator.py` — `.mos` text builders
- `src/modelica_testing/simulators/openmodelica/log_parser.py` — parse omc stdout
- `src/modelica_testing/simulators/openmodelica/runner.py` — `OpenModelicaRunner` + `OpenModelicaConfig`
- `tests/test_openmodelica_mos.py` — `.mos` generation unit tests
- `tests/test_openmodelica_log_parser.py` — stdout parsing unit tests
- `tests/test_openmodelica_runner.py` — real-`omc` integration tests (gated on `shutil.which("omc")`)
- `tests/fixtures/results_openmodelica/pid_controller_stdout.txt` — captured real stdout
- `tests/fixtures/results_openmodelica/pid_controller_res.mat` — captured real MAT, ~50 KB
- `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json` — Linux/OpenModelica config
- `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/ref_NNNN.json` — fresh baselines (produced by `--accept`)

### Modified files
- `src/modelica_testing/simulators/__init__.py` — add `"OpenModelica": ".openmodelica"` to `_import_builtin_backend` map
- `src/modelica_testing/simulators/dymola/runner.py` — update mat_reader imports
- `src/modelica_testing/simulators/dymola/persistent_runner.py` — update mat_reader import
- `tests/test_simulators.py` — update mat_reader import
- `CLAUDE.md` — add backend description to top-level phase block
- `docs/decisions.md` — add D69 entry

### Deleted files
- `src/modelica_testing/simulators/dymola/mat_reader.py` (hoisted to `common/`)

---

## Task 1: Hoist `mat_reader.py` to `simulators/common/`

**Goal:** Mechanical refactor so OpenModelica + Dymola share the MAT reader without signaling Dymola ownership. Rename `read_dymola_mat` → `read_result_mat` and `list_dymola_mat_variables` → `list_result_mat_variables`. Zero behavior change; baseline 637 stays green.

**Files:**
- Create: `src/modelica_testing/simulators/common/__init__.py`
- Create: `src/modelica_testing/simulators/common/mat_reader.py` (copy from `dymola/mat_reader.py`, rename exports)
- Modify: `src/modelica_testing/simulators/dymola/runner.py` (import path + symbol renames, lines 28 and 339)
- Modify: `src/modelica_testing/simulators/dymola/persistent_runner.py` (import path + symbol rename, line 42)
- Modify: `tests/test_simulators.py` (imports + usages at lines 9, 153, 160, 168, 177, 184, 191, 197, 204, 227)
- Delete: `src/modelica_testing/simulators/dymola/mat_reader.py`

- [ ] **Step 1: Create `common/__init__.py`**

Create `src/modelica_testing/simulators/common/__init__.py` with exactly this content:

```python
"""Cross-backend shared helpers (MAT reader, etc.)."""
```

- [ ] **Step 2: Copy `mat_reader.py` into `common/` and rename exports**

Copy `src/modelica_testing/simulators/dymola/mat_reader.py` → `src/modelica_testing/simulators/common/mat_reader.py` with the following symbol renames inside the new file (the format is DSresult, not Dymola-specific; OpenModelica uses the same format deliberately):

- Rename function `read_dymola_mat` → `read_result_mat` (definition at line 97 of the source file)
- Rename function `list_dymola_mat_variables` → `list_result_mat_variables` (definition at line 75 of the source file)

Leave `_scan_mat4_headers`, `_read_mat4_block`, `read_mat_time_extents`, `_parse_name_matrix` unchanged.

Also update the module docstring (first line) from whatever Dymola-specific wording it has to:

```python
"""Reader for DSresult-format MAT files (Dymola / OpenModelica shared output)."""
```

- [ ] **Step 3: Delete the original `dymola/mat_reader.py`**

```bash
rm src/modelica_testing/simulators/dymola/mat_reader.py
```

- [ ] **Step 4: Update `dymola/runner.py` imports**

In `src/modelica_testing/simulators/dymola/runner.py`:

Line 28 — change from:
```python
from .mat_reader import list_dymola_mat_variables, read_dymola_mat
```
to:
```python
from ..common.mat_reader import list_result_mat_variables, read_result_mat
```

Line 339 — change from:
```python
                from .mat_reader import read_mat_time_extents
```
to:
```python
                from ..common.mat_reader import read_mat_time_extents
```

Then replace every call-site usage in this file:
- `read_dymola_mat(` → `read_result_mat(`
- `list_dymola_mat_variables(` → `list_result_mat_variables(`

Do a final grep after editing: `grep -n 'dymola_mat' src/modelica_testing/simulators/dymola/runner.py` must return nothing.

- [ ] **Step 5: Update `dymola/persistent_runner.py` import**

In `src/modelica_testing/simulators/dymola/persistent_runner.py`, line 42 — change from:
```python
from .mat_reader import read_mat_time_extents
```
to:
```python
from ..common.mat_reader import read_mat_time_extents
```

(The symbol `read_mat_time_extents` does NOT get renamed — only the two with `dymola` in the name do.)

- [ ] **Step 6: Update `tests/test_simulators.py` imports + usages**

Line 9 — change from:
```python
from modelica_testing.simulators.dymola.mat_reader import read_dymola_mat
```
to:
```python
from modelica_testing.simulators.common.mat_reader import read_result_mat
```

Then replace every call site in the file:
- `read_dymola_mat(` → `read_result_mat(`

Verify with: `grep -n 'dymola_mat\|read_dymola' tests/test_simulators.py` — must return nothing after editing.

- [ ] **Step 7: Run the full suite**

```bash
uv run pytest -q --deselect tests/test_interactive_playwright.py
```

Expected: **637 passed** (baseline preserved; or 670 if Playwright isn't skipped and all those pass). No test count change from this task.

If any test fails: look for missed imports. A quick diagnostic is:

```bash
grep -rn 'dymola_mat\|read_dymola\|list_dymola' src/ tests/
```

If that returns any hits, fix the remaining references and re-run.

- [ ] **Step 8: Commit**

```bash
git add src/modelica_testing/simulators/common/__init__.py \
        src/modelica_testing/simulators/common/mat_reader.py \
        src/modelica_testing/simulators/dymola/runner.py \
        src/modelica_testing/simulators/dymola/persistent_runner.py \
        tests/test_simulators.py
git rm src/modelica_testing/simulators/dymola/mat_reader.py
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
refactor: hoist mat_reader from dymola/ to simulators/common/

OpenModelica shares the DSresult MAT format by design; the reader
isn't Dymola-specific. Rename read_dymola_mat -> read_result_mat
and list_dymola_mat_variables -> list_result_mat_variables.
Mechanical refactor, no behavior change — baseline 637 passing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `mos_generator.py` + unit tests

**Goal:** Pure-text `.mos` script builder with dependency classification (path vs. bare-name), variable filter regex assembly, and sentinel-bounded timing print block.

**Files:**
- Create: `src/modelica_testing/simulators/openmodelica/__init__.py`
- Create: `src/modelica_testing/simulators/openmodelica/mos_generator.py`
- Create: `tests/test_openmodelica_mos.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_openmodelica_mos.py`:

```python
"""Pure unit tests for OpenModelica .mos script generation."""

from pathlib import Path

from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.openmodelica.mos_generator import (
    build_simulate_mos,
    classify_dependency,
    build_variable_filter,
)


def _make_test(**overrides) -> TestModel:
    defaults = dict(
        model_id="Demo.Example.A",
        source_file=Path(""),
        source_package="Demo.Example",
        short_name="A",
        n_vars=0,
        variable_patterns=["x", "y.z[1]"],
        stop_time=10.0,
        tolerance=1e-6,
        method="dassl",
        number_of_intervals=500,
    )
    defaults.update(overrides)
    return TestModel(**defaults)


class TestClassifyDependency:
    def test_bare_library_name(self):
        assert classify_dependency("Modelica") == ("loadModel", "Modelica")

    def test_bare_dotted_name(self):
        assert classify_dependency("Modelica.Blocks") == ("loadModel", "Modelica.Blocks")

    def test_path_with_slash(self):
        kind, arg = classify_dependency("/abs/path/to/Lib")
        assert kind == "loadFile"
        assert arg.endswith("package.mo")
        assert arg.startswith("/abs/path/to/Lib")

    def test_path_ending_in_mo(self):
        kind, arg = classify_dependency("/a/b/package.mo")
        assert kind == "loadFile"
        assert arg == "/a/b/package.mo"

    def test_windows_style_path(self):
        kind, arg = classify_dependency("C:\\Libs\\Foo")
        assert kind == "loadFile"
        # normalized path ends with package.mo
        assert arg.endswith("package.mo")


class TestBuildVariableFilter:
    def test_includes_time_and_diagnostics(self):
        regex = build_variable_filter(
            patterns=["x", "y"],
            diagnostic_vars=["CPUtime", "EventCounter"],
        )
        # Must match time, the requested vars, and the diagnostics.
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("time")
        assert pat.fullmatch("x")
        assert pat.fullmatch("y")
        assert pat.fullmatch("CPUtime")
        assert pat.fullmatch("EventCounter")
        # Must NOT match unrelated.
        assert not pat.fullmatch("unrelated_var")

    def test_escapes_regex_metacharacters(self):
        """Names like 'pipe.T[1]' contain regex metacharacters — must be escaped."""
        regex = build_variable_filter(patterns=["pipe.T[1]"], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("pipe.T[1]")
        # The '.' must be literal, not "any char".
        assert not pat.fullmatch("pipeXT[1]")

    def test_glob_star_expands_to_regex(self):
        """Pattern 'pipe.T*' must match 'pipe.T[1]', 'pipe.Tabc', etc."""
        regex = build_variable_filter(patterns=["pipe.T*"], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("pipe.T[1]")
        assert pat.fullmatch("pipe.Tabc")
        assert not pat.fullmatch("pipeXT[1]")  # '.' still literal

    def test_anchored(self):
        """Filter must be fully anchored so OM's partial-match semantics
        don't over-match (pattern 'x' shouldn't hit 'phi' or 'x_der')."""
        regex = build_variable_filter(patterns=["x"], diagnostic_vars=[])
        assert regex.startswith("^(")
        assert regex.endswith(")$")

    def test_empty_patterns_produces_time_only_matcher(self):
        regex = build_variable_filter(patterns=[], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("time")
        assert not pat.fullmatch("anything_else")


class TestBuildSimulateMos:
    def test_includes_std_version_option(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
            std_version="latest",
        )
        assert 'setCommandLineOptions("--std=latest")' in mos

    def test_loads_bare_library_name_before_loadfile_deps(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=["Modelica", "/other/Lib"],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        load_model_line = mos.index("loadModel(Modelica)")
        load_file_line = mos.index('loadFile("/other/Lib/package.mo")')
        load_main_line = mos.index('loadFile("/lib/package.mo")')
        assert load_model_line < load_file_line < load_main_line

    def test_simulator_setup_between_loads_and_cd(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=["setDebugFlags(\"foo\")"],
            diagnostic_vars=[],
        )
        setup_pos = mos.index('setDebugFlags("foo")')
        cd_pos = mos.index('cd("/tmp/test_0001")')
        load_pos = mos.index('loadFile("/lib/package.mo")')
        assert load_pos < setup_pos < cd_pos

    def test_simulate_call_fields(self):
        mos = build_simulate_mos(
            test=_make_test(stop_time=42.0, tolerance=1e-9, method="euler"),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "simulate(Demo.Example.A" in mos
        assert "stopTime=42.0" in mos
        assert "tolerance=1e-09" in mos or "tolerance=1e-9" in mos
        assert 'method="euler"' in mos
        assert 'outputFormat="mat"' in mos
        assert 'fileNamePrefix="result"' in mos
        assert 'variableFilter="' in mos

    def test_sentinel_timing_block_present(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "<<<MT_PHASE_TIMINGS>>>" in mos
        assert "<<<MT_PHASE_TIMINGS_END>>>" in mos
        for field in ("timeFrontend", "timeBackend", "timeSimCode",
                      "timeTemplates", "timeCompile", "timeSimulation",
                      "timeTotal", "resultFile", "messages"):
            assert field in mos, f"missing timing field {field} in .mos"

    def test_test_dir_uses_forward_slashes(self):
        """Even on Windows paths, the emitted cd() should use forward slashes."""
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("C:/work/test_0001"),
            library_package_mo=Path("C:/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert 'cd("C:/work/test_0001")' in mos
        assert 'loadFile("C:/lib/package.mo")' in mos

    def test_empty_dependencies(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "loadModel(" not in mos
        assert mos.count("loadFile(") == 1  # only the main library
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_openmodelica_mos.py -q
```

Expected: `ModuleNotFoundError: No module named 'modelica_testing.simulators.openmodelica'` or similar.

- [ ] **Step 3: Create `openmodelica/__init__.py`**

Create `src/modelica_testing/simulators/openmodelica/__init__.py`:

```python
"""OpenModelica simulator backend (omc subprocess + .mos scripts).

MVP scope: single-subprocess per test via batch-style .mos driven by the
``omc`` binary. Persistent-worker mode (OMPython / OMCSessionZMQ) and FMU
export (``buildModelFMU``) are deferred follow-ups.

One-time bootstrap (per machine) to install the Modelica Standard Library:

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
"""

from .runner import OpenModelicaConfig, OpenModelicaRunner

__all__ = ["OpenModelicaConfig", "OpenModelicaRunner"]
```

Note: `runner.py` doesn't exist yet — the import will fail until Task 4. That's expected. Tests for `mos_generator.py` import `from ...openmodelica.mos_generator import ...` which doesn't go through `__init__.py`, so they work in isolation.

- [ ] **Step 4: Implement `mos_generator.py`**

Create `src/modelica_testing/simulators/openmodelica/mos_generator.py`:

```python
"""Pure-text builders for OpenModelica .mos scripts.

No I/O beyond string assembly — trivially unit-testable. The runner calls
:func:`build_simulate_mos` per test, writes the result to
``<test_dir>/simulate.mos``, then invokes ``omc`` on it.

The generated script prints phase timings inside a sentinel-bounded block
(``<<<MT_PHASE_TIMINGS>>>`` / ``<<<MT_PHASE_TIMINGS_END>>>``) so the log
parser can find them deterministically without scanning unbounded omc
output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ...discovery.test_registry import TestModel
from ..base import _pattern_to_regex

# Markers bounding the phase-timing print block. Picked to be distinct
# enough that no Modelica identifier or omc message contains them.
SENTINEL_BEGIN = "<<<MT_PHASE_TIMINGS>>>"
SENTINEL_END = "<<<MT_PHASE_TIMINGS_END>>>"


def classify_dependency(entry: str) -> tuple[str, str]:
    """Classify a Config.dependencies entry as loadModel vs loadFile.

    Returns (``"loadModel"``, name) for bare library names (no path
    separators, doesn't look like a file), or (``"loadFile"``,
    absolute_path) for path-like entries (resolves to a package.mo).
    """
    # Path-like if it contains a separator, ends in .mo, or resolves to an
    # existing file.
    looks_like_path = (
        "/" in entry or "\\" in entry or entry.endswith(".mo")
    )
    if not looks_like_path:
        return ("loadModel", entry)

    p = Path(entry)
    if entry.endswith(".mo"):
        resolved = p.resolve() if p.is_absolute() else p.resolve()
        return ("loadFile", str(resolved).replace("\\", "/"))
    # Directory form — append package.mo
    resolved = p.resolve() / "package.mo"
    return ("loadFile", str(resolved).replace("\\", "/"))


def build_variable_filter(
    patterns: Iterable[str],
    diagnostic_vars: Iterable[str],
) -> str:
    """Build OM's ``variableFilter`` regex for the given tracked-variable set.

    OM's ``variableFilter`` is a regex over variable names. We escape each
    name literally and join with ``|``. ``time`` is always included; so are
    the diagnostic variables. Returning a regex (not ``.*``) keeps the .mat
    small — OM dumps all parameters/aliases/derivatives by default.
    """
    alternatives: list[str] = []
    # Always include time
    alternatives.append(re.escape("time"))
    # Tracked variables: expand globs (*, ?) to regex via the framework's
    # existing glob-to-regex helper, so a pattern like ``pipe.T*`` becomes
    # ``pipe\.T.*`` and the filter actually matches the runtime variable
    # names (post-simulation ``resolve_variable_patterns`` still narrows
    # the set for the pass/fail comparison).
    for pat in patterns:
        alternatives.append(_pattern_to_regex(pat).pattern)
    # Diagnostic variables (literal names — no globs)
    for dv in diagnostic_vars:
        alternatives.append(re.escape(dv))
    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for a in alternatives:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    # OM's variableFilter is a POSIX ERE applied with partial-match semantics
    # by default — an unanchored alternation would over-match (e.g. "x" would
    # hit "phi" or "x_derivative"). Anchor with ^(...)$ to force whole-name
    # match.
    return "^(" + "|".join(unique) + ")$"


def _format_sim_kwarg(key: str, value) -> str:
    """Render one key=value for OM's simulate() call."""
    if isinstance(value, str):
        # OM escape: double-quote and escape backslashes + quotes
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    if isinstance(value, bool):
        return f"{key}={'true' if value else 'false'}"
    if isinstance(value, float):
        # Avoid scientific notation for integer-valued floats, use repr otherwise.
        if value == int(value):
            return f"{key}={value!r}"
        return f"{key}={value!r}"
    return f"{key}={value!r}"


def build_simulate_mos(
    *,
    test: TestModel,
    test_dir: Path,
    library_package_mo: Path,
    dependencies: list[str],
    simulator_setup: list[str],
    diagnostic_vars: list[str],
    std_version: str = "latest",
) -> str:
    """Assemble the full per-test .mos script.

    See the module docstring for the shape. All path arguments are emitted
    with forward slashes (OM accepts them on Windows too, and it sidesteps
    Modelica string-escaping of backslashes).
    """
    def fwd(path: Path) -> str:
        return str(path).replace("\\", "/")

    lines: list[str] = []
    lines.append(f'setCommandLineOptions("--std={std_version}");')

    for dep in dependencies:
        kind, arg = classify_dependency(dep)
        if kind == "loadModel":
            lines.append(f"loadModel({arg});")
        else:
            lines.append(f'loadFile("{arg}");')
        lines.append('getErrorString();')

    # Main library
    lines.append(f'loadFile("{fwd(library_package_mo)}");')
    lines.append("getErrorString();")

    # Setup commands (backend-specific; user owns these)
    for cmd in simulator_setup:
        c = cmd.strip()
        if not c.endswith(";"):
            c = c + ";"
        lines.append(c)

    lines.append(f'cd("{fwd(test_dir)}");')

    # simulate(...) call
    sim_kwargs: list[str] = []
    sim_kwargs.append(f"stopTime={float(test.stop_time)!r}")
    if test.number_of_intervals is not None:
        sim_kwargs.append(f"numberOfIntervals={int(test.number_of_intervals)}")
    elif test.output_interval is not None:
        sim_kwargs.append(f"outputInterval={float(test.output_interval)!r}")
    sim_kwargs.append(f"tolerance={float(test.tolerance)!r}")
    # OM's solver names are lowercase ("dassl", "euler", "rungekutta") — the
    # framework-wide default comes from Dymola conventions ("Dassl"), so we
    # normalize here rather than push the awareness into TestModel.
    sim_kwargs.append(_format_sim_kwarg("method", (test.method or "dassl").lower()))
    sim_kwargs.append('outputFormat="mat"')
    sim_kwargs.append('fileNamePrefix="result"')
    var_filter = build_variable_filter(test.variable_patterns, diagnostic_vars)
    sim_kwargs.append(_format_sim_kwarg("variableFilter", var_filter))

    lines.append(f"res := simulate({test.model_id}, {', '.join(sim_kwargs)});")
    lines.append("getErrorString();")

    # Sentinel-bounded phase-timing print block
    lines.append(f'print("{SENTINEL_BEGIN}\\n");')
    for field in (
        "timeFrontend", "timeBackend", "timeSimCode",
        "timeTemplates", "timeCompile", "timeSimulation", "timeTotal",
    ):
        lines.append(f'print("{field}=" + String(res.{field}) + "\\n");')
    lines.append('print("resultFile=" + res.resultFile + "\\n");')
    lines.append('print("messages=" + res.messages + "\\n");')
    lines.append(f'print("{SENTINEL_END}\\n");')

    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_openmodelica_mos.py -v
```

Expected: all ~12 tests pass (the test file has 3 classes with multiple tests each — final count should be between 10 and 14 depending on how the assertions land).

- [ ] **Step 6: Commit**

```bash
git add src/modelica_testing/simulators/openmodelica/__init__.py \
        src/modelica_testing/simulators/openmodelica/mos_generator.py \
        tests/test_openmodelica_mos.py
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
feat(openmodelica): .mos script generator + unit tests

Pure-text builder for the per-test .mos script. Classifies
Config.dependencies entries as loadModel (bare name) vs loadFile
(path), escapes variable-filter regex, emits sentinel-bounded
timing print block for deterministic stdout parsing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `log_parser.py` + unit tests + captured fixture

**Goal:** Parse `omc` stdout to extract the `SimulationResult` record fields bounded by `<<<MT_PHASE_TIMINGS>>>` / `<<<MT_PHASE_TIMINGS_END>>>`. Graceful failure on truncated / missing blocks.

**Files:**
- Create: `src/modelica_testing/simulators/openmodelica/log_parser.py`
- Create: `tests/test_openmodelica_log_parser.py`
- Create: `tests/fixtures/results_openmodelica/pid_controller_stdout.txt`

- [ ] **Step 1: Capture a real stdout fixture**

Run the same smoke test that was used during design and capture its stdout:

```bash
mkdir -p tests/fixtures/results_openmodelica
rm -rf /tmp/om_fixture_capture && mkdir -p /tmp/om_fixture_capture
cat > /tmp/om_fixture_capture/capture.mos <<'EOF'
setCommandLineOptions("--std=latest");
loadModel(Modelica);
getErrorString();
cd("/tmp/om_fixture_capture");
res := simulate(Modelica.Blocks.Examples.PID_Controller, stopTime=1.0, numberOfIntervals=50, tolerance=1e-6, method="dassl", outputFormat="mat", fileNamePrefix="result", variableFilter="time");
getErrorString();
print("<<<MT_PHASE_TIMINGS>>>\n");
print("timeFrontend=" + String(res.timeFrontend) + "\n");
print("timeBackend=" + String(res.timeBackend) + "\n");
print("timeSimCode=" + String(res.timeSimCode) + "\n");
print("timeTemplates=" + String(res.timeTemplates) + "\n");
print("timeCompile=" + String(res.timeCompile) + "\n");
print("timeSimulation=" + String(res.timeSimulation) + "\n");
print("timeTotal=" + String(res.timeTotal) + "\n");
print("resultFile=" + res.resultFile + "\n");
print("messages=" + res.messages + "\n");
print("<<<MT_PHASE_TIMINGS_END>>>\n");
EOF
(cd /tmp/om_fixture_capture && omc capture.mos) > tests/fixtures/results_openmodelica/pid_controller_stdout.txt 2>&1
```

Verify the sentinel block shows up:

```bash
grep -c 'MT_PHASE_TIMINGS' tests/fixtures/results_openmodelica/pid_controller_stdout.txt
```

Expected: `2` (begin + end). If `0`, something failed in the capture. The runner tests (Task 5) will regenerate the `.mat` fixture — for Task 3 we only need the stdout.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_openmodelica_log_parser.py`:

```python
"""Pure unit tests for OpenModelica stdout parsing."""

from pathlib import Path

import pytest

from modelica_testing.simulators.openmodelica.log_parser import (
    ParsedOmcOutput,
    parse_omc_stdout,
)

FIXTURES = Path(__file__).parent / "fixtures" / "results_openmodelica"


class TestParseSuccess:
    def test_real_pid_controller_fixture(self):
        text = (FIXTURES / "pid_controller_stdout.txt").read_text()
        parsed = parse_omc_stdout(text)
        assert parsed.success is True
        assert parsed.result_file.endswith("result_res.mat")
        assert parsed.timings is not None
        # Real values vary but these fields must exist and be non-negative
        for k in ("frontend", "backend", "simcode", "templates",
                  "compile", "simulation", "total"):
            assert k in parsed.timings
            assert parsed.timings[k] >= 0.0

    def test_synthetic_success(self):
        text = (
            "some preamble\n"
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.1\n"
            "timeBackend=0.05\n"
            "timeSimCode=0.01\n"
            "timeTemplates=0.02\n"
            "timeCompile=0.9\n"
            "timeSimulation=0.03\n"
            "timeTotal=1.11\n"
            "resultFile=/tmp/test_0001/result_res.mat\n"
            "messages=The simulation finished successfully.\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )
        p = parse_omc_stdout(text)
        assert p.success is True
        assert p.result_file == "/tmp/test_0001/result_res.mat"
        assert p.timings["frontend"] == 0.1
        assert p.timings["total"] == 1.11
        assert "finished successfully" in p.messages


class TestParseFailure:
    def test_empty_result_file_means_failure(self):
        text = (
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.0\n"
            "timeBackend=0.0\n"
            "timeSimCode=0.0\n"
            "timeTemplates=0.0\n"
            "timeCompile=0.0\n"
            "timeSimulation=0.0\n"
            "timeTotal=0.0\n"
            "resultFile=\n"
            "messages=Simulation Failed. Model: X does not exist!\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )
        p = parse_omc_stdout(text)
        assert p.success is False
        assert p.result_file == ""
        assert "does not exist" in p.messages

    def test_error_string_before_sentinel(self):
        text = (
            "Error: Failed to load package Foo\n"
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.0\n"
            "timeBackend=0.0\n"
            "timeSimCode=0.0\n"
            "timeTemplates=0.0\n"
            "timeCompile=0.0\n"
            "timeSimulation=0.0\n"
            "timeTotal=0.0\n"
            "resultFile=\n"
            "messages=\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )
        p = parse_omc_stdout(text)
        assert p.success is False
        # The pre-sentinel error text should be preserved somewhere — either
        # in the .error_notices list or folded into the combined error msg.
        assert any("Failed to load package" in n for n in p.error_notices)


class TestParseMalformed:
    def test_missing_sentinel_end_returns_graceful_failure(self):
        text = (
            "some omc output\n"
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.1\n"
            # truncated — no END sentinel
        )
        p = parse_omc_stdout(text)
        assert p.success is False
        assert p.timings is None

    def test_no_sentinels_at_all(self):
        # Timeout / crash scenario — omc never got to the print block.
        text = "omc crashed before the print block ran"
        p = parse_omc_stdout(text)
        assert p.success is False
        assert p.timings is None
        assert p.result_file == ""

    def test_multiple_getErrorString_notices_stitched(self):
        """Pre-sentinel Error/Warning/Notification notices are preserved."""
        text = (
            "Error: thing1\n"
            "Notification: thing2\n"
            "Warning: thing3\n"
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.0\n"
            "timeBackend=0.0\n"
            "timeSimCode=0.0\n"
            "timeTemplates=0.0\n"
            "timeCompile=0.0\n"
            "timeSimulation=0.0\n"
            "timeTotal=0.0\n"
            "resultFile=/tmp/r.mat\n"
            "messages=\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )
        p = parse_omc_stdout(text)
        # All three notices retained regardless of overall success.
        joined = "\n".join(p.error_notices)
        assert "thing1" in joined
        assert "thing3" in joined
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_openmodelica_log_parser.py -q
```

Expected: all fail with `ImportError: cannot import name 'parse_omc_stdout'`.

- [ ] **Step 4: Implement `log_parser.py`**

Create `src/modelica_testing/simulators/openmodelica/log_parser.py`:

```python
"""Parse OpenModelica's ``omc`` stdout for per-test results.

The runner prints a sentinel-bounded block containing the ``SimulationResult``
record fields. This module extracts that block, classifies success/failure,
and surfaces any Error/Warning/Notification notices that appeared before it.

Output shape is a small ``ParsedOmcOutput`` dataclass. The runner consumes it
to populate ``TestRunResult`` (success, timings, error_message).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .mos_generator import SENTINEL_BEGIN, SENTINEL_END


@dataclass
class ParsedOmcOutput:
    """Structured view of what the runner needs from omc stdout."""
    success: bool
    result_file: str = ""
    messages: str = ""
    timings: Optional[dict[str, float]] = None
    error_notices: list[str] = field(default_factory=list)


# Field name in the sentinel block -> key in ParsedOmcOutput.timings
_TIMING_KEYS = {
    "timeFrontend": "frontend",
    "timeBackend": "backend",
    "timeSimCode": "simcode",
    "timeTemplates": "templates",
    "timeCompile": "compile",
    "timeSimulation": "simulation",
    "timeTotal": "total",
}

# Lines like 'Error: ...' / 'Warning: ...' / 'Notification: ...'
_NOTICE_RE = re.compile(r"^\s*(?:Error|Warning|Notification)\b[: ].*$", re.MULTILINE)


def parse_omc_stdout(text: str) -> ParsedOmcOutput:
    """Parse captured stdout into a ``ParsedOmcOutput``.

    The parser is total: any shape of input produces a ``ParsedOmcOutput``,
    with ``success=False`` and ``timings=None`` on truncation / missing
    sentinels.
    """
    begin = text.find(SENTINEL_BEGIN)
    end = text.find(SENTINEL_END)

    # Pre-sentinel notices are collected regardless of whether the block is
    # complete — they help explain what went wrong.
    preamble = text if begin == -1 else text[:begin]
    error_notices = [m.group(0).strip() for m in _NOTICE_RE.finditer(preamble)]

    if begin == -1 or end == -1 or end <= begin:
        return ParsedOmcOutput(
            success=False,
            error_notices=error_notices,
        )

    block = text[begin + len(SENTINEL_BEGIN):end]
    kv: dict[str, str] = {}
    for line in block.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.rstrip("\r")
        kv[k] = v

    timings: dict[str, float] = {}
    for om_name, our_name in _TIMING_KEYS.items():
        raw = kv.get(om_name)
        if raw is None:
            continue
        try:
            timings[our_name] = float(raw)
        except ValueError:
            pass

    result_file = kv.get("resultFile", "")
    messages = kv.get("messages", "")

    success = bool(result_file) and "Failed" not in messages

    return ParsedOmcOutput(
        success=success,
        result_file=result_file,
        messages=messages,
        timings=timings or None,
        error_notices=error_notices,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_openmodelica_log_parser.py -v
```

Expected: all ~7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/modelica_testing/simulators/openmodelica/log_parser.py \
        tests/test_openmodelica_log_parser.py \
        tests/fixtures/results_openmodelica/pid_controller_stdout.txt
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
feat(openmodelica): stdout parser + captured fixture

Parses the sentinel-bounded SimulationResult block emitted by
mos_generator. Surfaces Error/Warning/Notification notices from
the pre-sentinel preamble. Total parser — any malformed input
produces success=False + timings=None, never raises.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `runner.py` + `OpenModelicaConfig` + registry wiring

**Goal:** Wire `OpenModelicaRunner` as the third backend. Implement `run_single_test` (subprocess) and `read_result` (shared MAT reader). Add unit tests that stub the subprocess call so the runner can be exercised without a real `omc`.

**Files:**
- Create: `src/modelica_testing/simulators/openmodelica/runner.py`
- Modify: `src/modelica_testing/simulators/__init__.py` (line 77–80 area)
- Modify: `tests/test_openmodelica_runner.py` (created in this task; integration tests come in Task 5)

- [ ] **Step 1: Write failing unit tests (stubbed subprocess)**

Create `tests/test_openmodelica_runner.py`:

```python
"""OpenModelica runner tests.

Unit tests here stub subprocess.run so they don't require an omc binary.
Task 5 adds real-omc integration tests gated on shutil.which("omc").
"""

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from modelica_testing.config import Config
from modelica_testing.discovery.test_registry import TestModel


def _write_stub_mat(mat_path: Path, stdout_fixture: Path):
    """Copy the captured MAT fixture into `mat_path` to simulate a run."""
    # Task 5 commits a real fixture; until then, the stub tests accept that
    # read_result will error with "file not found" which is fine — we only
    # unit-test the subprocess wrapper here.
    mat_path.parent.mkdir(parents=True, exist_ok=True)


class TestOpenModelicaConfig:
    def test_from_config_basic(self, tmp_path):
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )
        (tmp_path / "package.mo").write_text('package Lib end Lib;')
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
        )
        om = OpenModelicaConfig.from_config(cfg)
        assert om.omc_path  # resolved via shutil.which or falls back
        assert om.std_version == "latest"
        assert "CPUtime" in om.diagnostic_variables
        assert "EventCounter" in om.diagnostic_variables


class TestOpenModelicaRunnerUnit:
    def test_registered_as_OpenModelica(self):
        from modelica_testing.simulators import get_runner_class
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )

        class _DummyConfig:
            simulator = "OpenModelica"
            simulator_backend = "OpenModelica"

        cls = get_runner_class(_DummyConfig())
        assert cls is OpenModelicaRunner

    def test_capabilities_only_batch_fallback(self):
        from modelica_testing.simulators.base import Capability
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        assert OpenModelicaRunner.capabilities == frozenset(
            {Capability.BATCH_FALLBACK},
        )

    def test_artifact_files_are_static(self):
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        names = [name for name, _ in OpenModelicaRunner.artifact_files]
        assert "simulate.mos" in names
        assert "result_res.mat" in names
        assert "omc_stdout.txt" in names
        # No templates
        for n in names:
            assert "{" not in n and "}" not in n

    def test_run_single_test_writes_mos_and_calls_omc(
        self, tmp_path, monkeypatch,
    ):
        """Verifies: subprocess invoked with omc + simulate.mos in test_dir,
        stdout captured to omc_stdout.txt, run_result reflects parsed output."""
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )

        (tmp_path / "package.mo").write_text('package Lib end Lib;')
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
        )
        runner = OpenModelicaRunner(cfg)
        test = TestModel(
            model_id="Lib.A",
            source_file=Path(""),
            source_package="Lib",
            short_name="A",
            n_vars=0,
            variable_patterns=["x"],
            stop_time=1.0,
        )

        stdout_synth = (
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.1\n"
            "timeBackend=0.05\n"
            "timeSimCode=0.01\n"
            "timeTemplates=0.02\n"
            "timeCompile=0.9\n"
            "timeSimulation=0.03\n"
            "timeTotal=1.11\n"
            "resultFile=/tmp/somewhere/result_res.mat\n"
            "messages=The simulation finished successfully.\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )

        captured_call = {}

        def fake_run(cmd, cwd, capture_output, text, timeout):
            captured_call["cmd"] = list(cmd)
            captured_call["cwd"] = cwd
            return CompletedProcess(args=cmd, returncode=0, stdout=stdout_synth, stderr="")

        monkeypatch.setattr(
            "modelica_testing.simulators.openmodelica.runner.subprocess.run",
            fake_run,
        )

        result = runner.run_single_test(test, test_key="test_0001", index=1, total=1)
        # Subprocess invocation
        assert captured_call["cmd"][0].endswith("omc") or captured_call["cmd"][0] == "omc"
        assert captured_call["cmd"][1] == "simulate.mos"
        # stdout file written
        stdout_path = cfg.work_dir / "test_0001" / "omc_stdout.txt"
        assert stdout_path.exists()
        assert "<<<MT_PHASE_TIMINGS>>>" in stdout_path.read_text()
        # .mos written
        mos_path = cfg.work_dir / "test_0001" / "simulate.mos"
        assert mos_path.exists()
        assert "simulate(Lib.A" in mos_path.read_text()
        # run_result reflects parsed timing
        assert result.success is True
        assert result.translation_wall == pytest.approx(0.1 + 0.05 + 0.01 + 0.02 + 0.9)
        assert result.sim_wall == pytest.approx(0.03)
        assert result.statistics["timing"]["total"] == pytest.approx(1.11)

    def test_run_single_test_surfaces_failure(self, tmp_path, monkeypatch):
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        (tmp_path / "package.mo").write_text('package Lib end Lib;')
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
        )
        runner = OpenModelicaRunner(cfg)
        test = TestModel(
            model_id="Lib.DoesNotExist",
            source_file=Path(""),
            source_package="Lib",
            short_name="DoesNotExist",
            n_vars=0,
            variable_patterns=[],
            stop_time=1.0,
        )
        stdout_synth = (
            "Error: Class Lib.DoesNotExist not found.\n"
            "<<<MT_PHASE_TIMINGS>>>\n"
            "timeFrontend=0.0\n"
            "timeBackend=0.0\n"
            "timeSimCode=0.0\n"
            "timeTemplates=0.0\n"
            "timeCompile=0.0\n"
            "timeSimulation=0.0\n"
            "timeTotal=0.0\n"
            "resultFile=\n"
            "messages=Simulation Failed. Model: Lib.DoesNotExist does not exist!\n"
            "<<<MT_PHASE_TIMINGS_END>>>\n"
        )

        def fake_run(cmd, cwd, capture_output, text, timeout):
            return CompletedProcess(args=cmd, returncode=0, stdout=stdout_synth, stderr="")

        monkeypatch.setattr(
            "modelica_testing.simulators.openmodelica.runner.subprocess.run",
            fake_run,
        )

        result = runner.run_single_test(test, test_key="test_0001", index=1, total=1)
        assert result.success is False
        assert result.error_message
        assert "not found" in result.error_message or "does not exist" in result.error_message
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_openmodelica_runner.py -q
```

Expected: all fail at import or at `get_runner_class` dispatch since `runner.py` doesn't exist yet.

- [ ] **Step 3: Implement `runner.py`**

Create `src/modelica_testing/simulators/openmodelica/runner.py`:

```python
"""OpenModelica runner: omc subprocess + .mos scripts.

MVP scope analogous to Dymola's batch fallback: one ``omc`` process per test
driven by a generated ``simulate.mos``. Persistent workers (OMPython /
OMCSessionZMQ) are a follow-up; FMU export (``buildModelFMU``) too.

One-time per machine (bootstrap MSL):

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'

``simulator_setup`` commands run between library-loading and ``cd()``. These
are emitted as-is and are backend-specific (Dymola-syntactic commands like
``Advanced.UI.TranslationInCommandLog := true`` will fail on omc). Users
maintain a separate ``testing.linux.json`` for the OpenModelica run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ...config import Config
from ...discovery.test_registry import TestModel
from .. import register
from ..base import (
    Capability,
    DatasetType,
    SimulatorRunner,
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
)
from ..common.mat_reader import (
    list_result_mat_variables,
    read_result_mat,
)
from .log_parser import parse_omc_stdout
from .mos_generator import build_simulate_mos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenModelicaConfig:
    """OpenModelica-specific settings extracted from the universal Config."""
    omc_path: str
    simulator_setup: tuple[str, ...] = ()
    diagnostic_variables: tuple[str, ...] = ("CPUtime", "EventCounter")
    std_version: str = "latest"

    @classmethod
    def from_config(cls, config: Config) -> "OpenModelicaConfig":
        omc_path = config.simulator_path or shutil.which("omc") or "omc"
        return cls(
            omc_path=omc_path,
            simulator_setup=tuple(config.simulator_setup),
            diagnostic_variables=tuple(config.diagnostic_variables),
        )


@register("OpenModelica")
class OpenModelicaRunner(SimulatorRunner):
    """OpenModelica backend using omc as a subprocess driver."""

    capabilities = frozenset({Capability.BATCH_FALLBACK})
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("simulate.mos", "Simulation script"),
        ("result_res.mat", "Result file"),
        ("result.log", "Simulation log"),
        ("result_info.json", "Model info"),
        ("omc_stdout.txt", "omc output"),
    )

    RESULT_MAT_FILENAME = "result_res.mat"
    STDOUT_FILENAME = "omc_stdout.txt"
    MOS_FILENAME = "simulate.mos"

    def __init__(self, config: Config):
        super().__init__(config)
        self.om_config = OpenModelicaConfig.from_config(config)

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def run_single_test(
        self,
        test: TestModel,
        test_key: str,
        index: int,
        total: int,
    ) -> TestRunResult:
        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "translating")

        # Build and write the .mos
        mos_text = build_simulate_mos(
            test=test,
            test_dir=test_dir,
            library_package_mo=self.config.library_dir / "package.mo",
            dependencies=list(self.config.dependencies),
            simulator_setup=list(self.om_config.simulator_setup),
            diagnostic_vars=list(self.om_config.diagnostic_variables),
            std_version=self.om_config.std_version,
        )
        mos_path = test_dir / self.MOS_FILENAME
        mos_path.write_text(mos_text, encoding="utf-8")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout,
        )

        wall_start = time.monotonic()
        try:
            proc = subprocess.run(
                [self.om_config.omc_path, self.MOS_FILENAME],
                cwd=test_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - wall_start
            (test_dir / self.STDOUT_FILENAME).write_text(
                (exc.stdout or "") + "\n[TimeoutExpired]\n", encoding="utf-8",
            )
            msg = f"omc exceeded {timeout}s timeout"
            logger.warning("Test %s: %s", test.model_id, msg)
            if self.progress:
                self.progress.on_finish(
                    test_key, success=False, elapsed=elapsed,
                    detail=msg, timed_out=True,
                )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
                timed_out=True,
            )

        elapsed = time.monotonic() - wall_start
        stdout_text = proc.stdout or ""
        (test_dir / self.STDOUT_FILENAME).write_text(stdout_text, encoding="utf-8")

        parsed = parse_omc_stdout(stdout_text)

        # Build statistics.timing — wall-clock seconds captured Python-side.
        stats: dict = {}
        if parsed.timings is not None:
            stats["timing"] = dict(parsed.timings)

        # Translation-wall = sum of all pre-simulation phases.
        translation_wall: Optional[float] = None
        sim_wall: Optional[float] = None
        if parsed.timings is not None:
            t = parsed.timings
            translation_wall = (
                t.get("frontend", 0.0) + t.get("backend", 0.0)
                + t.get("simcode", 0.0) + t.get("templates", 0.0)
                + t.get("compile", 0.0)
            )
            sim_wall = t.get("simulation")

        if parsed.success and (test_dir / self.RESULT_MAT_FILENAME).exists():
            if self.progress:
                self.progress.on_phase(test_key, "simulating")
                self.progress.on_finish(test_key, success=True, elapsed=elapsed)
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
                translation_wall=translation_wall,
                sim_wall=sim_wall,
                statistics=stats or None,
            )

        # Failure: build an error message that surfaces both pre-sentinel
        # notices and the simulate() record's `messages` field.
        err_parts: list[str] = []
        if proc.returncode != 0:
            err_parts.append(f"omc exit code {proc.returncode}")
        if parsed.error_notices:
            err_parts.append("; ".join(parsed.error_notices[:5]))
        if parsed.messages:
            err_parts.append(parsed.messages.strip())
        if not parsed.result_file:
            err_parts.append("no result file produced")
        msg = " | ".join(p for p in err_parts if p)[:2048] or "omc simulation failed"

        # If MSL wasn't installed, surface a clear hint.
        if any("Failed to load package Modelica" in n for n in parsed.error_notices):
            msg = (
                "MSL not installed. Run: omc -e "
                "'updatePackageIndex(); installPackage(Modelica); "
                "getErrorString();' | " + msg
            )

        if self.progress:
            self.progress.on_finish(
                test_key, success=False, elapsed=elapsed, detail=msg[:120],
            )
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=msg,
            translation_wall=translation_wall,
            sim_wall=sim_wall,
            statistics=stats or None,
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: Optional[TestRunResult],
    ) -> TestResult:
        stats = dict(run_result.statistics) if run_result and run_result.statistics else None
        test_dir = self.config.work_dir / test_key
        mat_path = test_dir / self.RESULT_MAT_FILENAME

        # Enrich stats with wall-clock timing summary (mirrors DymolaRunner).
        if run_result and (run_result.translation_wall is not None
                           or run_result.sim_wall is not None):
            timing: dict[str, float] = {}
            if run_result.translation_wall is not None:
                timing["translation_wall"] = round(run_result.translation_wall, 2)
            if run_result.sim_wall is not None:
                timing["sim_wall"] = round(run_result.sim_wall, 2)
            if run_result.elapsed:
                acct = (run_result.translation_wall or 0.0) + (run_result.sim_wall or 0.0)
                timing["other_wall"] = round(max(0.0, run_result.elapsed - acct), 2)
                timing["total_wall"] = round(run_result.elapsed, 2)
            if stats is None:
                stats = {}
            # Preserve the raw per-phase seconds stats["timing"] already has,
            # and overlay these summary keys on top.
            stats.setdefault("timing", {}).update(timing)

        if not mat_path.exists():
            msg = (run_result.error_message if run_result else None) \
                or f"Result file not found: {mat_path}"
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=msg,
                statistics=stats,
            )

        # Variable resolution
        diag_names = list(self.om_config.diagnostic_variables)
        needed = _compute_needed_variables(mat_path, test, diag_names)

        mat_data = read_result_mat(mat_path, variable_names=needed)
        if mat_data is None:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse: {mat_path}",
                statistics=stats,
            )

        variables, diagnostics = _extract_variables(mat_data, test, diag_names)

        if diagnostics:
            if stats is None:
                stats = {}
            for diag in diagnostics:
                if len(diag.values) > 0:
                    sig = 7 if diag.values.dtype == np.float32 else 15
                    stats[diag.name] = float(f"%.{sig}g" % diag.values[-1])

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=diagnostics,
            statistics=stats,
        )


# ---------------------------------------------------------------------------
# Helpers (free functions for testability)
# ---------------------------------------------------------------------------

def _compute_needed_variables(
    mat_path: Path,
    test: TestModel,
    diagnostic_vars: list[str],
) -> Optional[set[str]]:
    """Determine which variable names to extract.

    Returns a set, or None to mean "load everything" (fallback if we can't
    read the name matrix).
    """
    needed: set[str] = set()
    if test.variable_patterns:
        all_names = list_result_mat_variables(mat_path)
        if all_names is None:
            return None
        resolved = resolve_variable_patterns(test.variable_patterns, all_names)
        needed.update(resolved)
    needed.update(diagnostic_vars)
    return needed if needed else None


def _extract_variables(
    mat_data: dict,
    test: TestModel,
    diagnostic_vars: list[str],
) -> tuple[list[VariableResult], list[VariableResult]]:
    """Build VariableResult lists for tracked + diagnostic variables.

    Same shape as ``dymola.runner._extract_variables`` for the spec-variable
    path. UnitTests (``unitTests.x[i]``) handling is delegated — OM variable
    naming for UnitTests components is identical to Dymola's.
    """
    results: list[VariableResult] = []
    seen: set[str] = set()
    idx = 1

    # UnitTests variables (source = "unit_tests" or "both")
    if test.source in ("unit_tests", "both") and test.n_vars > 0:
        for i in range(1, test.n_vars + 1):
            var_name = f"unitTests.x[{i}]"
            if var_name in mat_data:
                time_arr, values = mat_data[var_name]
                if len(test.x_expressions) == test.n_vars:
                    label = test.x_expressions[i - 1]
                elif len(test.x_expressions) == 1 and test.n_vars > 1:
                    label = f"{test.x_expressions[0]}[{i}]"
                else:
                    label = f"x[{i}]"
                results.append(VariableResult(
                    index=idx, time=time_arr, values=values, name=label,
                ))
                seen.add(var_name)
                seen.add(label)
                idx += 1

    # Pattern-based variables
    if test.variable_patterns:
        available = list(mat_data.keys())
        resolved = resolve_variable_patterns(test.variable_patterns, available)
        for var_name in resolved:
            if var_name in seen:
                continue
            if var_name in mat_data:
                time_arr, values = mat_data[var_name]
                results.append(VariableResult(
                    index=idx, time=time_arr, values=values, name=var_name,
                ))
                seen.add(var_name)
                idx += 1

    # Diagnostics (stored as scalar summaries, not full trajectories —
    # decision D54–D55)
    diagnostics: list[VariableResult] = []
    diag_idx = 1
    for var_name in diagnostic_vars:
        if var_name in mat_data:
            time_arr, values = mat_data[var_name]
            diagnostics.append(VariableResult(
                index=diag_idx, time=time_arr, values=values, name=var_name,
            ))
            diag_idx += 1

    return results, diagnostics
```

- [ ] **Step 4: Wire the registry**

In `src/modelica_testing/simulators/__init__.py`, line 77–80, extend the `builtins` dict from:

```python
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
    }
```

to:

```python
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
        "OpenModelica": ".openmodelica",
    }
```

Also update the module docstring at line 1 from `"""Simulator backends. Concrete today: Dymola, FMPy. Pluggable via ``@register``."""` to:

```python
"""Simulator backends. Concrete today: Dymola, FMPy, OpenModelica. Pluggable via ``@register``."""
```

- [ ] **Step 5: Run unit tests to verify they pass**

```bash
uv run pytest tests/test_openmodelica_runner.py -v
```

Expected: all ~5 unit tests pass.

Also run the full suite to verify nothing else broke:

```bash
uv run pytest -q --deselect tests/test_interactive_playwright.py
```

Expected: previous count + new tests (roughly 637 → 650). Exact count depends on how the runner unit tests collapse in pytest.

- [ ] **Step 6: Commit**

```bash
git add src/modelica_testing/simulators/openmodelica/runner.py \
        src/modelica_testing/simulators/__init__.py \
        tests/test_openmodelica_runner.py
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
feat(openmodelica): OpenModelicaRunner + OpenModelicaConfig

Wires omc subprocess path analogous to Dymola's batch fallback.
Registers "OpenModelica" in the backend map. run_single_test
writes .mos, invokes omc, parses stdout; read_result reuses the
hoisted common.mat_reader. Timeout hard-kills (omc is a standalone
executable).

Only Capability.BATCH_FALLBACK declared; PERSISTENT_WORKERS +
FMU_EXPORT deferred.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Real-`omc` integration tests + captured MAT fixture

**Goal:** Lock in the end-to-end `run_single_test` + `read_result` round-trip against real `omc` for the two target shapes (MSL-only + loadFile dependency). Gate on `shutil.which("omc")` so CI environments without omc still pass.

**Files:**
- Modify: `tests/test_openmodelica_runner.py`
- Create: `tests/fixtures/results_openmodelica/pid_controller_res.mat`

- [ ] **Step 1: Capture the MAT fixture**

```bash
rm -rf /tmp/om_fixture_mat && mkdir -p /tmp/om_fixture_mat
cat > /tmp/om_fixture_mat/capture.mos <<'EOF'
setCommandLineOptions("--std=latest");
loadModel(Modelica);
getErrorString();
cd("/tmp/om_fixture_mat");
simulate(Modelica.Blocks.Examples.PID_Controller, stopTime=1.0, numberOfIntervals=50, tolerance=1e-6, method="dassl", outputFormat="mat", fileNamePrefix="result", variableFilter="time|inertia1.phi");
EOF
(cd /tmp/om_fixture_mat && omc capture.mos) > /dev/null 2>&1
ls -la /tmp/om_fixture_mat/result_res.mat
cp /tmp/om_fixture_mat/result_res.mat tests/fixtures/results_openmodelica/pid_controller_res.mat
```

Expected: the MAT is small (variableFilter limits it to ~2 vars). Confirm with `wc -c < tests/fixtures/results_openmodelica/pid_controller_res.mat` — should be a few KB, not MB.

- [ ] **Step 2: Append integration tests to `test_openmodelica_runner.py`**

Add this block at the end of `tests/test_openmodelica_runner.py`:

```python
# ---------------------------------------------------------------------------
# Integration tests (real omc; skipped if not installed)
# ---------------------------------------------------------------------------

import shutil

omc_unavailable = pytest.mark.skipif(
    shutil.which("omc") is None,
    reason="omc not installed — integration tests skipped",
)


@omc_unavailable
class TestOpenModelicaIntegration:
    def test_msl_only_smoke(self, tmp_path):
        """End-to-end: MSL-only model via loadModel, real omc."""
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )

        # Empty library dir — this test only loads MSL.
        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")

        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=["Modelica"],
            timeout=120,
        )
        runner = OpenModelicaRunner(cfg)
        test = TestModel(
            model_id="Modelica.Blocks.Examples.PID_Controller",
            source_file=Path(""),
            source_package="Modelica.Blocks.Examples",
            short_name="PID_Controller",
            n_vars=0,
            variable_patterns=["inertia1.phi"],
            stop_time=1.0,
            tolerance=1e-6,
            number_of_intervals=50,
            method="dassl",
            source="spec",
        )
        run_result = runner.run_single_test(
            test, test_key="test_0001", index=1, total=1,
        )
        assert run_result.success is True, run_result.error_message
        assert run_result.translation_wall is not None
        assert run_result.sim_wall is not None

        test_result = runner.read_result(test, "test_0001", run_result)
        assert test_result.success is True
        var_names = [v.name for v in test_result.variables]
        assert "inertia1.phi" in var_names

    def test_variable_filter_shrinks_mat(self, tmp_path):
        """variableFilter keeps the MAT small (one var request ⇒ few names)."""
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        from modelica_testing.simulators.common.mat_reader import (
            list_result_mat_variables,
        )

        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=["Modelica"],
            timeout=120,
        )
        runner = OpenModelicaRunner(cfg)
        test = TestModel(
            model_id="Modelica.Blocks.Examples.PID_Controller",
            source_file=Path(""),
            source_package="Modelica.Blocks.Examples",
            short_name="PID_Controller",
            n_vars=0,
            variable_patterns=["inertia1.phi"],
            stop_time=0.5,
            number_of_intervals=20,
            source="spec",
        )
        rr = runner.run_single_test(test, test_key="t", index=1, total=1)
        assert rr.success is True, rr.error_message

        mat = cfg.work_dir / "t" / OpenModelicaRunner.RESULT_MAT_FILENAME
        names = list_result_mat_variables(mat)
        assert names is not None
        # With variableFilter == "time|inertia.phi|CPUtime|EventCounter" (plus
        # aliases), the .mat must contain << unfiltered count (>> 500).
        # Generous ceiling — variableFilter can still emit OM-internal
        # shadow variables that share names with the matched ones.
        assert len(names) < 200, f"variableFilter under-effective: {len(names)} vars"

    def test_missing_model_surfaces_clear_error(self, tmp_path):
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=[],
            timeout=30,
        )
        runner = OpenModelicaRunner(cfg)
        test = TestModel(
            model_id="Definitely.Not.Here",
            source_file=Path(""),
            source_package="Definitely.Not",
            short_name="Here",
            n_vars=0,
            variable_patterns=[],
            stop_time=1.0,
            source="spec",
        )
        rr = runner.run_single_test(test, test_key="t", index=1, total=1)
        assert rr.success is False
        assert rr.error_message
        assert "Here" in rr.error_message or "does not exist" in rr.error_message \
            or "not found" in rr.error_message.lower()

    def test_reading_captured_mat_fixture(self):
        """Regression test for the common.mat_reader on an OM-written MAT."""
        from modelica_testing.simulators.common.mat_reader import (
            read_result_mat,
            list_result_mat_variables,
        )
        fixture = FIXTURES / "pid_controller_res.mat"
        names = list_result_mat_variables(fixture)
        assert names is not None
        assert "time" in names
        assert any("inertia1.phi" == n or "inertia1.phi" in n for n in names)
        data = read_result_mat(fixture)
        assert data is not None
        assert "time" in data
```

Also ensure the top of the file has this import and `FIXTURES` constant (add if not present):

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "results_openmodelica"
```

- [ ] **Step 3: Run integration tests**

First, confirm omc is on PATH and MSL is installed:

```bash
which omc && omc -e 'loadModel(Modelica); getErrorString();'
```

Expected: `/usr/bin/omc` (or similar) and `getErrorString()` returns `""` (empty string).

Then run the tests:

```bash
uv run pytest tests/test_openmodelica_runner.py -v
```

Expected: **all** unit + integration tests pass. The integration tests take a few seconds each (omc compilation).

- [ ] **Step 4: Commit**

```bash
git add tests/test_openmodelica_runner.py \
        tests/fixtures/results_openmodelica/pid_controller_res.mat
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
test(openmodelica): real-omc integration tests + MAT fixture

End-to-end run_single_test + read_result against Modelica.Blocks.
Examples.PID_Controller via real omc. Gated on shutil.which('omc')
so CI without omc still passes. Captured MAT fixture also unlocks
common.mat_reader regression check against OM output.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `testing.linux.json` + ModelicaTestingLib sweep (USER REVIEW CHECKPOINT)

**Goal:** Produce fresh `OpenModelica/linux/` baselines for `ModelicaTestingLib` and visually verify the HTML report before committing baselines.

**Files:**
- Create: `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json`
- Create (by `--accept`): `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/ref_NNNN.json`

**WARNING:** This task includes a USER REVIEW gate before committing baselines. Do NOT commit the generated baselines without the user's explicit approval after they inspect the HTML report.

- [ ] **Step 1: Write `testing.linux.json`**

Create `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json` with exactly this content:

```json
{
  "source_path": "../../",
  "simulator": "OpenModelica",
  "simulators": {
    "OpenModelica": ["/usr/bin/omc", "omc"]
  },
  "dependencies": ["Modelica"],
  "test_spec": "test_spec.json",
  "recognizers": [
    {
      "name": "demo:icons-example-as-simulate-only",
      "applies_to": ["modelica"],
      "match": {
        "type": "extends",
        "class_pattern": "*Icons.Example"
      },
      "fields": {
        "simulate_only": {"from": "constant", "value": true},
        "stop_time": {"from": "experiment-annotation", "name": "StopTime"},
        "tolerance": {"from": "experiment-annotation", "name": "Tolerance"}
      }
    }
  ]
}
```

- [ ] **Step 2: Verify MSL is installed**

```bash
omc -e 'loadModel(Modelica); getErrorString();'
```

Expected: empty `""`. If it errors with "Failed to load package Modelica", install MSL:

```bash
omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'
```

- [ ] **Step 3: Dry run (discovery only)**

```bash
uv run modelica-testing --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json discover
```

Expected: a list of discovered `ModelicaTestingLib.Examples.*` tests. Record the count for Step 4's sanity check.

- [ ] **Step 4: Run with `--accept` and `--report`**

```bash
uv run modelica-testing \
    --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json \
    run --accept --report ./reports/openmodelica_mvp
```

Expected: each discovered test compiles + simulates, fresh baselines are written under `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/ref_NNNN.json`, and an HTML report is written under `./reports/openmodelica_mvp/`.

**If any test fails here**, the failure is almost certainly OM-vs-Dymola syntax divergence in that `.mo` file (spec risk #1). Inspect the per-test `omc_stdout.txt` under `<work_dir>/test_NNNN/`. Options:

- Patch the offending `.mo` file to use portable Modelica (preferred if the divergence is small).
- Mark the test `simulate_only=true` via recognizer if the value comparison is the problem but simulation succeeds.
- Skip the test entirely via a new recognizer that only applies on Linux+OpenModelica (exclude this test from OM discovery).

If fixes are needed, apply them, re-run the sweep, and include those fixes in this task's commit.

- [ ] **Step 5: Open the HTML report and eyeball it**

```bash
echo "Open in browser: $(realpath ./reports/openmodelica_mvp/index.html)"
```

USER REVIEW GATE: the human operator must verify, at minimum:

1. All discovered tests have rows on the index.
2. The per-test `Timing` section shows non-zero `translation_wall` + `sim_wall` values (if zero, the log parser is broken — regress to Task 3).
3. At least one per-test plot renders without script errors (open browser devtools, refresh; zero JS errors expected).
4. Variable counts in each test's variable table are sane (not 0, not tens of thousands).

Report a summary of what was observed to the user, then wait for approval before proceeding to Step 6. Do NOT commit baselines until approved.

- [ ] **Step 6: Commit baselines (ONLY after user approval)**

```bash
git add examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json \
        examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/
# If any library .mo files were patched for portability, add them too:
#   git add examples/modelica/ModelicaTestingLib/Examples/<file>.mo
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
feat(examples): ModelicaTestingLib baselines for OpenModelica/linux

End-to-end sweep against openmodelica backend on Linux. Fresh
baselines written via --accept; human-verified the HTML report
(timing populated, plots render, variable counts sane).

testing.linux.json is a peer to the existing Dymola-on-Windows
testing.json — the two baseline partitions coexist in
Resources/ReferenceResults/<backend>/<os>/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Docs — CLAUDE.md phase block + D69 decision entry

**Goal:** Update project documentation so the shipped state is discoverable. Add the decision record for the OpenModelica backend.

**Files:**
- Modify: `CLAUDE.md` — insert a new phase-block paragraph
- Modify: `docs/decisions.md` — append D69 entry

- [ ] **Step 1: Update CLAUDE.md**

In `CLAUDE.md`, find the current phase-block list (the chronologically-ordered summary paragraphs starting with **"Phase 1 status"**, **"Phase 2 status"**, etc.). Insert a new paragraph **after** the last one (current last is likely the reporter-polish bundled phase or paper close-out — append after the most recent):

```markdown
**OpenModelica backend (complete)**: D69. Third `SimulatorRunner` alongside Dymola + FMPy. MVP path uses `omc` as a subprocess driven by generated `.mos` scripts (analogous to Dymola's batch fallback; persistent-worker + FMU export deferred). `mat_reader` hoisted from `simulators/dymola/` to `simulators/common/` and renamed to `read_result_mat` — OM's `.mat` is deliberately DSresult-compatible, so zero new reader code was needed. `OpenModelicaRunner` declares `Capability.BATCH_FALLBACK` only; artifact list is fully static (`result_res.mat`, `result.log`, `result_info.json`, `simulate.mos`, `omc_stdout.txt`) via `fileNamePrefix="result"` on `simulate(...)`. Per-phase timings (`timeFrontend / Backend / SimCode / Templates / Compile / Simulation / Total`) captured from the `SimulationResult` record printed to stdout inside sentinel-bounded `<<<MT_PHASE_TIMINGS>>>` blocks — no regex on unbounded output. `variableFilter` regex escapes every tracked / diagnostic name plus `time`, keeping the `.mat` small. `Config.dependencies` entries are classified: bare names ⇒ `loadModel(Name)` (uses OM's installed-package store), path-like ⇒ `loadFile(path)`. MSL is pre-installed once per machine via `omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'` — auto-install is explicitly out of MVP scope. Validation: end-to-end sweep against `examples/modelica/ModelicaTestingLib/` on Linux produced fresh baselines under `ReferenceResults/OpenModelica/linux/`; HTML report renders with timing + plots. Tests: `test_openmodelica_mos.py` (mos generation), `test_openmodelica_log_parser.py` (stdout parsing), `test_openmodelica_runner.py` (unit + real-omc integration; gated on `shutil.which("omc")`). Test count: 637 → ~655. Decision D69.
```

Replace `~655` with the actual final count from the suite run in Task 6.

- [ ] **Step 2: Update `docs/decisions.md`**

Append to `docs/decisions.md`:

```markdown
## D69 — OpenModelica batch backend (2026-04-22)

**Decision:** Add `OpenModelicaRunner` as a third `SimulatorRunner`, using `omc` as a subprocess with generated `.mos` scripts. Analogous to Dymola's batch fallback, not persistent workers.

**Rationale:**
- Fastest path to exercise the multi-backend abstraction on Modelica source (FMPy is FMU-sourced; Dymola was the only Modelica-source backend).
- Zero new pip deps — `omc` is a standalone binary on the user's Linux machine.
- OpenModelica's default `.mat` format is DSresult-compatible; reusing the MAT reader was free (hoisted from `simulators/dymola/` to `simulators/common/` + symbol renames).
- Matches the Dymola backend's own historical progression (batch came first, persistent later).

**Scope:**
- `Capability.BATCH_FALLBACK` only.
- Static artifact names via `fileNamePrefix="result"` on `simulate(...)`.
- `Config.dependencies` handles bare names (`loadModel`) + paths (`loadFile`).
- Phase timings from sentinel-bounded `SimulationResult` print block in omc stdout.
- Validated end-to-end against `examples/modelica/ModelicaTestingLib/` on Linux.

**Rejected / deferred:**
- `PERSISTENT_WORKERS` via OMPython / `OMCSessionZMQ`. Follow-up.
- `FMU_EXPORT` via `buildModelFMU`. Follow-up (cross-backend chain is already experimental even on Dymola side).
- `check-openmodelica` CLI subcommand. Nice-to-have.
- Auto-install of MSL. One-time manual step documented in runner module docstring.
- Windows-side OpenModelica testing.

**Files:**
- `src/modelica_testing/simulators/common/mat_reader.py` (hoisted)
- `src/modelica_testing/simulators/openmodelica/{__init__,runner,mos_generator,log_parser}.py`
- `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.linux.json`
- `examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/OpenModelica/linux/`
- Tests: `tests/test_openmodelica_mos.py`, `tests/test_openmodelica_log_parser.py`, `tests/test_openmodelica_runner.py`, `tests/fixtures/results_openmodelica/`

**Spec:** `docs/superpowers/specs/2026-04-22-openmodelica-backend-design.md`.
```

- [ ] **Step 3: Run full suite one last time**

```bash
uv run pytest -q --deselect tests/test_interactive_playwright.py
```

Expected: all tests pass. Record the final count and, if the CLAUDE.md entry was written with a placeholder, return to Step 1 and replace `~655` with the actual count.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/decisions.md
git -c user.name="Scott Greenwood" -c user.email="greenwoodms@ornl.gov" commit -m "$(cat <<'EOF'
docs: record OpenModelica backend (D69)

CLAUDE.md phase block describes the shipped state.
docs/decisions.md gains D69 with full scope + rationale +
deferred items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Review checkpoints summary

| After task | Auto-check | Human review |
|---|---|---|
| 1 — mat_reader hoist | Full suite still at baseline 637 | — |
| 2 — mos_generator | New unit tests pass | — |
| 3 — log_parser | New unit tests pass | — |
| 4 — runner + registry | Unit tests pass; full suite unchanged | — |
| 5 — integration + fixture | Real-omc tests pass locally | — |
| 6 — ModelicaTestingLib sweep | `--accept --report` succeeds end-to-end | **YES** — HTML report visual check BEFORE baseline commit |
| 7 — docs + D69 | Full suite green at new count | — |

---

## Dependencies & assumptions

- **`omc` on PATH.** Verified at `/usr/bin/omc` (version 1.26.3) on this machine. Integration tests in Task 5 and the sweep in Task 6 require it.
- **MSL installed.** One-time bootstrap (`installPackage(Modelica)`) already run on this machine during the design smoke test. Runner errors with a clear hint if it's missing.
- **`ModelicaTestingLib` is portable to OpenModelica.** Risk noted in spec §Risks item 1 — any Dymola-specific syntax found during the Task 6 sweep gets patched or marked `simulate_only` in the same commit.
- **Linux/WSL test environment.** No Windows-side OpenModelica work in this plan.
- **No pip dependency additions.** Pure stdlib (`subprocess`, `shutil`, `re`, `pathlib`, `dataclasses`). Existing `numpy` used via the shared MAT reader.
