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
