"""Shared test fixtures."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def fixtures_dir():
    """Path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_models_dir():
    """Path to the ModelicaTestingLib Modelica library."""
    return PROJECT_ROOT / "examples" / "modelica" / "ModelicaTestingLib"


@pytest.fixture
def sample_dslog():
    """Path to the sample dslog.txt."""
    return FIXTURES_DIR / "results" / "Dymola" / "dslog.txt"


@pytest.fixture
def sample_test_spec():
    """Path to the sample test_spec.json."""
    return FIXTURES_DIR / "test_spec.json"


@pytest.fixture
def tmp_reference_dir(tmp_path):
    """Create a temporary reference results directory with testing.json."""
    ref_dir = tmp_path / "ReferenceResults"
    ref_dir.mkdir()

    config = {
        "simulator": "Dymola",
        "simulators": {},
        "dependencies": [],
    }
    (ref_dir / "testing.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    return ref_dir


@pytest.fixture
def tmp_config_dir(tmp_path, sample_models_dir):
    """Create a temporary directory with testing.json pointing to ModelicaTestingLib."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Copy model fixtures so package.mo exists at the expected path
    models_dest = config_dir / "ModelicaTestingLib"
    shutil.copytree(sample_models_dir, models_dest)

    config = {
        "package_path": "ModelicaTestingLib",
        "simulator": "Dymola",
        "simulators": {},
        "dependencies": [],
        "simulator_setup": ["OutputCPUtime := true;"],
    }
    (config_dir / "testing.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    return config_dir


def make_time_series(t_start=0.0, t_stop=10.0, n_points=101):
    """Generate a simple time series for testing."""
    time = np.linspace(t_start, t_stop, n_points)
    return time


def make_event_time_series():
    """Generate a time series with event boundaries (duplicate time points)."""
    time = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
    values = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 2.0])
    return time, values


def make_multi_event_time_series():
    """Generate a time series with multiple event boundaries."""
    time = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0])
    values = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 1.9, 2.1, 2.5, 3.0])
    return time, values
