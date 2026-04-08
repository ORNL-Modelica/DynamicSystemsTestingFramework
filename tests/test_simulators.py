"""Tests for simulator utilities: log parser, mat reader, variable pattern matching."""

from pathlib import Path

import numpy as np
import pytest

from modelica_testing.simulators.dymola.log_parser import parse_dslog
from modelica_testing.simulators.dymola.mat_reader import read_dymola_mat
from modelica_testing.simulators.dymola.runner import _extract_variables
from modelica_testing.simulators.base import resolve_variable_patterns, _pattern_to_regex
from modelica_testing.discovery.test_registry import TestModel


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
        assert "cpu_time" in stats["simulation"]

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
        assert stats["simulation"]["cpu_time"] == pytest.approx(0.234)
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


# ---------------------------------------------------------------------------
# MAT reader
# ---------------------------------------------------------------------------

class TestMatReader:
    def test_read_returns_dict(self):
        """read_dymola_mat returns a dict of variable name -> (time, values)."""
        data = read_dymola_mat(SAMPLE_MAT)
        assert data is not None
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_variables_present(self):
        """Expected variables exist in the parsed data."""
        data = read_dymola_mat(SAMPLE_MAT)
        assert "x" in data
        assert "y" in data
        assert "unitTests.x[1]" in data
        assert "unitTests.x[2]" in data

    def test_time_series_shape(self):
        """Time series variables have time and values with matching lengths."""
        data = read_dymola_mat(SAMPLE_MAT)
        # x and y are time series (from data_2), not parameters
        for name in ["x", "y", "unitTests.x[1]"]:
            time, values = data[name]
            assert len(time) == len(values), f"{name}: time={len(time)}, values={len(values)}"
            assert len(time) > 2  # Should have many points

    def test_parameter_is_constant(self):
        """Parameters (from data_1) have constant values across all time points."""
        data = read_dymola_mat(SAMPLE_MAT)
        _, values = data["unitTests.n"]
        # Parameter may have 2 points (data_1) or be broadcast — either way constant
        assert np.all(values == values[0])

    def test_time_monotonic(self):
        """Time array is monotonically non-decreasing (events may have duplicates)."""
        data = read_dymola_mat(SAMPLE_MAT)
        time, _ = data["x"]
        diffs = np.diff(time)
        assert np.all(diffs >= 0), "Time must be monotonically non-decreasing"

    def test_diagnostics_present(self):
        """CPUtime and EventCounter are available when OutputCPUtime was enabled."""
        data = read_dymola_mat(SAMPLE_MAT)
        assert "CPUtime" in data
        assert "EventCounter" in data

    def test_parameter_values(self):
        """Parameters (constant variables) have consistent values."""
        data = read_dymola_mat(SAMPLE_MAT)
        _, values = data["unitTests.n"]
        assert values[0] == 2.0  # n=2 in ConstantTest
        assert values[-1] == 2.0

    def test_nonexistent_file(self):
        """Nonexistent file returns None."""
        result = read_dymola_mat(Path("/nonexistent/file.mat"))
        assert result is None


# ---------------------------------------------------------------------------
# Diagnostic variable extraction
# ---------------------------------------------------------------------------

class TestDiagnosticExtraction:
    def _make_test(self):
        return TestModel(
            model_id="ModelicaTestingLib.Examples.ConstantTest",
            mo_file=Path(""),
            package_path="ModelicaTestingLib.Examples",
            short_name="ConstantTest",
            n_vars=2,
            variable_patterns=[],
            source="unit_tests",
        )

    def test_extract_default_diagnostics(self):
        """CPUtime and EventCounter extracted as diagnostics."""
        mat_data = read_dymola_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(
            mat_data, test, ["CPUtime", "EventCounter"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "CPUtime" in diag_names
        assert "EventCounter" in diag_names

    def test_diagnostics_not_in_variables(self):
        """Diagnostic variables don't appear in the regular variables list."""
        mat_data = read_dymola_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(
            mat_data, test, ["CPUtime", "EventCounter"],
        )
        var_names = [v.name for v in variables]
        assert "CPUtime" not in var_names
        assert "EventCounter" not in var_names

    def test_custom_diagnostic_variable(self):
        """Custom diagnostic variable name is extracted if present in mat data."""
        mat_data = read_dymola_mat(SAMPLE_MAT)
        test = self._make_test()
        # "x" exists in the mat data — treating it as diagnostic
        variables, diagnostics = _extract_variables(
            mat_data, test, ["x"],
        )
        diag_names = [d.name for d in diagnostics]
        assert "x" in diag_names

    def test_missing_diagnostic_skipped(self):
        """Diagnostic variable not in mat data is silently skipped."""
        mat_data = read_dymola_mat(SAMPLE_MAT)
        test = self._make_test()
        variables, diagnostics = _extract_variables(
            mat_data, test, ["NonExistentVar"],
        )
        assert len(diagnostics) == 0

    def test_empty_diagnostic_list(self):
        """Empty diagnostic list produces no diagnostics."""
        mat_data = read_dymola_mat(SAMPLE_MAT)
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
