"""Tests for backend utilities: log parser, mat reader, variable pattern matching."""

import logging
import struct
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model as _tm
from dstf.backends.common.mat_reader import read_result_mat
from dstf.backends.common.result_variables import extract_variables
from dstf.backends.dymola.log_parser import parse_dslog
from dstf.backends.types import _pattern_to_regex, resolve_variable_patterns
from dstf.discovery.test_registry import TestModel

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_MAT = FIXTURES_DIR / "results" / "Dymola" / "dsres.mat"


# ---------------------------------------------------------------------------
# dslog.txt parser
# ---------------------------------------------------------------------------


class TestLogParser:
    def test_parse_fixture(self, sample_dslog):
        """Parse the real Dymola dslog.txt fixture."""
        stats = parse_dslog(sample_dslog)
        assert stats is not None

    def test_simulation_cpu_time(self, sample_dslog):
        """Extract simulation CPU time (ConstantTest is trivially fast)."""
        stats = parse_dslog(sample_dslog)
        assert "simulation" in stats
        assert "cpu_time_integration" in stats["simulation"]

    def test_nonexistent_file(self, tmp_path):
        """Nonexistent file returns None."""
        result = parse_dslog(tmp_path / "nonexistent.txt")
        assert result is None

    def test_empty_file(self, tmp_path):
        """Empty file returns None (no stats found)."""
        empty = tmp_path / "dslog.txt"
        empty.write_text("")
        result = parse_dslog(empty)
        assert result is None

    def test_rich_dslog(self, tmp_path):
        """Parse a dslog with simulation runtime stats."""
        dslog = tmp_path / "dslog.txt"
        content = (
            "Integration started at 0 using Dassl\n"
            "\n"
            "Integration terminated successfully at 10\n"
            "  CPU-time for integration      : 0.234 seconds\n"
            "  Number of result points       : 501\n"
            "  Number of accepted steps      : 500\n"
            "  Number of f-evaluations (dynamics): 1002\n"
            "  Number of Jacobian-evaluations: 10\n"
            "  Number of state events        : 3\n"
            "  Number of step events         : 0\n"
        )
        dslog.write_text(content)
        stats = parse_dslog(dslog)
        assert stats is not None
        assert stats["simulation"]["cpu_time_integration"] == pytest.approx(0.234)
        assert stats["simulation"]["jacobian_evaluations"] == 10
        assert stats["simulation"]["state_events"] == 3
        assert stats["simulation"]["accepted_steps"] == 500

    def test_translation_log(self):
        """Parse the real translation_log.txt fixture."""
        stats = parse_dslog(FIXTURES_DIR / "results" / "Dymola" / "translation_log.txt")
        assert stats is not None
        assert "translation" in stats
        t = stats["translation"]
        assert t["continuous_time_states"] == 2
        assert t["scalar_unknowns"] == 4
        assert t["scalar_equations"] == 4
        assert t["original_components"] == 2
        assert t["numerical_jacobians"] == 0
        assert t["state_names"] == ["x", "y"]
        # Empty system lists for simple model
        assert t["nonlinear"] == []
        assert t["linear"] == []
        # ... and their summaries, always written: 0 for an empty list.
        for key in ("nonlinear", "linear"):
            assert t[f"{key}_count"] == 0
            assert t[f"{key}_total"] == 0
            assert t[f"{key}_max"] == 0

    def test_every_empty_list_kind_writes_zero_summaries(self):
        """Both extractors write the summaries of BOTH lists of a pair — the
        original and the after-manipulation one — even when empty."""
        from dstf.backends.dymola.log_parser import (
            _extract_system_pair,
            _extract_system_pair_simple,
        )

        stats: dict = {}
        _extract_system_pair_simple(
            "Sizes of nonlinear systems of equations: { }\n"
            "Sizes after manipulation of the nonlinear systems: { }\n",
            "nonlinear",
            stats,
            "translation",
            "nonlinear",
        )
        _extract_system_pair(
            "Sizes of simplified homotopy nonlinear systems of equations: { }\n"
            "Sizes after manipulation of the simplified homotopy nonlinear "
            "systems: { }\n",
            r"Sizes of simplified homotopy nonlinear systems of equations",
            r"Sizes after manipulation of the simplified homotopy nonlinear systems",
            stats,
            "translation",
            "init_homotopy_nonlinear",
        )
        t = stats["translation"]
        for key in (
            "nonlinear",
            "nonlinear_after_manipulation",
            "init_homotopy_nonlinear",
            "init_homotopy_nonlinear_after_manipulation",
        ):
            assert t[key] == []
            assert (t[f"{key}_count"], t[f"{key}_total"], t[f"{key}_max"]) == (
                0,
                0,
                0,
            ), key

    def test_translation_log_complex(self):
        """Parse a complex translation log with init section and system sizes."""
        stats = parse_dslog(FIXTURES_DIR / "results_additional" / "translation_log.txt")
        assert stats is not None
        t = stats["translation"]

        # DAE size
        assert t["scalar_unknowns"] == 8715

        # Original model
        assert t["original_components"] == 940
        assert t["differentiated_variables"] == 200

        # Translated model fields
        assert t["free_parameters"] == 173
        assert t["parameter_depending"] == 934
        assert t["continuous_time_states"] == 120
        assert t["mixed_systems"] == 20

        # Simulation nonlinear systems
        assert isinstance(t["nonlinear"], list)
        assert t["nonlinear_count"] == 62
        assert t["nonlinear_max"] == 9
        assert t["nonlinear_total"] == sum(t["nonlinear"])
        assert t["nonlinear_after_manipulation_max"] == 4

        # Simulation linear systems
        assert t["linear_count"] == 40
        assert t["linear_max"] == 4
        assert t["linear_after_manipulation_total"] == 0

        # Initialization section
        assert t["init_mixed_systems"] == 2
        assert t["init_numerical_jacobians"] == 2
        assert t["init_nonlinear"] == [193, 193]
        assert t["init_nonlinear_count"] == 2
        assert t["init_nonlinear_total"] == 386
        assert t["init_nonlinear_after_manipulation"] == [41, 41]
        assert t["init_linear"] == [100, 100]
        assert t["init_linear_after_manipulation"] == [20, 20]

        # Homotopy nonlinear (initialization only)
        assert t["init_homotopy_nonlinear"] == [153, 153]
        assert t["init_homotopy_nonlinear_after_manipulation"] == [31, 31]

        # State names: 60 static states, no garbage
        assert len(t["state_names"]) == 60
        assert (
            t["state_names"][0] == "pipe_nParallel.pipe.flowModel.firstOrder_dps_K[1].y"
        )
        assert t["state_names"][-1] == "pipe_single.wall.Us[1, 10]"
        # Should NOT contain dynamic state selection text
        assert all("From set" not in s for s in t["state_names"])
        assert all("Dynamically" not in s for s in t["state_names"])

    def test_dslog_jacobian_with_whitespace(self):
        """Jacobian-evaluations with whitespace padding before colon."""
        stats = parse_dslog(FIXTURES_DIR / "results_additional" / "dslog.txt")
        assert stats is not None
        assert stats["simulation"]["jacobian_evaluations"] == 14


# ---------------------------------------------------------------------------
# MAT reader
# ---------------------------------------------------------------------------


class TestMatReader:
    def test_read_returns_dict(self):
        """read_result_mat returns a dict of variable name -> (time, values)."""
        data = read_result_mat(SAMPLE_MAT)
        assert data is not None
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_variables_present(self):
        """Expected variables exist in the parsed data."""
        data = read_result_mat(SAMPLE_MAT)
        assert "x" in data
        assert "y" in data
        assert "unitTests.x[1]" in data
        assert "unitTests.x[2]" in data

    def test_time_series_shape(self):
        """Time series variables have time and values with matching lengths."""
        data = read_result_mat(SAMPLE_MAT)
        # x and y are time series (from data_2), not parameters
        for name in ["x", "y", "unitTests.x[1]"]:
            time, values = data[name]
            assert len(time) == len(values), (
                f"{name}: time={len(time)}, values={len(values)}"
            )
            assert len(time) > 2  # Should have many points

    def test_parameter_is_constant(self):
        """Parameters (from data_1) have constant values across all time points."""
        data = read_result_mat(SAMPLE_MAT)
        _, values = data["unitTests.n"]
        # Parameter may have 2 points (data_1) or be broadcast — either way constant
        assert np.all(values == values[0])

    def test_time_monotonic(self):
        """Time array is monotonically non-decreasing (events may have duplicates)."""
        data = read_result_mat(SAMPLE_MAT)
        time, _ = data["x"]
        diffs = np.diff(time)
        assert np.all(diffs >= 0), "Time must be monotonically non-decreasing"

    def test_diagnostics_present(self):
        """CPUtime and EventCounter are available when OutputCPUtime was enabled."""
        data = read_result_mat(SAMPLE_MAT)
        assert "CPUtime" in data
        assert "EventCounter" in data

    def test_parameter_values(self):
        """Parameters (constant variables) have consistent values."""
        data = read_result_mat(SAMPLE_MAT)
        _, values = data["unitTests.n"]
        assert values[0] == 2.0  # n=2 in ConstantTest
        assert values[-1] == 2.0

    def test_nonexistent_file(self):
        """Nonexistent file returns None."""
        result = read_result_mat(Path("/nonexistent/file.mat"))
        assert result is None


# ---------------------------------------------------------------------------
# Diagnostic variable extraction
# ---------------------------------------------------------------------------


class TestDiagnosticExtraction:
    def _make_test(self):
        return TestModel(
            model_id="ModelicaTestingLib.Examples.ConstantTest",
            source_file=Path(""),
            source_package="ModelicaTestingLib.Examples",
            short_name="ConstantTest",
            n_vars=2,
            variable_patterns=[],
            source="unit_tests",
        )

    def test_extract_default_diagnostics(self):
        """CPUtime and EventCounter extracted as diagnostics."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = extract_variables(
            mat_data,
            test,
            ["CPUtime", "EventCounter"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "CPUtime" in diag_names
        assert "EventCounter" in diag_names

    def test_diagnostics_not_in_variables(self):
        """Diagnostic variables don't appear in the regular variables list."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = extract_variables(
            mat_data,
            test,
            ["CPUtime", "EventCounter"],
        )
        var_names = [v.name for v in variables]
        assert "CPUtime" not in var_names
        assert "EventCounter" not in var_names

    def test_custom_diagnostic_variable(self):
        """Custom diagnostic variable name is extracted if present in mat data."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        # "x" exists in the mat data — treating it as diagnostic
        variables, diagnostics = extract_variables(
            mat_data,
            test,
            ["x"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "x" in diag_names

    def test_missing_diagnostic_skipped(self):
        """Diagnostic variable not in mat data is silently skipped."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = extract_variables(
            mat_data,
            test,
            ["NonExistentVar"],
        )
        assert len(diagnostics) == 0

    def test_empty_diagnostic_list(self):
        """Empty diagnostic list produces no diagnostics."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = extract_variables(mat_data, test, [])
        assert len(diagnostics) == 0


# ---------------------------------------------------------------------------
# Variable pattern matching
# ---------------------------------------------------------------------------


class TestVariablePatterns:
    """Tests for resolve_variable_patterns and _pattern_to_regex."""

    def test_exact_match(self):
        available = ["pipe.T[1]", "pipe.T[2]", "tank.level"]
        resolved = resolve_variable_patterns(["pipe.T[1]"], available)
        assert resolved == ["pipe.T[1]"]

    def test_wildcard_star(self):
        available = ["pipe.T[1]", "pipe.T[2]", "pipe.m_flow", "tank.level"]
        resolved = resolve_variable_patterns(["pipe.T*"], available)
        assert set(resolved) == {"pipe.T[1]", "pipe.T[2]"}

    def test_wildcard_question(self):
        available = ["x1", "x2", "x10", "y1"]
        resolved = resolve_variable_patterns(["x?"], available)
        assert set(resolved) == {"x1", "x2"}

    def test_brackets_literal(self):
        """Square brackets are treated as literal, not character classes."""
        available = ["pipe.T[1]", "pipe.T[2]", "pipe.T1"]
        resolved = resolve_variable_patterns(["pipe.T[1]"], available)
        assert resolved == ["pipe.T[1]"]

    def test_star_matches_all(self):
        available = ["x", "y", "z"]
        resolved = resolve_variable_patterns(["*"], available)
        assert set(resolved) == {"x", "y", "z"}

    def test_multiple_patterns(self):
        available = ["pipe.T[1]", "pipe.m_flow", "tank.level"]
        resolved = resolve_variable_patterns(["pipe.*", "tank.*"], available)
        assert set(resolved) == {"pipe.T[1]", "pipe.m_flow", "tank.level"}

    def test_no_match(self):
        available = ["pipe.T[1]", "tank.level"]
        resolved = resolve_variable_patterns(["nonexistent*"], available)
        assert resolved == []

    def test_patterns_resolve_in_the_order_they_were_written(self):
        """Explicit names as written, each glob's matches in the file's
        order, a name matched twice where it was first. Sorted, ``x[10]``
        came before ``x[2]``, and a test with no tree drew its plots — and
        an accept wrote its ``variables`` — alphabetically."""
        available = ["b", "x[1]", "x[2]", "x[10]", "a"]
        got = resolve_variable_patterns(["b", "x[*", "a", "b"], available)
        assert got == ["b", "x[1]", "x[2]", "x[10]", "a"]
        got = resolve_variable_patterns(["a", "*"], available)
        assert got == ["a", "b", "x[1]", "x[2]", "x[10]"]

    def test_pattern_to_regex_dots(self):
        """Dots in patterns are escaped (literal, not regex any-char)."""
        import re

        pattern = _pattern_to_regex("pipe.T[1]")
        assert re.match(pattern, "pipe.T[1]")
        assert not re.match(pattern, "pipeXT[1]")  # dot shouldn't match X

    def test_dedup_across_patterns(self):
        """Variables matched by multiple patterns appear only once."""
        available = ["pipe.T[1]", "pipe.T[2]"]
        resolved = resolve_variable_patterns(["pipe.*", "pipe.T*"], available)
        assert len(resolved) == 2  # No duplicates


# ---------------------------------------------------------------------------
# Simulator registry
# ---------------------------------------------------------------------------

from dstf.backends import (
    _REGISTRY,
    Backend,
    _import_builtin_backend,
    get_backend,
    register,
)


class TestBackendRegistry:
    def test_dymola_registered_after_import(self):
        """Importing the dymola module registers 'Dymola'."""
        _import_builtin_backend("Dymola")
        assert "Dymola" in _REGISTRY

    def test_register_decorator(self):
        """@register adds a class to the registry."""

        @register("TestBackend")
        class _TestBackend(Backend):
            def read_result(self, test, test_key, run_result):
                pass

        assert "TestBackend" in _REGISTRY
        assert _REGISTRY["TestBackend"] is _TestBackend
        # Clean up
        del _REGISTRY["TestBackend"]

    def test_unknown_backend_raises(self):
        """get_backend raises ValueError for unknown backends."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.backend = "NoSuchSimulator"
        config.backend_name = "NoSuchSimulator"

        with pytest.raises(ValueError, match="Unsupported backend"):
            get_backend(config)


# ---------------------------------------------------------------------------
# DymolaConfig
# ---------------------------------------------------------------------------

from dstf.backends.dymola.backend import DymolaConfig


class TestDymolaConfig:
    def test_from_config_defaults(self):
        """DymolaConfig.from_config extracts Dymola-specific fields."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.show_ide = True
        config.backend_setup = ["Foo := true;"]
        config.diagnostic_variables = ["CPUtime", "CustomVar"]

        dc = DymolaConfig.from_config(config)
        assert dc.show_ide is True
        assert dc.backend_setup == ["Foo := true;"]
        assert dc.diagnostic_variables == ["CPUtime", "CustomVar"]

    def test_from_config_copies_lists(self):
        """Lists are copied, not shared references."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.show_ide = False
        original_setup = ["Cmd1;"]
        config.backend_setup = original_setup
        config.diagnostic_variables = ["CPUtime"]

        dc = DymolaConfig.from_config(config)
        assert dc.backend_setup == ["Cmd1;"]
        assert dc.backend_setup is not original_setup

    def test_frozen(self):
        """DymolaConfig is immutable."""
        dc = DymolaConfig()
        with pytest.raises(AttributeError):
            dc.show_ide = True

    def test_defaults(self):
        """Default values match expected Dymola defaults."""
        dc = DymolaConfig()
        assert dc.show_ide is False
        assert dc.backend_setup == []
        assert "CPUtime" in dc.diagnostic_variables
        assert "EventCounter" in dc.diagnostic_variables


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# * Finding 23 — shared subprocess-output decode helper;
#   ``TimeoutExpired.stdout/.stderr`` are bytes even with ``text=True``, so
#   the timeout handlers must not TypeError on them.
# * Finding 75 — a whitespace-only stderr tail must not IndexError.
# * Finding 74 — mat_reader Aclass (binNormal refused) + dataInfo column-0
#   guards (column 0 used to wrap to the last row = the wrong variable).
# * Finding 22 (+76-part) — Dymola batch timeout: tree-kill the process
#   group, then salvage the tests that verifiably completed on disk.
# =========================================================================


def _write_mat4(path: Path, blocks: list[tuple[str, np.ndarray, int]]) -> None:
    """Write a minimal MAT4 file: list of (name, 2D array, mopt)."""
    dtype_by_p = {0: "<f8", 2: "<i4", 5: "<u1"}
    with open(path, "wb") as f:
        for name, arr, mopt in blocks:
            arr = np.asarray(arr)
            mrows, ncols = arr.shape
            name_bytes = name.encode("ascii") + b"\x00"
            f.write(struct.pack("<5i", mopt, mrows, ncols, 0, len(name_bytes)))
            f.write(name_bytes)
            dt = dtype_by_p[(mopt % 100) // 10]
            f.write(arr.astype(dt).tobytes(order="F"))


def _char_matrix(strings: list[str]) -> np.ndarray:
    """Char-code matrix stored (max_len, n_strings) — the on-disk binTrans
    orientation the reader transposes back with ``.T``."""
    max_len = max(len(s) for s in strings)
    m = np.zeros((max_len, len(strings)), dtype=np.uint8)
    for j, s in enumerate(strings):
        for i, ch in enumerate(s):
            m[i, j] = ord(ch)
    return m


def _write_dsres_mat(
    path: Path,
    aclass_kind: str = "binTrans",
    y_data_col: int = 0,
) -> None:
    """Synthesize a tiny DSresult mat: variables time, x, y.

    ``y_data_col`` is y's dataInfo column entry — 0 reproduces finding 74's
    wrap-to-last-row bug; a positive value makes y a normal variable.
    """
    n = 5
    t = np.linspace(0.0, 1.0, n)
    x = 2.0 * t
    data_2 = np.vstack([t, x])  # rows: 0=time, 1=x
    # dataInfo rows per var: (matrix_idx, col, interp, protected)
    info_rows = np.array(
        [
            [0, 1, 0, -1],  # time
            [2, 2, 0, -1],  # x → data_2 col index 2 → row 1
            [2, y_data_col, 0, -1],  # y
        ],
        dtype=np.int32,
    )
    _write_mat4(
        path,
        [
            ("Aclass", _char_matrix(["Atrajectory", "1.1", "", aclass_kind]), 51),
            ("name", _char_matrix(["time", "x", "y"]), 51),
            ("dataInfo", info_rows.T, 20),  # on-disk (4, n_vars)
            ("data_2", data_2, 0),
        ],
    )


# ---------------------------------------------------------------------------
# Finding 23 — shared subprocess-output decode helper
# ---------------------------------------------------------------------------


class TestDecodeOutput:
    def test_none_becomes_empty(self):
        from dstf.backends.common.proc_output import decode_output

        assert decode_output(None) == ""

    def test_bytes_decoded_utf8(self):
        from dstf.backends.common.proc_output import decode_output

        assert decode_output("héllo".encode()) == "héllo"

    def test_invalid_bytes_replaced_not_raised(self):
        from dstf.backends.common.proc_output import decode_output

        out = decode_output(b"ok \xff\xfe bad")
        assert "ok" in out and "bad" in out

    def test_str_passthrough(self):
        from dstf.backends.common.proc_output import decode_output

        assert decode_output("already text") == "already text"


class TestTimeoutExpiredBytesHandling:
    """Finding 23: TimeoutExpired.stdout/.stderr are bytes even with
    text=True; the timeout handlers must not TypeError on them."""

    def test_julia_backend_timeout_with_bytes_output(self, tmp_path, monkeypatch):
        from dstf.backends.julia import backend as jr

        fake_julia = tmp_path / "julia"
        fake_julia.write_text("#!/bin/sh\n")
        user = tmp_path / "T.jl"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            backend_path=str(fake_julia),
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        backend = jr.JuliaBackend(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["julia"], timeout=1, output=b"partial out", stderr=b"partial err"
            )

        monkeypatch.setattr(jr.subprocess, "run", fake_run)
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        stdout_txt = (tmp_path / "work" / "test_0001" / "julia_stdout.txt").read_text()
        assert "partial out" in stdout_txt

    def test_python_backend_timeout_with_bytes_output(self, tmp_path, monkeypatch):
        from dstf.backends.python import backend as pr

        user = tmp_path / "T.py"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            backend_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        backend = pr.PythonBackend(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["python"], timeout=1, output=b"po", stderr=b"pe"
            )

        monkeypatch.setattr(pr.subprocess, "run", fake_run)
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        assert (
            "po" in (tmp_path / "work" / "test_0001" / "python_stdout.txt").read_text()
        )

    def test_openmodelica_backend_timeout_with_bytes_output(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.openmodelica.backend import OpenModelicaBackend
        from dstf.config import Config

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        backend = OpenModelicaBackend(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["omc"], timeout=1, output=b"omc partial", stderr=None
            )

        monkeypatch.setattr(
            "dstf.backends.openmodelica.backend.subprocess.run", fake_run
        )
        test = _tm("Lib.A", variable_patterns=["x"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        stdout_txt = (tmp_path / "work" / "test_0001" / "omc_stdout.txt").read_text()
        assert "omc partial" in stdout_txt
        assert "[TimeoutExpired]" in stdout_txt


# ---------------------------------------------------------------------------
# Finding 75 — whitespace-only stderr tail must not IndexError
# ---------------------------------------------------------------------------


class TestStderrTailGuard:
    def test_julia_whitespace_only_stderr(self, tmp_path, monkeypatch):
        from dstf.backends.julia import backend as jr

        fake_julia = tmp_path / "julia"
        fake_julia.write_text("#!/bin/sh\n")
        user = tmp_path / "T.jl"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            backend_path=str(fake_julia),
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        backend = jr.JuliaBackend(cfg)
        monkeypatch.setattr(
            jr.subprocess,
            "run",
            lambda *a, **kw: CompletedProcess(
                args=a, returncode=1, stdout="", stderr="   \n\t\n"
            ),
        )
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert "returned 1" in (rr.error_message or "")

    def test_python_whitespace_only_stderr(self, tmp_path, monkeypatch):
        from dstf.backends.python import backend as pr

        user = tmp_path / "T.py"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            backend_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        backend = pr.PythonBackend(cfg)
        monkeypatch.setattr(
            pr.subprocess,
            "run",
            lambda *a, **kw: CompletedProcess(
                args=a, returncode=1, stdout="", stderr="  \n \n"
            ),
        )
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = backend.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert "returned 1" in (rr.error_message or "")


# ---------------------------------------------------------------------------
# Finding 74 — mat_reader Aclass + dataInfo column-0 guards
# ---------------------------------------------------------------------------


class TestMatReaderGuards:
    def test_binnormal_layout_refused(self, tmp_path, caplog):
        from dstf.backends.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binNormal", y_data_col=2)
        with caplog.at_level(logging.ERROR, logger="dstf.backends.common.mat_reader"):
            result = read_result_mat(mat)
        assert result is None
        assert "binNormal" in caplog.text

    def test_binnormal_layout_refused_in_name_listing(self, tmp_path, caplog):
        from dstf.backends.common.mat_reader import list_result_mat_variables

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binNormal", y_data_col=2)
        with caplog.at_level(logging.ERROR, logger="dstf.backends.common.mat_reader"):
            names = list_result_mat_variables(mat)
        assert names is None
        assert "binNormal" in caplog.text

    def test_binnormal_layout_refused_in_time_extents(self, tmp_path):
        """data_2 row 0 is time only in binTrans — a binNormal file yielded
        garbage extents (and thus a bogus 'stopped early' verdict)."""
        from dstf.backends.common.mat_reader import read_mat_time_extents

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binNormal", y_data_col=2)
        assert read_mat_time_extents(mat) is None

    def test_bintrans_time_extents_still_read(self, tmp_path):
        from dstf.backends.common.mat_reader import read_mat_time_extents

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=2)
        assert read_mat_time_extents(mat) == (0.0, 1.0)

    def test_mat_reaches_time_verdicts(self, tmp_path):
        """mat_reaches_time: reached / stopped-short / unreadable (D99).

        The success-path belt and the disk rescue share this helper so
        their verdicts about the same file can never disagree."""
        from dstf.backends.common.mat_reader import mat_reaches_time

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat)  # binTrans fixture, time 0.0 .. 1.0
        reached, last = mat_reaches_time(mat, 1.0)
        assert reached is True and last == pytest.approx(1.0)
        reached, last = mat_reaches_time(mat, 2.0)
        assert reached is False and last == pytest.approx(1.0)
        reached, last = mat_reaches_time(tmp_path / "missing.mat", 1.0)
        assert reached is False and last is None

    def test_bintrans_still_reads_normally(self, tmp_path):
        from dstf.backends.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=2)
        result = read_result_mat(mat)
        assert result is not None
        assert "x" in result and "y" in result
        _, x_vals = result["x"]
        np.testing.assert_allclose(x_vals, 2.0 * np.linspace(0.0, 1.0, 5))

    def test_datainfo_column_zero_skips_variable(self, tmp_path):
        """col 0 used to become index -1 → last row's data (the WRONG
        variable) returned under the requested name."""
        from dstf.backends.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=0)
        result = read_result_mat(mat)
        assert result is not None
        assert "x" in result
        assert "y" not in result  # skipped, not silently wrong

    def test_datainfo_column_zero_with_selection(self, tmp_path):
        from dstf.backends.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=0)
        result = read_result_mat(mat, variable_names={"x", "y"})
        assert result is not None
        assert "x" in result
        assert "y" not in result


# ---------------------------------------------------------------------------
# Finding 22 (+76-part) — Dymola batch timeout: tree-kill + disk salvage
# ---------------------------------------------------------------------------


class _FakeDymolaProc:
    def __init__(self):
        self.pid = 2**28 + 12345  # (almost certainly) nonexistent pid
        self.communicate_calls: list[float | None] = []
        self.killed = False
        self.kill_calls = 0
        self.stdout = None
        self.stderr = None

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired(cmd="dymola", timeout=timeout)
        return (b"", b"")

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.kill_calls += 1
        self.killed = True


class TestDymolaBatchTimeout:
    def test_tree_kill_and_salvage_of_completed_tests(self, tmp_path, monkeypatch):
        from dstf.backends.dymola import backend as dr

        wd = tmp_path / "work"
        wd.mkdir()
        cfg = SimpleNamespace(
            show_ide=False,
            backend_setup=[],
            diagnostic_variables=["CPUtime"],
            work_dir=wd,
            backend_path="dymola-fake",
            timeout=1,
            parallel=1,
        )
        backend = dr.DymolaBackend(cfg)

        test_a = _tm("Lib.A", stop_time=1.0)
        test_b = _tm("Lib.B", stop_time=1.0)
        items = [(test_a, "test_0001"), (test_b, "test_0002")]

        # Test A verifiably completed on disk before the batch deadline.
        dir_a = wd / "test_0001"
        dir_a.mkdir()
        n = 5
        t = np.linspace(0.0, 1.0, n)
        _write_mat4(dir_a / "dsres.mat", [("data_2", np.vstack([t, 2 * t]), 0)])
        (dir_a / "dsfinal.txt").write_text("done")
        # Test B produced nothing.
        (wd / "test_0002").mkdir()

        fake = _FakeDymolaProc()
        monkeypatch.setattr(dr.subprocess, "Popen", lambda *a, **kw: fake)
        # The batch timeout path delegates to the shared tree-killer (the
        # worker watchdog uses the same one); record the call rather than
        # letting it walk a nonexistent PID.
        killed: list[tuple[int, str | None]] = []
        monkeypatch.setattr(
            dr,
            "kill_process_tree",
            lambda pid, name_hint=None: killed.append((pid, name_hint)),
        )

        startup = wd / "startup.mos"
        shutdown = wd / "shutdown.mos"
        startup.write_text("// stub")
        shutdown.write_text("// stub")

        results = backend._run_batch(items, startup, shutdown, 0, 2)

        # Tree-kill happened, and the post-kill communicate was bounded.
        assert killed == [(fake.pid, "dymola")]
        assert len(fake.communicate_calls) == 2
        assert fake.communicate_calls[1] is not None
        # The tree kill was a no-op here (that is what the stub does), so the
        # owned Popen must still have been killed — otherwise dymosim keeps
        # the pipes and communicate() blocks, which is finding 22 all over.
        assert fake.killed is True
        assert fake.kill_calls == 1

        by_id = {r.model_id: r for r in results}
        assert by_id["Lib.A"].success is True  # salvaged from disk
        assert by_id["Lib.A"].timed_out is False
        assert by_id["Lib.B"].success is False
        assert by_id["Lib.B"].timed_out is True

    def test_live_process_is_not_double_killed(self, tmp_path, monkeypatch):
        """When the tree kill lands, the Popen fallback stays out of it."""
        from dstf.backends.dymola import backend as dr

        wd = tmp_path / "work"
        wd.mkdir()
        cfg = SimpleNamespace(
            show_ide=False,
            backend_setup=[],
            diagnostic_variables=["CPUtime"],
            work_dir=wd,
            backend_path="dymola-fake",
            timeout=1,
            parallel=1,
        )
        backend = dr.DymolaBackend(cfg)
        items = [(_tm("Lib.A", stop_time=1.0), "test_0001")]
        (wd / "test_0001").mkdir()

        fake = _FakeDymolaProc()
        monkeypatch.setattr(dr.subprocess, "Popen", lambda *a, **kw: fake)
        monkeypatch.setattr(
            dr,
            "kill_process_tree",
            lambda pid, name_hint=None: setattr(fake, "killed", True),
        )

        startup = wd / "startup.mos"
        shutdown = wd / "shutdown.mos"
        startup.write_text("// stub")
        shutdown.write_text("// stub")

        backend._run_batch(items, startup, shutdown, 0, 1)

        assert fake.killed is True  # by the tree kill
        assert fake.kill_calls == 0  # poll() reads dead, so no second kill


# =========================================================================
# C-v round 1, item 9 — batch-Dymola run provenance.
#
# The batch lane stamped tool_version null: unlike the persistent lane it has
# no live session to ask, and launching a Dymola purely to probe would cost a
# licence seat. The startup .mos script it already runs reports the version to
# a file, which the backend reads back after a batch returns.
# =========================================================================


class TestBatchDymolaToolVersion:
    def _cfg(self, tmp_path):
        return SimpleNamespace(
            work_dir=tmp_path,
            dependencies=[],
            library_dir=tmp_path / "Lib",
            backend_setup=[],
        )

    def test_startup_mos_reports_the_dymola_version_to_a_file(self, tmp_path):
        from dstf.backends.dymola.backend import (
            TOOL_VERSION_FILENAME,
            DymolaConfig,
            _generate_startup_mos,
        )

        script = _generate_startup_mos(self._cfg(tmp_path), DymolaConfig())
        text = script.read_text(encoding="utf-8")
        assert "DymolaVersion()" in text
        assert TOOL_VERSION_FILENAME in text
        # It must land in the work dir the backend reads back from.
        assert (tmp_path / TOOL_VERSION_FILENAME).as_posix() in text

    def test_a_stale_version_file_is_cleared_before_the_run(self, tmp_path):
        """A previous run's version must never be read back as this run's —
        the user may have upgraded Dymola between them."""
        from dstf.backends.dymola.backend import (
            TOOL_VERSION_FILENAME,
            DymolaConfig,
            _generate_startup_mos,
        )

        (tmp_path / TOOL_VERSION_FILENAME).write_text("Dymola 2019\n", encoding="utf-8")
        _generate_startup_mos(self._cfg(tmp_path), DymolaConfig())
        assert not (tmp_path / TOOL_VERSION_FILENAME).exists()

    def test_read_back_takes_the_first_line_and_degrades_to_none(self, tmp_path):
        """Every worker's startup appends the same string. No file (MSL not
        loaded, Dymola never started) leaves tool_version null, as before."""
        from dstf.backends.dymola.backend import (
            TOOL_VERSION_FILENAME,
            _read_startup_tool_version,
        )

        assert _read_startup_tool_version(tmp_path) is None
        (tmp_path / TOOL_VERSION_FILENAME).write_text(
            "\nDymola 2026x Refresh 1\nDymola 2026x Refresh 1\n", encoding="utf-8"
        )
        assert _read_startup_tool_version(tmp_path) == "Dymola 2026x Refresh 1"


# =========================================================================
# D138 amended — Dymola's logs are the fallback reader of a result's run
# statistics. A result written before run_stats.json existed still has
# dslog.txt / translation_log.txt beside its dsres.mat; read_result reads
# them when no kept run statistics came with the result.
# =========================================================================


class TestDymolaStatisticsFromLogs:
    def _backend(self, work_dir):
        from dstf.backends.dymola import backend as dr

        cfg = SimpleNamespace(
            show_ide=False,
            backend_setup=[],
            diagnostic_variables=["CPUtime"],
            work_dir=work_dir,
            backend_path="dymola-fake",
            timeout=1,
            parallel=1,
        )
        return dr.DymolaBackend(cfg)

    def _test_dir(self, work_dir, *, logs=True):
        import shutil

        test_dir = work_dir / "test_0001"
        test_dir.mkdir(parents=True)
        shutil.copy(SAMPLE_MAT, test_dir / "dsres.mat")
        if logs:
            for name in ("dslog.txt", "translation_log.txt"):
                shutil.copy(FIXTURES_DIR / "results" / "Dymola" / name, test_dir / name)
        return test_dir

    def test_logs_are_read_when_the_run_kept_no_statistics(self, tmp_path):
        self._test_dir(tmp_path)
        test = _tm("Lib.A", variable_patterns=["x"])
        stats = self._backend(tmp_path).read_result(test, "test_0001", None).statistics
        assert stats is not None
        assert stats["translation"]["continuous_time_states"] is not None
        assert "cpu_time_integration" in stats["simulation"]
        # The diagnostics still fold in after the logs.
        assert "CPUtime" in stats
        # The logs hold no phase walls; nothing invents them.
        assert "timing" not in stats

    def test_kept_run_statistics_win_over_the_logs(self, tmp_path):
        from dstf.backends.base import kept_run_result
        from dstf.backends.types import TestRunResult
        from dstf.storage.run_stats import write_run_stats

        test_dir = self._test_dir(tmp_path)
        write_run_stats(
            test_dir,
            TestRunResult(
                model_id="Lib.A",
                test_key="test_0001",
                success=True,
                elapsed=1.0,
                statistics={"simulation": {"cpu_time_integration": 99.0}},
            ),
        )
        test = _tm("Lib.A", variable_patterns=["x"])
        kept = kept_run_result(test_dir, "Lib.A", "test_0001")
        stats = self._backend(tmp_path).read_result(test, "test_0001", kept).statistics
        assert stats["simulation"] == {"cpu_time_integration": 99.0}
        assert "translation" not in stats

    def test_translation_log_alone_is_read(self, tmp_path):
        """A test that translated and never simulated leaves only
        translation_log.txt: its statistics are the translation's."""
        import shutil

        from dstf.backends.dymola.log_parser import parse_test_logs

        name = "translation_log.txt"
        shutil.copy(FIXTURES_DIR / "results" / "Dymola" / name, tmp_path / name)
        stats = parse_test_logs(tmp_path)
        assert stats is not None
        assert stats["translation"]["continuous_time_states"] is not None
        assert "simulation" not in stats

    def test_a_malformed_log_does_not_abort_the_read(self, tmp_path, capsys):
        """A log whose number does not parse costs THAT test its statistics
        from that file, with one warning naming the file — never the read
        of the result, or of the suite."""
        test_dir = self._test_dir(tmp_path, logs=False)
        dslog = test_dir / "dslog.txt"
        dslog.write_text("CPU-time for integration      : 1.2.3 s\n", encoding="utf-8")
        test = _tm("Lib.A", variable_patterns=["x"])
        result = self._backend(tmp_path).read_result(test, "test_0001", None)
        assert result.success
        assert "simulation" not in (result.statistics or {})
        warns = [
            line
            for line in capsys.readouterr().err.splitlines()
            if line.startswith("# WARN:")
        ]
        assert len(warns) == 1 and str(dslog) in warns[0]

    def test_a_malformed_log_keeps_the_other_logs_statistics(self, tmp_path, capsys):
        import shutil

        from dstf.backends.dymola.log_parser import parse_test_logs

        name = "translation_log.txt"
        shutil.copy(FIXTURES_DIR / "results" / "Dymola" / name, tmp_path / name)
        (tmp_path / "dslog.txt").write_text(
            "CPU-time for integration      : 1.2.3 s\n", encoding="utf-8"
        )
        stats = parse_test_logs(tmp_path)
        assert stats is not None and "translation" in stats
        assert "simulation" not in stats
        assert "dslog.txt" in capsys.readouterr().err

    def test_no_logs_reads_as_before(self, tmp_path):
        self._test_dir(tmp_path, logs=False)
        test = _tm("Lib.A", variable_patterns=["x"])
        stats = self._backend(tmp_path).read_result(test, "test_0001", None).statistics
        assert stats is not None
        assert "translation" not in stats
        assert "simulation" not in stats

    def test_batch_run_merges_both_logs(self, tmp_path, monkeypatch):
        import shutil

        from dstf.backends.dymola import backend as dr

        backend = self._backend(tmp_path)
        test_dir = tmp_path / "test_0001"
        test_dir.mkdir()
        t = np.linspace(0.0, 1.0, 5)
        _write_mat4(test_dir / "dsres.mat", [("data_2", np.vstack([t, 2 * t]), 0)])
        (test_dir / "dsfinal.txt").write_text("done")
        for name in ("dslog.txt", "translation_log.txt"):
            shutil.copy(FIXTURES_DIR / "results" / "Dymola" / name, test_dir / name)

        class _DoneProc:
            pid = 1

            def communicate(self, timeout=None):
                return (b"", b"")

        monkeypatch.setattr(dr.subprocess, "Popen", lambda *a, **kw: _DoneProc())
        startup = tmp_path / "startup.mos"
        shutdown = tmp_path / "shutdown.mos"
        startup.write_text("// stub")
        shutdown.write_text("// stub")

        items = [(_tm("Lib.A", stop_time=1.0), "test_0001")]
        (result,) = backend._run_batch(items, startup, shutdown, 0, 1)
        assert result.success is True
        assert "continuous_time_states" in result.statistics["translation"]
        assert "cpu_time_integration" in result.statistics["simulation"]

    def _completed_test_dir(self, work_dir):
        """A test Dymola finished — its result reaches stop_time 1.0 and
        dsfinal.txt exists — with both logs beside it."""
        import shutil

        test_dir = work_dir / "test_0001"
        test_dir.mkdir()
        t = np.linspace(0.0, 1.0, 5)
        _write_mat4(test_dir / "dsres.mat", [("data_2", np.vstack([t, 2 * t]), 0)])
        (test_dir / "dsfinal.txt").write_text("done")
        for name in ("dslog.txt", "translation_log.txt"):
            shutil.copy(FIXTURES_DIR / "results" / "Dymola" / name, test_dir / name)
        return test_dir

    def test_the_batch_timeout_salvage_merges_both_logs(self, tmp_path, monkeypatch):
        """The batch timed out, and the test had finished before it: the
        salvaged result carries both logs' statistics — the salvage read
        dslog.txt alone."""
        from dstf.backends.dymola import backend as dr

        backend = self._backend(tmp_path)
        self._completed_test_dir(tmp_path)

        class _SlowProc:
            pid = 1
            calls = 0

            def communicate(self, timeout=None):
                _SlowProc.calls += 1
                if _SlowProc.calls == 1:
                    raise dr.subprocess.TimeoutExpired("dymola", timeout)
                return (b"", b"")

            def poll(self):
                return 0

        monkeypatch.setattr(dr.subprocess, "Popen", lambda *a, **kw: _SlowProc())
        monkeypatch.setattr(dr, "kill_process_tree", lambda *a, **kw: None)
        startup = tmp_path / "startup.mos"
        shutdown = tmp_path / "shutdown.mos"
        startup.write_text("// stub")
        shutdown.write_text("// stub")

        items = [(_tm("Lib.A", stop_time=1.0), "test_0001")]
        (result,) = backend._run_batch(items, startup, shutdown, 0, 1)
        assert _SlowProc.calls == 2  # it did time out
        assert result.success is True
        assert "continuous_time_states" in result.statistics["translation"]
        assert "cpu_time_integration" in result.statistics["simulation"]

    def test_the_persistent_workers_disk_rescue_merges_both_logs(self, tmp_path):
        """The worker's interface died after Dymola finished the test: the
        result rescued from disk carries both logs' statistics."""
        from dstf.backends.dymola.persistent_backend import DymolaWorker

        self._completed_test_dir(tmp_path)
        worker = DymolaWorker(
            worker_id=0,
            config=SimpleNamespace(
                work_dir=tmp_path,
                backend_path=None,
                dependencies=[],
                library_dir=tmp_path,
            ),
            dymola_config=SimpleNamespace(
                show_ide=False, backend_setup=[], diagnostic_variables=[]
            ),
            dymola_interface_cls=object,
        )

        def run_test(test, test_key, progress=None):
            raise ConnectionError("interface dropped")

        worker.run_test = run_test  # type: ignore[method-assign]
        result = worker.run_test_with_timeout(
            _tm("Lib.A", stop_time=1.0), "test_0001", timeout=10
        )
        assert result.success is True
        assert "continuous_time_states" in result.statistics["translation"]
        assert "cpu_time_integration" in result.statistics["simulation"]
