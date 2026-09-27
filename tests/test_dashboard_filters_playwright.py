"""Browser tests for the dashboard's row model: its column filters, its
count pills and its sort.

The page is rendered from ``reporting.rows``: one column list, every row
carrying every column's attributes, the pills drawn from the statuses and
keyed by status class. These drive it in Chromium, because what they pin
is what dashboard.js does with those attributes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.playwright

from dstf.reporting.dashboard_render import render_final  # noqa: E402


def _entry(i: int, model: str, sources: dict | None = None) -> dict:
    return {
        "test_key": f"test_{i:04d}",
        "model_id": model,
        "status": "passed",
        "elapsed": 1.0,
        "worker_id": 0,
        "report_dir": f"test_{i:04d}",
        "field_sources": sources or {},
    }


def _render(work_dir: Path, entries: list[dict], sidecars: dict | None = None) -> Path:
    snap = {
        "total": len(entries),
        "elapsed": 5.0,
        "eta_seconds": None,
        "start_wall": 1.0,
        "updated_at": 6.0,
        "counts": {"passed": len(entries)},
        "tests": entries,
    }
    (work_dir / "status.json").write_text(json.dumps(snap), encoding="utf-8")
    for key, summary in (sidecars or {}).items():
        d = work_dir / "reports" / key
        d.mkdir(parents=True, exist_ok=True)
        (d / "comparison_data.json").write_text(
            json.dumps({"summary": summary}), encoding="utf-8"
        )
    render_final(work_dir)
    return work_dir / "dashboard.html"


def _visible(page) -> list[str]:
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('tbody tr[data-model]'))"
        ".filter(r => r.style.display !== 'none').map(r => r.dataset.model)"
    )


def _type(page, key: str, text: str) -> None:
    box = page.locator(f'input.col-filter[data-col-filter="{key}"]')
    box.fill(text)
    box.dispatch_event("input")


def test_the_resolution_filter_filters(tmp_path, playwright_browser):
    """The Resolution filter had no row attribute to read: any text typed
    there hid every row."""
    html = _render(
        tmp_path,
        [
            _entry(1, "Lib.A", {"stop_time": "annotation"}),
            _entry(2, "Lib.B", {"stop_time": "test_spec"}),
        ],
    )
    page = playwright_browser.new_page()
    page.goto(html.as_uri())
    assert _visible(page) == ["Lib.A", "Lib.B"]
    _type(page, "resolution", "annotation")
    assert _visible(page) == ["Lib.A"]
    page.close()


def _four_verdicts(work_dir: Path) -> Path:
    """SIM_FAIL, PASS, FAIL and a PASS with warnings. Lib.A, FIRST in the
    page's order, has no score: a sort that does not put it last itself
    leaves it where it was."""

    def summary(model, status, worst, warnings=0):
        return {
            "model_id": model,
            "written_at": 2.0,
            "status": status,
            "worst_nrmse": worst,
            "n_warnings": warnings,
        }

    return _render(
        work_dir,
        [_entry(i, m) for i, m in enumerate(("Lib.A", "Lib.B", "Lib.C", "Lib.D"), 1)],
        {
            "test_0001": summary("Lib.A", "SIM_FAIL", None),
            "test_0002": summary("Lib.B", "PASS", 1e-6),
            "test_0003": summary("Lib.C", "FAIL", 0.5),
            "test_0004": summary("Lib.D", "PASS", 1e-3, warnings=2),
        },
    )


def _pills(page) -> dict[str, tuple[str, str]]:
    return page.evaluate(
        "() => Object.fromEntries(Array.from("
        "document.querySelectorAll('.counter[data-pill]')).map(p => "
        "[p.dataset.pill, [p.querySelector('.label').textContent, "
        "p.querySelector('.value').textContent]]))"
    )


def test_the_pills_count_filter_and_clear(tmp_path, playwright_browser):
    page = playwright_browser.new_page()
    page.goto(_four_verdicts(tmp_path).as_uri())
    pills = _pills(page)
    assert pills["pass"] == ["PASS", "2"]
    assert pills["fail"] == ["FAIL", "1"]
    assert pills["sim-fail"] == ["SIM_FAIL", "1"]
    assert pills["no-ref"] == ["NO_REF", "0"]
    assert pills["warnings"] == ["warnings", "1"]
    assert pills["total"] == ["Total", "4"]
    page.click('.counter[data-pill="fail"]')
    assert _visible(page) == ["Lib.C"]
    page.click('.counter[data-pill="total"]')
    assert _visible(page) == ["Lib.A", "Lib.B", "Lib.C", "Lib.D"]
    page.click('.counter[data-pill="warnings"]')
    assert _visible(page) == ["Lib.D"]
    page.close()


def test_a_row_with_no_value_sorts_last_both_ways(tmp_path, playwright_browser):
    page = playwright_browser.new_page()
    page.goto(_four_verdicts(tmp_path).as_uri())

    def order():
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('tbody tr'))"
            ".map(r => r.dataset.model)"
        )

    header = page.locator('th[data-key="nrmse"]')
    header.click()  # a number column descends first
    assert order() == ["Lib.C", "Lib.D", "Lib.B", "Lib.A"]
    header.click()
    assert order() == ["Lib.B", "Lib.D", "Lib.C", "Lib.A"]
    page.close()


def test_a_row_with_no_verdict_moves_the_bar_and_no_pill(tmp_path, playwright_browser):
    """A simulated row the compare phase has not scored reads SIMULATED; it
    is in no pill, and the bar's "pending" segment carries it."""
    page = playwright_browser.new_page()
    page.goto(_render(tmp_path, [_entry(1, "Lib.A"), _entry(2, "Lib.B")]).as_uri())
    widths = page.evaluate(
        "() => Object.fromEntries(Array.from("
        "document.querySelectorAll('#progress-bar span')).map(s => "
        "[s.className, s.style.width]))"
    )
    assert widths["b-pending"] == "100%"
    assert widths["b-pass"] == "0%"
    assert all(v == "0" for k, (_, v) in _pills(page).items() if k != "total")
    badge = page.locator("tbody tr >> .status-badge").first
    assert badge.text_content() == "SIMULATED"
    page.close()


def test_a_state_saved_under_the_old_pill_keys_hides_nothing(
    tmp_path, playwright_browser
):
    """The pills were keyed "passed" / "failed"; a state saved under those
    keys restored a pill no pill carries, which hid every row."""
    html = _four_verdicts(tmp_path)
    ctx = playwright_browser.new_context()
    page = ctx.new_page()
    page.goto(html.as_uri())
    page.evaluate(
        "() => localStorage.setItem('dstf-dashboard-v1:' + location.pathname,"
        " JSON.stringify({activeStatuses: ['passed']}))"
    )
    page.reload()
    assert _visible(page) == ["Lib.A", "Lib.B", "Lib.C", "Lib.D"]
    ctx.close()
