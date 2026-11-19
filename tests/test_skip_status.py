"""SKIPPED: a test marked skip for the run's backend and OS reads SKIPPED,
with its reason, on every surface, and a test carrying a mark never changes
the exit code — the one "counts against" rule of ``reporting.status``.

A marked test that ran anyway (``--include-skipped``, or queued in the tool)
keeps its real status and carries the mark, so a FAIL counted in
``Failed:`` is visibly a marked one.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from conftest import make_test_model
from dstf.backends.types import TestResult
from dstf.comparison import comparator
from dstf.comparison.types import TestComparison
from dstf.config import Config
from dstf.discovery.marks import EVERY_BACKEND, SkipMark
from dstf.reporting import status as st
from dstf.reporting.console_report import print_report
from dstf.reporting.dashboard_render import render_final
from dstf.reporting.junit_report import generate_junit_report
from dstf.reporting.results_summary import read_summary
from dstf.reporting.results_summary_text import as_markdown
from dstf.reporting.verdicts import build_verdicts
from dstf.storage.reference_store import ReferenceStore

ON_PYTHON = SkipMark("Python", "r")
#: A reason with every character a text format must escape.
AWKWARD = 'say "no" <now> & then'


def _store(tmp_path) -> ReferenceStore:
    lib = tmp_path / "Lib"
    lib.mkdir(exist_ok=True)
    (lib / "package.mo").write_text("package Lib\nend Lib;\n", encoding="utf-8")
    config = Config(
        source_path=lib, reference_root=tmp_path / "refs", work_dir=tmp_path / "work"
    )
    return ReferenceStore(config)


def _skipped(model_id: str = "Lib.B", mark: SkipMark = ON_PYTHON) -> TestComparison:
    return comparator.skipped(make_test_model(model_id), mark)


def _passing(model_id: str = "Lib.A", **fields) -> TestComparison:
    return TestComparison(model_id=model_id, passed=True, **fields)


def _marked_fail(model_id: str = "Lib.C") -> TestComparison:
    return TestComparison(
        model_id=model_id, passed=False, error_message="it fails", mark=ON_PYTHON
    )


def _plain(text: str) -> list[str]:
    return re.sub(r"\x1b\[\d+m", "", text).splitlines()


# -- R3a: the status, and the one entry point that builds it -------------------


def test_a_test_skipped_by_its_mark_reads_skipped_with_its_reason():
    comp = _skipped()
    assert st.classify(comp) == st.SKIPPED == "SKIPPED"
    assert st.STATUS_LABELS[st.SKIPPED] == "SKIPPED"
    assert (comp.skipped, comp.mark, comp.ran) == (True, ON_PYTHON, False)
    assert comp.error_message == "skipped on Python: r"


def test_compare_all_skips_a_marked_test_and_scores_the_rest(tmp_path):
    store = _store(tmp_path)
    a, b = make_test_model("Lib.A"), make_test_model("Lib.B")
    results = {m: TestResult(model_id=m, success=True) for m in ("Lib.A", "Lib.B")}

    comps = comparator.compare_all([a, b], results, store, marks={"Lib.B": ON_PYTHON})

    assert [st.classify(c) for c in comps] == ["PASS", "SKIPPED"]
    assert [c.mark for c in comps] == [None, ON_PYTHON]


def test_compare_all_with_include_skipped_scores_a_marked_test_carrying_its_mark(
    tmp_path,
):
    store = _store(tmp_path)
    b = make_test_model("Lib.B")
    results = {"Lib.B": TestResult(model_id="Lib.B", success=False)}

    (comp,) = comparator.compare_all(
        [b], results, store, marks={"Lib.B": ON_PYTHON}, include_skipped=True
    )

    assert st.classify(comp) == "SIM_FAIL"
    assert (comp.skipped, comp.mark) == (False, ON_PYTHON)


# -- R3b: the console ---------------------------------------------------------


def test_the_console_counts_skipped_apart_and_says_why(capsys):
    code = print_report([_passing(), _skipped()])
    lines = _plain(capsys.readouterr().out)
    assert code == 0
    assert "  SKIPPED  Lib.B (skipped on Python: r)" in lines
    assert "Results: 1/1 passed · 1 skipped" in lines
    assert not any("NOT RUN" in line for line in lines)


def test_the_console_says_a_marked_test_that_ran_and_one_line_per_mark(capsys):
    code = print_report([_passing("Lib.A", mark=ON_PYTHON), _marked_fail()])
    lines = _plain(capsys.readouterr().out)
    assert code == 0
    assert "  FAIL  Lib.C (marked skip on Python)" in lines
    assert "  Failed: 1" in lines
    assert lines[-2:] == [
        "Lib.A: marked skip on Python, passes now — remove the mark",
        "Lib.C: marked skip on Python, still does not pass (FAIL)",
    ]


# -- R3c: JUnit ---------------------------------------------------------------


def _junit(tmp_path, comps) -> ET.Element:
    out = tmp_path / "junit.xml"
    generate_junit_report(comps, out)
    return ET.parse(out).getroot()


def test_junit_writes_a_skipped_test_as_skipped_with_its_reason(tmp_path):
    root = _junit(tmp_path, [_passing(), _skipped()])
    case = root.find(".//testcase[@name='Lib.B']")
    assert case is not None
    assert [(e.tag, e.get("message")) for e in case] == [
        ("skipped", "skipped on Python: r")
    ]
    # `tests=` counts it; it is one skipped, no failure
    assert (root.get("tests"), root.get("failures")) == ("2", "0")
    suite = root.find("testsuite[@name='Lib.B']")
    assert suite is not None
    assert (suite.get("tests"), suite.get("skipped")) == ("1", "1")


def test_junit_writes_a_marked_test_that_still_fails_as_skipped(tmp_path):
    """A CI gate that reads JUnit agrees with the exit code: 0."""
    root = _junit(tmp_path, [_marked_fail()])
    case = root.find(".//testcase[@name='Lib.C']")
    assert case is not None
    assert [(e.tag, e.get("message")) for e in case] == [
        ("skipped", "marked skip on Python (r); still fails: FAIL")
    ]
    assert (root.get("failures"), root.get("errors")) == ("0", "0")


# -- R3d: summary -------------------------------------------------------------


def _verdicts_file(folder, tests: list[dict], totals: dict) -> None:
    folder.mkdir(parents=True)
    (folder / "verdicts.json").write_text(
        json.dumps(
            {
                "metadata": {"backend": "Python", "os": "linux"},
                "totals": totals,
                "tests": tests,
            }
        ),
        encoding="utf-8",
    )


def test_summary_lists_a_marked_test_apart_never_as_not_passing(tmp_path):
    _verdicts_file(
        tmp_path / "main",
        [{"model_id": "Lib.C", "status": "PASS", "mark": None}],
        {"total": 1, "PASS": 1},
    )
    _verdicts_file(
        tmp_path / "new",
        [
            {"model_id": "Lib.A", "status": "PASS", "mark": None},
            {"model_id": "Lib.B", "status": "SKIPPED", "mark": "Python: r"},
            {"model_id": "Lib.C", "status": "FAIL", "mark": "Python: r"},
        ],
        {"total": 2, "PASS": 1, "FAIL": 1, "SKIPPED": 1},
    )
    summary = read_summary(
        [("main", tmp_path / "main"), ("new", tmp_path / "new")], base="main"
    )
    cell = summary.cells[("new", "Python/linux")]
    assert cell.not_passing == []
    assert cell.not_new == 0
    assert [(m.model_id, m.status, m.mark) for m in cell.marked] == [
        ("Lib.B", "SKIPPED", "Python: r"),
        ("Lib.C", "FAIL", "Python: r"),
    ]
    markdown = as_markdown(summary)
    assert "<summary>Python/linux, new: 2 marked skip</summary>" in markdown
    assert (
        'Marked skip, failing no run: <code title="Lib.B">B</code> (SKIPPED, '
        'Python: r), <code title="Lib.C">C</code> (FAIL, Python: r).'
    ) in markdown


# -- R3e: the exit code and "nothing ran" -------------------------------------


def test_a_skipped_test_never_fails_a_run_and_is_not_nothing_ran():
    assert st.exit_code([_skipped()]) == 0
    assert st.nothing_ran([_skipped()]) is False
    # a guard: a run with no test in scope at all is still "nothing ran"
    assert st.nothing_ran([]) is True
    assert st.exit_code([]) == 1


def test_a_run_whose_unmarked_tests_have_no_result_is_still_nothing_ran():
    not_run = TestComparison("Lib.A", passed=False, sim_success=False, ran=False)
    assert st.nothing_ran([_skipped(), not_run]) is True
    assert st.exit_code([_skipped(), not_run]) == 1


def test_every_marked_test_not_run_is_not_nothing_ran():
    """Every test marked, run with --include-skipped, and none of them got
    a result: nothing that counts was asked to run, so the exit stays 0."""
    not_run = TestComparison(
        "Lib.A", passed=False, sim_success=False, ran=False, mark=ON_PYTHON
    )
    assert st.nothing_ran([not_run]) is False
    assert st.exit_code([not_run]) == 0


# -- R3f: a marked test that ran and failed counts against nothing ------------


def test_a_marked_failure_counts_against_nothing():
    unmarked = TestComparison("Lib.D", passed=False)
    assert st.counts_against(_marked_fail()) is False
    assert st.counts_against(unmarked) is True
    assert st.exit_code([_passing(), _marked_fail()]) == 0
    assert st.exit_code([_marked_fail(), unmarked]) == 1


def test_verdicts_json_says_the_mark_and_a_marked_failure_exits_0():
    doc = build_verdicts([_passing(), _skipped(), _marked_fail()], [])
    assert doc["exit_code"] == 0
    assert [(t["model_id"], t["status"], t["mark"]) for t in doc["tests"]] == [
        ("Lib.A", "PASS", None),
        ("Lib.B", "SKIPPED", "Python: r"),
        ("Lib.C", "FAIL", "Python: r"),
    ]
    assert doc["tests"][1]["message"] == "skipped on Python: r"


def test_verdicts_json_totals_leave_skipped_out_of_total_and_always_say_it():
    doc = build_verdicts([_passing(), _skipped()], [])
    assert doc["totals"] == {
        "total": 1,
        "PASS": 1,
        "FAIL": 0,
        "SIM_FAIL": 0,
        "NO_REF": 0,
        "NOT RUN": 0,
        "SKIPPED": 1,
    }
    assert build_verdicts([_passing()], [])["totals"]["SKIPPED"] == 0


# -- the words ----------------------------------------------------------------


def test_a_mark_on_the_entry_reads_on_every_backend(capsys):
    print_report([_skipped(mark=SkipMark(EVERY_BACKEND, "broken"))])
    lines = _plain(capsys.readouterr().out)
    assert "  SKIPPED  Lib.B (skipped on every backend: broken)" in lines
    assert "Results: 0/0 passed · 1 skipped" in lines


def test_a_reason_with_quotes_brackets_and_ampersands_survives_every_text_surface(
    tmp_path, capsys
):
    comps = [_skipped(mark=SkipMark("Python", AWKWARD))]
    # the console: as written
    print_report(comps)
    assert f"  SKIPPED  Lib.B (skipped on Python: {AWKWARD})" in _plain(
        capsys.readouterr().out
    )
    # JUnit: a file a parser reads back to the same text
    case = _junit(tmp_path, comps).find(".//testcase[@name='Lib.B']")
    assert case is not None
    skipped = case.find("skipped")
    assert skipped is not None
    assert skipped.get("message") == f"skipped on Python: {AWKWARD}"
    # the dashboard: escaped once, never raw markup
    work = tmp_path / "work"
    work.mkdir()
    render_final(work, comparisons=comps)
    page = (work / "dashboard.html").read_text(encoding="utf-8")
    assert "skipped on Python: say &#34;no&#34; &lt;now&gt; &amp; then" in page
    assert "<now>" not in page


# -- the dashboard rows -------------------------------------------------------


def _rows(page: str) -> dict[str, str]:
    body = page.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
    return {
        re.search(r'data-model="([^"]+)"', tr).group(1): tr  # type: ignore[union-attr]
        for tr in re.findall(r"<tr\b.*?</tr>", body, flags=re.S)
    }


def test_a_skipped_row_says_its_reason_and_keeps_no_timing_of_an_older_run(
    tmp_path,
):
    work = tmp_path / "work"
    work.mkdir()
    # The last run that ran Lib.B left its row and its kept walls.
    (work / "status.json").write_text(
        json.dumps(
            {
                "total": 1,
                "elapsed": 5.0,
                "tests": [
                    {
                        "test_key": "test_0001",
                        "model_id": "Lib.B",
                        "status": "passed",
                        "elapsed": 2.5,
                        "worker_id": 0,
                        "report_dir": "test_0001",
                        "detail": "an older message",
                    }
                ],
                "updated_at": 6.0,
            }
        ),
        encoding="utf-8",
    )
    kept = work / "test_0001"
    kept.mkdir()
    (kept / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.B",
                "translation_wall": 1.75,
                "sim_wall": 9.25,
                "elapsed": 11.0,
                "statistics": {},
                "run_metadata": None,
            }
        ),
        encoding="utf-8",
    )
    # Scored as it ran, the row shows what that run kept and said.
    render_final(work, comparisons=[_passing("Lib.B")])
    ran = _rows((work / "dashboard.html").read_text(encoding="utf-8"))["Lib.B"]
    assert "9.25" in ran and "an older message" in ran

    render_final(work, comparisons=[_skipped(), _marked_fail()])
    rows = _rows((work / "dashboard.html").read_text(encoding="utf-8"))

    assert 'data-status="skipped"' in rows["Lib.B"]
    assert "skipped on Python: r" in rows["Lib.B"]
    assert "an older message" not in rows["Lib.B"]
    assert "9.25" not in rows["Lib.B"] and "2.5" not in rows["Lib.B"]
    assert 'data-status="fail"' in rows["Lib.C"]
    assert "it fails (marked skip on Python)" in rows["Lib.C"]


def test_the_dashboard_has_a_skipped_pill():
    assert st.SKIPPED in st.PILL_STATUSES
    assert st.STATUS_CLASSES[st.SKIPPED] == "skipped"


# -- the tool -----------------------------------------------------------------


def test_the_test_pages_strip_says_why_a_test_was_skipped():
    from dstf.authoring.situation import Situation, strip_for

    strip = strip_for(status=st.SKIPPED, mark=ON_PYTHON)
    assert (strip.situation, strip.tone, strip.sentence) == (
        Situation.SKIPPED,
        "grey",
        "Skipped on Python: r. Queue it to run it anyway.",
    )
    # a reason that ends its own sentence is not given a second stop
    ended = strip_for(status=st.SKIPPED, mark=SkipMark(EVERY_BACKEND, "Broken."))
    assert (
        ended.sentence == "Skipped on every backend: Broken. Queue it to run it anyway."
    )


def test_skipped_wears_a_neutral_of_its_own_in_both_themes():
    from dstf.reporting import theme

    for name in ("default", "dark"):
        colours = theme.status_colours(name)
        assert colours["skipped"]["fill"] == theme.SKIPPED_FILL
        assert colours["skipped"] != colours["not-run"]
        assert f"--dstf-skipped-fill: {theme.SKIPPED_FILL};" in theme.css_variables(
            name
        )
