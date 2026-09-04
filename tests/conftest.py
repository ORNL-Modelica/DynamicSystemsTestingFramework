"""Shared test fixtures."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from dstf.discovery.test_registry import TestModel

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


def make_test_model(model_id: str = "Lib.T", **kw) -> TestModel:
    """A directly-constructed :class:`TestModel` for backend-level tests.

    Direct construction bypasses discovery's default resolution — finding 13
    turned the simulation fields into ``None`` sentinels that ``discover_tests``
    fills in — so ``stop_time`` / ``tolerance`` / ``method`` are given concrete
    values here; backends read them unconditionally. Any field can be overridden
    by keyword.

    Lives in conftest rather than in one test module because four backend test
    files had byte-identical copies of it. Import it as
    ``from conftest import make_test_model`` (tests/ is on sys.path under
    pytest's default prepend import mode).
    """
    parts = model_id.rsplit(".", 1)
    defaults = dict(
        model_id=model_id,
        source_file=Path(""),
        source_package=parts[0] if len(parts) > 1 else "",
        short_name=parts[-1],
        n_vars=0,
        source="spec",
        stop_time=1.0,
        tolerance=1e-6,
        method="Dassl",
    )
    defaults.update(kw)
    return TestModel(**defaults)


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
        "backend": "Dymola",
        "backends": {},
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
        "source_path": "ModelicaTestingLib",
        "backend": "Dymola",
        "backends": {},
        "dependencies": [],
        "backend_setup": ["OutputCPUtime := true;"],
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


@pytest.fixture(scope="module")
def playwright_browser():
    """Shared Chromium for the browser-driven reporter tests.

    Lives in conftest — NOT imported between test modules — because a
    fixture import is invisible to pyflakes (only the parameter name
    appears in tests), and the D88 ruff F401 autofix stripped exactly
    such imports from the per-mode browser files, silently breaking
    them in every environment that has Playwright (undetected because
    none did until 2026-08-31). conftest placement cannot be
    autofix-stripped. Lazy import: environments without Playwright
    skip at fixture setup, never at collection.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()
