"""The dashboard's notice of a run that simulated only the tests a change
touches (`run --changed-since`), in a real browser: it stays in the sticky
header as the table scrolls, the table's header row still sits right under
that header when the notice wraps, and it is drawn in the theme's warning
tint in both bundled themes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.playwright

from dstf.reporting.dashboard_render import render_final, render_live  # noqa: E402
from dstf.reporting.theme import status_colours  # noqa: E402

ROWS = 60

SELECTION = {
    "changed_since": "origin/main",
    "commits": ["da94e89"],
    "files": 2,
    "selected": ROWS,
    "of": 340,
    "could_not_be_asked": 0,
}

MEASURE = """() => {
  const chrome = document.getElementById('sticky-chrome').getBoundingClientRect();
  const th = document.querySelector('#results-table thead tr:first-child th')
    .getBoundingClientRect();
  const notice = document.getElementById('run-notice');
  const box = notice.getBoundingClientRect();
  return {chromeBottom: chrome.bottom, thTop: th.top, top: box.top,
          bottom: box.bottom, height: box.height,
          fontSize: parseFloat(getComputedStyle(notice).fontSize),
          background: getComputedStyle(notice).backgroundColor,
          text: notice.innerText};
}"""


def _rgb(colour: str) -> str:
    """``#rrggbb`` as the browser computes it."""
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgb({r}, {g}, {b})"


def _page(work_dir: Path, theme: str, mode: str) -> Path:
    work_dir.mkdir(parents=True)
    snapshot = {
        "total": ROWS,
        "elapsed": 5.0,
        "eta_seconds": None,
        "start_wall": 1000.0,
        "updated_at": 1005.0,
        "counts": {"passed": ROWS},
        "selection": SELECTION,
        "tests": [
            {
                "test_key": f"test_{i:04d}",
                "model_id": f"Lib.Examples.Model{i:03d}",
                "status": "passed",
                "elapsed": 1.0,
                "worker_id": 0,
                "report_dir": f"test_{i:04d}",
            }
            for i in range(1, ROWS + 1)
        ],
    }
    (work_dir / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (render_final if mode == "final" else render_live)(work_dir, theme=theme)
    page = work_dir / "dashboard.html"
    # the live page refreshes itself every 5 s: not while it is measured
    html = page.read_text(encoding="utf-8")
    page.write_text(
        html.replace('<meta http-equiv="refresh" content="5">', ""), encoding="utf-8"
    )
    return page


@pytest.mark.parametrize("mode", ["final", "live"])
@pytest.mark.parametrize("theme", ["default", "dark"])
def test_the_notice_stays_in_the_header_over_the_scrolled_table(
    tmp_path, playwright_browser, theme, mode
):
    page_file = _page(tmp_path / f"{theme}-{mode}", theme, mode)
    page = playwright_browser.new_page(viewport={"width": 520, "height": 500})
    try:
        page.goto(page_file.as_uri())
        page.wait_for_timeout(200)
        page.evaluate("window.scrollTo(0, window.innerHeight)")
        page.wait_for_timeout(200)
        assert page.evaluate("window.scrollY") > 0
        m = page.evaluate(MEASURE)
    finally:
        page.close()

    ran = "were run" if mode == "final" else "are being run"
    assert m["text"].startswith(f"Not a full run. {ROWS} of 340 tests {ran}: ")
    # wrapped at this width: one line with its padding is under 2.5 em
    assert m["height"] > 2.5 * m["fontSize"], m
    # still on screen, inside the sticky header
    assert 0 <= m["top"] and m["bottom"] <= m["chromeBottom"], m
    # the table's header row right under the sticky header, not under it
    # or below a gap
    assert abs(m["thTop"] - m["chromeBottom"]) < 1, m
    assert m["background"] == _rgb(status_colours(theme)["warn"]["tint"])
