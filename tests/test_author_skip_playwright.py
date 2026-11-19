"""A test marked skip in the tool, in Chromium (R4k): `dstf author` over the
two-package FakeLib with D marked skip on Python. Unqueued, its Suite row
reads SKIPPED with its reason, Accept stores nothing of it, and its Test
page's strip says why and offers Queue, with no plot of the result its run
before the mark left; queued from that strip, it is simulated, scored and
offered to Accept. Both themes are looked at: viewport screenshots of the
Suite page and the strip, kept under ``DSTF_KEEP_SHOTS`` when it is set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from playwright.sync_api import expect  # noqa: E402

from test_author_queue_playwright import (  # noqa: E402
    RUN_TIMEOUT,
    _console,
    _suite_status,
)
from test_author_suite_playwright import (  # noqa: E402
    EXPECT_TIMEOUT,
    _box,
    _keep,
    _served,
    _stop,
)
from test_session import B, D  # noqa: E402
from test_session_queue import SIMULATE  # noqa: E402
from test_themes_playwright import switch_theme  # noqa: E402

REASON = "its solver differs <here> & there"
SAID = f"skipped on Python: {REASON}"


def _mark_d(cfg: Path) -> None:
    """D marked skip on Python; its script one that returns x = t, so a
    queued run of it passes against its baseline x = t (its last result,
    left before the mark, is x = 1.001 t)."""
    (cfg.parent.parent / "Examples" / "D.py").write_text(SIMULATE, encoding="utf-8")
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == D]
    entry["backends"] = {"Python": {"skip": REASON}}
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


def test_a_marked_test_is_skipped_on_both_pages_until_it_is_queued(
    tmp_path, playwright_browser
):
    proc, url, page, context, errors, cfg = _served(
        tmp_path, playwright_browser, setup=_mark_d
    )
    try:
        # The Suite: its row SKIPPED, its Message the mark's reason.
        expect(_suite_status(page, D)).to_have_text("SKIPPED", timeout=EXPECT_TIMEOUT)
        row = page.locator(f".ag-row[row-id='{D}']")
        expect(row.locator("[col-id='detail']")).to_have_text(SAID)
        expect(row.locator("[col-id='sim']")).to_have_text("—")
        _keep(page, tmp_path, "skip-suite-light")
        switch_theme(page, "dark")
        expect(_suite_status(page, D)).to_have_text("SKIPPED")
        expect(page.locator(".q-menu")).to_have_count(0)  # the ⋮ menu gone
        _keep(page, tmp_path, "skip-suite-dark")
        switch_theme(page, "default")

        # Accept, D selected beside B: it says what it will not do with D,
        # and the line the write leaves says D was not stored.
        _box(page, B).click()
        _box(page, D).click()
        accept = "Accept 1 result as baselines"
        page.get_by_role("button", name=accept, exact=True).click()
        dialog = page.locator(".q-dialog")
        expect(dialog).to_contain_text(
            f"{D} · SKIPPED · {SAID} — queue it to run it", timeout=EXPECT_TIMEOUT
        )
        dialog.get_by_role("button", name=accept, exact=True).click()
        note = page.locator(".dstf-queue-notes")
        expect(note).to_contain_text(
            f"not stored: {D} is marked skip on Python ({REASON})",
            timeout=EXPECT_TIMEOUT,
        )

        # Its page: the strip says why and offers Queue; no plot draws the
        # result its run before the mark left.
        page.goto(f"{url}test/{D}")
        strip = page.locator(".dstf-strip-text")
        expect(strip).to_have_text(
            f"Skipped on Python: {REASON}. Queue it to run it anyway.",
            timeout=EXPECT_TIMEOUT,
        )
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text("SKIPPED")
        queue = page.locator(".dstf-strip").get_by_role("button", name="Queue 1")
        expect(queue).to_be_visible()
        expect(page.get_by_text("no plots: this test has no result")).to_be_visible()
        expect(page.locator(".js-plotly-plot")).to_have_count(0)
        _keep(page, tmp_path, "skip-strip-light")
        switch_theme(page, "dark")
        expect(strip).to_contain_text("Queue it to run it anyway.")
        expect(page.locator(".q-menu")).to_have_count(0)
        _keep(page, tmp_path, "skip-strip-dark")
        switch_theme(page, "default")

        # Queued from the strip: simulated, scored, and Accept offers it.
        queue.click()
        dialog = page.locator(".q-dialog")
        dialog.get_by_role("button", name="Queue 1", exact=True).click()
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text(
            "PASS", timeout=RUN_TIMEOUT
        )
        expect(strip).not_to_contain_text("Skipped")
        page.goto(url)
        expect(_suite_status(page, D)).to_have_text("PASS", timeout=EXPECT_TIMEOUT)
        # The tab kept its selection, B and D: both are offered now.
        offered = page.get_by_role(
            "button", name="Accept 2 results as baselines", exact=True
        ).first
        expect(offered).to_be_enabled(timeout=EXPECT_TIMEOUT)
        offered.click()
        expect(page.locator(".q-dialog")).to_contain_text(
            f"{D} · PASS · will store", timeout=EXPECT_TIMEOUT
        )
        assert _console(errors) == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            _stop(proc)
