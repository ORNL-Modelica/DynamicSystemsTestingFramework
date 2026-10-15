"""Shared test fixtures."""

import json
import shutil
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest

from dstf.discovery.test_registry import TestModel
from dstf.storage.baseline import Baseline
from dstf.storage.baseline_codec import decode_series

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
        source_file=None,
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


#: A baseline's provenance, as accept writes one, for tests that store a
#: baseline directly (`store_reference` requires one).
PROVENANCE = {
    "backend": "Dymola",
    "backend_name": "Dymola",
    "tool_version": None,
    "library_versions": None,
    "dstf_version": None,
    "os": "linux",
    "run_started": None,
}

#: The same for every model, for `accept_results`.
PROVENANCES: Mapping[str, dict] = defaultdict(lambda: PROVENANCE)


def series(name: str, time: list, variables: list[dict], **fields) -> Baseline:
    """A named baseline from the ON-DISK shape — ``variables`` as the file's
    ``{index, name, expression, values}`` entries, any other top-level field
    (``provenance``, ``simulation``, ...) by keyword — read by the codec,
    as a soft check's file is."""
    return decode_series({"time": time, "variables": variables, **fields}, name)


def python_example_copy(tmp_path, *, baselines: bool = True):
    """Copy ``examples/python/PythonTestingLib`` into *tmp_path* and discover it.

    Returns ``(config, tests)`` — a :class:`~dstf.config.Config` reading the
    copy's ``testing.json`` with an explicit ``work_dir`` under *tmp_path*
    (the default work dir is derived from the CWD, which a test must never
    write into), and the discovered :class:`TestModel` list. With
    ``baselines=False`` the committed ``Python/`` reference tree is removed,
    so the copy has no primary baselines.

    Lives in conftest rather than in one test module so the run-pipeline,
    quiet-backend and shell tests share one runnable library copy without any
    test module importing another (same reason as ``make_test_model``).
    """
    from dstf.config import Config
    from dstf.discovery.test_registry import discover_tests

    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    ref_root = lib / "Resources" / "ReferenceResults"
    if not baselines:
        shutil.rmtree(ref_root / "Python", ignore_errors=True)

    config = Config(config_file=ref_root / "testing.json", work_dir=tmp_path / "work")
    return config, discover_tests(config)


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


#: Background threads the authoring Session starts per open. They outlive the
#: test that opened the Session unless joined, and the overlay-index one
#: reads the process-wide ``_sibling_backend_index`` cache — a late thread
#: from an earlier test turns a later test's ``cache_info()`` counts into a
#: flake (reproduced with a slowed index build). The rescan thread a cached
#: open starts re-merges into its Session and emits on it; one left running
#: from an earlier test must not act while a later test runs. The shell's
#: open thread is here for the same reason: it outlives a test that fails
#: mid-open, and ``_scan_cache_home``'s redirect must not be undone while
#: its ``store_scan`` still runs on it.
_SESSION_THREADS = ("dstf-overlay-index", "dstf-rescan", "dstf-open")


@pytest.fixture(autouse=True)
def _restore_dstf_modules_after_a_ui_simulation():
    """NiceGUI's user simulation pops every ``@ui.page`` handler's module AND
    ALL ITS PARENTS from ``sys.modules`` when it tears down
    (``nicegui/testing/general.py``). The shell registers the Test page's
    route from ``dstf.authoring.shell.shell``, so ``dstf`` itself goes while
    every other ``dstf.*`` module stays cached — and a cache hit never
    re-links a submodule to its parent, so the next
    ``import dstf.authoring.session as m`` raises ``ImportError: cannot
    import name 'authoring' from 'dstf'`` and pytest's string-target
    ``monkeypatch.setattr("dstf.reporting...")`` raises ``AttributeError:
    module 'dstf' has no attribute 'reporting'``. Nothing pops anything in
    the real process; this puts the SAME module objects back, so identity
    holds and no attribute is touched. (Re-linking submodules as attributes
    instead overwrote ``dstf.cli.main`` — a function re-exported from the
    submodule of that name — with the module; found when a new UI test
    file sorted before ``test_compare_accept.py``.)"""
    import sys

    before = {
        k: v
        for k, v in sys.modules.copy().items()  # a copy: a thread may import meanwhile
        if k == "dstf" or k.startswith("dstf.")
    }
    yield
    sys.modules.update(before)


#: The paths the system's opener was asked for (`_no_system_opener`).
_LAUNCHED: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def _no_system_opener():
    """No test opens a browser: the one function that starts the system's
    opener (``start`` / ``open`` / ``xdg-open``) is replaced by a recorder
    for the whole session — a module-scoped fixture that runs `dstf run`
    is covered too, which a function-scoped patch would not be."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("dstf.reporting.report_suite._launch_opener", _LAUNCHED.append)
        yield


@pytest.fixture
def launched_openers():
    """The paths this test asked the system's opener for, in order."""
    _LAUNCHED.clear()
    return _LAUNCHED


@pytest.fixture(autouse=True)
def _scan_cache_home(tmp_path_factory, monkeypatch):
    """Point ``XDG_CACHE_HOME`` at a per-test temporary dir. Any test that
    opens a Modelica project through the Session writes the scan cache
    (``dstf.discovery.scan_cache``), and without this it would write under
    the user's real ``~/.cache/dstf/scan/``."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))


@pytest.fixture(autouse=True)
def _join_session_threads(_scan_cache_home):
    """Join every Session background thread a test left running — the
    overlay-index warm and the background rescan (``_SESSION_THREADS``).
    Depends on ``_scan_cache_home`` so it tears down FIRST: a rescan still
    running when the redirect is undone would store its scan under the real
    ``XDG_CACHE_HOME``."""
    yield
    import threading

    for thread in threading.enumerate():
        if thread.name in _SESSION_THREADS:
            thread.join(timeout=30.0)
