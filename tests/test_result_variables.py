"""Tests for the shared DSresult-MAT result-extraction helpers (U-3).

``compute_needed_variables`` / ``extract_variables`` / the statistics folds
used to be duplicated in the Dymola and OpenModelica backends. They now live in
``dstf.backends.common.result_variables`` and both backends call them, so the
behavior is pinned once here.
"""

import logging
from pathlib import Path

import numpy as np

from dstf.backends.common.result_variables import (
    compute_needed_variables,
    extract_variables,
    fold_diagnostics_into_statistics,
    merge_timing_stats,
)
from dstf.backends.types import VariableResult
from dstf.discovery.test_registry import TestModel

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_MAT = FIXTURES_DIR / "results" / "Dymola" / "dsres.mat"


def _spec_test(patterns, model_id="Lib.Examples.PatternTest"):
    return TestModel(
        model_id=model_id,
        source_file=Path(""),
        source_package="Lib.Examples",
        short_name=model_id.rsplit(".", 1)[-1],
        n_vars=0,
        variable_patterns=list(patterns),
        source="spec",
    )


def _unit_tests_test(n_vars, model_id="Lib.Examples.UnitTest", **overrides):
    defaults = dict(
        model_id=model_id,
        source_file=Path(""),
        source_package="Lib.Examples",
        short_name=model_id.rsplit(".", 1)[-1],
        n_vars=n_vars,
        variable_patterns=[],
        source="unit_tests",
    )
    defaults.update(overrides)
    return TestModel(**defaults)


def _series(values, dtype=np.float64):
    arr = np.asarray(values, dtype=dtype)
    return np.arange(len(arr), dtype=np.float64), arr


class TestComputeNeededVariables:
    def test_unit_tests_vars_and_diagnostics(self, tmp_path):
        """UnitTests names + diagnostics need no .mat name-matrix read."""
        needed = compute_needed_variables(
            tmp_path / "absent.mat",
            _unit_tests_test(2),
            ["CPUtime"],
        )
        assert needed == {"unitTests.x[1]", "unitTests.x[2]", "CPUtime"}

    def test_patterns_resolved_against_mat_names(self):
        """Globs are resolved through resolve_variable_patterns on real names."""
        needed = compute_needed_variables(SAMPLE_MAT, _spec_test(["unitTests.x*"]), [])
        assert needed
        assert "unitTests.x[1]" in needed
        assert all(n.startswith("unitTests.x") for n in needed)

    def test_the_component_names_the_variables(self, tmp_path):
        """``UnitTests ut(...)`` writes ``ut.x[i]``, not ``unitTests.x[i]``."""
        needed = compute_needed_variables(
            tmp_path / "absent.mat",
            _unit_tests_test(2, unit_tests_instance="ut"),
            [],
        )
        assert needed == {"ut.x[1]", "ut.x[2]"}

    def test_unreadable_mat_falls_back_to_everything(self, tmp_path):
        """Unreadable name matrix => None, meaning 'load everything'."""
        bogus = tmp_path / "bogus.mat"
        bogus.write_bytes(b"not a mat file")
        assert compute_needed_variables(bogus, _spec_test(["pipe.*"]), []) is None

    def test_nothing_requested_is_none(self, tmp_path):
        assert compute_needed_variables(tmp_path / "x.mat", _spec_test([]), []) is None


class TestExtractVariables:
    def test_missing_unit_test_variable_warns_with_model_id(self, caplog):
        """A requested unitTests variable absent from the output is loud."""
        test = _unit_tests_test(2, model_id="Lib.Examples.Loud")
        mat_data = {"unitTests.x[1]": _series([1.0, 2.0])}
        with caplog.at_level(logging.WARNING):
            variables, _ = extract_variables(mat_data, test, [])
        assert len(variables) == 1
        messages = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any("unitTests.x[2]" in m and "Lib.Examples.Loud" in m for m in messages)

    def test_unit_test_variables_keep_their_result_file_names(self):
        """The name is the one the .mat carries; the model's expression
        rides beside it as a label."""
        test = _unit_tests_test(2, x_expressions=["a.Q", "pipe.T[1]"])
        mat_data = {
            "unitTests.x[1]": _series([1.0, 2.0]),
            "unitTests.x[2]": _series([3.0, 4.0]),
        }
        variables, _ = extract_variables(mat_data, test, [])
        assert [(v.name, v.expression) for v in variables] == [
            ("unitTests.x[1]", "a.Q"),
            ("unitTests.x[2]", "pipe.T[1]"),
        ]

    def test_extraction_reads_the_components_own_instance(self):
        test = _unit_tests_test(1, x_expressions=["a"], unit_tests_instance="ut")
        variables, _ = extract_variables({"ut.x[1]": _series([1.0])}, test, [])
        assert [v.name for v in variables] == ["ut.x[1]"]

    def test_a_pattern_naming_the_component_variable_is_not_a_second_variable(self):
        """De-dupe is by result-file name: the pattern resolves to the name
        the UnitTests pass already emitted."""
        test = _unit_tests_test(
            1,
            model_id="Lib.Examples.Both",
            x_expressions=["pipe.T[1]"],
            variable_patterns=["unitTests.x[1]"],
            source="both",
        )
        variables, _ = extract_variables({"unitTests.x[1]": _series([1.0])}, test, [])
        assert [v.name for v in variables] == ["unitTests.x[1]"]

    def test_a_pattern_equal_to_an_expression_is_its_own_variable(self):
        """Two variables of the result file are two variables: the pattern
        names ``pipe.T[1]``, which the component only LABELS ``x[1]`` by."""
        test = _unit_tests_test(
            1,
            model_id="Lib.Examples.Both",
            x_expressions=["pipe.T[1]"],
            variable_patterns=["pipe.T[1]"],
            source="both",
        )
        mat_data = {
            "unitTests.x[1]": _series([1.0]),
            "pipe.T[1]": _series([1.0]),
        }
        variables, _ = extract_variables(mat_data, test, [])
        assert [(v.index, v.name, v.expression) for v in variables] == [
            (1, "unitTests.x[1]", "pipe.T[1]"),
            (2, "pipe.T[1]", ""),
        ]

    def test_pattern_resolution_passthrough(self):
        """Pattern variables come back in resolve_variable_patterns order."""
        test = _spec_test(["pipe.T*", "tank.level"])
        mat_data = {
            "pipe.T[1]": _series([1.0]),
            "pipe.T[2]": _series([2.0]),
            "tank.level": _series([3.0]),
            "unrelated": _series([4.0]),
        }
        variables, _ = extract_variables(mat_data, test, [])
        names = [v.name for v in variables]
        assert set(names) == {"pipe.T[1]", "pipe.T[2]", "tank.level"}
        assert [v.index for v in variables] == [1, 2, 3]

    def test_pattern_variable_already_seen_is_not_duplicated(self):
        """A UnitTests variable's name re-matched by a glob is emitted once."""
        test = TestModel(
            model_id="Lib.Examples.Both",
            source_file=Path(""),
            source_package="Lib.Examples",
            short_name="Both",
            n_vars=1,
            variable_patterns=["unitTests.x*"],
            source="both",
        )
        mat_data = {"unitTests.x[1]": _series([1.0])}
        variables, _ = extract_variables(mat_data, test, [])
        assert len(variables) == 1

    def test_diagnostics_split_from_variables(self):
        test = _spec_test(["pipe.T[1]"])
        mat_data = {
            "pipe.T[1]": _series([1.0]),
            "CPUtime": _series([0.5]),
        }
        variables, diagnostics = extract_variables(mat_data, test, ["CPUtime"])
        assert [v.name for v in variables] == ["pipe.T[1]"]
        assert [d.name for d in diagnostics] == ["CPUtime"]


class TestFoldDiagnosticsIntoStatistics:
    def _diag(self, values, dtype):
        time_arr, arr = _series(values, dtype=dtype)
        return VariableResult(index=1, time=time_arr, values=arr, name="CPUtime")

    def test_float32_rounded_to_seven_significant_digits(self):
        diag = self._diag([0.0, 1.2345678901234], np.float32)
        stats = fold_diagnostics_into_statistics(None, [diag])
        assert stats == {"CPUtime": float("%.7g" % np.float32(1.2345678901234))}

    def test_float64_keeps_fifteen_significant_digits(self):
        diag = self._diag([0.0, 1.2345678901234], np.float64)
        stats = fold_diagnostics_into_statistics({}, [diag])
        assert stats["CPUtime"] == float("%.15g" % 1.2345678901234)

    def test_existing_stats_preserved(self):
        diag = self._diag([0.0, 2.0], np.float64)
        stats = fold_diagnostics_into_statistics({"simulation": {"steps": 3}}, [diag])
        assert stats["simulation"] == {"steps": 3}
        assert stats["CPUtime"] == 2.0

    def test_no_diagnostics_leaves_stats_untouched(self):
        assert fold_diagnostics_into_statistics(None, []) is None
        original = {"a": 1}
        assert fold_diagnostics_into_statistics(original, []) is original

    def test_empty_diagnostic_series_skipped(self):
        empty = VariableResult(
            index=1,
            time=np.array([], dtype=np.float64),
            values=np.array([], dtype=np.float64),
            name="CPUtime",
        )
        assert fold_diagnostics_into_statistics(None, [empty]) == {}


class TestMergeTimingStats:
    def test_creates_stats_when_none(self):
        stats = merge_timing_stats(None, {"total_wall": 1.0})
        assert stats == {"timing": {"total_wall": 1.0}}

    def test_preexisting_timing_keys_preserved(self):
        """Non-destructive: a backend's per-phase seconds survive the overlay."""
        stats = merge_timing_stats(
            {"timing": {"frontend": 0.25}},
            {"total_wall": 1.0},
        )
        assert stats["timing"] == {"frontend": 0.25, "total_wall": 1.0}

    def test_summary_keys_win_on_collision(self):
        stats = merge_timing_stats({"timing": {"total_wall": 9.0}}, {"total_wall": 1.0})
        assert stats["timing"]["total_wall"] == 1.0

    def test_other_stats_untouched(self):
        stats = merge_timing_stats({"simulation": {"steps": 3}}, {"sim_wall": 2.0})
        assert stats["simulation"] == {"steps": 3}
        assert stats["timing"] == {"sim_wall": 2.0}
