"""The simulation queue in a real browser (D150): `dstf author` over the
two-package FakeLib, its `simulate` scripts replaced by ones a test can
hold, so every state of the queue is on the page for as long as the test
looks at it. The Python backend runs one subprocess per test — real runs,
real result files, a real cancel.

What only a browser shows: the panel's rows and their buttons under a real
click, the header's indicator and its colour, the strip of a queued, a
running and a cancelled test, the panel's place at a narrow width, and a
Stop server that leaves no simulation behind."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import psutil
import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from dstf.authoring.situation import CANCELLED_SENTENCE  # noqa: E402
from test_author_suite_playwright import (  # noqa: E402
    EXPECT_TIMEOUT,
    _box,
    _keep,
    _served,
)
from test_session import CASES_X, A, B, C, D, G  # noqa: E402
from test_session_queue import SIMULATE  # noqa: E402

pytestmark = pytest.mark.playwright

#: A run's end: a subprocess, its result read back, a tick.
RUN_TIMEOUT = 60_000


def _gated(*boom: str):
    """A `setup` for `_served`: every test's script is `SIMULATE`; the ones
    named in ``boom`` raise."""

    def setup(cfg: Path) -> None:
        examples = cfg.parent.parent / "Examples"
        for script in examples.rglob("*.py"):
            script.write_text(SIMULATE, encoding="utf-8")
        (examples / "A.scale").write_text("1.25", encoding="utf-8")
        for model_id in boom:
            (examples / f"{_short(model_id)}.boom").write_text("", encoding="utf-8")

    return setup


def _short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


def _hold(cfg: Path, *models: str) -> None:
    for m in models:
        (cfg.parent.parent / "Examples" / f"{_short(m)}.hold").write_text("")


def _release(cfg: Path, *models: str) -> None:
    for m in models:
        (cfg.parent.parent / "Examples" / f"{_short(m)}.hold").unlink()


def _stop(proc) -> None:
    proc.terminate()
    proc.wait(30)


def _queue(page, caption: str) -> None:
    """The Suite toolbar's Queue icon, whose name is its caption, then the
    dialog's button of the same caption."""
    page.get_by_role("button", name=caption, exact=True).first.click()
    page.locator(".q-dialog").get_by_role("button", name=caption, exact=True).click()


def _row(page, model_id: str):
    """The model's row in the queue panel — its last, when it ran twice."""
    return page.locator(f".dstf-queue-row[data-model='{model_id}']").last


def _words(page, model_id: str):
    return _row(page, model_id).locator(".dstf-queue-words")


def _badge(page, model_id: str):
    return _row(page, model_id).locator(".dstf-badge")


def _suite_status(page, model_id: str):
    return page.locator(f".ag-row[row-id='{model_id}'] .dstf-badge-cell")


#: What NiceGUI's client warns on EVERY load of a Test page, with or without
#: a queue: the page adds two listeners to the layout after the handshake
#: (`rail.py`'s drawer width, `test_page.py`'s plot visibility). Not the
#: queue's; a test that visits a Test page reads its console past it.
LAYOUT_LISTENERS = (
    "Event listeners changed after initial definition. Re-rendering affected elements."
)


def _console(errors: list[str]) -> list[str]:
    return [e for e in errors if e != LAYOUT_LISTENERS]


INDICATOR = "a.dstf-indicator"
COUNTS = ".dstf-queue-head .dstf-why >> nth=0"
NUMBERS = (
    "() => [...document.querySelectorAll('.dstf-queue-num')].map(e => e.textContent)"
)


#: Run in the server before the CLI: a click's read waits on a file.
CLICK_HOOKS = Path(__file__).with_name("slow_click_hooks.py")


def test_a_click_shows_its_tests_as_taken_while_the_library_is_checked(
    tmp_path, playwright_browser
):
    """Between a Queue click and the rows nothing on the page moved while
    the files were being checked. The tests the click names read QUEUED at
    once — in the table, in the panel, in the header's count — and a second
    click on them says they are already being queued."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, setup=_gated(), hooks=CLICK_HOOKS
    )
    hold = cfg.parent / "hold-click"
    try:
        hold.write_text("", encoding="utf-8")
        _hold(cfg, A)
        for model_id in (A, B):
            _box(page, model_id).click()
        _queue(page, "Queue 2")

        # The read is out: no item exists, and both tests show as taken.
        for model_id in (A, B):
            expect(_words(page, model_id)).to_have_text(
                "waiting · checking the library", timeout=EXPECT_TIMEOUT
            )
            expect(_badge(page, model_id)).to_have_text("QUEUED")
            expect(_suite_status(page, model_id)).to_have_text("QUEUED")
            expect(_row(page, model_id).get_by_role("button")).to_have_count(0)
        expect(page.locator(INDICATOR)).to_have_text("2 queued")
        expect(page.locator(COUNTS)).to_have_text("2 queued")
        assert page.evaluate(NUMBERS) == ["1", "2"]
        _keep(page, tmp_path, "queue-checking")

        # The same click again, while the first is being read: said in the
        # dialog, and nothing is queued twice.
        _queue(page, "Queue 2")
        dialog = page.locator(".q-dialog")
        expect(dialog).to_contain_text(
            "all 2 tests are already being queued", timeout=EXPECT_TIMEOUT
        )
        dialog.get_by_role("button", name="Close").click()
        expect(dialog).to_have_count(0)
        expect(page.locator(".dstf-queue-row")).to_have_count(2)

        # The read is back: the rows are the queue's own.
        hold.unlink()
        expect(_words(page, A)).to_have_text(
            re.compile(r"^running · "), timeout=RUN_TIMEOUT
        )
        expect(_words(page, B)).to_have_text("waiting · 1 ahead")
        expect(_row(page, B).get_by_role("button", name="Remove")).to_have_count(1)
        expect(page.locator(INDICATOR)).to_have_text("1 running · 1 queued")
        _release(cfg, A)
        for model_id in (A, B):
            expect(_words(page, model_id)).to_have_text(
                re.compile(r"^finished"), timeout=RUN_TIMEOUT
            )
        assert _console(errors) == []
    finally:
        hold.unlink(missing_ok=True)
        _stop(proc)


def test_an_earlier_runs_row_says_how_it_ended_and_the_latest_carries_the_verdict(
    tmp_path, playwright_browser
):
    """A test queued twice has two rows. Both read the test's verdict as it
    stands — the later run's — and both offered Accept, which accepts the
    later run's results under the earlier row's name. The earlier row says
    how its run ended and opens the test; the verdict and what acts on the
    test are the latest row's."""
    proc, _url, page, _context, errors, _cfg = _served(
        tmp_path, playwright_browser, setup=_gated()
    )
    try:
        _box(page, D).click()
        _queue(page, "Queue 1")
        expect(_words(page, D)).to_have_text(
            re.compile(r"^finished"), timeout=RUN_TIMEOUT
        )
        expect(_badge(page, D)).to_have_text("PASS")
        only = page.locator(f".dstf-queue-row[data-model='{D}']")
        expect(only).to_have_count(1)
        assert only.get_by_role("button").count() == 3  # Open test, Accept, Queue

        _queue(page, "Queue 1")  # the same test again
        rows = page.locator(f".dstf-queue-row[data-model='{D}']")
        expect(rows).to_have_count(2, timeout=EXPECT_TIMEOUT)
        expect(rows.last.locator(".dstf-queue-words")).to_have_text(
            re.compile(r"^finished"), timeout=RUN_TIMEOUT
        )
        first, last = rows.first, rows.last
        expect(first.locator(".dstf-queue-words")).to_have_text(
            re.compile(r"^finished · \d+ s$")
        )
        expect(first.locator(".dstf-badge")).to_have_count(0)
        expect(first.get_by_role("button")).to_have_count(1)
        expect(first.get_by_role("button", name="Open test")).to_have_count(1)
        expect(last.locator(".dstf-badge")).to_have_text("PASS")
        assert last.get_by_role("button").count() == 3
        assert page.evaluate(NUMBERS) == ["1", "2"]
        expect(page.locator(COUNTS)).to_have_text("2 finished")
        _keep(page, tmp_path, "queue-two-runs")
        assert _console(errors) == []
    finally:
        _stop(proc)


def test_three_queued_one_removed_one_cancelled_one_finished_and_accepted(
    tmp_path, playwright_browser
):
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, setup=_gated()
    )
    try:
        expect(page.locator(INDICATOR)).to_have_text("idle")
        expect(page.locator(COUNTS)).to_have_text("queue empty")
        _keep(page, tmp_path, "queue-header-idle")

        # Three queued, one worker: numbered in run order.
        _hold(cfg, A, B)
        for model_id in (A, B, D):
            _box(page, model_id).click()
        _queue(page, "Queue 3")
        expect(page.locator(INDICATOR)).to_have_text(
            "1 running · 2 queued", timeout=EXPECT_TIMEOUT
        )
        assert page.evaluate(NUMBERS) == ["1", "2", "3"]
        expect(_words(page, A)).to_have_text(
            re.compile(r"^running · \d+ s · worker 1$")
        )
        expect(_words(page, B)).to_have_text("waiting · 1 ahead")
        expect(_words(page, D)).to_have_text("waiting · 2 ahead")
        for model_id, word in ((A, "RUNNING"), (B, "QUEUED"), (D, "QUEUED")):
            expect(_badge(page, model_id)).to_have_text(word)
            expect(_suite_status(page, model_id)).to_have_text(word)
        expect(page.locator(COUNTS)).to_have_text("1 running · 2 queued")
        # the seconds move without a repaint of the rows
        first = _words(page, A).text_content()
        expect(_words(page, A)).not_to_have_text(first, timeout=EXPECT_TIMEOUT)
        _keep(page, tmp_path, "queue-suite-mid-queue")
        assert errors == [], "queued"

        # Remove the third before it starts: its row goes, its verdict stands.
        _row(page, D).get_by_role("button", name="Remove").click()
        expect(page.locator(".dstf-queue-row")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        expect(page.locator(INDICATOR)).to_have_text("1 running · 1 queued")
        expect(_suite_status(page, D)).to_have_text("PASS")
        assert errors == [], "removed"

        # Cancel the running one: cancelled, and the next starts.
        _row(page, A).get_by_role("button", name="Cancel").click()
        expect(_words(page, A)).to_have_text("cancelled", timeout=RUN_TIMEOUT)
        expect(_badge(page, A)).to_have_text("CANCELLED")
        expect(_suite_status(page, A)).to_have_text("CANCELLED")
        expect(page.locator(INDICATOR)).to_have_text("1 running", timeout=RUN_TIMEOUT)
        expect(_words(page, B)).to_have_text(re.compile(r"^running · "))
        assert errors == [], "cancelled"

        # The running test's page: its strip, its Cancel, no result yet.
        _row(page, B).get_by_role("link").click()
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.B$"), timeout=EXPECT_TIMEOUT
        )
        strip = page.locator(".dstf-strip-text")
        expect(strip).to_have_text(
            re.compile(r"^Running on worker 1 — \d+ s$"), timeout=EXPECT_TIMEOUT
        )
        expect(
            page.locator(".dstf-strip").get_by_role("button", name="Cancel")
        ).to_be_visible()
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text("NOT RUN")
        expect(page.locator(INDICATOR)).to_have_text("1 running")
        _keep(page, tmp_path, "queue-test-running")
        assert _console(errors) == [], "running page"

        # The cancelled test's page: the sentence and the one next action.
        page.locator(INDICATOR).click()  # the indicator opens the Suite's queue
        expect(page).to_have_url(re.compile(r"/#queue$"), timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-queue-row")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        _row(page, A).get_by_role("button", name="Open test").click()
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-strip-text")).to_have_text(
            CANCELLED_SENTENCE, timeout=EXPECT_TIMEOUT
        )
        expect(
            page.locator(".dstf-strip").get_by_role("button", name="Queue again")
        ).to_be_visible()
        _keep(page, tmp_path, "queue-test-cancelled")
        assert _console(errors) == [], "cancelled page"
        page.get_by_role("link", name="← Suite").click()
        expect(page.locator(".dstf-queue-row")).to_have_count(2, timeout=EXPECT_TIMEOUT)

        # Two workers, and an append while one runs: A, B and D are still
        # checked; B runs, so two are queued and the caption says so.
        workers = page.locator(".dstf-queue-workers input")
        workers.fill("2")
        workers.blur()
        _hold(cfg, D)
        _queue(page, "Queue 2 (1 already queued)")
        expect(page.locator(INDICATOR)).to_have_text(
            "2 running · 1 queued", timeout=RUN_TIMEOUT
        )
        assert page.evaluate(NUMBERS) == ["1", "2", "3", "4"]
        assert _console(errors) == [], "appended"
        expect(_words(page, A)).to_have_text(
            re.compile(r"^running · \d+ s · worker 2$")
        )
        expect(_words(page, D)).to_have_text("waiting · 2 ahead")

        # The second finishes: its badge is its verdict; Open test, Accept.
        _release(cfg, B)
        expect(_words(page, B)).to_have_text(
            re.compile(r"^finished · \d+ s$"), timeout=RUN_TIMEOUT
        )
        expect(_badge(page, B)).to_have_text("NO_REF")
        expect(_suite_status(page, B)).to_have_text("NO_REF")
        expect(_row(page, B).get_by_role("button", name="Open test")).to_be_visible()
        accept = "Accept 1 result as baselines"
        _row(page, B).get_by_role("button", name=accept, exact=True).click()
        page.locator(".q-dialog").get_by_role("button", name=accept, exact=True).click()
        expect(page.locator(".dstf-queue-notes")).to_contain_text(
            "Accept: stored 1 baseline(s)", timeout=EXPECT_TIMEOUT
        )
        expect(_suite_status(page, B)).to_have_text("PASS", timeout=EXPECT_TIMEOUT)
        expect(_badge(page, B)).to_have_text("PASS")
        assert _console(errors) == [], "accepted"

        # Everything ends; Clear finished empties the panel, not the results.
        _release(cfg, A, D)
        expect(page.locator(INDICATOR)).to_have_text("idle", timeout=RUN_TIMEOUT)
        expect(page.locator(COUNTS)).to_have_text("3 finished · 1 cancelled")
        expect(_suite_status(page, A)).to_have_text("FAIL")
        page.get_by_role("button", name="Clear finished").click()
        expect(page.locator(".dstf-queue-row")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        expect(page.locator(COUNTS)).to_have_text("queue empty")
        expect(_suite_status(page, A)).to_have_text("FAIL")
        assert _console(errors) == []
        assert errors.count(LAYOUT_LISTENERS) == 2  # the two Test pages opened
    finally:
        _stop(proc)


def test_an_edit_made_while_its_test_runs_is_scored_and_a_raise_is_said(
    tmp_path, playwright_browser
):
    """A's tolerance is staged while A RUNS — the Session's seam: the page
    of a running test has no result and draws no criterion to edit — and
    its verdict when it ends is the edited one. B's `simulate` raises: its
    row says the Python backend's message, one line, the whole on hover."""
    proc, url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, hooks=True, setup=_gated(B)
    )
    try:
        expect(_suite_status(page, A)).to_have_text("FAIL")
        _hold(cfg, A)
        _box(page, A).click()
        _queue(page, "Queue 1")
        expect(_words(page, A)).to_have_text(
            re.compile(r"^running · "), timeout=RUN_TIMEOUT
        )
        with urllib.request.urlopen(f"{url}__stage_tolerance?model={A}") as r:
            assert json.loads(r.read()) == {"pending": 1}
        expect(page.get_by_role("button", name="1 pending edit")).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        expect(_words(page, A)).to_have_text(re.compile(r"^running · "))  # not refused

        _release(cfg, A)
        expect(_words(page, A)).to_have_text(
            re.compile(r"^finished · "), timeout=RUN_TIMEOUT
        )
        expect(_suite_status(page, A)).to_have_text("PASS")
        expect(_badge(page, A)).to_have_text("PASS")

        _box(page, A).click()
        _box(page, B).click()
        _queue(page, "Queue 1")
        expect(_words(page, B)).to_have_text(
            re.compile(r"^failed: "), timeout=RUN_TIMEOUT
        )
        expect(_words(page, B)).to_have_text(
            "failed: Python execution failed: RuntimeError: boom in simulate"
        )
        expect(_badge(page, B)).to_have_text("SIM_FAIL")
        expect(_suite_status(page, B)).to_have_text("SIM_FAIL")
        _words(page, B).hover()
        expect(page.locator(".q-tooltip")).to_contain_text(
            "Traceback (most recent call last):", timeout=EXPECT_TIMEOUT
        )
        assert errors == []
    finally:
        _stop(proc)


FAIL_DARK = """() => {
  const probe = document.createElement('span');
  probe.style.color = 'var(--dstf-fail-dark)';
  document.body.appendChild(probe);
  const want = getComputedStyle(probe).color;
  probe.remove();
  return [getComputedStyle(document.querySelector('a.dstf-indicator')).color, want];
}"""


def test_no_worker_can_start_fails_the_waiting_tests_and_reddens_the_header(
    tmp_path, playwright_browser
):
    message = "No Stub worker could start — worker 0: licence server unreachable"
    proc, url, page, _context, errors, _cfg = _served(
        tmp_path, playwright_browser, hooks=True, setup=_gated()
    )
    try:
        with urllib.request.urlopen(f"{url}__no_workers") as r:
            json.loads(r.read())
        _box(page, A).click()
        _box(page, B).click()
        _queue(page, "Queue 2")

        for model_id in (A, B):
            expect(_words(page, model_id)).to_have_text(
                f"failed: {message}", timeout=RUN_TIMEOUT
            )
            expect(_badge(page, model_id)).to_have_text("NOT RUN")
        # nothing is left queued, and nothing ran: the verdicts stand
        expect(page.locator(COUNTS)).to_have_text("2 failed")
        expect(_suite_status(page, A)).to_have_text("FAIL")
        expect(_suite_status(page, B)).to_have_text("NO_REF")
        indicator = page.locator(INDICATOR)
        expect(indicator).to_have_class(re.compile(r"dstf-indicator-failed"))
        expect(indicator).to_have_text(re.compile(r"^No Stub worker could start"))
        got, want = page.evaluate(FAIL_DARK)
        assert got == want, (got, want)
        expect(page.locator(".dstf-queue-error")).to_have_text(message)
        _keep(page, tmp_path, "queue-header-failed")

        page.get_by_role("button", name="Clear finished").click()
        expect(indicator).to_have_text("idle", timeout=EXPECT_TIMEOUT)
        expect(indicator).not_to_have_class(re.compile(r"dstf-indicator-failed"))
        expect(page.locator(".dstf-queue-error")).to_be_hidden()
        assert errors == []
    finally:
        _stop(proc)


def test_stop_server_asks_about_the_queue_and_leaves_no_simulation(
    tmp_path, playwright_browser
):
    proc, _url, page, _context, _errors, cfg = _served(
        tmp_path, playwright_browser, setup=_gated()
    )
    try:
        _hold(cfg, A)
        _box(page, A).click()
        _box(page, G).click()
        _queue(page, "Queue 2")
        expect(page.locator(INDICATOR)).to_have_text(
            "1 running · 1 queued", timeout=RUN_TIMEOUT
        )
        children = psutil.Process(proc.pid).children(recursive=True)
        assert children, "the held simulation is a child of the server"

        page.locator(".dstf-header-menu").click()
        page.locator(".q-menu").get_by_text("Stop server", exact=True).click()
        dialog = page.locator(".q-dialog")
        expect(dialog).to_contain_text(
            "2 tests are queued or running — stop anyway?", timeout=EXPECT_TIMEOUT
        )
        dialog.get_by_role("button", name="Stop anyway").click()

        proc.wait(60)
        _gone, alive = psutil.wait_procs(children, timeout=10)
        assert alive == []
        manifest = json.loads((tmp_path / "work" / "batch_manifest.json").read_text())
        status = {e["model_id"]: e.get("last_run_status") for e in manifest.values()}
        assert status[A] == "cancelled" and status[G] == "passed"  # G never started
    finally:
        if proc.poll() is None:
            _stop(proc)


def test_the_panel_is_a_line_above_the_table_at_1024(tmp_path, playwright_browser):
    """Beside the table at 1280 (the Main artboard); under 1200 px there is
    no room for both, and the panel sits above it at the table's width —
    nothing passes the window's edge."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, width=1024, setup=_gated()
    )
    try:
        _hold(cfg, A)
        for model_id in (A, C, D):
            _box(page, model_id).click()
        _queue(page, "Queue 3")
        expect(page.locator(INDICATOR)).to_have_text(
            "1 running · 2 queued", timeout=RUN_TIMEOUT
        )
        panel = page.locator(".dstf-queue").bounding_box()
        grid = page.locator(".dstf-suite-grid").bounding_box()
        assert panel is not None and grid is not None
        assert panel["y"] + panel["height"] <= grid["y"]
        assert abs(panel["width"] - grid["width"]) <= 1
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )
        # the header's counts are not cut at this width
        assert page.locator(INDICATOR).evaluate("e => e.scrollWidth <= e.clientWidth")
        _keep(page, tmp_path, "queue-panel-1024")

        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_timeout(300)
        panel = page.locator(".dstf-queue").bounding_box()
        grid = page.locator(".dstf-suite-grid").bounding_box()
        assert panel is not None and grid is not None
        assert panel["x"] >= grid["x"] + grid["width"] and panel["width"] == 420
        _release(cfg, A)
        expect(page.locator(INDICATOR)).to_have_text("idle", timeout=RUN_TIMEOUT)
        assert errors == []
    finally:
        _stop(proc)


def test_the_rows_scroll_inside_the_panel_above_the_table(tmp_path, playwright_browser):
    """Above the table (under 1200 px) every row of the queue was a row the
    table sat lower by: fifteen uncleared rows put it below the fold. The
    rows scroll inside about a third of the window; beside the table, where
    the panel pushes nothing, they are not held."""
    proc, _url, page, _context, errors, cfg = _served(
        tmp_path, playwright_browser, width=1024, setup=_gated()
    )
    every = (A, B, C, D, G, CASES_X)
    read = (
        "e => ({client: e.clientHeight, scroll: e.scrollHeight,"
        " lefts: new Set([...e.children].map(c => c.getBoundingClientRect().left)).size})"
    )
    try:
        page.set_viewport_size({"width": 1024, "height": 600})
        _hold(cfg, A)
        for model_id in every:
            _box(page, model_id).click()
        _queue(page, "Queue 6")
        expect(page.locator(".dstf-queue-row")).to_have_count(6, timeout=RUN_TIMEOUT)
        # A's start is the queue's last change while A is held: each change
        # draws the rows again, and a row read before it is gone after
        expect(page.locator(INDICATOR)).to_have_text(
            "1 running · 5 queued", timeout=RUN_TIMEOUT
        )
        rows = page.locator(".dstf-queue-rows")
        held = rows.evaluate(read)
        assert held["client"] <= 0.34 * 600 + 1 < held["scroll"], held
        # Held, they are still one column: a NiceGUI column does not wrap
        # (the `no-wrap` it carried for this did nothing, and is gone).
        assert held["lefts"] == 1, held
        grid = page.locator(".dstf-suite-grid").bounding_box()
        assert grid is not None and grid["y"] < 600 - 100, grid
        # The last row is reached by scrolling the rows, not the page.
        _row(page, CASES_X).scroll_into_view_if_needed()
        expect(_row(page, CASES_X)).to_be_in_viewport()
        assert page.evaluate("() => window.scrollY") == 0
        _keep(page, tmp_path, "queue-panel-capped")

        page.set_viewport_size({"width": 1280, "height": 600})
        page.wait_for_function(
            "() => { const e = document.querySelector('.dstf-queue-rows');"
            " return e.clientHeight === e.scrollHeight; }",
            timeout=EXPECT_TIMEOUT,
        )
        _release(cfg, A)
        expect(page.locator(INDICATOR)).to_have_text("idle", timeout=RUN_TIMEOUT)
        assert errors == [], errors
    finally:
        _stop(proc)
