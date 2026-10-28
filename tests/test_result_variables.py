"""Tests for the shared DSresult-MAT result-extraction helpers (U-3).

``compute_needed_variables`` / ``extract_variables`` / the statistics folds
used to be duplicated in the Dymola and OpenModelica backends. They now live in
``dstf.backends.common.result_variables`` and both backends call them, so the
behavior is pinned once here.
"""

import logging
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.common.result_variables import (
    compute_needed_variables,
    extract_variables,
    merge_timing_stats,
    run_timing,
)
from dstf.backends.types import TestRunResult, VariableResult
from dstf.discovery.test_registry import TestModel

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_MAT = FIXTURES_DIR / "results" / "Dymola" / "dsres.mat"


def _spec_test(patterns, model_id="Lib.Examples.PatternTest"):
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=0,
        variable_patterns=list(patterns),
        source="spec",
    )


def _unit_tests_test(n_vars, model_id="Lib.Examples.UnitTest", **overrides):
    defaults = dict(
        model_id=model_id,
        source_file=None,
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

    @pytest.mark.parametrize(
        "test",
        [
            TestModel(
                model_id="Lib.Examples.Both",
                source_file=None,
                n_vars=1,
                variable_patterns=["unitTests.x*"],
                source="both",
            ),
            _unit_tests_test(
                1,
                model_id="Lib.Examples.Both",
                x_expressions=["pipe.T[1]"],
                variable_patterns=["unitTests.x[1]"],
                source="both",
            ),
        ],
        ids=["glob", "literal"],
    )
    def test_pattern_variable_already_seen_is_not_duplicated(self, test):
        """A UnitTests variable's name re-matched by a glob is emitted once.
        De-dupe is by result-file name: a literal pattern naming the
        component variable resolves to the name the UnitTests pass already
        emitted, and is not a second variable."""
        mat_data = {"unitTests.x[1]": _series([1.0])}
        variables, _ = extract_variables(mat_data, test, [])
        assert [v.name for v in variables] == ["unitTests.x[1]"]

    def test_diagnostics_split_from_variables(self):
        test = _spec_test(["pipe.T[1]"])
        mat_data = {
            "pipe.T[1]": _series([1.0]),
            "CPUtime": _series([0.5]),
        }
        variables, diagnostics = extract_variables(mat_data, test, ["CPUtime"])
        assert [v.name for v in variables] == ["pipe.T[1]"]
        assert [d.name for d in diagnostics] == ["CPUtime"]


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


def _run(**kw) -> TestRunResult:
    return TestRunResult(
        **{"model_id": "Lib.A", "test_key": "test_0001", "success": True, **kw}
    )


class TestRunTiming:
    def test_all_walls_known(self):
        rr = _run(elapsed=10.004, translation_wall=3.333, sim_wall=5.555)
        assert run_timing(rr) == {
            "translation_wall": 3.33,
            "sim_wall": 5.55,
            "other_wall": 1.12,
            "total_wall": 10.0,
        }

    def test_a_sim_wall_only_has_no_other_wall(self):
        """FMPy / Julia / the Python backend measure the simulate call only."""
        assert run_timing(_run(elapsed=1.5, sim_wall=1.25)) == {
            "sim_wall": 1.25,
            "total_wall": 1.5,
        }

    def test_a_translation_wall_only_has_no_other_wall(self):
        """A translation failure: the missing sim wall is not counted as 0."""
        rr = _run(success=False, elapsed=2.0, translation_wall=1.5)
        assert run_timing(rr) == {"translation_wall": 1.5, "total_wall": 2.0}

    def test_no_wall_is_no_block(self):
        """Dymola batch: its elapsed is a batch average, not this test's."""
        assert run_timing(_run(elapsed=4.0)) == {}

    def test_other_wall_is_never_negative(self):
        rr = _run(elapsed=1.0, translation_wall=0.7, sim_wall=0.6)
        assert run_timing(rr)["other_wall"] == 0.0

    def test_no_elapsed_no_total(self):
        assert run_timing(_run(sim_wall=0.5)) == {"sim_wall": 0.5}


def _tree(*leaves):
    from dstf.comparison.tree_spec import parse_metric_tree

    return parse_metric_tree({"combinator": "and", "children": list(leaves)})


class TestTreeVariablesAreTracked:
    """A variable a criterion names is extracted — APPENDED after the
    UnitTests variables and the patterns, so their indices stay put."""

    def _test(self):
        return _unit_tests_test(
            1,
            x_expressions=["q"],
            variable_patterns=["a"],
            source="both",
            metric_tree_spec=_tree(
                {"metric": "nrmse", "variable": "b"},
                {"metric": "nrmse", "variable": "a"},
                {
                    "metric": "scalar",
                    "variable": "CPUtime",
                    "quantity": {"reduce": "max"},
                },
                {
                    "metric": "scalar",
                    "variable": "ev",
                    "quantity": "translation.continuous_time_states",
                },
            ),
        )

    def test_needed_names_the_tree_variable_not_a_diagnostic_twice(self, tmp_path):
        test = self._test()
        test.variable_patterns = []
        needed = compute_needed_variables(tmp_path / "absent.mat", test, ["CPUtime"])
        assert needed == {"unitTests.x[1]", "b", "a", "CPUtime"}

    def test_indices_ut_then_pattern_then_tree(self):
        mat = {
            "unitTests.x[1]": _series([1.0]),
            "a": _series([2.0]),
            "b": _series([3.0]),
            "CPUtime": _series([0.1]),
        }
        variables, diagnostics = extract_variables(mat, self._test(), ["CPUtime"])
        assert [(v.index, v.name) for v in variables] == [
            (1, "unitTests.x[1]"),
            (2, "a"),
            (3, "b"),
        ]
        assert [d.name for d in diagnostics] == ["CPUtime"]

    def test_a_missing_tree_variable_warns(self, caplog):
        mat = {"unitTests.x[1]": _series([1.0]), "a": _series([2.0])}
        with caplog.at_level(logging.WARNING):
            variables, _ = extract_variables(mat, self._test(), ["CPUtime"])
        assert [v.name for v in variables] == ["unitTests.x[1]", "a"]
        assert any("b" in r.getMessage().split() for r in caplog.records) or any(
            "Variable b not found" in r.getMessage() for r in caplog.records
        )


def test_tracked_results_warns_of_a_nameless_variable(caplog):
    """A result variable with no name is a backend bug: nothing tracks it by
    a name, so it is left out — and said, not dropped quietly."""
    from dstf.backends.common.result_variables import tracked_results
    from dstf.backends.types import TestResult

    t = np.array([0.0, 1.0])
    result = TestResult(
        model_id="Lib.Examples.PatternTest",
        success=True,
        variables=[
            VariableResult(index=1, time=t, values=t, name="a"),
            VariableResult(index=2, time=t, values=t, name=""),
        ],
    )
    with caplog.at_level(logging.WARNING):
        tracked = tracked_results(_spec_test(["a"]), result)
    assert [v.name for v in tracked] == ["a"]
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1, warned
    assert "Lib.Examples.PatternTest" in warned[0] and "2" in warned[0], warned
