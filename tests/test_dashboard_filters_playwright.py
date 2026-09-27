"""Browser tests for the dashboard's row model: its column filters, its
count pills and its sort — and for ``filters.js``, the typed-filter module
the dashboard (and, in S2, the tool's Suite table) compiles every column
filter through.

The page is rendered from ``reporting.rows``: one column list, every row
carrying every column's attributes, the pills drawn from the statuses and
keyed by status class. These drive it in Chromium, because what they pin
is what the page's JS does with those attributes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.playwright

from dstf.reporting.dashboard_render import render_final  # noqa: E402

_FILTERS_JS = (
    Path(__file__).parents[1]
    / "src"
    / "dstf"
    / "reporting"
    / "templates"
    / "filters.js"
)


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


# ---- filters.js, alone ------------------------------------------------------

CASES = [
    # (kind, query, regex, case, value, expected)
    (
        "text",
        "Pipe",
        False,
        False,
        "Fluid.Pipes.GenericPipe",
        True,
    ),  # substring, any case
    ("text", "pipe", False, True, "Fluid.Pipes.GenericPipe", False),  # Aa on
    ("text", "*Pipe", False, False, "GenericPipe", True),  # glob, anchored
    ("text", "*Pipe", False, False, "GenericPipe_Test", False),
    ("text", "x[1]", False, False, "unitTests.x[1]", True),  # brackets literal
    ("text", "*x[1]", False, False, "unitTests.x[1]", True),  # ... in a glob too
    ("text", "*pipe", False, True, "GenericPipe", False),  # Aa on a glob
    ("text", "^Gen.*Test$", True, False, "GenericPipe_Test", True),  # .* on
    ("text", "gen", True, True, "GenericPipe", False),  # .* and Aa on
    ("text", "(", True, False, "anything", None),  # bad regex -> None (marked bad)
    ("number", ">1e-4", False, False, 2e-4, True),
    ("number", ">1e-4", False, False, 1e-4, False),
    ("number", ">=1e-4", False, False, 1e-4, True),
    ("number", "<5", False, False, 3, True),
    ("number", "1..10", False, False, 10, True),  # inclusive
    ("number", "1..10", False, False, 10.5, False),
    ("number", "0", False, False, 0, True),  # bare number: equal
    ("number", ">", False, False, 1, None),  # bad -> None
    ("number", ">1e-4", False, False, None, False),  # a missing value never matches
    ("number", "<5", False, False, None, False),
]


def test_filters_js_reads_every_query_kind(tmp_path, playwright_browser):
    page_html = tmp_path / "filters.html"
    page_html.write_text(
        "<html><body><script>"
        + _FILTERS_JS.read_text(encoding="utf-8")
        + "</script></body></html>",
        encoding="utf-8",
    )
    page = playwright_browser.new_page()
    page.goto(page_html.as_uri())
    got = page.evaluate(
        """(cases) => cases.map(([kind, q, regex, cs, value]) => {
            try {
              const pred = DstfFilters.compile(kind, q, {regex, case: cs});
              return pred(value);
            } catch (e) {
              if (e instanceof DstfFilters.BadFilter) return null;
              throw e;
            }
        })""",
        [list(c[:5]) for c in CASES],
    )
    assert got == [c[5] for c in CASES]
    assert page.evaluate("() => DstfFilters.compile('text', '', {})") is None
    page.close()


def test_filters_js_can_be_inlined_by_jinja():
    """dashboard.html inlines it with an include: a Jinja delimiter in it
    would be read as template syntax."""
    text = _FILTERS_JS.read_text(encoding="utf-8")
    assert not any(d in text for d in ("{{", "{%", "{#"))


# ---- the dashboard filters through it ---------------------------------------


def test_typed_filters_on_the_dashboard(tmp_path, playwright_browser):
    ctx = playwright_browser.new_context()
    page = ctx.new_page()
    page.goto(_four_verdicts(tmp_path).as_uri())
    everyone = ["Lib.A", "Lib.B", "Lib.C", "Lib.D"]

    # A number filter reads the raw value: Lib.B is 1e-6, Lib.A has none.
    _type(page, "nrmse", ">1e-5")
    assert _visible(page) == ["Lib.C", "Lib.D"]
    _type(page, "nrmse", "")
    _type(page, "nrmse", "=1e-6")
    assert _visible(page) == ["Lib.B"]
    _type(page, "nrmse", "")

    # .* on: the Model filter is a regex.
    page.click("#toggle-regex")
    assert page.get_attribute("#toggle-regex", "aria-pressed") == "true"
    _type(page, "model", r"^Lib\.A$")
    assert _visible(page) == ["Lib.A"]

    # A bad regex marks its input and filters nothing.
    _type(page, "model", "(")
    box = page.locator('input.col-filter[data-col-filter="model"]')
    assert "bad" in (box.get_attribute("class") or "")
    assert _visible(page) == everyone

    # After a reload the toggle is still pressed AND still applied.
    _type(page, "model", r"^Lib\.B$")
    page.reload()
    assert page.get_attribute("#toggle-regex", "aria-pressed") == "true"
    assert _visible(page) == ["Lib.B"]
    ctx.close()


def test_a_number_filter_matches_the_value_not_its_text(tmp_path, playwright_browser):
    """The cell reads 1.2346e-05; the filter compares the value itself."""
    html = _render(
        tmp_path,
        [_entry(1, "Lib.A"), _entry(2, "Lib.B")],
        {
            "test_0001": {"model_id": "Lib.A", "worst_nrmse": 1.23456e-5},
            "test_0002": {"model_id": "Lib.B", "worst_nrmse": 2e-5},
        },
    )
    page = playwright_browser.new_page()
    page.goto(html.as_uri())
    assert page.locator('tr[data-model="Lib.A"]').get_attribute("data-nrmse") == (
        "1.2346e-05"
    )
    _type(page, "nrmse", "=1.23456e-5")
    assert _visible(page) == ["Lib.A"]
    page.close()


def test_a_zero_count_matches_its_filter(tmp_path, playwright_browser):
    """Warnings `=0` reads the sort value: a 0 there is a value."""
    page = playwright_browser.new_page()
    page.goto(_four_verdicts(tmp_path).as_uri())
    _type(page, "warnings", "=0")
    assert _visible(page) == ["Lib.A", "Lib.B", "Lib.C"]
    page.close()


def test_the_bar_s_red_segment_carries_every_failure(tmp_path, playwright_browser):
    """FAIL and SIM_FAIL both paint the bar red: 2 of 4 rows."""
    page = playwright_browser.new_page()
    page.goto(_four_verdicts(tmp_path).as_uri())
    width = page.evaluate(
        "() => document.querySelector('#progress-bar span.b-fail').style.width"
    )
    assert width == "50%"
    page.close()


def test_a_regex_dot_matches_only_rows_with_a_value(tmp_path, playwright_browser):
    """An empty Message cell reads "—", but the filter reads "": `.` is
    "has a message", and "—" matches nothing."""
    entries = [_entry(1, "Lib.A"), _entry(2, "Lib.B")]
    entries[0]["detail"] = "a message"
    html = _render(tmp_path, entries)
    ctx = playwright_browser.new_context()
    page = ctx.new_page()
    page.goto(html.as_uri())
    page.click("#toggle-regex")
    _type(page, "detail", ".")
    assert _visible(page) == ["Lib.A"]
    page.click("#toggle-regex")
    _type(page, "detail", "—")
    assert _visible(page) == []
    ctx.close()
