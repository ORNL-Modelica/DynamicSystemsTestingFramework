"""Integration tests for the Python subprocess backend (D80).

These tests gate on scipy availability (needed by the SimpleRamp example).
The registration test doesn't need scipy and always runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_test_model as _tm

PROJECT_ROOT = Path(__file__).resolve().parents[1]


_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "python" / "PythonTestingLib"
)
_CONFIG = _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "testing.json"


def _scipy_available() -> bool:
    """True iff scipy is importable from the *subprocess* Python that dstf
    will spawn for end-to-end tests.

    The pytest process and the dstf subprocess can resolve to different Python
    interpreters (e.g. ``uv run pytest`` falling through to a global pytest
    on PATH while ``uv run dstf`` uses the project ``.venv``). Probing the
    pytest process's own ``sys.path`` would lie about whether the simulation
    will actually find scipy. Subprocess-probing is slow (~150ms) but only
    runs once at module load.
    """
    result = subprocess.run(
        ["uv", "run", "python", "-c", "import scipy"],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def test_python_backend_registered():
    """The Python backend registers when its submodule is imported."""
    from dstf.backends import get_backend_class
    from dstf.config import Config

    cfg = Config(config_file=_CONFIG) if _CONFIG.exists() else None
    if cfg is None:
        # Example config not written yet (earlier task ordering); fabricate.
        # Force-import to trigger registration, then check the registry.
        import dstf.backends.python  # noqa: F401
        from dstf.backends import _REGISTRY

        assert "Python" in _REGISTRY
        assert _REGISTRY["Python"].__name__ == "PythonBackend"
        return
    cls = get_backend_class(cfg)
    assert cls.__name__ == "PythonBackend"


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
        '"library_name": "MyPyLib", "backends": {"Python": ["python"]}, '
        '"backend": "Python"}'
    )
    from dstf.config import Config

    cfg = Config(config_file=cfg_path)
    assert cfg.source_type == "python"
    assert cfg.source_path.name == "MyPyLib"
    assert cfg.backend_name == "Python"
    assert cfg.backend == "Python"


_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dstf"
    / "backends"
    / "python"
    / "run_test.py"
)


def _run_driver(user_file: Path, result_path: Path, stop_time=1.0, tolerance=1e-6):
    """Invoke run_test.py as a subprocess and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [
            shutil.which("python") or "python3",
            str(_DRIVER),
            str(user_file),
            str(stop_time),
            str(tolerance),
            str(result_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
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
        "def simulate(stop_time, tolerance):\n    raise ValueError('boom')\n"
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
    user.write_text("def simulate(stop_time, tolerance):\n    return 'not a dict'\n")
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
        [
            "uv",
            "run",
            "dstf",
            "--config",
            str(_CONFIG),
            "run",
            "--filter",
            "*SimpleRamp",
            "--work-dir",
            str(tmp_path / "wd1"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
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
        [
            "uv",
            "run",
            "dstf",
            "--config",
            str(_CONFIG),
            "run",
            "--filter",
            "*ConstantCsv",
            "--work-dir",
            str(tmp_path / "wd2"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout, result.stdout


@pytestmark_e2e
def test_python_simple_ramp_self_regression(tmp_path):
    """With baselines committed, SimpleRamp rerun must PASS."""
    baseline_dir = _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "Python"
    if not any(baseline_dir.rglob("ref_*.json")):
        pytest.skip(
            "No Python baselines committed under PythonTestingLib/ReferenceResults; "
            "run `dstf --config ... run --accept` first"
        )
    result = subprocess.run(
        [
            "uv",
            "run",
            "dstf",
            "--config",
            str(_CONFIG),
            "run",
            "--filter",
            "*SimpleRamp",
            "--work-dir",
            str(tmp_path / "wd3"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout, result.stdout


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Finding 29 — non-numeric driver output, both sides: the driver rejects it
# with a named error, and the backend turns a tampered/legacy result.json into
# one failed test instead of aborting the whole read phase.
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 29 — non-numeric driver output: driver + backend sides
# ---------------------------------------------------------------------------

_PY_DRIVER = PROJECT_ROOT / "src" / "dstf" / "backends" / "python" / "run_test.py"


class TestPythonNonNumericValues:
    def test_driver_rejects_non_numeric_values(self, tmp_path):
        import shutil as _sh

        user = tmp_path / "bad_types.py"
        user.write_text(
            "def simulate(stop_time, tolerance):\n"
            "    return {'time': [0.0, 1.0], 'variables': {'x': ['a', 'b']}}\n"
        )
        result = tmp_path / "result.json"
        proc = subprocess.run(
            [
                _sh.which("python") or "python3",
                str(_PY_DRIVER),
                str(user),
                "1.0",
                "1e-6",
                str(result),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        payload = json.loads(result.read_text())
        assert payload["success"] is False
        assert "'x'" in payload["error"]
        assert "non-numeric" in payload["error"]

    def test_driver_rejects_non_numeric_time(self, tmp_path):
        import shutil as _sh

        user = tmp_path / "bad_time.py"
        user.write_text(
            "def simulate(stop_time, tolerance):\n"
            "    return {'time': ['zero'], 'variables': {'x': [1.0]}}\n"
        )
        result = tmp_path / "result.json"
        proc = subprocess.run(
            [
                _sh.which("python") or "python3",
                str(_PY_DRIVER),
                str(user),
                "1.0",
                "1e-6",
                str(result),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        payload = json.loads(result.read_text())
        assert payload["success"] is False
        assert "time" in payload["error"]

    def test_read_result_turns_non_numeric_into_failed_test(self, tmp_path):
        """Backend-side belt: a hand-tampered/legacy result.json with string
        values must fail the one test, not abort the whole read phase."""
        from dstf.backends.python import backend as pr

        cfg = SimpleNamespace(
            backend_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        backend = pr.PythonBackend(cfg)
        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        (test_dir / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": [0.0, 1.0],
                    "variables": {"x": ["not", "numbers"]},
                }
            )
        )
        test = _tm("T", variable_patterns=["x"])
        res = backend.read_result(test, "test_0001", None)
        assert res.success is False
        assert "x" in (res.error_message or "")
