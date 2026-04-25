# Python-Driven Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth `SimulatorRunner` backend (`PythonRunner`) that executes arbitrary Python scripts as test sources, plus a `PythonTestingLib` fixture with both a scipy-based simulation example and a CSV-loader example. The CSV loader proves the backend abstraction is truly language/simulator-agnostic, not secretly Modelica-shaped.

**Architecture:** Python subprocess-per-test runner mirroring the Julia D77 pattern: framework-shipped `run_test.py` driver loads the user's `.py` file via `importlib.util`, calls `simulate(stop_time, tolerance) -> dict`, and writes a JSON result file that the runner reads back into a `TestResult`. Registered as `"Python"` via the existing `@register` decorator. Batch-only for the MVP (no persistent worker — defer until the batch path is proven, mirroring the D77→D78 progression). The user-facing contract is deliberately minimal: any function that returns `{"time": [...], "variables": {...}}` is a valid test source, whether it runs scipy, loads a CSV, or fetches from a server.

**Tech Stack:** Python ≥ 3.10, `subprocess`, `importlib.util`, `numpy`, existing project deps. Example library adds `scipy` (for the scipy example). No new framework deps.

**Scope context:** This plan is the concrete delivery of the B-tier #45 move from `docs/SESSION_HANDOFF.md`. It validates that the backend abstraction (`TestModel`, `TestResult`, `SimulatorRunner`, `Recognizer`, `spec_parser`) is not secretly Modelica-shaped. Out of scope (per D66 economy-of-tools): time-offset alignment, amplitude scaling, clock-drift resampling, dynamic data fetching, parameter estimation — all noted as follow-ups in `docs/ideas.md`.

---

## File structure

### New files
- `src/modelica_testing/simulators/python/__init__.py` — namespace marker + runner re-export
- `src/modelica_testing/simulators/python/runner.py` — `PythonRunner` + `PythonConfig`
- `src/modelica_testing/simulators/python/run_test.py` — child-side driver (loads user `.py`, calls `simulate()`, writes JSON)
- `examples/python/PythonTestingLib/Examples/SimpleRamp.py` — scipy-based linear ramp (counterpart to Modelica/Julia SimpleTest)
- `examples/python/PythonTestingLib/Examples/ConstantCsv.py` — CSV-loader test (proof: no ODE solver involved)
- `examples/python/PythonTestingLib/Examples/data/constant_experiment.csv` — pre-recorded trajectory for `ConstantCsv`
- `examples/python/PythonTestingLib/pyproject.toml` — declares scipy dep for users installing the example env
- `examples/python/PythonTestingLib/README.md` — one-paragraph user-facing note (install scipy, run via CLI)
- `examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json` — config (source_type: "python")
- `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json` — two test entries
- `tests/test_python_runner.py` — integration tests (gated on scipy availability)

### Modified files
- `src/modelica_testing/simulators/__init__.py` — add `"Python": ".python"` to `_import_builtin_backend`
- `src/modelica_testing/config.py` — add `"Python": "python"` to `BACKEND_BINARY_NAMES`; confirm `source_type="python"` works via the existing non-Modelica branch
- `src/modelica_testing/discovery/spec_parser.py` — generalize `julia_rel = entry.get("source")` to `source_rel` (variable rename only; the `source` JSON field already works for `.py` just as it does for `.jl`); keep `fmu` as-is (FMPy-specific alias)
- `docs/decisions.md` — add D80 entry
- `docs/ideas.md` — add follow-up notes for alignment-preprocessing and dynamic-data-fetching
- `docs/SESSION_HANDOFF.md` — update backend count (4 → 5), library count (2 → 3), add Python row to backend table
- `CLAUDE.md` — update Project Overview paragraph (four backends → five)

### Deleted files
None.

---

## Task 1: Scaffold Python backend module + register with backend loader

**Goal:** Minimal Python backend module that registers itself when imported. Import plumbing only — no runner logic yet. This proves the registration path works before building anything substantial on top.

**Files:**
- Create: `src/modelica_testing/simulators/python/__init__.py`
- Create: `src/modelica_testing/simulators/python/runner.py` (stub)
- Modify: `src/modelica_testing/simulators/__init__.py` (add entry to `_import_builtin_backend`)
- Test: `tests/test_python_runner.py` (just the registration test for now)

- [ ] **Step 1: Create the package `__init__.py`**

Create `src/modelica_testing/simulators/python/__init__.py` with exactly:

```python
"""Python subprocess runner (D80).

Mirrors the Julia D77 pattern: framework-shipped driver script loads the
user's ``.py`` file, calls ``simulate(stop_time, tolerance)``, and writes
a JSON result. Primary motivation is validating that the backend
abstraction is truly language/simulator-agnostic — see the ConstantCsv
example under ``examples/python/PythonTestingLib/`` which uses zero ODE
machinery.
"""

from . import runner  # noqa: F401  (import triggers @register side effect)
```

- [ ] **Step 2: Create the stub `runner.py`**

Create `src/modelica_testing/simulators/python/runner.py` with exactly:

```python
"""Stub — full implementation lands in Task 4."""
from __future__ import annotations

from .. import register
from ..base import Capability, DatasetType, SimulatorRunner


@register("Python")
class PythonRunner(SimulatorRunner):
    """Placeholder; run_single_test / read_result arrive in Task 4."""

    capabilities = frozenset({Capability.BATCH_FALLBACK})
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = ()

    def run_single_test(self, test, test_key, index, total):
        raise NotImplementedError("PythonRunner under construction — see Task 4")

    def read_result(self, test, test_key, run_result):
        raise NotImplementedError("PythonRunner under construction — see Task 4")
```

- [ ] **Step 3: Register the backend in the import map**

Open `src/modelica_testing/simulators/__init__.py` and locate the `_import_builtin_backend` function (currently ends around line 86). Update the `builtins` dict:

Replace:
```python
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
        "OpenModelica": ".openmodelica",
        "Julia": ".julia",
    }
```

With:
```python
    builtins = {
        "Dymola": ".dymola",
        "FMPy": ".fmpy",
        "OpenModelica": ".openmodelica",
        "Julia": ".julia",
        "Python": ".python",
    }
```

Also update the module docstring at line 1. Replace:
```python
"""Simulator backends. Concrete today: Dymola, FMPy, OpenModelica, Julia. Pluggable via ``@register``."""
```

With:
```python
"""Simulator backends. Concrete today: Dymola, FMPy, OpenModelica, Julia, Python. Pluggable via ``@register``."""
```

- [ ] **Step 4: Write the registration test**

Create `tests/test_python_runner.py` with:

```python
"""Integration tests for the Python subprocess backend (D80).

These tests gate on scipy availability (needed by the SimpleRamp example).
The registration test doesn't need scipy and always runs.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest


_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1]
    / "examples" / "python" / "PythonTestingLib"
)
_CONFIG = _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "testing.json"


def _scipy_available() -> bool:
    return importlib.util.find_spec("scipy") is not None


def test_python_runner_registered():
    """The Python runner registers when its submodule is imported."""
    from modelica_testing.simulators import get_runner_class
    from modelica_testing.config import Config
    cfg = Config(config_file=_CONFIG) if _CONFIG.exists() else None
    if cfg is None:
        # Example config not written yet (earlier task ordering); fabricate.
        # Force-import to trigger registration, then check the registry.
        import modelica_testing.simulators.python  # noqa: F401
        from modelica_testing.simulators import _REGISTRY
        assert "Python" in _REGISTRY
        assert _REGISTRY["Python"].__name__ == "PythonRunner"
        return
    cls = get_runner_class(cfg)
    assert cls.__name__ == "PythonRunner"
```

- [ ] **Step 5: Run the registration test**

Run: `uv run pytest tests/test_python_runner.py::test_python_runner_registered -v`

Expected: PASS.

- [ ] **Step 6: Run full suite to confirm no regression**

Run: `uv run pytest -q`

Expected: 753 passed (752 previous + 1 new) + 1 skipped, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add src/modelica_testing/simulators/python/ \
        src/modelica_testing/simulators/__init__.py \
        tests/test_python_runner.py
git commit -m "$(cat <<'EOF'
feat(python): scaffold Python backend + registration (D80 stage 1)

Stub PythonRunner that registers via @register("Python") but raises
NotImplementedError on run_single_test/read_result. Proves the import +
registration path works before full implementation in Task 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `source_type="python"` support to config

**Goal:** `BACKEND_BINARY_NAMES["Python"] = "python"` so auto-detection finds the Python interpreter. Verify the existing non-Modelica branch in `Config.__post_init__` already handles `source_type="python"` correctly (it should — Julia uses the same branch).

**Files:**
- Modify: `src/modelica_testing/config.py` (one-line addition to `BACKEND_BINARY_NAMES`)
- Test: `tests/test_python_runner.py` (add config-loading test)

- [ ] **Step 1: Inspect the current `BACKEND_BINARY_NAMES` map**

Run: `grep -n "BACKEND_BINARY_NAMES" src/modelica_testing/config.py`

Note the location (currently defined around line 39). The map should contain entries for Dymola, FMPy, OpenModelica, Julia.

- [ ] **Step 2: Add the Python entry**

Open `src/modelica_testing/config.py`. Find the `BACKEND_BINARY_NAMES` dict. Add the `"Python"` entry after the existing `"Julia"` entry. The existing dict looks roughly like:

```python
BACKEND_BINARY_NAMES = {
    "Dymola": "dymola",
    "FMPy": "",
    "OpenModelica": "omc",
    "Julia": "julia",
}
```

Edit so it reads:

```python
BACKEND_BINARY_NAMES = {
    "Dymola": "dymola",
    "FMPy": "",
    "OpenModelica": "omc",
    "Julia": "julia",
    "Python": "python",
}
```

(If your system has only `python3` on PATH, the `PythonConfig.from_config` fallback in Task 4 will check for `python3` too. The map entry only affects auto-detection hints.)

- [ ] **Step 3: Write the config-loading test**

Append to `tests/test_python_runner.py`:

```python
def test_python_config_loads_without_package_mo(tmp_path):
    """source_type='python' must not trigger Modelica package.mo lookup."""
    lib = tmp_path / "MyPyLib"
    (lib / "Examples").mkdir(parents=True)
    (lib / "Examples" / "Foo.py").write_text(
        "def simulate(stop_time, tolerance):\n"
        "    return {'time': [0.0, 1.0], 'variables': {'x': [0.0, 1.0]}}\n"
    )
    ref_root = lib / "Resources" / "ReferenceResults"
    ref_root.mkdir(parents=True)
    cfg_path = ref_root / "testing.json"
    cfg_path.write_text(
        '{"source_type": "python", "source_path": "../..", '
        '"library_name": "MyPyLib", "simulators": {"Python": ["python"]}, '
        '"simulator": "Python"}'
    )
    from modelica_testing.config import Config
    cfg = Config(config_file=cfg_path)
    assert cfg.source_type == "python"
    assert cfg.source_path.name == "MyPyLib"
    assert cfg.simulator == "Python"
    assert cfg.simulator_backend == "Python"
```

- [ ] **Step 4: Run the config test**

Run: `uv run pytest tests/test_python_runner.py::test_python_config_loads_without_package_mo -v`

Expected: PASS. If it fails with a `package.mo not found` error, the non-Modelica branch in `Config.__post_init__` is mis-guarded and must be fixed before proceeding — investigate `config.py` lines 328–373 (the `if source_type_hint == "modelica": / else:` split).

- [ ] **Step 5: Commit**

```bash
git add src/modelica_testing/config.py tests/test_python_runner.py
git commit -m "$(cat <<'EOF'
feat(python): wire source_type='python' into config auto-detect

Add BACKEND_BINARY_NAMES['Python'] = 'python'. The non-Modelica branch
in Config.__post_init__ already handles the rest (source_path
resolution, library_name inference, skip package.mo lookup) — verified
with a new loading test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Build the child-side `run_test.py` driver

**Goal:** Framework-shipped script that the subprocess invokes. Parses CLI args, loads the user's `.py` file via `importlib.util`, calls `simulate(stop_time, tolerance)`, writes a JSON result. Catches all exceptions so the framework always sees a structured failure rather than a process crash.

**Files:**
- Create: `src/modelica_testing/simulators/python/run_test.py`
- Test: `tests/test_python_runner.py` (driver-in-isolation tests)

- [ ] **Step 1: Write the failing driver-isolation tests first**

Append to `tests/test_python_runner.py`:

```python
_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "src" / "modelica_testing" / "simulators" / "python" / "run_test.py"
)


def _run_driver(user_file: Path, result_path: Path, stop_time=1.0, tolerance=1e-6):
    """Invoke run_test.py as a subprocess and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [shutil.which("python") or "python3", str(_DRIVER),
         str(user_file), str(stop_time), str(tolerance), str(result_path)],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_driver_success_path(tmp_path):
    user = tmp_path / "ramp.py"
    user.write_text(
        "def simulate(stop_time, tolerance):\n"
        "    n = 11\n"
        "    return {\n"
        "        'time': [i * stop_time / (n - 1) for i in range(n)],\n"
        "        'variables': {'x': [i * 2.0 * stop_time / (n - 1) for i in range(n)]},\n"
        "    }\n"
    )
    result = tmp_path / "result.json"
    rc, out, err = _run_driver(user, result)
    assert rc == 0, err
    assert result.exists()
    import json
    payload = json.loads(result.read_text())
    assert payload["success"] is True
    assert len(payload["time"]) == 11
    assert payload["variables"]["x"][-1] == pytest.approx(2.0)


def test_driver_missing_simulate_function(tmp_path):
    user = tmp_path / "bad.py"
    user.write_text("# Empty file — no simulate() defined.\n")
    result = tmp_path / "result.json"
    rc, out, err = _run_driver(user, result)
    assert rc != 0
    assert result.exists()  # structured failure, not a crash
    import json
    payload = json.loads(result.read_text())
    assert payload["success"] is False
    assert "simulate" in payload["error"].lower()


def test_driver_simulate_raises(tmp_path):
    user = tmp_path / "raises.py"
    user.write_text(
        "def simulate(stop_time, tolerance):\n"
        "    raise ValueError('boom')\n"
    )
    result = tmp_path / "result.json"
    rc, out, err = _run_driver(user, result)
    assert rc != 0
    import json
    payload = json.loads(result.read_text())
    assert payload["success"] is False
    assert "boom" in payload["error"]


def test_driver_malformed_return(tmp_path):
    user = tmp_path / "bad_return.py"
    user.write_text(
        "def simulate(stop_time, tolerance):\n"
        "    return 'not a dict'\n"
    )
    result = tmp_path / "result.json"
    rc, out, err = _run_driver(user, result)
    assert rc != 0
    import json
    payload = json.loads(result.read_text())
    assert payload["success"] is False
```

- [ ] **Step 2: Run these tests; they should fail because the driver doesn't exist yet**

Run: `uv run pytest tests/test_python_runner.py::test_driver_success_path -v`

Expected: FAIL (FileNotFoundError or similar — `run_test.py` doesn't exist).

- [ ] **Step 3: Implement the driver**

Create `src/modelica_testing/simulators/python/run_test.py` with exactly this content:

```python
"""Framework-shipped Python driver for a single test (D80).

Invoked by PythonRunner as:
    python run_test.py <user_file> <stop_time> <tolerance> <result_path>

The user file must define:
    simulate(stop_time: float, tolerance: float) -> dict

returning a dict with:
    "time":      list[float] (monotonically non-decreasing)
    "variables": dict[str, list[float]]  (each same length as "time")

Result file is JSON:
    {"success": true,  "time": [...], "variables": {...}}
or on failure:
    {"success": false, "error": "...", "time": [], "variables": {}}

Exceptions are caught so the framework always gets a structured result
rather than a process-level crash.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def _load_user_module(user_file: Path):
    # Allow sibling imports from the user's file directory.
    parent = str(user_file.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location("_user_test", user_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {user_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_failure(result_path: Path, message: str) -> None:
    result_path.write_text(
        json.dumps({
            "success": False,
            "error": message,
            "time": [],
            "variables": {},
        }),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "Usage: run_test.py <user_file> <stop_time> <tolerance> <result_path>",
            file=sys.stderr,
        )
        return 2

    user_file = Path(argv[0])
    try:
        stop_time = float(argv[1])
        tolerance = float(argv[2])
    except ValueError as exc:
        print(f"Invalid numeric arg: {exc}", file=sys.stderr)
        return 2
    result_path = Path(argv[3])

    try:
        module = _load_user_module(user_file)
        if not hasattr(module, "simulate"):
            raise AttributeError(
                f"{user_file.name} must define simulate(stop_time, tolerance)"
            )
        payload = module.simulate(stop_time, tolerance)
        if (
            not isinstance(payload, dict)
            or "time" not in payload
            or "variables" not in payload
        ):
            raise ValueError(
                "simulate() must return a dict with 'time' and 'variables' keys"
            )
        if not isinstance(payload["variables"], dict):
            raise ValueError("'variables' must be a dict of name -> list[float]")

        time_list = list(payload["time"])
        variables = {
            str(k): list(v) for k, v in payload["variables"].items()
        }
        for name, values in variables.items():
            if len(values) != len(time_list):
                raise ValueError(
                    f"variable '{name}' has {len(values)} samples; "
                    f"expected {len(time_list)} to match 'time'"
                )

        result_path.write_text(
            json.dumps({
                "success": True,
                "time": time_list,
                "variables": variables,
            }),
            encoding="utf-8",
        )
        print(
            f"OK: {len(variables)} variables, {len(time_list)} time points"
        )
        return 0
    except BaseException:
        tb = traceback.format_exc()
        print(f"FAIL:\n{tb}", file=sys.stderr)
        try:
            _write_failure(result_path, tb)
        except Exception:
            return 2
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Rerun the four driver tests; all should pass**

Run: `uv run pytest tests/test_python_runner.py -k "test_driver_" -v`

Expected: 4 PASS (`test_driver_success_path`, `test_driver_missing_simulate_function`, `test_driver_simulate_raises`, `test_driver_malformed_return`).

- [ ] **Step 5: Commit**

```bash
git add src/modelica_testing/simulators/python/run_test.py tests/test_python_runner.py
git commit -m "$(cat <<'EOF'
feat(python): child-side driver loads user .py and emits JSON result

run_test.py is the framework-shipped subprocess entry point. Uses
importlib.util to load the user's file, calls simulate(stop_time,
tolerance), validates the returned dict shape, and writes a JSON
result. Catches all exceptions → structured {success: false, error}
so the parent PythonRunner always has something to parse.

Four driver-in-isolation tests cover: success, missing simulate(),
simulate() raises, malformed return type.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `PythonRunner` (replace the Task 1 stub)

**Goal:** Full `PythonRunner.run_single_test` + `read_result` matching the Julia batch runner's structure. Binary resolution via `PythonConfig.from_config` (mirrors `JuliaConfig`). Subprocess per test, timeout honored, stdout/stderr captured to artifacts, structured failure JSON surfaced.

**Files:**
- Modify: `src/modelica_testing/simulators/python/runner.py` (replace stub with full implementation)

- [ ] **Step 1: Replace the stub with the full runner**

Overwrite `src/modelica_testing/simulators/python/runner.py` with exactly this content:

```python
"""Python / subprocess runner (D80).

Invokes a shipped driver script (``run_test.py``) via the configured
Python interpreter. The driver loads the user's ``.py`` file — which
must define ``simulate(stop_time, tolerance) -> dict`` — executes it,
and writes a JSON result. This runner reads it back into a ``TestResult``.

Batch-only for the MVP; each test spawns one Python subprocess. Startup
cost is roughly 30-100 ms per test (importlib + any user-file imports);
for trivial tests this dominates over the actual ``simulate()`` call.
A persistent-worker path (long-lived Python subprocess with stdin-JSON
dispatch) is a future enhancement if per-test overhead becomes an issue
— mirrors the D77 → D78 Julia progression.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)


# Shipped driver script.
_DRIVER_PATH = Path(__file__).resolve().parent / "run_test.py"


@dataclass(frozen=True)
class PythonConfig:
    """Python-specific settings extracted from the unified :class:`Config`.

    ``python_binary`` is the absolute path to the Python interpreter that
    will run user test scripts. It must have whatever packages the user's
    scripts import (scipy, pandas, ...). The framework does not manage
    the user's environment — pick a ``python`` that has their deps.
    """
    python_binary: Path

    @classmethod
    def from_config(cls, config: Config) -> "PythonConfig":
        resolved: Optional[Path] = None
        if config.simulator_path:
            p = Path(config.simulator_path).expanduser()
            if p.exists():
                resolved = p
        if resolved is None:
            for name in ("python", "python3"):
                on_path = shutil.which(name)
                if on_path:
                    resolved = Path(on_path)
                    break
        if resolved is None:
            raise RuntimeError(
                "Python binary not found. Ensure 'python' or 'python3' is on "
                "PATH, or set an explicit path under testing.json's "
                "'simulators' map, e.g. {\"Python\": [\"/path/to/venv/bin/python\"]}."
            )
        return cls(python_binary=resolved)


@register("Python")
class PythonRunner(SimulatorRunner):
    """Subprocess-per-test Python runner."""

    capabilities = frozenset({
        Capability.BATCH_FALLBACK,
        # Deliberately absent:
        #   PERSISTENT_WORKERS — stdin-driven long-lived Python process deferred.
        #   FMU_EXPORT — not meaningful for arbitrary Python scripts.
        #   EXPERIMENT_INGEST — a PythonRunner can both simulate and ingest;
        #     the flag is backend-level so we don't declare either role.
    })
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("result.json", "Simulation result (time + variables)"),
        ("python_stdout.txt", "Python stdout"),
        ("python_stderr.txt", "Python stderr"),
    )

    RESULT_FILENAME = "result.json"

    def __init__(self, config: Config):
        super().__init__(config)
        self.python_config = PythonConfig.from_config(config)

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
        user_file = _resolve_python_source(test, self.config)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"Python source not found for {test.model_id}. Ensure the "
                    f"test_spec.json entry has a 'source' field resolving to "
                    f"an existing .py file under source_path."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)
        result_path = test_dir / self.RESULT_FILENAME
        stdout_path = test_dir / "python_stdout.txt"
        stderr_path = test_dir / "python_stderr.txt"

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "simulating")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout
        )

        cmd = [
            str(self.python_config.python_binary),
            str(_DRIVER_PATH),
            str(user_file),
            str(test.stop_time),
            str(test.tolerance),
            str(result_path),
        ]
        logger.debug("Python cmd: %s", " ".join(cmd))

        wall_start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=test_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - wall_start
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8")
            msg = f"Python execution exceeded {timeout}s timeout"
            if self.progress:
                self.progress.on_finish(
                    test_key, success=False, elapsed=elapsed,
                    detail=msg, timed_out=True,
                )
            return TestRunResult(
                model_id=test.model_id, test_key=test_key, success=False,
                elapsed=elapsed, error_message=msg, sim_wall=elapsed,
                timed_out=True,
            )

        elapsed = time.monotonic() - wall_start
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        if proc.returncode != 0:
            err = _read_failure_error(result_path) or (
                (proc.stderr or "").strip().splitlines()[-1]
                if proc.stderr else f"python returned {proc.returncode}"
            )
            if self.progress:
                self.progress.on_finish(
                    test_key, success=False, elapsed=elapsed, detail=err,
                )
            return TestRunResult(
                model_id=test.model_id, test_key=test_key, success=False,
                elapsed=elapsed, error_message=f"Python execution failed: {err}",
                sim_wall=elapsed,
            )

        if self.progress:
            self.progress.on_finish(test_key, success=True, elapsed=elapsed)

        return TestRunResult(
            model_id=test.model_id, test_key=test_key, success=True,
            elapsed=elapsed, sim_wall=elapsed,
            statistics={"simulation": {"wall_time": elapsed}},
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
    ) -> TestResult:
        result_path = self.config.work_dir / test_key / self.RESULT_FILENAME
        if not result_path.exists():
            return TestResult(
                model_id=test.model_id, success=False,
                error_message=(
                    f"No Python result at {result_path} (did execution run?)"
                ),
                statistics=run_result.statistics if run_result else None,
            )

        try:
            with result_path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return TestResult(
                model_id=test.model_id, success=False,
                error_message=f"Failed to parse {result_path.name}: {exc}",
                statistics=run_result.statistics if run_result else None,
            )

        if not payload.get("success", False):
            return TestResult(
                model_id=test.model_id, success=False,
                error_message=payload.get(
                    "error", "Python driver reported failure"
                ),
                statistics=run_result.statistics if run_result else None,
            )

        time_arr = np.asarray(payload.get("time", []), dtype=np.float64)
        available = list(payload.get("variables", {}).keys())
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []

        variables = [
            VariableResult(
                index=i + 1,
                name=name,
                time=time_arr,
                values=np.asarray(
                    payload["variables"][name], dtype=np.float64
                ),
            )
            for i, name in enumerate(requested)
            if name in payload["variables"]
        ]

        return TestResult(
            model_id=test.model_id, success=True, variables=variables,
            diagnostics=[],
            statistics=run_result.statistics if run_result else None,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_python_source(test: TestModel, config: Config) -> Optional[Path]:
    """Resolve the user's ``.py`` file.

    Priority: ``test.source_file`` (spec_parser fills this when the entry
    has a ``"source"`` field) → ``<source_path>/<model_id>.py`` fallback.
    """
    if test.source_file is not None and str(test.source_file):
        p = Path(test.source_file)
        if not p.is_absolute() and config.source_path:
            p = config.source_path / p
        return p
    if config.source_path:
        return config.source_path / f"{test.model_id}.py"
    return None


def _read_failure_error(result_path: Path) -> Optional[str]:
    """If the driver wrote a failure JSON, pull its 'error' message."""
    if not result_path.exists():
        return None
    try:
        with result_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        if not payload.get("success", True):
            return payload.get("error")
    except Exception:
        return None
    return None
```

- [ ] **Step 2: Rerun the registration test**

Run: `uv run pytest tests/test_python_runner.py::test_python_runner_registered -v`

Expected: PASS.

- [ ] **Step 3: Run full suite to confirm no regression**

Run: `uv run pytest -q`

Expected: 758 passed + 1 skipped, 0 failures. (Task 3 added 4 driver tests + Tasks 1/2 added 2 config/registration tests; Task 4 adds no new tests.)

- [ ] **Step 4: Commit**

```bash
git add src/modelica_testing/simulators/python/runner.py
git commit -m "$(cat <<'EOF'
feat(python): implement PythonRunner subprocess-per-test path

Mirrors JuliaRunner structure. PythonConfig resolves the interpreter
(simulator_path → shutil.which python/python3). run_single_test spawns
the driver, captures stdout/stderr, surfaces structured failure JSON.
read_result parses the result JSON and materializes VariableResults
using resolve_variable_patterns.

No persistent variant yet — defer until the batch path is proven
across the example library (same D77→D78 progression as Julia).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Scipy-based example — `SimpleRamp.py`

**Goal:** First example in the new `PythonTestingLib` library. Uses `scipy.integrate.solve_ivp` to integrate `dx/dt = 2.0` — the Python counterpart to `ModelicaTestingLib.Examples.SimpleTest` and `JuliaMtkTestingLib.Examples.SimpleRamp`. Exercises a real ODE solver through the subprocess contract.

**Files:**
- Create: `examples/python/PythonTestingLib/Examples/SimpleRamp.py`
- Create: `examples/python/PythonTestingLib/pyproject.toml`
- Create: `examples/python/PythonTestingLib/README.md`

- [ ] **Step 1: Verify scipy is available (install if not)**

Run: `uv run python -c "import scipy; print(scipy.__version__)"`

If this fails, install scipy into whichever Python `uv run which python` points at. Note the venv-drift caveat in `docs/SESSION_HANDOFF.md` — on this machine `uv run` may resolve miniforge3. If `uv run` can't find scipy, try: `uv pip install scipy` or `/home/fig/miniforge3/bin/pip install scipy`.

- [ ] **Step 2: Create `SimpleRamp.py`**

Create `examples/python/PythonTestingLib/Examples/SimpleRamp.py` with:

```python
"""Linear ramp: x(t) = 2t. scipy ODE counterpart to

* ``ModelicaTestingLib.Examples.SimpleTest`` (Dymola/OpenModelica), and
* ``JuliaMtkTestingLib.Examples.SimpleRamp`` (Julia/MTK).

This is the "real simulation" half of the Python backend showcase — it
exists to prove that any Python ODE solver (scipy, numba-rk4, custom)
can serve as a test source. The companion ``ConstantCsv.py`` is the
"no simulator at all" half that proves the backend abstraction is not
secretly shaped around ODE simulation.
"""
from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp


def simulate(stop_time: float, tolerance: float) -> dict:
    """Integrate dx/dt = 2, x(0) = 0 up to ``stop_time``.

    The framework calls this function once per test run, passing the
    ``stop_time`` and ``tolerance`` values from test_spec.json's
    ``simulation`` block. The returned dict is serialized to JSON by
    the framework's ``run_test.py`` driver.
    """
    sol = solve_ivp(
        fun=lambda t, y: [2.0],
        t_span=(0.0, stop_time),
        y0=[0.0],
        rtol=tolerance,
        atol=tolerance,
        dense_output=False,
        t_eval=np.linspace(0.0, stop_time, 101),
    )
    if not sol.success:
        raise RuntimeError(f"solve_ivp failed: {sol.message}")
    return {
        "time": sol.t.tolist(),
        "variables": {"x": sol.y[0].tolist()},
    }
```

- [ ] **Step 3: Create `pyproject.toml` for the example library**

Create `examples/python/PythonTestingLib/pyproject.toml` with:

```toml
# Optional: users of PythonTestingLib can `uv pip install -e .` here
# to pick up scipy. The framework itself does not require scipy —
# it's only needed by the SimpleRamp example.

[project]
name = "python-testing-lib"
version = "0.0.1"
description = "Example Python tests for the modelica-testing framework"
requires-python = ">=3.10"
dependencies = [
    "numpy",
    "scipy",
]
```

- [ ] **Step 4: Create `README.md` for the example library**

Create `examples/python/PythonTestingLib/README.md` with:

```markdown
# PythonTestingLib

Example Python-source test library for the `modelica-testing` framework.

## Setup

Install the Python dependencies that the examples import (scipy is
used by `SimpleRamp.py`; the `ConstantCsv.py` example has no deps
beyond the standard library):

```bash
cd examples/python/PythonTestingLib
uv pip install -e .
```

## Run

```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

## Test contract

Each `.py` file under `Examples/` must define a top-level function:

```python
def simulate(stop_time: float, tolerance: float) -> dict:
    return {
        "time": [...],               # list[float]
        "variables": {"x": [...]},   # dict[str, list[float]]
    }
```

`stop_time` and `tolerance` come from the matching `simulation` block
in `Resources/ReferenceResults/test_spec.json`. Return whatever
makes sense — an scipy ODE solution, pre-recorded CSV data, results
from a REST call, etc. The framework's only requirement is that
`time` is monotonically non-decreasing and each variable list has
the same length as `time`.
```

- [ ] **Step 5: Verify `SimpleRamp.py` runs via the driver directly**

Run:
```bash
mkdir -p /tmp/mt_python_smoke
uv run python src/modelica_testing/simulators/python/run_test.py \
    examples/python/PythonTestingLib/Examples/SimpleRamp.py \
    5.0 1e-6 /tmp/mt_python_smoke/result.json
cat /tmp/mt_python_smoke/result.json | python -c \
    "import json,sys; p=json.load(sys.stdin); print('ok=', p['success'], 'npts=', len(p['time']), 'x_end=', p['variables']['x'][-1])"
```

Expected output:
```
ok= True npts= 101 x_end= 10.0
```

(The value may differ by float epsilon; the key is ~10.0, confirming the solver ran.)

- [ ] **Step 6: Commit**

```bash
git add examples/python/PythonTestingLib/
git commit -m "$(cat <<'EOF'
feat(python): add SimpleRamp scipy example + library scaffolding

First example in PythonTestingLib — counterpart to the Modelica and
Julia SimpleTest/SimpleRamp tests. Uses scipy.integrate.solve_ivp to
integrate dx/dt = 2 up to stop_time. Proves the Python subprocess
contract works end-to-end against a real ODE solver.

Also adds pyproject.toml (scipy dep) and README (user-facing
contract for simulate()).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: CSV-loader example — `ConstantCsv.py`

**Goal:** Second example. Reads pre-recorded trajectory data from a CSV file and returns it verbatim. Contains **zero** numerical simulation code. This is the architectural proof that a "SimulatorRunner" can produce a trajectory from any source, not just an ODE solver.

**Files:**
- Create: `examples/python/PythonTestingLib/Examples/ConstantCsv.py`
- Create: `examples/python/PythonTestingLib/Examples/data/constant_experiment.csv`

- [ ] **Step 1: Create the pre-recorded CSV**

Create `examples/python/PythonTestingLib/Examples/data/constant_experiment.csv` with this exact content:

```csv
t,x
0.0,1.0
0.25,1.0
0.5,1.0
0.75,1.0
1.0,1.0
1.25,1.0
1.5,1.0
1.75,1.0
2.0,1.0
2.25,1.0
2.5,1.0
2.75,1.0
3.0,1.0
3.25,1.0
3.5,1.0
3.75,1.0
4.0,1.0
4.25,1.0
4.5,1.0
4.75,1.0
5.0,1.0
```

This is a constant x=1.0 signal over t ∈ [0, 5] sampled at 0.25s. Any signal would work — the point is that the "simulation" is literally just reading the file.

- [ ] **Step 2: Create `ConstantCsv.py`**

Create `examples/python/PythonTestingLib/Examples/ConstantCsv.py` with:

```python
"""Pre-recorded experiment data loader — the "no simulator" test.

This file contains zero numerical-integration code. The ``simulate()``
function loads pre-recorded (t, x) pairs from a CSV and returns them
verbatim. It exists to prove the backend abstraction is not secretly
shaped around ODE simulation: a "SimulatorRunner" can serve any
source of time-series data, including pure data playback.

The ``stop_time`` argument is respected as a clip window — samples with
``t > stop_time`` are excluded. ``tolerance`` is ignored (there is no
solver tolerance to honor for pre-recorded data).

This is the minimal version: already-aligned CSV with well-defined
``t`` and ``x`` columns. More sophisticated use cases (server fetches,
clock-drift alignment, windowing) are user-space concerns — any of
them can be implemented in a Python test file today. Framework-level
alignment/fitting is tracked as a follow-up in ``docs/ideas.md``.
"""
from __future__ import annotations

import csv
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "constant_experiment.csv"


def simulate(stop_time: float, tolerance: float) -> dict:
    # tolerance is irrelevant for pre-recorded data; kept in the
    # signature to satisfy the framework contract.
    del tolerance

    times: list[float] = []
    values: list[float] = []
    with _DATA_FILE.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = float(row["t"])
            if t > stop_time:
                break
            times.append(t)
            values.append(float(row["x"]))

    return {
        "time": times,
        "variables": {"x": values},
    }
```

- [ ] **Step 3: Verify `ConstantCsv.py` runs via the driver directly**

Run:
```bash
uv run python src/modelica_testing/simulators/python/run_test.py \
    examples/python/PythonTestingLib/Examples/ConstantCsv.py \
    5.0 1e-6 /tmp/mt_python_smoke/csv_result.json
cat /tmp/mt_python_smoke/csv_result.json | python -c \
    "import json,sys; p=json.load(sys.stdin); print('ok=', p['success'], 'npts=', len(p['time']), 'x_first=', p['variables']['x'][0], 'x_last=', p['variables']['x'][-1])"
```

Expected output:
```
ok= True npts= 21 x_first= 1.0 x_last= 1.0
```

- [ ] **Step 4: Commit**

```bash
git add examples/python/PythonTestingLib/Examples/ConstantCsv.py \
        examples/python/PythonTestingLib/Examples/data/
git commit -m "$(cat <<'EOF'
feat(python): add ConstantCsv — the "no simulator" proof

CSV loader that reads pre-recorded (t, x) pairs and returns them
verbatim. Zero numerical-integration code. Validates that the
backend abstraction (TestModel / TestResult / SimulatorRunner) is
not secretly shaped around ODE simulation — a valid test source
can be pure data playback.

Framework-level alignment/fitting (time-offset, amplitude scaling,
clock-drift resampling) is noted as a follow-up; users who need it
today can implement it in the .py file itself.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Example library config files

**Goal:** `testing.json` and `test_spec.json` that wire the two examples into the CLI. Mirrors the Julia library layout.

**Files:**
- Create: `examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json`
- Create: `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json`

- [ ] **Step 1: Create `testing.json`**

Create `examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json` with:

```json
{
  "source_type": "python",
  "source_path": "../..",
  "library_name": "PythonTestingLib",
  "test_spec": "./test_spec.json",
  "reference_root": "./",
  "simulators": {
    "Python": ["python", "python3"]
  },
  "simulator": "Python",
  "tolerance": 0.01
}
```

Notes on each field:
- `source_type: "python"` — triggers the non-Modelica branch in `Config.__post_init__` (no package.mo lookup).
- `source_path: "../.."` — relative to this config file, resolves to `examples/python/PythonTestingLib/`.
- `simulators.Python` — list of candidate interpreter names; `PythonConfig.from_config` will try them in order.
- `tolerance: 0.01` — NRMSE comparison tolerance (looser than the simulator tolerance because scipy + Dassl will disagree at ~1e-3 level on the ramp).

- [ ] **Step 2: Create `test_spec.json`**

Create `examples/python/PythonTestingLib/Resources/ReferenceResults/test_spec.json` with:

```json
{
  "tests": [
    {
      "model": "PythonTestingLib.Examples.SimpleRamp",
      "source": "../../Examples/SimpleRamp.py",
      "variables": ["x"],
      "simulation": {
        "stop_time": 5.0,
        "tolerance": 1e-6
      }
    },
    {
      "model": "PythonTestingLib.Examples.ConstantCsv",
      "source": "../../Examples/ConstantCsv.py",
      "variables": ["x"],
      "simulation": {
        "stop_time": 5.0,
        "tolerance": 1e-6
      },
      "comparison": {
        "variable_overrides": {
          "x": {
            "mode": "range",
            "min_value": 0.99,
            "max_value": 1.01
          }
        }
      }
    }
  ]
}
```

Notes:
- `SimpleRamp` uses default NRMSE comparison against the saved baseline.
- `ConstantCsv` uses a `range` check — this is the *baseline-free* mode that asserts a signal stays between bounds. Fitting for a pre-recorded constant signal: the CSV says x should be 1.0, so `[0.99, 1.01]` is a tight range. Demonstrates that experiment-data tests don't necessarily need a baseline file at all.
- Both entries use the generic `source` field, same as Julia's spec (`source: "..../foo.jl"`).

- [ ] **Step 3: Discovery sanity check (no simulation yet)**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    discover
```

Expected output: two test entries listed (`PythonTestingLib.Examples.SimpleRamp`, `PythonTestingLib.Examples.ConstantCsv`).

- [ ] **Step 4: Commit**

```bash
git add examples/python/PythonTestingLib/Resources/ReferenceResults/
git commit -m "$(cat <<'EOF'
feat(python): testing.json + test_spec.json for PythonTestingLib

Wires the two examples into the CLI. SimpleRamp uses default NRMSE
comparison against a saved baseline; ConstantCsv uses a baseline-free
range check demonstrating that experiment-data tests don't need a
reference file at all.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: End-to-end smoke + baseline acceptance

**Goal:** Run both tests end-to-end through the CLI, accept them as baselines, commit the baselines, rerun and confirm self-regression passes.

**Files:**
- Create (via `--accept`): `examples/python/PythonTestingLib/Resources/ReferenceResults/Python/<os>/ref_0001.json`
- Create (via `--accept`): `examples/python/PythonTestingLib/Resources/ReferenceResults/Python/<os>/ref_0002.json`
- Create (via `--accept`): `examples/python/PythonTestingLib/Resources/ReferenceResults/manifest.json`

- [ ] **Step 1: First CLI run (no baselines yet — expect NO_REF)**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

Expected: both tests simulate successfully. `ConstantCsv` should already PASS (range check is baseline-free — 1.0 is within [0.99, 1.01]). `SimpleRamp` should report NO_REF (no baseline to compare against).

- [ ] **Step 2: Accept baselines**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run --accept
```

Expected: baselines written under `examples/python/PythonTestingLib/Resources/ReferenceResults/Python/<os>/ref_0001.json` and `ref_0002.json`. Check:
```bash
ls examples/python/PythonTestingLib/Resources/ReferenceResults/Python/*/
```

You should see `ref_0001.json`, `ref_0002.json`, and `manifest.json` under a `linux/` (or `windows/`) subdir.

- [ ] **Step 3: Self-regression — rerun and verify PASS**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

Expected: both tests PASS. `SimpleRamp` matches its own baseline (NRMSE < 0.01 trivially). `ConstantCsv` passes the range check.

- [ ] **Step 4: Commit the baselines**

```bash
git add examples/python/PythonTestingLib/Resources/ReferenceResults/Python/ \
        examples/python/PythonTestingLib/Resources/ReferenceResults/manifest.json
git commit -m "$(cat <<'EOF'
baseline(python): accept SimpleRamp + ConstantCsv baselines

Self-regression confirms both pass on rerun: SimpleRamp via NRMSE
against its own saved scipy trajectory, ConstantCsv via the
baseline-free range check.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Real end-to-end integration tests in pytest

**Goal:** Wire the CLI-driven smoke tests into pytest so they guard against regression going forward. Mirror `tests/test_julia_runner.py` structure: gate on scipy availability + baseline presence.

**Files:**
- Modify: `tests/test_python_runner.py` (append CLI-driven integration tests)

- [ ] **Step 1: Append integration tests**

Append to `tests/test_python_runner.py`:

```python
# ---------------------------------------------------------------------------
# CLI-driven end-to-end tests (gated on scipy availability)
# ---------------------------------------------------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not _scipy_available(),
    reason="scipy not available; SimpleRamp example needs it",
)


@pytestmark_e2e
def test_python_simple_ramp_smoke(tmp_path):
    """End-to-end: run SimpleRamp via the CLI. Simulation must succeed.

    Exercises the whole pipeline (discovery → Python subprocess →
    read_result → comparator).
    """
    result = subprocess.run(
        ["uv", "run", "modelica-testing",
         "--config", str(_CONFIG),
         "run", "--filter", "*SimpleRamp",
         "--work-dir", str(tmp_path / "wd1")],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    result_json = tmp_path / "wd1" / "test_0001" / "result.json"
    assert result_json.exists()
    import json
    payload = json.loads(result_json.read_text())
    assert payload["success"] is True
    assert "x" in payload["variables"]


@pytestmark_e2e
def test_python_constant_csv_passes_range_check(tmp_path):
    """ConstantCsv must PASS on a fresh run (baseline-free range check)."""
    result = subprocess.run(
        ["uv", "run", "modelica-testing",
         "--config", str(_CONFIG),
         "run", "--filter", "*ConstantCsv",
         "--work-dir", str(tmp_path / "wd2")],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout, result.stdout


@pytestmark_e2e
def test_python_simple_ramp_self_regression(tmp_path):
    """With baselines committed, SimpleRamp rerun must PASS."""
    baseline_dir = (
        _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "Python"
    )
    if not any(baseline_dir.rglob("ref_*.json")):
        pytest.skip(
            "No Python baselines committed under PythonTestingLib/ReferenceResults; "
            "run `modelica-testing --config ... run --accept` first"
        )
    result = subprocess.run(
        ["uv", "run", "modelica-testing",
         "--config", str(_CONFIG),
         "run", "--filter", "*SimpleRamp",
         "--work-dir", str(tmp_path / "wd3")],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout, result.stdout
```

- [ ] **Step 2: Run the new integration tests**

Run: `uv run pytest tests/test_python_runner.py -v`

Expected: all tests pass (registration + config + 4 driver + 3 integration = 9 tests).

- [ ] **Step 3: Run the full suite to confirm no regression**

Run: `uv run pytest -q`

Expected: 761 passed (752 previous + 9 new) + 1 skipped, 0 failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_python_runner.py
git commit -m "$(cat <<'EOF'
test(python): end-to-end CLI integration tests

Three additions to test_python_runner.py: SimpleRamp smoke (result
JSON on disk), ConstantCsv baseline-free PASS, SimpleRamp
self-regression PASS. Gated on scipy availability.

Mirrors the tests/test_julia_runner.py pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Internal-refactor pass — generalize spec_parser source field

**Goal:** The one-and-only refactor pass in this plan. Currently `spec_parser.py` has `julia_rel = entry.get("source")` with a comment saying "for the Julia/MTK backend (D77)." Rename the variable to `source_rel` and update the docstring/comment to reflect that `source` is the generic path-to-source-file field used by any non-Modelica backend (Julia, Python, and future ones). Keep the `fmu` alias as-is (FMPy-specific historical name; no benefit from renaming since no new FMU tests are added by this plan). This is a mechanical rename — zero behavior change.

**Files:**
- Modify: `src/modelica_testing/discovery/spec_parser.py` (around lines 85–106)

- [ ] **Step 1: Read the current spec_parser source-field handling**

Open `src/modelica_testing/discovery/spec_parser.py` and locate the block roughly at lines 82–106 that handles `fmu_rel` and `julia_rel`. It should look like:

```python
        # Optional source-file field: path (relative to spec file) to a
        # simulation source. Backend-specific:
        #   "fmu"    → FMU binary for the FMPy backend.
        #   "source" → .jl file for the Julia/MTK backend (D77).
        # Modelica tests omit this and source_file stays empty (the .mo
        # lives in the package discovered via source_package).
        source_file = Path("")
        fmu_rel = entry.get("fmu")
        julia_rel = entry.get("source")
        if fmu_rel:
            fmu_path = (spec_path.parent / fmu_rel).resolve()
            if not fmu_path.exists():
                logger.warning(
                    "Test '%s' references missing FMU: %s", model_id, fmu_path
                )
            source_file = fmu_path
        elif julia_rel:
            julia_path = (spec_path.parent / julia_rel).resolve()
            if not julia_path.exists():
                logger.warning(
                    "Test '%s' references missing source file: %s",
                    model_id, julia_path,
                )
            source_file = julia_path
```

- [ ] **Step 2: Rename `julia_rel` / `julia_path` → `source_rel` / `source_path_resolved`; update docstring**

Replace the entire block above with:

```python
        # Optional source-file field: path (relative to spec file) to a
        # simulation source. Generic across non-Modelica backends:
        #   "source" → the source file (.jl for Julia, .py for Python,
        #              .fmu also accepted here for symmetry).
        #   "fmu"    → legacy FMPy-specific alias (pre-D77); still
        #              supported but "source" is preferred for new tests.
        # Modelica tests omit this and source_file stays empty (the .mo
        # lives in the package discovered via source_package).
        source_file = Path("")
        fmu_rel = entry.get("fmu")
        source_rel = entry.get("source")
        if fmu_rel:
            fmu_path = (spec_path.parent / fmu_rel).resolve()
            if not fmu_path.exists():
                logger.warning(
                    "Test '%s' references missing FMU: %s", model_id, fmu_path
                )
            source_file = fmu_path
        elif source_rel:
            source_path_resolved = (spec_path.parent / source_rel).resolve()
            if not source_path_resolved.exists():
                logger.warning(
                    "Test '%s' references missing source file: %s",
                    model_id, source_path_resolved,
                )
            source_file = source_path_resolved
```

- [ ] **Step 3: Run the full suite to confirm zero behavior change**

Run: `uv run pytest -q`

Expected: 761 passed + 1 skipped, 0 failures. Pure rename — if any test breaks, the rename wasn't purely mechanical and needs investigation.

- [ ] **Step 4: Also run the Julia + Python smoke flows**

Run:
```bash
# Julia (if available):
[ -x "$(command -v julia)" ] && uv run modelica-testing \
    --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json \
    run

# Python:
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

Expected: both sets of tests PASS (or Julia skipped if not installed).

- [ ] **Step 5: Commit**

```bash
git add src/modelica_testing/discovery/spec_parser.py
git commit -m "$(cat <<'EOF'
refactor(discovery): generalize spec_parser source-field naming

Rename julia_rel/julia_path to source_rel/source_path_resolved.
The "source" JSON field was already generic (works for .jl and .py
alike) — this is a variable-and-comment rename to make that explicit.
"fmu" stays as a legacy alias (no new FMU tests need renaming).

Zero behavior change; full suite + Julia + Python smokes all green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Update documentation

**Goal:** `CLAUDE.md`, `docs/SESSION_HANDOFF.md`, `docs/decisions.md`, `docs/ideas.md` all reflect the new backend + library + noted follow-ups.

**Files:**
- Modify: `CLAUDE.md` (Project Overview)
- Modify: `docs/SESSION_HANDOFF.md` (backend table, library list, test count, state)
- Modify: `docs/decisions.md` (add D80 entry)
- Modify: `docs/ideas.md` (add follow-ups)

- [ ] **Step 1: Update `CLAUDE.md`**

Open `CLAUDE.md`. In the "Project Overview" section (line 7), find the sentence about backends:

```
...simulates via **Dymola** (Python interface default, batch `.mos` fallback), **FMPy** (prebuilt FMUs), **OpenModelica** (OMPython persistent-worker default, `omc` batch fallback), or **Julia/ModelingToolkit** (persistent `julia` worker)...
```

Replace with:

```
...simulates via **Dymola** (Python interface default, batch `.mos` fallback), **FMPy** (prebuilt FMUs), **OpenModelica** (OMPython persistent-worker default, `omc` batch fallback), **Julia/ModelingToolkit** (persistent `julia` worker), or **Python** (subprocess-per-test; any `simulate(stop_time, tolerance) -> dict` — scipy, pandas, CSV, HTTP, etc.)...
```

Also update the "Project history" sentence at line 9 to append `, D80 Python-driven tests`:

```
D69–D70 OpenModelica, D77–D79 Julia/MTK, D80 Python-driven tests
```

- [ ] **Step 2: Update `docs/SESSION_HANDOFF.md`**

Open `docs/SESSION_HANDOFF.md`. Apply three edits:

**(a) Header counts** — In the top block (around lines 5–11), update the backend and library counts:

Replace:
```
- **4 simulator backends**: Dymola, FMPy, OpenModelica, Julia/MTK
- **2 test libraries**: `ModelicaTestingLib` (10 tests), `JuliaMtkTestingLib` (7 tests)
```

With:
```
- **5 simulator backends**: Dymola, FMPy, OpenModelica, Julia/MTK, Python
- **3 test libraries**: `ModelicaTestingLib` (10 tests), `JuliaMtkTestingLib` (7 tests), `PythonTestingLib` (2 tests)
```

Also update the test count line (currently "752 tests passing") to the actual final post-Task 9 number (761).

**(b) Backend table** — In the "Backends" section (around lines 62–67), append a row for Python:

Below the Julia/MTK row, add:
```
| **Python** | `PythonRunner` | subprocess per test (batch) | ✗ (MVP) | — | Arbitrary Python: scipy, CSV, pandas, HTTP, ... |
```

**(c) Add a "D80 status" block** below the existing "D77–D79" coverage summary. Insert this new section just before the "## Known limitations" header:

```markdown
### D80 — Python-driven tests (this session)

* New backend `PythonRunner` (`src/modelica_testing/simulators/python/`)
  mirroring the Julia D77 pattern: framework-shipped `run_test.py`
  driver loads the user's `.py` file via `importlib.util`, calls
  `simulate(stop_time, tolerance) -> dict`, writes a JSON result.
* New fixture library `examples/python/PythonTestingLib/` with two
  tests: `SimpleRamp` (scipy-based ODE, counterpart to
  ModelicaTestingLib/JuliaMtkTestingLib SimpleTest/SimpleRamp) and
  `ConstantCsv` (CSV loader — *zero* ODE code, architectural proof
  that the backend abstraction is not secretly simulator-shaped).
* Minor refactor: `spec_parser.py`'s `julia_rel` → `source_rel`
  variable rename to reflect that `"source"` is the generic
  non-Modelica source-file field.
* Batch-only MVP; persistent-worker Python deferred (same D77→D78
  progression as Julia).
```

- [ ] **Step 3: Add D80 to `docs/decisions.md`**

Open `docs/decisions.md`. Append (after the last D79 entry):

```markdown

## D80: Python-driven tests — validating the backend abstraction

- **What**: Added a fifth `SimulatorRunner` backend (`PythonRunner`) that
  runs arbitrary Python scripts as test sources. User-facing contract:
  a `.py` file exports `simulate(stop_time: float, tolerance: float) -> dict`
  returning `{"time": [...], "variables": {name: [...]}}`. Batch-only
  subprocess per test; no persistent variant yet.
- **Why**: Primary motivation was validating that the backend
  abstraction (`TestModel`, `TestResult`, `SimulatorRunner`,
  `Recognizer`, `spec_parser`) is truly language/simulator-agnostic
  rather than secretly Modelica-shaped. Secondary motivation:
  unlocks pyomo / scipy / custom-solver / CSV-loader / HTTP-fetch use
  cases that were impossible before.
- **Architecture**: Copies the Julia D77 subprocess+JSON-over-disk
  contract unchanged. New `src/modelica_testing/simulators/python/`
  package with `runner.py` + `run_test.py` driver. New fixture library
  `examples/python/PythonTestingLib/` with `SimpleRamp.py` (scipy ODE)
  and `ConstantCsv.py` (CSV loader). The CSV-loader example is the key
  architectural validation — it contains zero numerical-integration
  code and still produces a passing test via a baseline-free range
  check.
- **Refactor pass** (minimal, per the plan's scope discipline):
  `spec_parser.py`'s `julia_rel` variable renamed to `source_rel` to
  reflect that the `"source"` JSON field is generic across non-Modelica
  backends. No other renames — the remaining Modelica-flavored
  `TestModel` fields (`x_expressions`, `x_raw`, `x_reference`,
  `error_expected`, `number_of_intervals`, `output_interval`, `method`)
  are left alone because the Python backend simply ignores them and
  the D77 Julia backend already does the same. Renaming them would be
  churn without improving generality.
- **Deferred**: Persistent-worker Python (D77→D78 progression to
  follow once a real perf ceiling hits). Alignment/fitting of
  experiment data (see `docs/ideas.md` #49). Dynamic-data fetching is
  not deferred — any user writes an HTTP client in their `.py` file
  today.
- **Validation**:
  - `SimpleRamp` + `ConstantCsv` both PASS on self-regression.
  - Full suite: 761 passing + 1 skipped, 0 regressions.
  - Driver-in-isolation tests cover success, missing simulate(),
    exception in simulate(), malformed return type.

### Rejected alternatives

- **Base class for subprocess+JSON runners** (factor out shared code
  from JuliaRunner and PythonRunner). Tempting but premature per YAGNI:
  two backends with ~30% shared code isn't enough to justify an
  abstraction. If a third subprocess-JSON backend arrives (e.g., R,
  Octave, Matlab), extract then. Note the `/simplify` skill would
  flag this candidate on review.
- **Support `importlib` over subprocess** (run user code in-process
  for speed). Rejected: loses the process isolation that makes
  framework-level timeouts reliable and prevents user-code crashes
  from taking down the test runner.
- **Built-in CSV/parquet loaders as capability-flag-gated first-class
  sources**. Rejected: users with CSV data write one 5-line `.py`
  file; framework complexity budget better spent elsewhere (D66
  economy-of-tools).
- **Alignment/fitting in-framework as part of #45**. Deferred to
  ideas.md follow-up. Without a concrete user demand it's easy to
  build generic alignment machinery that fits nobody; wait for real
  use case.
```

- [ ] **Step 4: Add follow-up entries to `docs/ideas.md`**

Open `docs/ideas.md`. Find the "Priority Matrix" table. Append two new rows at the next available index (inspect the end of the table for the current highest number — let's call it `N`):

```markdown
| N+1 | Experiment-data alignment preprocessing | M | Medium | Time-offset / amplitude-scale alignment for CSV baselines before scoring. Belongs in the comparison layer as a preprocessing wrapper or new ComparisonMode. Wait for concrete user demand — D66 says calibration-adjacent work belongs downstream. |
| N+2 | Persistent-worker Python (PythonCall / stdin-JSON) | M | Low | Long-lived Python subprocess with stdin-dispatch, like JuliaPersistentRunner. Per-test startup is ~30-100 ms today; pays off once a suite has hundreds of Python tests. Mirror the D77→D78 Julia progression. |
```

Then below the matrix, append a paragraph-level note:

```markdown

## D80 follow-ups (Python-driven tests)

- **Experiment-data alignment** (ideas-matrix N+1): The current
  ConstantCsv example requires the user's CSV to already be correctly
  sampled and aligned. Real-world experiment data often needs
  time-offset alignment (cross-correlation / min-NRMSE over shifts),
  amplitude scaling, clock-drift re-sampling, or steady-state window
  selection. Belongs in the comparison layer as either a new
  `ComparisonMode` that wraps a base mode with preprocessing, or as
  a generic preprocessing hook on the MetricTree. Explicit
  non-goal: parameter estimation or calibration (D66 → downstream
  tools). Wait for a concrete user demand with specific alignment
  requirements before building — the generic machinery risks fitting
  nobody.

- **Dynamic data fetching** (no framework change needed): users
  whose test data lives on a REST API, S3 bucket, time-series
  database, etc. can already implement this in their `.py` file
  today. The `simulate(stop_time, tolerance)` function can do
  arbitrary I/O — fetch the data, filter it, return the trajectory.
  Framework-level "data-source" plug-ins would be premature; revisit
  only if multiple users converge on the same data-source pattern.

- **Persistent-worker Python** (ideas-matrix N+2): the batch-only
  MVP pays ~30-100 ms per test in subprocess + importlib startup.
  For small suites this is invisible; for 100+ Python tests it
  becomes the dominant cost. The Julia D77→D78 progression is the
  template — long-lived subprocess + stdin-JSON dispatch, same
  contract on the wire.
```

- [ ] **Step 5: Commit docs updates**

```bash
git add CLAUDE.md docs/SESSION_HANDOFF.md docs/decisions.md docs/ideas.md
git commit -m "$(cat <<'EOF'
docs: D80 Python-driven tests (CLAUDE.md, handoff, decisions, ideas)

CLAUDE.md overview lists Python backend. SESSION_HANDOFF.md counts
update (4→5 backends, 2→3 libraries, 752→761 tests) + new D80 block.
decisions.md D80 entry covers motivation (validate abstraction),
architecture (mirror Julia D77), refactor scope (spec_parser rename
only), rejected alternatives. ideas.md adds alignment-preprocessing
and persistent-worker-Python follow-ups with explicit "wait for
concrete demand" guards.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Final whole-suite sanity

**Goal:** Confirm everything works end-to-end across the full matrix before calling the feature done.

**Files:** none modified; this is a verification pass.

- [ ] **Step 1: Full pytest suite**

Run: `uv run pytest -q`

Expected: 761 passed + 1 skipped (reference_fmus) + possibly a few conditional skips, 0 failures.

- [ ] **Step 2: Python smoke via CLI**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run
```

Expected: both tests PASS.

- [ ] **Step 3: Report generation smoke**

Run:
```bash
uv run modelica-testing \
    --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json \
    run --report ./reports/python
ls reports/python/
```

Expected: HTML report generated without error (existence of `index.html` and per-test subdirs is sufficient — no need to browser-verify for the MVP).

- [ ] **Step 4: Sanity-check the other backends still work**

Run each (Dymola only works on Windows, Julia/OM depend on install):

```bash
# OpenModelica (if installed):
[ -x "$(command -v omc)" ] && uv run modelica-testing \
    --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json \
    run || true

# Julia (if installed):
[ -x "$(command -v julia)" ] && uv run modelica-testing \
    --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json \
    run || true
```

Expected: whichever backends are installed still PASS on their example libraries (the `spec_parser.py` rename must not have broken them — full regression coverage was via pytest, but a manual smoke is extra reassurance).

- [ ] **Step 5: Log status and close out**

No commit needed unless Step 3 or 4 surfaces issues. If something fails, investigate before declaring complete. If everything passes, the feature is done — the Python backend is live, the CSV-loader example has proven the backend abstraction is language-agnostic, and follow-ups are queued in ideas.md.

---

## Post-implementation (optional, recommended)

After Task 12 passes clean, run `/simplify` on the new code:

```
/simplify
```

Candidates it should flag:
- `PythonRunner` and `JuliaRunner` share ~30% of their structure
  (`run_single_test` subprocess scaffolding, `read_result` JSON
  parsing, the `_read_failure_error` helper). *Decision when it
  flags:* leave as-is for now. Per D66 + the "no premature
  abstraction" rule, three similar lines is better than a premature
  abstraction. If a third subprocess+JSON backend ships, extract a
  shared base then.
- The `PythonConfig`/`JuliaConfig` dataclasses have identical
  resolution logic. Same call: leave them as-is. They're 20 lines
  each and future backend-specific config fields (e.g., virtualenv
  activation for Python) will diverge them naturally.

If `/simplify` flags anything *within* the new Python code (not
cross-backend), address it in a follow-up commit.

---

## Scope reminders

**This plan does:**
- Add one new backend (`Python`) with a batch-only runner.
- Add two example tests proving the abstraction (scipy ODE + CSV loader).
- Do one minimal, mechanical refactor (`julia_rel` → `source_rel`).
- Document thoroughly (D80 entry, handoff update, ideas follow-ups).

**This plan explicitly does NOT do:**
- Time-offset alignment / amplitude scaling / resampling for experiment data.
- Dynamic data fetching (user writes Python; no framework change needed).
- Parameter estimation / calibration (D66 → downstream).
- Persistent-worker Python (deferred; mirror Julia D77→D78).
- Renaming the wider set of Modelica-shaped `TestModel` fields
  (`x_expressions`, `error_expected`, `number_of_intervals`, etc.) —
  Python backend ignores them, same as Julia.
- Extracting a shared subprocess+JSON base class (YAGNI until 3rd backend).
- Tool rename (separate D-tier item).

If a reviewer pushes to expand scope, the answer is "not in this
plan — log it as a follow-up in ideas.md."
