"""Tests for simulator utilities: log parser, mat reader, variable pattern matching."""

from pathlib import Path

import numpy as np
import pytest

from dstf.simulators.dymola.log_parser import parse_dslog
from dstf.simulators.common.mat_reader import read_result_mat
from dstf.simulators.dymola.runner import _extract_variables
from dstf.simulators.base import resolve_variable_patterns, _pattern_to_regex
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
        assert t["state_names"][0] == "pipe_nParallel.pipe.flowModel.firstOrder_dps_K[1].y"
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
            assert len(time) == len(values), f"{name}: time={len(time)}, values={len(values)}"
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
        variables, diagnostics = _extract_variables(
            mat_data, test, ["CPUtime", "EventCounter"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "CPUtime" in diag_names
        assert "EventCounter" in diag_names

    def test_diagnostics_not_in_variables(self):
        """Diagnostic variables don't appear in the regular variables list."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(
            mat_data, test, ["CPUtime", "EventCounter"],
        )
        var_names = [v.name for v in variables]
        assert "CPUtime" not in var_names
        assert "EventCounter" not in var_names

    def test_custom_diagnostic_variable(self):
        """Custom diagnostic variable name is extracted if present in mat data."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        # "x" exists in the mat data — treating it as diagnostic
        variables, diagnostics = _extract_variables(
            mat_data, test, ["x"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "x" in diag_names

    def test_missing_diagnostic_skipped(self):
        """Diagnostic variable not in mat data is silently skipped."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(
            mat_data, test, ["NonExistentVar"],
        )
        assert len(diagnostics) == 0

    def test_empty_diagnostic_list(self):
        """Empty diagnostic list produces no diagnostics."""
        mat_data = read_result_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(mat_data, test, [])
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

from dstf.simulators import (
    _REGISTRY,
    register,
    get_runner,
    _import_builtin_backend,
    SimulatorRunner,
)


class TestSimulatorRegistry:
    def test_dymola_registered_after_import(self):
        """Importing the dymola module registers 'Dymola'."""
        _import_builtin_backend("Dymola")
        assert "Dymola" in _REGISTRY

    def test_register_decorator(self):
        """@register adds a class to the registry."""
        @register("TestBackend")
        class _TestRunner(SimulatorRunner):
            def read_result(self, test, test_key, run_result):
                pass

        assert "TestBackend" in _REGISTRY
        assert _REGISTRY["TestBackend"] is _TestRunner
        # Clean up
        del _REGISTRY["TestBackend"]

    def test_unknown_backend_raises(self):
        """get_runner raises ValueError for unknown backends."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.simulator_backend = "NoSuchSimulator"
        config.simulator = "NoSuchSimulator"

        with pytest.raises(ValueError, match="Unsupported simulator backend"):
            get_runner(config)


# ---------------------------------------------------------------------------
# DymolaConfig
# ---------------------------------------------------------------------------

from dstf.simulators.dymola.runner import DymolaConfig


class TestDymolaConfig:
    def test_from_config_defaults(self):
        """DymolaConfig.from_config extracts Dymola-specific fields."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.show_ide = True
        config.simulator_setup = ["Foo := true;"]
        config.diagnostic_variables = ["CPUtime", "CustomVar"]

        dc = DymolaConfig.from_config(config)
        assert dc.show_ide is True
        assert dc.simulator_setup == ["Foo := true;"]
        assert dc.diagnostic_variables == ["CPUtime", "CustomVar"]

    def test_from_config_copies_lists(self):
        """Lists are copied, not shared references."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.show_ide = False
        original_setup = ["Cmd1;"]
        config.simulator_setup = original_setup
        config.diagnostic_variables = ["CPUtime"]

        dc = DymolaConfig.from_config(config)
        assert dc.simulator_setup == ["Cmd1;"]
        assert dc.simulator_setup is not original_setup

    def test_frozen(self):
        """DymolaConfig is immutable."""
        dc = DymolaConfig()
        with pytest.raises(AttributeError):
            dc.show_ide = True

    def test_defaults(self):
        """Default values match expected Dymola defaults."""
        dc = DymolaConfig()
        assert dc.show_ide is False
        assert dc.simulator_setup == []
        assert "CPUtime" in dc.diagnostic_variables
        assert "EventCounter" in dc.diagnostic_variables
