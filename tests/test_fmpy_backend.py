"""Phase 2.3 end-to-end: FmpyBackend simulates a Reference-FMU.

Verifies the actual simulation path — not just registration. Skipped cleanly
if fmpy isn't installed or the Reference-FMUs binaries haven't been fetched.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model as _tm
from dstf.discovery.test_registry import TestModel

PROJECT_ROOT = Path(__file__).parent.parent
REFERENCE_FMUS_DIR = PROJECT_ROOT / "examples" / "fmu" / "reference-fmus-binaries"


def _fmpy_available() -> bool:
    try:
        import fmpy  # noqa: F401

        return True
    except ImportError:
        return False


def _reference_fmus_available() -> bool:
    return REFERENCE_FMUS_DIR.exists() and any(REFERENCE_FMUS_DIR.rglob("*.fmu"))


# Gate on the two dependencies being present. Carried by the classes that
# simulate a real FMU rather than by the module: the backend regression tests
# folded in at the bottom drive a fake `fmpy` module and need neither.
_needs_real_fmpy = [
    pytest.mark.fmpy,
    pytest.mark.reference_fmus,
    pytest.mark.skipif(
        not _fmpy_available() or not _reference_fmus_available(),
        reason=(
            "Requires fmpy installed + Reference-FMUs fetched "
            "(run: uv run python scripts/fetch_reference_fmus.py)"
        ),
    ),
]


def _make_config(tmp_path: Path) -> SimpleNamespace:
    """Minimal config stand-in — the backend only reads work_dir + progress."""
    return SimpleNamespace(
        work_dir=tmp_path,
        progress=None,
        source_type="fmu",
        backend_name="FMPy",
        parallel=1,
        timeout=60,
        diagnostic_variables=[],
    )


def _make_test(fmu_path: Path, variables: list[str], stop_time: float = 3.0):
    """Build a TestModel pointing at an FMU with the given output variables."""

    return TestModel(
        model_id=fmu_path.stem,
        source_file=fmu_path,  # FMPy backend reads .fmu path from here
        source_package="",
        short_name=fmu_path.stem,
        n_vars=len(variables),
        variable_patterns=variables,
        stop_time=stop_time,
        tolerance=1e-6,
        method="Dassl",  # gets mapped to CVode by FmpyBackend
        source="spec",
    )


# ---------------------------------------------------------------------------
# Single-FMU simulation
# ---------------------------------------------------------------------------


class TestFmpySimulation:
    pytestmark = _needs_real_fmpy

    def test_bouncing_ball_simulates_end_to_end(self, tmp_path):
        """BouncingBall simulates, result round-trips through save/load.

        BouncingBall is the canonical FMI test model: a ball bounces until its
        energy is dissipated. We track `h` (height) — must start at ~1 and
        eventually settle near 0.
        """
        from dstf.backends.fmpy import FmpyBackend

        fmu = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
        test = _make_test(fmu, variables=["h"], stop_time=3.0)
        config = _make_config(tmp_path)

        backend = FmpyBackend(config)  # type: ignore[arg-type]
        run_result = backend.run_single_test(test, "test_0001", index=1, total=1)

        assert run_result.success, f"Simulation failed: {run_result.error_message}"
        assert run_result.elapsed > 0
        assert (tmp_path / "test_0001" / "result.npz").exists()

        # Read back
        result = backend.read_result(test, "test_0001", run_result)
        assert result.success
        assert len(result.variables) == 1
        h = result.variables[0]
        assert h.name == "h"
        assert len(h.time) > 10  # CVode with default output should produce many points
        assert h.time[0] == pytest.approx(0.0)
        assert h.time[-1] == pytest.approx(3.0, abs=0.05)
        # Physical sanity: ball starts above 0, settles toward 0
        assert h.values[0] > 0.5
        assert abs(h.values[-1]) < h.values[0]

    def test_wildcard_variables(self, tmp_path):
        """``variables=['*']`` records all FMU outputs."""
        from dstf.backends.fmpy import FmpyBackend

        fmu = REFERENCE_FMUS_DIR / "2.0" / "VanDerPol.fmu"
        test = _make_test(fmu, variables=["*"], stop_time=5.0)
        config = _make_config(tmp_path)

        backend = FmpyBackend(config)  # type: ignore[arg-type]
        run_result = backend.run_single_test(test, "test_0001", index=1, total=1)
        assert run_result.success

        result = backend.read_result(test, "test_0001", run_result)
        assert result.success
        # VanDerPol has at least x0 + x1 as states; expect at least 2 tracked
        names = [v.name for v in result.variables]
        assert len(names) >= 2, f"Expected ≥2 variables under '*', got {names}"

    def test_missing_fmu_reports_failure_gracefully(self, tmp_path):
        """A non-existent FMU produces a clean failure, not an exception."""
        from dstf.backends.fmpy import FmpyBackend

        test = _make_test(Path("/nonexistent/fake.fmu"), variables=["h"])
        config = _make_config(tmp_path)

        backend = FmpyBackend(config)  # type: ignore[arg-type]
        run_result = backend.run_single_test(test, "test_0001", index=1, total=1)

        assert not run_result.success
        assert "not found" in (run_result.error_message or "").lower()

    def test_result_includes_time_column_matching_variables(self, tmp_path):
        """Time vector and per-variable values are the same length."""
        from dstf.backends.fmpy import FmpyBackend

        fmu = REFERENCE_FMUS_DIR / "2.0" / "Dahlquist.fmu"
        test = _make_test(fmu, variables=["x"], stop_time=2.0)
        config = _make_config(tmp_path)

        backend = FmpyBackend(config)  # type: ignore[arg-type]
        backend.run_single_test(test, "test_0001", index=1, total=1)
        result = backend.read_result(
            test,
            "test_0001",
            run_result=None,  # type: ignore[arg-type]
        )
        assert result.success
        x = result.variables[0]
        assert len(x.time) == len(x.values)
        assert x.time.dtype == np.float64
        assert x.values.dtype == np.float64

    def test_a_criterion_with_no_patterns_keeps_the_fmus_outputs(self, tmp_path):
        """BouncingBall with no patterns and a tree naming the parameter
        ``g``: the file holds fmpy's default set (its outputs h and v) AND g
        — requesting the tree's names alone took the outputs off the plot.
        Only g is tracked."""
        from dstf.backends.fmpy import FmpyBackend
        from dstf.comparison.tree_spec import parse_metric_tree

        fmu = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
        test = _make_test(fmu, variables=[], stop_time=1.0)
        test.metric_tree_spec = parse_metric_tree(
            {"metric": "range", "variable": "g", "max_value": 99}
        )
        backend = FmpyBackend(_make_config(tmp_path))  # type: ignore[arg-type]
        run_result = backend.run_single_test(test, "test_0001", index=1, total=1)
        assert run_result.success, run_result.error_message
        names = FmpyBackend.result_names(tmp_path / "test_0001")
        assert names is not None and sorted(names) == ["g", "h", "v"], names
        result = backend.read_result(test, "test_0001", run_result)
        assert [(v.index, v.name) for v in result.variables] == [(1, "g")]


# ---------------------------------------------------------------------------
# Helper-function tests (no FMU needed)
# ---------------------------------------------------------------------------


class TestFmpyHelpers:
    pytestmark = _needs_real_fmpy

    def test_save_load_roundtrip(self, tmp_path):
        """Structured-array persistence preserves column names + data."""
        from dstf.backends.fmpy.backend import _load_result, _save_result

        arr = np.zeros(5, dtype=[("time", "f8"), ("h", "f8"), ("v", "f8")])
        arr["time"] = [0.0, 0.1, 0.2, 0.3, 0.4]
        arr["h"] = [1.0, 0.9, 0.8, 0.7, 0.6]
        arr["v"] = [0.0, -1.0, -2.0, -3.0, -4.0]

        path = tmp_path / "result.npz"
        _save_result(path, arr)
        loaded = _load_result(path)

        assert loaded.dtype.names == ("time", "h", "v")
        np.testing.assert_array_equal(loaded["h"], arr["h"])
        np.testing.assert_array_equal(loaded["time"], arr["time"])

    def test_resolve_requested_outputs_wildcard(self):
        """``['*']`` expands to the full available list."""
        from dstf.backends.fmpy.backend import _resolve_requested_outputs

        test = _make_test(Path("x.fmu"), variables=["*"])
        available = ["h", "v", "m"]
        assert _resolve_requested_outputs(test, available) == available

    def test_resolve_requested_outputs_empty(self):
        """No patterns means no comparison variables (simulate-only)."""
        from dstf.backends.fmpy.backend import _resolve_requested_outputs

        test = _make_test(Path("x.fmu"), variables=[])
        assert _resolve_requested_outputs(test, ["h", "v"]) == []


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Finding 73 — the FMPy timeout result sets ``timed_out``, and the result
# save happens inside the try so an OSError fails the one test, not the run.
#
# These run against a fake ``fmpy`` module injected into ``sys.modules``, so
# unlike the simulation tests above they need neither fmpy nor the
# Reference-FMU binaries — which is why the dependency gate moved from a
# module-level ``pytestmark`` onto the classes that actually need it.
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 73 — FMPy timeout flag + save inside try
# ---------------------------------------------------------------------------


def _install_fake_fmpy(monkeypatch, simulate_fn):
    fake = types.ModuleType("fmpy")
    fake.read_model_description = lambda p: SimpleNamespace(
        modelVariables=[SimpleNamespace(name="h")]
    )
    fake.simulate_fmu = simulate_fn
    monkeypatch.setitem(sys.modules, "fmpy", fake)


class TestFmpyBackendFixes:
    def _make_backend(self, tmp_path, monkeypatch, simulate_fn):
        _install_fake_fmpy(monkeypatch, simulate_fn)
        from dstf.backends.fmpy.backend import FmpyBackend

        cfg = SimpleNamespace(
            work_dir=tmp_path / "work",
            source_type="fmu",
            backend_name="FMPy",
            parallel=1,
            timeout=60,
            diagnostic_variables=[],
        )
        return FmpyBackend(cfg)

    def _make_fmu_test(self, tmp_path, **kw):
        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"fake fmu")
        return _tm("Model", source_file=fmu, variable_patterns=["h"], **kw)

    def test_timeout_result_sets_timed_out_flag(self, tmp_path, monkeypatch):
        def slow_sim(**kw):
            time.sleep(0.5)
            return None

        backend = self._make_backend(tmp_path, monkeypatch, slow_sim)
        test = self._make_fmu_test(tmp_path, timeout=1)
        test.timeout = None
        backend.config.timeout = 0.05
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert (
            rr.timed_out is True
        )  # finding 73: was False → dashboard/console disagreed

    def test_save_oserror_fails_single_test_not_run(self, tmp_path, monkeypatch):
        backend = self._make_backend(tmp_path, monkeypatch, lambda **kw: object())
        monkeypatch.setattr(
            "dstf.backends.fmpy.backend._save_result",
            lambda path, arr: (_ for _ in ()).throw(OSError("disk full")),
        )
        test = self._make_fmu_test(tmp_path)
        rr = backend.run_single_test(test, "test_0001", 1, 1)  # must NOT raise
        assert rr.success is False
        assert "disk full" in (rr.error_message or "")


# ---------------------------------------------------------------------------
# The missing-dependency signal is the Backend contract's RuntimeError
# ---------------------------------------------------------------------------


class TestFmpyMissingDependency:
    """A machine without fmpy is an unavailable backend, not a bug.

    ``Backend.preflight`` names ``RuntimeError`` as the signal, and it is what
    ``backends._instantiate`` translates into ``BackendUnavailableError`` (the
    OpenModelica and Dymola dependency probes convert the same way). The
    constructor raised ``ImportError`` instead, which ``_instantiate`` — narrow
    by design — let through as a traceback.
    """

    def test_select_backend_reports_it_as_unavailable_with_the_hint(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends import BackendUnavailableError, select_backend
        from dstf.config import Config

        # `None` in sys.modules is the interpreter's own "this import fails".
        monkeypatch.setitem(sys.modules, "fmpy", None)

        source = tmp_path / "fmus"
        source.mkdir()
        config = Config(
            source_path=source,
            reference_root=tmp_path / "refs",
            source_type="fmu",
            backend_name="FMPy",
            work_dir=tmp_path / "work",
        )

        with pytest.raises(BackendUnavailableError) as excinfo:
            select_backend(config, persistent=False)

        message = str(excinfo.value)
        assert "optional 'fmpy' extra" in message
        assert "--extra fmpy" in message


# ---------------------------------------------------------------------------
# A variable a criterion names is recorded and read (U2 Task 1)
# ---------------------------------------------------------------------------


class TestFmpyTracksTheCriteriasVariables:
    #: What the fake FMU declares, and what it records for ``output=None``
    #: (fmpy's `_get_output_variables`: here, its one output ``h``).
    DECLARED = ("h", "v", "g", "unitTests.x[1]")
    DEFAULT = ("h",)

    def _backend(self, tmp_path, monkeypatch, captured):
        fake = types.ModuleType("fmpy")
        fake.read_model_description = lambda p: SimpleNamespace(
            modelVariables=[SimpleNamespace(name=n) for n in self.DECLARED]
        )
        fake.simulation = SimpleNamespace(
            _get_output_variables=lambda md: [
                SimpleNamespace(name=n) for n in self.DEFAULT
            ]
        )

        def sim(**kw):
            captured.update(kw)
            # fmpy 0.3's Recorder records only declared names
            wanted = kw["output"] or self.DEFAULT
            names = ["time", *(n for n in wanted if n in self.DECLARED)]
            arr = np.zeros(3, dtype=[(n, "f8") for n in names])
            arr["time"] = [0.0, 0.5, 1.0]
            return arr

        fake.simulate_fmu = sim
        monkeypatch.setitem(sys.modules, "fmpy", fake)
        from dstf.backends.fmpy.backend import FmpyBackend

        cfg = SimpleNamespace(
            work_dir=tmp_path / "work",
            source_type="fmu",
            backend_name="FMPy",
            parallel=1,
            timeout=60,
            diagnostic_variables=["CPUtime", "EventCounter"],
        )
        return FmpyBackend(cfg)

    def _test(self, tmp_path, patterns, *, unit_tests=False, absent=False):
        from dstf.comparison.tree_spec import parse_metric_tree

        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"fake fmu")
        children = [
            {"metric": "range", "variable": "g", "max_value": 20},
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": "max"},
            },
        ]
        if absent:
            children.append({"metric": "range", "variable": "nope", "max_value": 20})
        kw = dict(source="both", n_vars=1, x_expressions=["h"]) if unit_tests else {}
        return _tm(
            "Model",
            source_file=fmu,
            variable_patterns=patterns,
            metric_tree_spec=parse_metric_tree(
                {"combinator": "and", "children": children}
            ),
            **kw,
        )

    def test_no_patterns_records_the_default_set_and_the_tree_variable(
        self, tmp_path, monkeypatch
    ):
        """No patterns: fmpy's default set (``output=None``) is recorded
        still, with the tree's variable — a criterion does not take the
        FMU's outputs off the plot. Only the tracked are read."""
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, [])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success, rr.error_message
        assert captured["output"] == ["h", "g"]
        result = backend.read_result(test, "test_0001", rr)
        assert [(v.index, v.name) for v in result.variables] == [(1, "g")]

    def test_no_patterns_and_nothing_tracked_records_fmpys_default(
        self, tmp_path, monkeypatch
    ):
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"fake fmu")
        rr = backend.run_single_test(
            _tm("Model", source_file=fmu, variable_patterns=[]), "test_0001", 1, 1
        )
        assert rr.success, rr.error_message
        assert captured["output"] is None

    def test_patterns_first_then_the_tree_variable(self, tmp_path, monkeypatch):
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, ["h"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert captured["output"] == ["h", "g"]
        result = backend.read_result(test, "test_0001", rr)
        assert [(v.index, v.name) for v in result.variables] == [(1, "h"), (2, "g")]

    def test_the_unit_tests_variable_is_recorded_and_an_absent_name_is_not(
        self, tmp_path, monkeypatch
    ):
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, [], unit_tests=True, absent=True)
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success, rr.error_message
        assert "unitTests.x[1]" in captured["output"]
        assert "nope" not in captured["output"]
        result = backend.read_result(test, "test_0001", rr)
        assert [v.name for v in result.variables] == ["unitTests.x[1]", "g"]

    def test_the_index_order_is_unit_tests_then_patterns_then_tree(
        self, tmp_path, monkeypatch
    ):
        """Dymola's and OpenModelica's order (`extract_variables`):
        `migrate_names` reads indices 1..N as the UnitTests variables."""
        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, ["h"], unit_tests=True)
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert captured["output"][:3] == ["unitTests.x[1]", "h", "g"]
        result = backend.read_result(test, "test_0001", rr)
        assert [(v.index, v.name) for v in result.variables] == [
            (1, "unitTests.x[1]"),
            (2, "h"),
            (3, "g"),
        ]

    def test_a_criterion_on_a_name_the_file_lacks_warns_on_read(
        self, tmp_path, monkeypatch, caplog
    ):
        """As `extract_variables` warns for Dymola and OpenModelica."""
        import logging

        captured: dict = {}
        backend = self._backend(tmp_path, monkeypatch, captured)
        test = self._test(tmp_path, ["h"], absent=True)
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            backend.read_result(test, "test_0001", rr)
        warned = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert warned == ["Variable nope not found in simulation output for Model"]
