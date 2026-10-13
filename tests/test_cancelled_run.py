"""A cancelled run is a RECORDED state: never "failed", never a result.

A test whose simulation was stopped on request leaves whatever the simulator
had flushed (Dymola's `stop` file leaves a `dsres.mat` to the reached time
AND a `dsfinal.txt`). Left "pending", its manifest entry falls through to
the `status.json` cross-check, where an earlier run's "failed" row for the
same model is waiting to be applied to it. So the manifest says "cancelled",
that entry is authoritative, and every reader treats it as a test with no
result.

What a person then reads of it (D150): CANCELLED on the dashboard — the
word of its live row, on NOT RUN's class — and NOT RUN, the classifier's
verdict, where there is no row to read why from (the console, JUnit). It
counts as NOT RUN everywhere: not a failure, unless nothing else ran.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

from conftest import make_test_model, python_example_copy
from dstf.backends import select_backend
from dstf.backends.base import run_verdicts
from dstf.backends.types import TestRunResult
from dstf.storage.batch_manifest import (
    CANCELLED_RUN_STATUS,
    BatchManifest,
    unfinished_run_models,
)


def _manifest(tmp_path, status: str | None = None) -> BatchManifest:
    entry: dict = {"model_id": "Lib.A", "ref_id": None}
    if status is not None:
        entry["last_run_status"] = status
    return BatchManifest(work_dir=tmp_path, manifest={"test_0001": entry})


def _stale_failed_row(work_dir) -> None:
    (work_dir / "status.json").write_text(
        json.dumps({"tests": [{"model_id": "Lib.A", "status": "failed"}]}),
        encoding="utf-8",
    )


def test_a_cancelled_result_is_recorded_cancelled_not_failed(tmp_path):
    manifest = _manifest(tmp_path, "pending")
    manifest.record_result(
        TestRunResult(
            model_id="Lib.A",
            test_key="test_0001",
            success=False,
            error_message="Cancelled",
            cancelled=True,
        )
    )
    on_disk = json.loads((tmp_path / "batch_manifest.json").read_text())
    assert (
        on_disk["test_0001"]["last_run_status"] == CANCELLED_RUN_STATUS == "cancelled"
    )


def test_no_stale_status_row_is_applied_to_a_cancelled_entry(tmp_path):
    _stale_failed_row(tmp_path)
    # The hazard, for contrast: the same entry left pending takes the row.
    assert run_verdicts(tmp_path, [_manifest(tmp_path, "pending")]) == {
        "Lib.A": "failed"
    }
    assert run_verdicts(tmp_path, [_manifest(tmp_path, "cancelled")]) == {}


def test_a_cancelled_entry_blocks_acceptance_like_an_unfinished_one(tmp_path):
    _manifest(tmp_path, "cancelled").save()
    assert unfinished_run_models(tmp_path) == {"Lib.A"}


def test_a_cancelled_test_reads_back_as_no_result(tmp_path):
    """The file the cancelled run left parses — and is not read."""
    config, tests = python_example_copy(tmp_path)
    backend = select_backend(config, persistent=False)
    backend.quiet = True
    ramp = next(t for t in tests if t.model_id.endswith("SimpleRamp"))
    backend.run_tests([ramp])
    assert backend.read_last_results([ramp])[ramp.model_id].success

    manifest = BatchManifest.load(config.work_dir / "batch_manifest.json")
    key = next(iter(manifest.manifest))
    manifest.record_result(
        TestRunResult(
            model_id=ramp.model_id, test_key=key, success=False, cancelled=True
        )
    )

    assert backend.read_last_results([ramp]) == {}


def test_a_cancelled_run_in_hand_yields_no_result(tmp_path):
    """`read_results` with the cancelled run result itself (the queue's case):
    not the failed result a run that broke gets."""
    config, _ = python_example_copy(tmp_path)
    backend = select_backend(config, persistent=False)
    backend.quiet = True
    test = make_test_model("Lib.A")
    manifest, keyed = backend.key_tests([test])
    rr = TestRunResult(
        model_id="Lib.A", test_key=keyed[0][1], success=False, cancelled=True
    )
    manifest.record_result(rr)
    manifest.results = [rr]

    assert backend.read_results([manifest], [test]) == {}
    assert backend.result_of_run(test, keyed[0][1], rr) is None


# ---------------------------------------------------------------------------
# What a person reads of it: `dstf compare` over a work dir that holds one
# ---------------------------------------------------------------------------

_RAMP = "PythonTestingLib.Examples.SimpleRamp"
_CSV = "PythonTestingLib.Examples.ConstantCsv"


@pytest.fixture
def drained(tmp_path, monkeypatch):
    """The example library after a queue drained with SimpleRamp cancelled:
    ConstantCsv run and passing, PointsCheck never run, then SimpleRamp's
    run stopped — keyed, wiped, recorded and written to status.json by the
    pool's own item path (`ItemRun`), as the queue does around a cancelled
    run. Returns a runner of `dstf compare` and the work dir."""
    from dstf.backends.pool import ItemState, QueueItem
    from dstf.cli import main
    from dstf.pipeline import ItemRun
    from dstf.storage.reference_store import ReferenceStore

    config, tests = python_example_copy(tmp_path)

    def dstf(*args: str) -> int:
        return main(
            ["--config", str(config.config_file), args[0]]
            + ["--work-dir", str(config.work_dir), *args[1:]]
        )

    assert dstf("run", "--filter", _CSV) == 0
    backend = select_backend(config, persistent=False)
    backend.quiet = True
    path = ItemRun(config, backend=backend, store=ReferenceStore(config))
    ramp = next(t for t in tests if t.model_id == _RAMP)
    key = path.begin(ramp)
    stopped = TestRunResult(
        model_id=_RAMP,
        test_key=key,
        success=False,
        error_message="Cancelled",
        cancelled=True,
    )
    assert path.end(ramp, key, stopped) is None
    item = QueueItem(
        ramp,
        state=ItemState.CANCELLED,
        test_key=key,
        worker_id=0,
        run_result=stopped,
        message="Cancelled",
    )
    path.write_status([item])
    return dstf, config.work_dir


def _dashboard_rows(page) -> dict[str, tuple[str, str]]:
    """`{model: (data-status, the badge's word)}` of a dashboard page."""
    html = page.read_text(encoding="utf-8")
    body = html.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
    rows = {}
    for tr in body.split("<tr")[1:]:
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tr.split(">", 1)[0]))
        (word,) = re.findall(r'<span class="status-badge [\w-]+">([^<]*)</span>', tr)
        rows[attrs["data-model"].rsplit(".", 1)[-1]] = (attrs["data-status"], word)
    return rows


def test_the_queue_s_own_page_has_the_cancelled_row(drained):
    """What `write_status` left before any compare: the live page."""
    _dstf, work = drained
    assert json.loads((work / "status.json").read_text())["tests"][0]["status"] == (
        "cancelled"
    )
    assert _dashboard_rows(work / "dashboard.html") == {
        "SimpleRamp": ("not-run", "CANCELLED")
    }


@pytest.mark.parametrize("route", ["console", "report", "html"])
def test_compare_s_dashboard_says_cancelled_and_counts_it_not_run(
    drained, route, tmp_path
):
    """Every page `dstf compare` writes: the cancelled test reads CANCELLED
    on NOT RUN's class — beside a test nobody ran, which reads NOT RUN on
    the same — and the exit code is the tests' that ran: nothing failed
    (D145)."""
    dstf, work = drained
    out = tmp_path / "out.html"
    extra = {
        "console": [],
        "report": ["--report"],
        "html": ["--report-format", "html", "--output", str(out)],
    }[route]

    assert dstf("compare", *extra) == 0

    for page in [work / "dashboard.html"] + ([out] if route == "html" else []):
        assert _dashboard_rows(page) == {
            "SimpleRamp": ("not-run", "CANCELLED"),
            "ConstantCsv": ("pass", "PASS"),
            "PointsCheck": ("not-run", "NOT RUN"),
        }, page


def test_the_console_and_junit_say_the_verdict_not_run(drained, tmp_path, capsys):
    """Neither has a live row to read why from: both state the classifier's
    verdict, and count the test where the dashboard's pill does."""
    dstf, _work = drained
    capsys.readouterr()

    assert dstf("compare") == 0
    out = capsys.readouterr().out
    (line,) = [ln for ln in out.splitlines() if _RAMP in ln]
    assert "NOT RUN" in line and "CANCELLED" not in out
    assert "NOT RUN: 2" in out  # with PointsCheck, which nobody ran

    xml = tmp_path / "junit.xml"
    assert dstf("compare", "--report-format", "junit", "--output", str(xml)) == 0
    root = ET.parse(xml).getroot()
    totals = {
        key: sum(int(suite.get(key)) for suite in root.iter("testsuite"))
        for key in ("failures", "errors", "skipped")
    }
    assert totals == {"failures": 0, "errors": 0, "skipped": 2}
    (case,) = [tc for tc in root.iter("testcase") if tc.get("name") == _RAMP]
    assert [(c.tag, c.get("message")) for c in case] == [("skipped", "not run")]


def test_a_compare_of_only_the_cancelled_test_exits_1_nothing_ran(drained, capsys):
    """Counted with NOT RUN for the exit code too: a command in which the
    cancelled test is all there is ran nothing (D145 §5)."""
    dstf, work = drained
    capsys.readouterr()

    assert dstf("compare", "--filter", _RAMP) == 1

    assert (
        "Nothing ran: no test in scope has a result (1 NOT RUN) — exit 1."
        in capsys.readouterr().err
    )
    assert _dashboard_rows(work / "dashboard.html") == {
        "SimpleRamp": ("not-run", "CANCELLED")
    }
