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

    Exercises the whole pipeline (discovery -> Python subprocess ->
    read_result -> comparator).
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
