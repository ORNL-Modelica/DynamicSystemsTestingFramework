"""`dstf summary`'s facts (``reporting.results_summary``): a pass/run
matrix over several runs' ``verdicts.json`` — a row per backend/os, a
column per label — and, against a base label, which tests not passing
also do not pass there."""

import json

import pytest

from dstf.reporting.results_summary import SummaryError, read_summary


def _leaf(label, passed, *, criterion="", error=None, advisory=False):
    return {
        "kind": "leaf",
        "label": label,
        "passed": passed,
        "criterion": criterion,
        "error": error,
        "advisory": advisory,
        "children": [],
    }


def _test(model_id, status, *, message=None, tree=None):
    return {
        "model_id": model_id,
        "ref_id": None,
        "status": status,
        "message": message,
        "warnings": [],
        "tree": tree,
    }


def _write(folder, tests, *, backend="Python", os_="linux", metadata=True, totals=None):
    """A work dir's verdicts.json as a scoring run writes one."""
    folder.mkdir(parents=True, exist_ok=True)
    counts = {"PASS": 0, "FAIL": 0, "SIM_FAIL": 0, "NO_REF": 0, "NOT RUN": 0}
    for t in tests:
        counts[t["status"]] += 1
    doc = {
        "written_at": "2026-11-11T00:00:00+00:00",
        "metadata": {
            "backend": backend,
            "backend_name": f"{backend} entry",
            "os": os_,
            "tool_version": "x",
            "dstf_version": "1.4.0",
        }
        if metadata
        else None,
        "totals": totals or {"total": len(tests), **counts},
        "exit_code": 0,
        "tests": tests,
    }
    (folder / "verdicts.json").write_text(json.dumps(doc), encoding="utf-8")
    return folder / "verdicts.json"


def test_a_cell_is_pass_count_over_the_total_by_backend_and_os(tmp_path):
    _write(
        tmp_path / "a",
        [
            _test("Lib.P", "PASS"),
            _test("Lib.F", "FAIL"),
            _test("Lib.N", "NOT RUN"),
            _test("Lib.R", "NO_REF"),
        ],
        backend="Dymola",
    )
    got = read_summary([("head", tmp_path / "a")])
    assert got.labels == ["head"]
    assert got.folders == ["Dymola/linux"]
    cell = got.cells[("head", "Dymola/linux")]
    assert (cell.passed, cell.total) == (1, 4)
    assert cell.totals == {
        "total": 4,
        "PASS": 1,
        "FAIL": 1,
        "SIM_FAIL": 0,
        "NO_REF": 1,
        "NOT RUN": 1,
    }
    # NOT RUN and NO_REF are not passing
    assert [(f.model_id, f.status) for f in cell.not_passing] == [
        ("Lib.F", "FAIL"),
        ("Lib.N", "NOT RUN"),
        ("Lib.R", "NO_REF"),
    ]


def test_the_folder_is_the_reference_folder_s_backend_not_the_entry_name(tmp_path):
    _write(tmp_path / "a", [_test("Lib.P", "PASS")], backend="OpenModelica")
    got = read_summary([("head", tmp_path / "a")])
    assert got.folders == ["OpenModelica/linux"]


def test_a_repeated_label_adds_its_other_backend_os(tmp_path):
    """Windows and Linux work dirs live in different places: one label,
    two DIRs, two rows of the same column."""
    _write(tmp_path / "lin", [_test("Lib.P", "PASS")], os_="linux")
    _write(tmp_path / "win", [_test("Lib.P", "FAIL")], os_="windows")
    got = read_summary([("head", tmp_path / "lin"), ("head", tmp_path / "win")])
    assert got.labels == ["head"]
    assert got.folders == ["Python/linux", "Python/windows"]
    assert got.cells[("head", "Python/linux")].passed == 1
    assert got.cells[("head", "Python/windows")].passed == 0


def test_rows_are_backend_then_os_and_columns_the_labels_in_order(tmp_path):
    _write(tmp_path / "b1", [_test("Lib.P", "PASS")], backend="OpenModelica")
    _write(tmp_path / "b2", [_test("Lib.P", "PASS")], backend="Dymola", os_="windows")
    _write(tmp_path / "b3", [_test("Lib.P", "PASS")], backend="Dymola")
    got = read_summary(
        [("z", tmp_path / "b1"), ("a", tmp_path / "b2"), ("z", tmp_path / "b3")]
    )
    assert got.labels == ["z", "a"]
    assert got.folders == ["Dymola/linux", "Dymola/windows", "OpenModelica/linux"]


def test_verdicts_under_reports_are_not_a_run(tmp_path):
    _write(tmp_path / "wd", [_test("Lib.P", "PASS")])
    _write(tmp_path / "wd" / "reports" / "x", [_test("Lib.P", "FAIL")])
    got = read_summary([("head", tmp_path / "wd")])
    assert got.cells[("head", "Python/linux")].passed == 1


# --- against a base label -----------------------------------------------------


def test_a_test_not_passing_on_the_base_too_is_not_new(tmp_path):
    _write(
        tmp_path / "base",
        [_test("Lib.A", "PASS"), _test("Lib.B", "FAIL"), _test("Lib.C", "SIM_FAIL")],
    )
    _write(
        tmp_path / "head",
        [
            _test("Lib.A", "FAIL"),
            _test("Lib.B", "FAIL"),
            _test("Lib.C", "FAIL"),
            _test("Lib.D", "FAIL"),
        ],
    )
    got = read_summary(
        [("base", tmp_path / "base"), ("head", tmp_path / "head")], base="base"
    )
    cell = got.cells[("head", "Python/linux")]
    assert cell.not_new == 2
    assert {f.model_id: f.base for f in cell.not_passing} == {
        "Lib.A": "PASS",
        "Lib.B": "FAIL",
        "Lib.C": "SIM_FAIL",
        "Lib.D": "not in base",
    }
    # the base's own cell is compared with nothing
    assert got.cells[("base", "Python/linux")].not_new is None


@pytest.mark.parametrize(
    "on_base, failing, none_either",
    [
        # the base failed it too: not new, whatever it is here
        ("FAIL", 1, 0),
        ("SIM_FAIL", 1, 0),
        # it passed on the base, or the base does not hold it: new
        ("PASS", 0, 0),
        (None, 0, 0),
        # nothing was scored on the base: new; with none here either, counted so
        ("NO_REF", 0, 1),
        ("NOT RUN", 0, 0),
    ],
)
def test_not_new_is_failed_on_the_base_too_or_the_same_status(
    tmp_path, on_base, failing, none_either
):
    """A test is not new when the base failed it (FAIL or SIM_FAIL) or ended
    with the same word; a base with no baseline for it (NO_REF) or that never
    ran it (NOT RUN) did not fail it, so a FAIL here is new. A test with no
    baseline here (NO_REF) is no failure, new or not, whatever the base did:
    it is counted apart, as having none on the base either or not."""
    base_tests = [_test("Lib.Other", "PASS")]
    if on_base is not None:
        base_tests.append(_test("Lib.A", on_base))
    _write(tmp_path / "base", base_tests)
    for here, expected in (("FAIL", (failing, 0)), ("NO_REF", (0, none_either))):
        _write(tmp_path / here, [_test("Lib.A", here)])
        got = read_summary(
            [("base", tmp_path / "base"), ("head", tmp_path / here)], base="base"
        )
        cell = got.cells[("head", "Python/linux")]
        assert (cell.not_new, cell.none_on_base) == expected, here


def test_not_new_is_read_in_the_same_backend_os_only(tmp_path):
    """Lib.A fails on the base under OpenModelica and passes there under
    Dymola: failing under Dymola on the branch is new."""
    _write(tmp_path / "b-om", [_test("Lib.A", "FAIL")], backend="OpenModelica")
    _write(tmp_path / "b-dy", [_test("Lib.A", "PASS")], backend="Dymola")
    _write(tmp_path / "h-dy", [_test("Lib.A", "FAIL")], backend="Dymola")
    got = read_summary(
        [
            ("base", tmp_path / "b-om"),
            ("base", tmp_path / "b-dy"),
            ("head", tmp_path / "h-dy"),
        ],
        base="base",
    )
    cell = got.cells[("head", "Dymola/linux")]
    assert cell.not_new == 0
    assert [f.base for f in cell.not_passing] == ["PASS"]


def test_with_no_base_run_for_the_backend_os_nothing_is_counted_and_why_said(
    tmp_path,
):
    _write(tmp_path / "base", [_test("Lib.A", "FAIL")], backend="Dymola")
    _write(tmp_path / "head", [_test("Lib.A", "FAIL")], backend="OpenModelica")
    got = read_summary(
        [("base", tmp_path / "base"), ("head", tmp_path / "head")], base="base"
    )
    cell = got.cells[("head", "OpenModelica/linux")]
    assert cell.not_new is None
    assert cell.base_note == "no base run for OpenModelica/linux"
    assert [f.base for f in cell.not_passing] == [None]


# --- what each list row says ------------------------------------------------------


def test_a_row_says_the_first_failing_criterion_that_is_not_advisory(tmp_path):
    tree = {
        "kind": "combinator",
        "label": "and[3]",
        "passed": False,
        "children": [
            _leaf("a", True, criterion="NRMSE 1e-3 vs 1e-2 → PASS"),
            _leaf("w", False, criterion="NRMSE 0.5 → FAIL", advisory=True),
            {
                "kind": "combinator",
                "label": "or[1]",
                "passed": False,
                "children": [
                    _leaf("x", False, criterion="NRMSE 5.788e-02 vs 1.000e-02 → FAIL")
                ],
            },
            _leaf("y", False, criterion="later → FAIL"),
        ],
    }
    _write(tmp_path / "wd", [_test("Lib.A", "FAIL", tree=tree)])
    (row,) = (
        read_summary([("head", tmp_path / "wd")])
        .cells[("head", "Python/linux")]
        .not_passing
    )
    assert row.reason == "x: NRMSE 5.788e-02 vs 1.000e-02 → FAIL"


def test_a_failing_criterion_under_a_combinator_that_passed_is_not_named(tmp_path):
    """``and(or(A fails, B passes), C fails)``: the or passed, so A did not
    fail the test; C did."""
    tree = {
        "kind": "combinator",
        "label": "and[2]",
        "passed": False,
        "children": [
            {
                "kind": "combinator",
                "label": "or[2]",
                "passed": True,
                "children": [
                    _leaf("A", False, criterion="A → FAIL"),
                    _leaf("B", True, criterion="B → PASS"),
                ],
            },
            _leaf("C", False, criterion="C → FAIL"),
        ],
    }
    _write(tmp_path / "wd", [_test("Lib.A", "FAIL", tree=tree)])
    (row,) = (
        read_summary([("head", tmp_path / "wd")])
        .cells[("head", "Python/linux")]
        .not_passing
    )
    assert row.reason == "C: C → FAIL"


def test_a_criterion_s_error_is_said_beside_it(tmp_path):
    tree = {
        "kind": "combinator",
        "label": "and[1]",
        "passed": False,
        "children": [
            _leaf(
                "y", False, criterion="", error="variable 'y' not found in the result"
            )
        ],
    }
    _write(tmp_path / "wd", [_test("Lib.A", "FAIL", tree=tree)])
    (row,) = (
        read_summary([("head", tmp_path / "wd")])
        .cells[("head", "Python/linux")]
        .not_passing
    )
    assert row.reason == "y (variable 'y' not found in the result)"


def test_with_no_failing_criterion_the_message_s_last_line_is_said(tmp_path):
    """A SIM_FAIL message is a traceback: its first line says nothing."""
    trace = (
        "Python execution failed: Traceback (most recent call last):\n"
        '  File "x.py", line 29, in simulate\n'
        "RuntimeError: broken on purpose\n\n"
    )
    long = "x" * 300
    _write(
        tmp_path / "wd",
        [
            _test("Lib.A", "SIM_FAIL", message=trace),
            _test("Lib.B", "FAIL", message=f"first\n{long}"),
            _test("Lib.C", "NOT RUN"),
        ],
    )
    rows = (
        read_summary([("head", tmp_path / "wd")])
        .cells[("head", "Python/linux")]
        .not_passing
    )
    assert [r.reason for r in rows] == [
        "RuntimeError: broken on purpose",
        "x" * 199 + "…",
        "",
    ]


#: Two OpenModelica failures as a large library's run recorded them: the
#: call is echoed first, the error after it.
RANKINE = (
    "OMCSessionZMQ error: OMCSessionException: [OMC log for 'sendExpression("
    "simulate(TRANSFORM.Fluid.Examples.RankineCycle, stopTime=1.0, "
    'tolerance=0.0001, method="dassl", outputFormat="mat", fileNamePrefix='
    '"result", variableFilter="^(time|unitTests\\\\.x\\\\[1\\\\]|CPUtime|'
    "EventCounter)$\"), False)']: [translation:error:63] Internal error It is "
    "not possible to select continuous time states because Number of "
    "Equations 15 greater than number of States 0 to select from.\n"
)
DELAY = (
    "OMCSessionZMQ error: OMCSessionException: [OMC log for 'sendExpression("
    "simulate(TRANSFORM.Fluid.Pipes.Examples.TransportDelayPipeTests."
    'CheckDelayTime, stopTime=11.0, tolerance=0.0001, method="dassl", '
    'outputFormat="mat", fileNamePrefix="result", variableFilter="^(time|'
    "unitTests\\\\.x\\\\[1\\\\]|CPUtime|EventCounter)$\"), False)']: "
    "[translation:error:195] Function argument positiveVelocity=noEvent("
    "transportDelayPipe.v >= 0.0) in call to spatialDistribution has "
    "variability continuous which is not a discrete expression.\n"
)
WARNING = (
    "LOG_STDOUT        | warning | The default linear solver fails, the "
    "fallback solver with total pivoting is started at time 0.000000. That "
    "might raise performance issues, for more information use -lv LOG_LS."
)


def _reasons(tmp_path, *messages):
    _write(
        tmp_path / "wd",
        [_test(f"Lib.T{i}", "SIM_FAIL", message=m) for i, m in enumerate(messages)],
    )
    rows = (
        read_summary([("head", tmp_path / "wd")])
        .cells[("head", "Python/linux")]
        .not_passing
    )
    return [r.reason for r in rows]


def test_an_openmodelica_error_is_said_from_its_tag_not_the_echoed_call(tmp_path):
    assert _reasons(tmp_path, RANKINE, DELAY) == [
        "[translation:error:63] Internal error It is not possible to select "
        "continuous time states because Number of Equations 15 greater than "
        "number of States 0 to select from.",
        "[translation:error:195] Function argument positiveVelocity=noEvent("
        "transportDelayPipe.v >= 0.0) in call to spatialDistribution has "
        "variability continuous which is not a discrete expression.",
    ]


def test_a_long_error_is_cut_from_its_tag(tmp_path):
    long = "OMC log for 'simulate(Lib.A)': [backend:error:7] " + "z" * 300
    assert _reasons(tmp_path, long) == ["[backend:error:7] " + "z" * 181 + "…"]


def test_a_repeated_warning_gives_way_to_the_line_that_says_why(tmp_path):
    """A failure as a run keeps it (``backends.common.failure_message``):
    the warning it repeated said once, the reason before a later repeat."""
    from dstf.backends.common.failure_message import failure_message

    reason_line = "LOG_STDOUT        | error   | the reason the run stopped"
    kept = failure_message(
        "\n".join(
            [
                "Simulation execution failed for model: Lib.T0",
                *[WARNING] * 40,
                reason_line,
                *[WARNING] * 2,
            ]
        )
    )
    assert kept.endswith(f"{WARNING} (repeated 2 times)")
    assert _reasons(tmp_path, kept) == [reason_line]


def test_a_repeated_line_is_said_when_nothing_else_is(tmp_path):
    assert _reasons(tmp_path, "w (repeated 3 times)\n") == ["w (repeated 3 times)"]


# --- refusals -----------------------------------------------------------------------


def test_a_dir_with_no_verdicts_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SummaryError) as got:
        read_summary([("head", tmp_path / "empty")])
    assert str(got.value) == (
        f"head={tmp_path / 'empty'}: no verdicts.json in it (a scoring "
        "`dstf run` or `dstf compare` writes one in its work dir)"
    )


def test_a_dir_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(SummaryError) as got:
        read_summary([("head", tmp_path / "nope")])
    assert str(got.value) == f"head={tmp_path / 'nope'}: no such directory"


def test_verdicts_with_no_metadata_are_refused(tmp_path):
    path = _write(tmp_path / "wd", [_test("Lib.A", "PASS")], metadata=False)
    with pytest.raises(SummaryError) as got:
        read_summary([("head", tmp_path / "wd")])
    assert str(got.value) == (
        f"{path}: its metadata names no backend and os (it was written "
        "without the run's status.json)"
    )


def test_two_verdicts_for_one_label_and_backend_os_are_refused(tmp_path):
    """Two libraries under one DIR: the metadata names no library, so the
    two files are one cell twice."""
    one = _write(tmp_path / "wd" / "LibA", [_test("LibA.X", "PASS")])
    two = _write(tmp_path / "wd" / "LibB", [_test("LibB.Y", "PASS")])
    with pytest.raises(SummaryError) as got:
        read_summary([("head", tmp_path / "wd")])
    assert str(got.value) == (
        f"head: two verdicts.json for Python/linux, {one} and {two}: "
        "pass each work dir as its own LABEL=DIR"
    )


def test_one_dir_given_twice_under_a_label_is_refused_as_given_twice(tmp_path):
    """Not two libraries in one work dir: one file read twice."""
    path = _write(tmp_path / "wd", [_test("Lib.A", "PASS")])
    with pytest.raises(SummaryError) as got:
        read_summary(
            [("head", tmp_path / "wd"), ("head", tmp_path / "wd" / ".." / "wd")]
        )
    assert str(got.value) == (
        f"head: {path} is given twice (one DIR twice, or one DIR inside another)"
    )


def test_a_base_naming_no_label_is_refused(tmp_path):
    _write(tmp_path / "wd", [_test("Lib.A", "PASS")])
    with pytest.raises(SummaryError) as got:
        read_summary([("head", tmp_path / "wd")], base="main")
    assert str(got.value) == "--base main: no such label (the labels are head)"


def test_a_verdicts_file_that_is_not_json_is_refused(tmp_path):
    (tmp_path / "wd").mkdir()
    (tmp_path / "wd" / "verdicts.json").write_text("{", encoding="utf-8")
    with pytest.raises(SummaryError, match=r"verdicts\.json: not JSON \("):
        read_summary([("head", tmp_path / "wd")])
