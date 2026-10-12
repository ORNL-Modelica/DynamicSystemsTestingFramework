"""The Suite page and the Test page in Chromium, served by `dstf author`
(brief §3.2, §3.3, §3.8; roadmap §2's gate predicates over FakeLib; the
Suite table, S2a). NiceGUI's user simulation renders nothing — and the Suite
table's rows live in the browser (AG Grid) — so this says the page a user
opens works end to end: the process serves on 127.0.0.1 within seconds, the
rows fill in with verdicts, the table's pills, typed filters, group rows,
sort, names, column chooser and selection work and persist, a selection
survives a click into a test and back within the tab, the icon actions say
their counts, the font token reaches the body, nothing scrolls sideways, and
Stop server ends the process.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes, AG Grid's `row-id` / `col-id` and by role and name."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

from playwright.sync_api import expect  # noqa: E402

from dstf.reporting.theme import FONT_STACK  # noqa: E402
from test_session import A, C, D, G, _project, _two_package_project  # noqa: E402

READY_TIMEOUT = 30.0  # seconds to wait for the server's first 200
EXPECT_TIMEOUT = 15_000  # ms; a refresh is a websocket round trip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


#: The routes `author_hooks.py` adds, run before the CLI in the server.
HOOKS = Path(__file__).with_name("author_hooks.py")
_WITH_HOOKS = (
    "import sys; exec(open(sys.argv[1]).read()); from dstf.cli.main import "
    "main_entry; sys.argv = ['dstf'] + sys.argv[2:]; main_entry()"
)


def _start(
    cfg: Path, work: Path, log: Path, *, hooks: bool | Path = False
) -> tuple[subprocess.Popen, str]:
    """`dstf author` as a plain process: NiceGUI's `ui.run` treats an
    inherited PYTEST_CURRENT_TEST as "I am a screen test" and demands
    NICEGUI_SCREEN_TEST_PORT, ignoring --port. ``hooks`` adds
    `author_hooks.py`'s routes — or runs the file given in their place."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    sink = log.open("wb")
    script = HOOKS if hooks is True else hooks
    launcher = ["-c", _WITH_HOOKS, str(script)] if script else ["-m", "dstf"]
    proc = subprocess.Popen(
        [
            sys.executable,
            *launcher,
            "--config",
            str(cfg),
            "author",
            "--work-dir",
            str(work),
            "--port",
            str(port),
            "--no-browser",
        ],
        stdout=sink,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return proc, f"http://127.0.0.1:{port}/"


def _wait_until_serving(url: str, proc: subprocess.Popen, log: Path) -> float:
    """Seconds to the first 200 — the page must appear before the results
    are read, so this is short even over a large project."""
    started = time.monotonic()
    deadline = started + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"dstf author exited with {proc.returncode}\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return time.monotonic() - started
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    pytest.fail(f"no 200 from {url} within {READY_TIMEOUT}s\n{log.read_text()}")


#: A test's row in the Suite table (a group row is a full-width row, not
#: in the centre container).
ROWS = ".ag-center-cols-container .ag-row"


def _box(page, model_id: str):
    """The row's selection checkbox."""
    return page.locator(f".ag-row[row-id='{model_id}'] .ag-selection-checkbox input")


#: The table as the grid shows it now, top to bottom: `G:<package>` for a
#: group row, a test's short name for its row.
SHOWN = """() => {
  const out = [];
  DstfSuite.api.forEachNodeAfterFilterAndSort(n => out.push(
    n.data._g ? 'G:' + n.data._pkg : n.data._name_short));
  return out.join(' ');
}"""


def _shown(page, want: str) -> None:
    """The table reads `want` (it repaints on the next frame, so wait)."""
    try:
        page.wait_for_function(
            f"() => ({SHOWN})() === {json.dumps(want)}", timeout=EXPECT_TIMEOUT
        )
    except Exception:
        assert page.evaluate(SHOWN) == want
        raise


def _filter(page, key: str):
    return page.locator(f"input.dstf-ff[data-col='{key}']")


def _no_sideways_scroll(page) -> None:
    layout = page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth,"
        " client: document.documentElement.clientWidth, x: window.scrollX})"
    )
    assert layout["scroll"] <= layout["client"] and layout["x"] == 0, layout


def test_the_suite_page_fills_in_keeps_its_selection_across_a_click_and_stops(
    tmp_path, playwright_browser
):
    cfg, work = _project(tmp_path)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)

        # The header draws and the rows fill in with their verdicts.
        expect(page.locator(".dstf-header")).to_contain_text(
            "DSTF", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-header")).to_contain_text(
            "FakeLib", timeout=EXPECT_TIMEOUT
        )
        # The table's scripts ran: they are in the page's first HTML (after
        # the handshake NiceGUI inserts head HTML that never runs a script).
        assert page.evaluate("typeof DstfSuite") == "object"
        expect(page.locator(ROWS)).to_have_count(5, timeout=EXPECT_TIMEOUT)
        badges = page.locator(".dstf-badge-cell")
        expect(badges.filter(has_text="PASS")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        # C's run never finished it: NOT RUN (D145), not SIM_FAIL.
        for word, n in (
            ("^FAIL$", 1),
            ("^NOT RUN$", 1),
            ("NO_REF", 1),
            ("SIM_FAIL", 0),
        ):
            expect(badges.filter(has_text=re.compile(word))).to_have_count(
                n, timeout=EXPECT_TIMEOUT
            )
        # Each badge carries its verdict's fill (`theme.STATUS`: fail
        # #f44336, pass #4CAF50, NOT RUN the neutral grey #888888).
        fill = "e => getComputedStyle(e).backgroundColor"
        assert (
            badges.filter(has_text=re.compile("^FAIL$")).first.evaluate(fill)
            == "rgb(244, 67, 54)"
        )
        assert badges.filter(has_text="PASS").first.evaluate(fill) == "rgb(76, 175, 80)"
        assert (
            badges.filter(has_text="NOT RUN").first.evaluate(fill)
            == "rgb(136, 136, 136)"
        )
        # The pills carry the classifier's counts; NOT RUN has its own.
        pills = page.locator("#dstf-suite-tools button[data-pill]")
        expect(pills.filter(has_text="PASS 2")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(pills.filter(has_text=re.compile(r"^FAIL 1$"))).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )  # not SIM_FAIL 1
        not_run = page.locator("#dstf-suite-tools button[data-pill='not-run']")
        expect(not_run).to_have_text("NOT RUN 1", timeout=EXPECT_TIMEOUT)
        not_run.click()
        _shown(page, "G:FakeLib C")
        not_run.click()

        # The font token reaches the body (roadmap §1 predicate 2's last gap):
        # the computed stack STARTS with the token's first family — Quasar's
        # own default also ends in a generic sans-serif, so the tail proves
        # nothing (the Task 9 review).
        font = page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert font.strip().startswith(FONT_STACK.split(",")[0]), font
        # Nothing scrolls sideways (the old shell's tab strip did).
        _no_sideways_scroll(page)

        # Select D and G; the icon actions say their object and count. Two,
        # so the Test page's "1 selected test" below cannot be the Suite's
        # selection leaking through (the Task 9 review).
        _box(page, D).click()
        _box(page, G).click()
        accept = page.get_by_role(
            "button", name=re.compile(r"^Accept 2 results as baselines")
        )
        expect(accept).to_be_enabled(timeout=EXPECT_TIMEOUT)
        rerun = page.get_by_role("button", name=re.compile(r"^Queue 2$"))
        expect(rerun).to_be_enabled(timeout=EXPECT_TIMEOUT)

        # A click on A's name views A: the Test page, in this tab.
        page.locator(".ag-row[row-id='FakeLib.A'] a.dstf-name-link").click()
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT
        )
        # The model and its verdict moved into the sticky test header, which
        # is a second row of the shared one (decision 8), not the page body.
        expect(page.locator(".dstf-subheader")).to_contain_text(
            "FakeLib.A", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-subheader .dstf-badge")).to_have_text(
            "FAIL", timeout=EXPECT_TIMEOUT
        )
        assert (
            page.locator(".dstf-subheader .dstf-badge").evaluate(fill)
            == "rgb(244, 67, 54)"
        )
        # Its TEST-placement buttons speak of this one test.
        expect(page.get_by_role("button", name=re.compile(r"^Queue 1$"))).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )

        # Back to the Suite: D and G are still selected, A is not (viewing ≠
        # selecting).
        page.get_by_role("link", name="← Suite").click()
        expect(page.locator(ROWS)).to_have_count(5, timeout=EXPECT_TIMEOUT)
        for checked in (D, G):
            expect(_box(page, checked)).to_be_checked(timeout=EXPECT_TIMEOUT)
        expect(_box(page, A)).not_to_be_checked()
        expect(page.locator("#dstf-suite-selection")).to_contain_text("2 selected")
        assert errors == []

        # Stop server ends the process (no confirm: nothing pending, no job).
        # It is an item of the header's ⋮ menu (3c), which is not role
        # `menuitem`: it is found by its text inside the open menu.
        page.locator(".dstf-header-menu").click()
        page.locator(".q-menu").get_by_text("Stop server", exact=True).click()
        proc.wait(15)
        assert proc.returncode is not None
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved


def _served(
    tmp_path,
    playwright_browser,
    *,
    hooks: bool | Path = False,
    width: int = 1280,
    setup=None,
    init_script: str | None = None,
    rows: int = 6,
):
    """`dstf author` over the two-package FakeLib and a page on its Suite
    table, drawn: (proc, url, page, context, errors, cfg). ``setup(cfg)``
    runs before the server starts (``rows``: the test rows it leaves),
    ``init_script`` in the page before its own scripts. The caller stops the
    process — this does when the table never draws."""
    cfg, work = _two_package_project(tmp_path)
    if setup is not None:
        setup(cfg)
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log, hooks=hooks)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(
            viewport={"width": width, "height": 900}
        )
        page = context.new_page()
        if init_script is not None:
            page.add_init_script(init_script)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda m: errors.append(m.text) if m.type in ("error", "warning") else None,
        )
        page.goto(url)
        expect(page.locator(ROWS)).to_have_count(rows, timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-badge-cell", has_text="NO_REF")).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )
    except BaseException:
        proc.terminate()
        proc.wait(10)
        raise
    return proc, url, page, context, errors, cfg


ALL = "G:FakeLib A B C D G G:FakeLib.Cases X"


def test_the_suite_table_groups_filters_sorts_and_names(tmp_path, playwright_browser):
    """The board's Q1 A / Q1b A: group rows inside the table that collapse
    by mouse and by keyboard; the count pills; typed column filters with
    `.*` and `Aa` once in the toolbar; a sort that keeps each package's rows
    under its header, empties last both ways; the names icon; the column
    chooser. No AG Grid warning: no Enterprise feature is asked for."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        _shown(page, ALL)
        # One group row per package, a chevron click collapses it.
        group = page.locator(".dstf-grp", has_text="FakeLib.Cases")
        expect(page.locator(".dstf-grp")).to_have_count(2)
        group.click()
        _shown(page, "G:FakeLib A B C D G G:FakeLib.Cases")
        expect(page.locator(".ag-row[row-id='FakeLib.Cases.X']")).to_have_count(0)
        expect(group).to_have_attribute("aria-expanded", "false")
        group.click()
        _shown(page, ALL)
        # The keyboard: focus A's name cell, up to its group row, Enter
        # collapses it and Space opens it again.
        page.locator(".ag-row[row-id='FakeLib.A'] .ag-cell[col-id='status']").click()
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Enter")
        _shown(page, "G:FakeLib G:FakeLib.Cases X")
        page.keyboard.press(" ")
        _shown(page, ALL)

        # The pills: FAIL alone; its count is over every row.
        fail = page.locator("button[data-pill='fail']")
        fail.click()
        expect(fail).to_have_attribute("aria-pressed", "true")
        _shown(page, "G:FakeLib A")
        expect(page.locator(".dstf-ff-status")).to_have_text("FAIL")
        expect(page.locator("button[data-pill='pass']")).to_have_text("PASS 2")
        fail.click()
        _shown(page, ALL)
        # EDITED has nothing to count: shown, disabled.
        expect(page.locator("button[data-pill='edited']")).to_be_disabled()

        # Typed filters: a number reads the value, not its text.
        _filter(page, "nrmse").fill(">1e-5")
        _shown(page, "G:FakeLib A D G")
        # A's is 0.153094…, its text "1.5309e-01": only the value passes.
        _filter(page, "nrmse").fill(">0.15309")
        _shown(page, "G:FakeLib A")
        _filter(page, "nrmse").fill("")
        # Text by glob over the FULL test name.
        _filter(page, "model").fill("*.B")
        _shown(page, "G:FakeLib B")
        _filter(page, "model").fill("")
        # `.*` makes every column filter a regex; a bad one marks its input,
        # says why, and hides nothing.
        regex = page.get_by_role("button", name="Regex in every column filter")
        regex.click()
        expect(regex).to_have_attribute("aria-pressed", "true")
        _filter(page, "model").fill(r"^FakeLib\.A$")
        _shown(page, "G:FakeLib A")
        _filter(page, "model").fill("((")
        _shown(page, ALL)
        expect(_filter(page, "model")).to_have_class(re.compile(r"\bbad\b"))
        assert _filter(page, "model").get_attribute("title")
        _filter(page, "model").fill("")
        regex.click()
        # `Aa`: a case-sensitive text filter.
        case = page.get_by_role("button", name="Match case in every column filter")
        _filter(page, "model").fill("fakelib.a")
        _shown(page, "G:FakeLib A")
        case.click()
        _shown(page, "")
        case.click()
        _filter(page, "model").fill("")

        # A sort on Worst NRMSE keeps each package's rows under its header;
        # the unscored sort last, ascending and descending.
        header = page.locator(".ag-header-cell[col-id='nrmse'] .ag-header-cell-label")
        header.click()
        _shown(page, "G:FakeLib D G A B C G:FakeLib.Cases X")
        header.click()
        _shown(page, "G:FakeLib A D G B C G:FakeLib.Cases X")
        header.click()
        _shown(page, ALL)

        # Short names by default; the names icon switches to full ones.
        name = page.locator(".ag-row[row-id='FakeLib.A'] a.dstf-name-link")
        expect(name).to_have_text("A")
        page.get_by_role("button", name="Full names").click()
        expect(name).to_have_text("FakeLib.A")
        page.get_by_role("button", name="Short names").click()
        expect(name).to_have_text("A")
        # Flat: one sort over every row, no group rows.
        page.get_by_role("button", name="Show flat").click()
        _shown(page, "A B C X D G")
        page.get_by_role("button", name="Group by package").click()
        _shown(page, ALL)

        # The column chooser offers Reference (hidden) — not a live run's
        # Key, Worker or Elapsed — and hides Message.
        page.get_by_role("button", name="Choose columns").click()
        menu = page.locator("#dstf-colmenu")
        expect(menu.locator("label", has_text="Reference")).to_have_count(1)
        expect(
            menu.locator("label", has_text="Reference").locator("input")
        ).not_to_be_checked()
        for live in ("Key", "Worker", "Elapsed"):
            expect(menu.locator("label", has_text=live)).to_have_count(0)
        # The column that counts criteria says so — in its header, in the
        # chooser and in its filter's hint — and nothing says Variables.
        expect(menu.locator("label", has_text="Criteria")).to_have_count(1)
        expect(menu.locator("label", has_text="Variables")).to_have_count(0)
        criteria = page.locator(".ag-header-cell[col-id='criteria']")
        expect(criteria.locator(".ag-header-cell-text")).to_have_text("Criteria")
        expect(page.locator(".ag-header-cell[col-id='vars']")).to_have_count(0)
        menu.locator("label", has_text="Message").locator("input").uncheck()
        expect(page.locator(".ag-header-cell[col-id='detail']")).to_have_count(0)
        page.keyboard.press("Escape")
        page.mouse.click(5, 890)
        expect(menu).to_have_count(0)
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: Every column filter input with its placeholder, title, and whether the
#: placeholder reads whole in the input (measured in the input's own font
#: against its content box).
HINTS = """() => [...document.querySelectorAll('input.dstf-ff')].map(el => {
  const cs = getComputedStyle(el);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = cs.font;
  const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return {col: el.dataset.col, placeholder: el.placeholder, title: el.title,
          fits: ctx.measureText(el.placeholder).width <= room};
})"""


def test_every_column_filter_carries_the_dashboards_hint(tmp_path, playwright_browser):
    """The owner saw no grey hint in the Suite table's filters: every text
    and number filter reads the dashboard's placeholder for its kind
    (``*Pipe*``, ``>1e-4``), whole at the tool's column widths, with the
    dashboard's explanation as its tooltip; the status cell mirrors the
    pills. Every column is shown, so the hidden ones are measured too."""
    from dstf.authoring.shell.suite_grid import TOOL_COLUMNS
    from dstf.reporting.rows import FILTER_HINTS

    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        expect(page.locator(".dstf-ff-status")).to_have_text("all")
        _keep(page, tmp_path, "suite-filter-hints-1280")
        page.evaluate(
            "() => DstfSuite.api.setColumnsVisible("
            "DstfSuite.api.getColumns().map(c => c.getColId()), true)"
        )
        want = {c.key: c.kind for c in TOOL_COLUMNS if c.kind != "status"}
        page.wait_for_function(
            f"() => document.querySelectorAll('input.dstf-ff').length === {len(want)}",
            timeout=EXPECT_TIMEOUT,
        )
        got = page.evaluate(HINTS)
        assert sorted(h["col"] for h in got) == sorted(want)
        for h in got:
            placeholder, title = FILTER_HINTS[want[h["col"]]]
            assert h["placeholder"] == placeholder, h
            assert h["title"].endswith(title), h
            assert h["fits"], h
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_the_suite_tables_selection_and_icon_actions(tmp_path, playwright_browser):
    """Q1a B: "2 selected · Clear", counting the selected rows a filter
    hides; Rerun and Accept as icons whose tooltips are their captions, a
    click opening the same dialog; Clear empties the selection; a name
    opens its Test page."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        selection = page.locator("#dstf-suite-selection")
        expect(selection).to_have_text("")
        _box(page, A).click()
        _box(page, D).click()
        expect(selection).to_contain_text("2 selected", timeout=EXPECT_TIMEOUT)
        rerun = page.get_by_role("button", name="Queue 2", exact=True)
        accept = page.get_by_role(
            "button", name="Accept 2 results as baselines", exact=True
        )
        expect(rerun).to_be_enabled(timeout=EXPECT_TIMEOUT)
        expect(accept).to_be_enabled()
        rerun.hover()
        expect(page.locator(".q-tooltip")).to_have_text(
            "Queue 2", timeout=EXPECT_TIMEOUT
        )
        # A filter that hides one selected row says so; the actions still
        # count both.
        _filter(page, "model").fill("*.A")
        expect(selection).to_contain_text("2 selected (1 hidden)")
        expect(rerun).to_be_enabled()
        _filter(page, "model").fill("")
        expect(selection).not_to_contain_text("hidden")
        accept.click()
        expect(page.locator(".q-dialog")).to_contain_text(
            "Accept", timeout=EXPECT_TIMEOUT
        )
        page.keyboard.press("Escape")
        expect(page.locator(".q-dialog")).to_have_count(0, timeout=EXPECT_TIMEOUT)
        page.get_by_role("button", name="Clear selection").click()
        expect(selection).to_have_text("", timeout=EXPECT_TIMEOUT)
        expect(_box(page, A)).not_to_be_checked()
        expect(
            page.get_by_role("button", name="Queue needs a selection", exact=True)
        ).to_be_disabled(timeout=EXPECT_TIMEOUT)
        page.locator(".ag-row[row-id='FakeLib.A'] a.dstf-name-link").click()
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.A$"), timeout=EXPECT_TIMEOUT
        )
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_the_suite_tables_state_survives_a_reload(tmp_path, playwright_browser):
    """The view is the viewer's, kept in localStorage under a key per
    project (its config file): a filter, a pill, a collapsed group,
    grouping, a sort and a hidden column come back; the selection is the
    tab's."""
    proc, url, page, context, errors, cfg = _served(tmp_path, playwright_browser)
    try:
        _box(page, D).click()
        # C and FakeLib.Cases.X: the NOT RUN pill and "C" in Test; FakeLib
        # collapsed, so C is under a closed header.
        page.locator("button[data-pill='not-run']").click()
        _filter(page, "model").fill("C")
        page.locator(".ag-row[row-id='g:FakeLib'] .dstf-grp").click()
        page.locator(".ag-header-cell[col-id='nrmse'] .ag-header-cell-label").click()
        page.get_by_role("button", name="Choose columns").click()
        page.locator("#dstf-colmenu label", has_text="Message").locator(
            "input"
        ).uncheck()
        page.mouse.click(5, 890)
        _shown(page, "G:FakeLib G:FakeLib.Cases X")
        selection = page.locator("#dstf-suite-selection")
        expect(selection).to_contain_text("1 selected (1 hidden)")
        keys = page.evaluate("() => Object.keys(localStorage)")
        assert keys == [f"dstf-suite-v1:{cfg.resolve()}"], keys
        page.reload()
        expect(page.locator(ROWS).first).to_be_attached(timeout=EXPECT_TIMEOUT)
        _shown(page, "G:FakeLib G:FakeLib.Cases X")
        expect(_filter(page, "model")).to_have_value("C")
        expect(page.locator("button[data-pill='not-run']")).to_have_attribute(
            "aria-pressed", "true"
        )
        expect(page.locator(".ag-header-cell[col-id='detail']")).to_have_count(0)
        expect(
            page.locator(".ag-header-cell[col-id='nrmse'][role='columnheader']")
        ).to_have_attribute("aria-sort", "ascending")
        # The selection is the tab's, selected again once the grid is ready.
        expect(selection).to_contain_text("1 selected (1 hidden)")
        assert page.evaluate(
            "() => DstfSuite.api.getSelectedRows().map(r => r.id)"
        ) == [D]
        # Grouping off survives too.
        page.get_by_role("button", name="Show flat").click()
        _shown(page, "C X")
        page.reload()
        expect(page.locator(ROWS).first).to_be_attached(timeout=EXPECT_TIMEOUT)
        _shown(page, "C X")
        expect(page.get_by_role("button", name="Group by package")).to_be_visible()
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_an_edited_test_has_its_pill_and_its_cell(tmp_path, playwright_browser):
    """An edit staged before the page is drawn: the EDITED pill counts it
    and filters to it; its Edits cell says how many are unsaved."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, hooks=True
    )
    try:
        with urllib.request.urlopen(url + "__stage?model=FakeLib.A") as r:
            assert json.loads(r.read())["pending"] == 2
        page.reload()
        edited = page.locator("button[data-pill='edited']")
        expect(edited).to_have_text("EDITED 1", timeout=EXPECT_TIMEOUT)
        cell = page.locator(".ag-row[row-id='FakeLib.A'] .ag-cell[col-id='edits']")
        expect(cell).to_have_text("2 unsaved")
        edited.click()
        _shown(page, "G:FakeLib A")
        # EDITED is OR'd with the status pills.
        page.locator("button[data-pill='no-ref']").click()
        _shown(page, "G:FakeLib A B")
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_save_is_unfilled_at_zero_pending_and_filled_at_one(
    tmp_path, playwright_browser
):
    """At 0 pending the disabled Save was still painted primary blue (a
    disabled Quasar button is only dimmed): it read as the active action.
    Its computed background is transparent there, as Discard's is, and
    filled once an edit is pending."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, hooks=True
    )
    try:
        save = page.get_by_role("button", name=re.compile(r"^Save \d+ spec edit"))
        discard = page.get_by_role("button", name=re.compile(r"^Discard \d+ edit"))
        expect(save).to_be_disabled(timeout=EXPECT_TIMEOUT)
        clear = "rgba(0, 0, 0, 0)"
        bg = "el => getComputedStyle(el).backgroundColor"
        assert save.evaluate(bg) == clear == discard.evaluate(bg)
        with urllib.request.urlopen(url + "__stage?model=FakeLib.A") as r:
            assert json.loads(r.read())["pending"] == 2
        expect(save).to_be_enabled(timeout=EXPECT_TIMEOUT)
        assert save.evaluate(bg) != clear
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_a_rescore_repaints_its_row_and_keeps_the_view(tmp_path, playwright_browser):
    """A staged edit rescores A while the page is open: its row repaints —
    PASS, "2 unsaved", the EDITED pill counts it — in one transaction, and
    the sort, the filter and the selection stay (the grid is not rebuilt)."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, hooks=True
    )
    try:
        page.evaluate(
            "() => { window.__readies = 0; const r = DstfSuite.ready;"
            " DstfSuite.ready = (...a) => { window.__readies++; return r(...a); }; }"
        )
        page.locator(".ag-header-cell[col-id='nrmse'] .ag-header-cell-label").click()
        _filter(page, "nrmse").fill(">1e-5")
        _box(page, D).click()
        _box(page, G).click()
        _shown(page, "G:FakeLib D G A")
        with urllib.request.urlopen(url + "__stage?model=FakeLib.A") as r:
            assert json.loads(r.read())["pending"] == 2
        row = ".ag-row[row-id='FakeLib.A'] .ag-cell"
        expect(page.locator(f"{row}[col-id='status']")).to_have_text(
            "PASS", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(f"{row}[col-id='edits']")).to_have_text("2 unsaved")
        expect(page.locator("button[data-pill='edited']")).to_have_text("EDITED 1")
        expect(page.locator("button[data-pill='pass']")).to_have_text("PASS 3")
        _shown(page, "G:FakeLib D G A")
        expect(_filter(page, "nrmse")).to_have_value(">1e-5")
        expect(
            page.locator(".ag-header-cell[col-id='nrmse'][role='columnheader']")
        ).to_have_attribute("aria-sort", "ascending")
        assert sorted(
            page.evaluate("() => DstfSuite.api.getSelectedRows().map(r => r.id)")
        ) == [D, G]
        expect(page.locator("#dstf-suite-selection")).to_contain_text("2 selected")
        assert page.evaluate("() => window.__readies") == 0
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_a_python_side_touch_does_not_rebuild_the_grid(tmp_path, playwright_browser):
    """NiceGUI's aggrid re-creates the grid on ANY element update: the
    selection, the sort and every row patched since the build would be
    lost. The grid refuses the update: a touch from Python leaves a
    selection made after the build and a row patched after it."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, hooks=True
    )
    try:
        page.evaluate(
            "() => { window.__readies = 0; const r = DstfSuite.ready;"
            " DstfSuite.ready = (...a) => { window.__readies++; return r(...a); }; }"
        )
        _box(page, G).click()
        expect(page.locator("#dstf-suite-selection")).to_contain_text("1 selected")
        # A row a staged edit repainted after the build (Task 5's path).
        with urllib.request.urlopen(url + "__stage?model=FakeLib.A") as r:
            assert json.loads(r.read())["pending"] == 2
        expect(
            page.locator(".ag-row[row-id='FakeLib.A'] .ag-cell[col-id='edits']")
        ).to_have_text("2 unsaved", timeout=EXPECT_TIMEOUT)
        # A row patched after the build, as a transaction from Python does.
        page.evaluate(
            "() => { const r = {...DstfSuite.api.getRowNode('FakeLib.B').data,"
            " detail: 'patched'}; DstfSuite.apply({update: [r]}); }"
        )
        with urllib.request.urlopen(url + "__touch") as r:
            assert json.loads(r.read())  # a grid was touched
        page.wait_for_timeout(1500)  # a rebuild would have landed by now
        assert page.evaluate(
            "() => DstfSuite.api.getSelectedRows().map(r => r.id)"
        ) == [G]
        assert (
            page.evaluate("() => DstfSuite.api.getRowNode('FakeLib.B').data.detail")
            == "patched"
        )
        assert (
            page.evaluate("() => DstfSuite.api.getRowNode('FakeLib.A').data.status")
            == "PASS"
        )
        assert page.evaluate("() => window.__readies") == 0  # never rebuilt
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def _d_sleeps(seconds: float):
    """A `setup` for `_served`: D's simulation sleeps, so a rerun of D is
    seen running."""

    def setup(cfg: Path) -> None:
        (cfg.parent.parent / "Examples" / "D.py").write_text(
            "import time\n\n\ndef simulate(stop_time, tolerance):\n"
            f"    time.sleep({seconds})\n    return {{}}\n",
            encoding="utf-8",
        )

    return setup


def test_rows_not_scored_yet_read_scoring_and_never_queued(
    tmp_path, playwright_browser
):
    """While a project opens every row is known and none is scored. They
    counted under the queue's pill — `QUEUED 6`, beside a header reading
    `idle` and a panel reading `queue empty`. They read SCORING, under a
    pill of that word that goes when the last row has its verdict."""
    cfg, work = _two_package_project(tmp_path)
    hold = cfg.parent / "hold-open"
    hold.write_text("", encoding="utf-8")
    log = tmp_path / "author.log"
    proc, url = _start(
        cfg, work, log, hooks=Path(__file__).with_name("slow_open_hooks.py")
    )
    try:
        _wait_until_serving(url, proc, log)
        page = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        ).new_page()
        page.goto(url)
        expect(page.locator(ROWS)).to_have_count(6, timeout=EXPECT_TIMEOUT)
        badges = page.locator(".dstf-badge-cell")
        expect(badges.filter(has_text=re.compile("^SCORING$"))).to_have_count(6)
        scoring = page.locator("button[data-pill='scoring']")
        expect(scoring).to_have_text("SCORING 6")
        expect(page.locator("button[data-pill='queued']")).to_have_count(0)
        expect(page.locator("a.dstf-indicator")).to_have_text("idle")
        # the neutral grey, as a queued row's is
        assert badges.first.evaluate("e => getComputedStyle(e).backgroundColor") == (
            "rgb(136, 136, 136)"
        )

        hold.unlink()
        expect(badges.filter(has_text="PASS")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        expect(badges.filter(has_text="SCORING")).to_have_count(0)
        expect(scoring).to_have_count(0)
        expect(page.locator("button[data-pill='queued']")).to_have_count(0)
    finally:
        hold.unlink(missing_ok=True)
        proc.terminate()
        proc.wait(10)


def test_a_running_pill_that_is_on_stays_until_switched_off(
    tmp_path, playwright_browser
):
    """RUNNING is shown while a job runs. Switched on, it stays when the
    job ends, reading 0, so it can be switched off: it hid at 0 while it
    still filtered, and the table stayed empty with no pill to press —
    after a reload too, the view being stored."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, setup=_d_sleeps(4)
    )
    try:
        running = page.locator("button[data-pill='running']")
        expect(running).to_have_count(0)  # nothing runs
        _box(page, D).click()
        rerun = re.compile(r"^Queue 1$")
        page.get_by_role("button", name=rerun).first.click()
        page.locator(".q-dialog").get_by_role("button", name=rerun).click()
        expect(running).to_have_text("RUNNING 1", timeout=EXPECT_TIMEOUT)
        running.click()
        expect(running).to_have_attribute("aria-pressed", "true")
        _shown(page, "G:FakeLib D")
        # The job ends: the pill stays, pressed, reading 0.
        expect(running).to_have_text("RUNNING 0", timeout=60_000)
        expect(running).to_have_attribute("aria-pressed", "true")
        expect(running).to_be_enabled()
        _shown(page, "")
        page.reload()
        expect(running).to_have_text("RUNNING 0", timeout=EXPECT_TIMEOUT)
        expect(running).to_have_attribute("aria-pressed", "true")
        _shown(page, "")
        running.click()
        _shown(page, ALL)
        expect(running).to_have_count(0)  # off at 0: gone
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_enter_on_a_test_cell_opens_its_page(tmp_path, playwright_browser):
    """The keyboard's way to a test: Enter on a focused Test cell opens its
    page, as a click on its name does."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        page.locator(".ag-row[row-id='FakeLib.B'] .ag-cell[col-id='status']").click()
        page.keyboard.press("ArrowRight")
        assert (
            page.evaluate("() => document.activeElement.getAttribute('col-id')")
            == "model"
        )
        assert errors == [], (
            errors
        )  # the Suite page's; the Test page is not this test's
        page.keyboard.press("Enter")
        expect(page).to_have_url(
            re.compile(r"/test/FakeLib\.B$"), timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-subheader")).to_contain_text(
            "FakeLib.B", timeout=EXPECT_TIMEOUT
        )
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_enter_on_any_other_cell_stays_on_the_suite(tmp_path, playwright_browser):
    """Only the Test cell opens a page: Enter on a focused Status or Message
    cell does nothing, as a click on it does."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        for col in ("status", "detail"):
            page.locator(
                f".ag-row[row-id='FakeLib.B'] .ag-cell[col-id='{col}']"
            ).click()
            assert (
                page.evaluate("() => document.activeElement.getAttribute('col-id')")
                == col
            )
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)  # a navigation would have started
            assert page.url == url, col
        expect(page.locator(ROWS)).to_have_count(6)
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_the_header_checkbox_selects_only_the_rows_shown(tmp_path, playwright_browser):
    """ "Select all" is the rows the filters show: never a row a filter or a
    closed group hides — Accept would store baselines for tests nobody saw
    — and never a group row."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        # NOT RUN: C and FakeLib.Cases.X; the Cases group closed hides X.
        page.locator("button[data-pill='not-run']").click()
        page.locator(".ag-row[row-id='g:FakeLib.Cases'] .dstf-grp").click()
        _shown(page, "G:FakeLib C G:FakeLib.Cases")
        page.locator(".ag-header-select-all input").click()
        selection = page.locator("#dstf-suite-selection")
        expect(selection).to_contain_text("1 selected", timeout=EXPECT_TIMEOUT)
        expect(selection).not_to_contain_text("hidden")
        assert page.evaluate(
            "() => DstfSuite.api.getSelectedNodes().map(n => n.data.id)"
        ) == ["FakeLib.C"]
        expect(page.get_by_role("button", name="Queue 1", exact=True)).to_be_enabled(
            timeout=EXPECT_TIMEOUT
        )
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_a_toolbar_control_keeps_the_focus_through_its_repaint(
    tmp_path, playwright_browser
):
    """The toolbar is drawn again on every refresh (every live transaction
    too): the focused control is focused again, so Space on a pill or Enter
    on a view icon does not drop the keyboard to the page."""
    proc, url, page, context, errors, _ = _served(tmp_path, playwright_browser)
    try:
        focused = "() => document.activeElement.getAttribute('aria-label')"
        page.locator("button[data-pill='pass']").focus()
        page.keyboard.press(" ")
        _shown(page, "G:FakeLib D G")
        expect(page.locator("button[data-pill='pass']")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert page.evaluate(focused) == "PASS 2"
        page.keyboard.press(" ")
        _shown(page, ALL)
        assert page.evaluate(focused) == "PASS 2"
        page.get_by_role("button", name="Show flat").focus()
        page.keyboard.press("Enter")
        _shown(page, "A B C X D G")
        assert page.evaluate(focused) == "Group by package"
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: Holds the grid's `ready` until the page calls `window.__release()`: the
#: rows are drawn, and transactions from Python arrive, before the grid is
#: ready.
HOLD_READY = """(() => {
  let d;
  Object.defineProperty(window, 'DstfSuite', {
    configurable: true,
    get() { return d; },
    set(v) {
      d = v;
      const ready = v.ready;
      v.ready = (...a) => new Promise(r => { window.__release = r; })
        .then(() => ready(...a));
    },
  });
})();"""


def test_a_transaction_before_the_grid_is_ready_is_applied_once_it_is(
    tmp_path, playwright_browser
):
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, hooks=True, init_script=HOLD_READY
    )
    try:
        assert page.evaluate("() => DstfSuite.api") is None  # not ready yet
        with urllib.request.urlopen(url + "__stage?model=FakeLib.A") as r:
            assert json.loads(r.read())["pending"] == 2
        page.wait_for_function(
            "() => (DstfSuite.pending || []).length > 0", timeout=EXPECT_TIMEOUT
        )
        page.evaluate("() => window.__release()")
        expect(
            page.locator(".ag-row[row-id='FakeLib.A'] .ag-cell[col-id='edits']")
        ).to_have_text("2 unsaved", timeout=EXPECT_TIMEOUT)
        expect(page.locator("button[data-pill='edited']")).to_have_text("EDITED 1")
        assert page.evaluate("() => DstfSuite.pending") == []
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: Where a column's header cell ends, against the grid's own viewport.
COLUMN_RIGHT = """(col) => {
  const cell = document.querySelector(`.ag-header-cell[col-id='${col}']`);
  const view = document.querySelector('.ag-center-cols-viewport');
  return [cell.getBoundingClientRect().right, view.getBoundingClientRect().right];
}"""
#: How far the grid's body scrolls sideways.
GRID_SIDEWAYS = """() => {
  const v = document.querySelector('.ag-center-cols-viewport');
  return v.scrollWidth - v.clientWidth;
}"""


#: The longest short name of TRANSFORM's 331 tests, in its package.
LONG_NAME = (
    "TRANSFORM.HeatAndMassTransfer.ClosureRelations.HeatTransfer.Examples."
    "check_Alphas_TwoPhase_3Region_CHFtransition_F1D"
)


def _with_long_name(cfg: Path) -> None:
    """One more test, named as long as TRANSFORM's longest (no result:
    NOT RUN, with a message) — and C's run recorded failed, so its row
    carries the reader's long message (a pending C is NOT RUN, with the
    short one, D145)."""
    manifest_path = cfg.parents[2] / "work" / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_0003"]["last_run_status"] = "failed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"].append(
        {
            "model": LONG_NAME,
            "source": "../Examples/A.py",
            "variables": ["x"],
            "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
        }
    )
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


#: Whether a row's Test name is cut at its cell's edge.
NAME_CUT = """(id) => {
  const cell = document.querySelector(`.ag-row[row-id='${id}'] .ag-cell[col-id='model']`);
  const a = cell.querySelector('a');
  return a.scrollWidth > a.clientWidth
    || a.getBoundingClientRect().right > cell.getBoundingClientRect().right;
}"""


def _tooltip(page, model_id: str, col: str) -> str:
    """What AG Grid's tooltip reads over a cell, hovered with the mouse
    near the cell's right edge (a cut text's end)."""
    page.mouse.move(0, 0)
    expect(page.locator(".ag-tooltip")).to_have_count(0, timeout=EXPECT_TIMEOUT)
    box = page.locator(
        f".ag-row[row-id='{model_id}'] .ag-cell[col-id='{col}']"
    ).bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] - 12, box["y"] + box["height"] / 2)
    tip = page.locator(".ag-tooltip")
    expect(tip).to_have_count(1, timeout=EXPECT_TIMEOUT)
    return tip.inner_text()


def test_the_table_fits_1280_and_shows_edits_at_900(tmp_path, playwright_browser):
    """Edits comes before Message, which takes the width left over: at 1280
    the grid does not scroll sideways and a name as long as TRANSFORM's
    longest reads whole, and at 900 the Edits column is in view without
    scrolling it (the name and Message may be cut: their tooltips read
    them whole)."""
    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, setup=_with_long_name, rows=7
    )
    try:
        page.wait_for_function(
            f"() => ({GRID_SIDEWAYS})() <= 0", timeout=EXPECT_TIMEOUT
        )
        assert page.evaluate(NAME_CUT, LONG_NAME) is False
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_function(
            f"() => {{ const [r, v] = ({COLUMN_RIGHT})('edits'); return r <= v; }}",
            timeout=EXPECT_TIMEOUT,
        )
        _no_sideways_scroll(page)
        _keep(page, tmp_path, "suite-table-900")
        assert page.evaluate(NAME_CUT, LONG_NAME) is True
        assert _tooltip(page, LONG_NAME, "model") == LONG_NAME
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_function(
            f"() => ({GRID_SIDEWAYS})() <= 0", timeout=EXPECT_TIMEOUT
        )
        _keep(page, tmp_path, "suite-table-1280")
        # A message cut at 1280 reads whole on hover; "—" has no tooltip.
        message = _tooltip(page, C, "detail")
        assert message.startswith("Run recorded failed — No Python result at "), message
        assert message.endswith("(did execution run?)"), message
        page.mouse.move(0, 0)
        box = page.locator(f".ag-row[row-id='{A}'] .ag-cell[col-id='detail']")
        box.hover()
        page.wait_for_timeout(1000)  # past the 300 ms show delay
        expect(page.locator(".ag-tooltip")).to_have_count(0)
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: An id as long as TRANSFORM's longest (149 characters, no space to wrap at).
LONGEST_ID = (
    "TRANSFORM.HeatAndMassTransfer.DiscritizedModels.ClassicalMethod.Examples."
    "GenericPipe_withWall_2DConduction_TwoPhaseBoiling_CHFtransitionCheck_Full3Dx"
)
assert len(LONGEST_ID) == 149 and " " not in LONGEST_ID

#: The tooltip's box against the viewport's width, and whether its text
#: runs past the box (a box kept in view over text that is not).
TIP_BOX = """() => {
  const t = document.querySelector('.ag-tooltip');
  const r = t.getBoundingClientRect();
  return {left: r.left, right: r.right, vw: document.documentElement.clientWidth,
          overflow: t.scrollWidth - t.clientWidth};
}"""


def test_a_long_ids_tooltip_stays_in_the_viewport_at_900(tmp_path, playwright_browser):
    """At 900 px a 149-character id without a space ran past the right edge
    of the page in its tooltip (x=175 to 1151): the tooltip wraps within the
    viewport, anywhere in the id, and at 720 px on a wider page."""

    def setup(cfg: Path) -> None:
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": LONGEST_ID,
                "source": "../Examples/A.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    proc, _url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, setup=setup, rows=7, width=900
    )
    try:
        assert _tooltip(page, LONGEST_ID, "model") == LONGEST_ID
        box = page.evaluate(TIP_BOX)
        _keep(page, tmp_path, "suite-tooltip-900")
        assert 0 <= box["left"] and box["right"] <= box["vw"], box
        assert box["overflow"] <= 0, box
        # On a wide page it wraps at a reading width, not the page's.
        page.set_viewport_size({"width": 1600, "height": 900})
        assert _tooltip(page, LONGEST_ID, "model") == LONGEST_ID
        box = page.evaluate(TIP_BOX)
        assert box["right"] - box["left"] <= 720 and box["overflow"] <= 0, box
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: The rows AG Grid drew and where the last one ends, against its viewport.
ROWS_DRAWN = """() => {
  const vp = document.querySelector('.ag-body-viewport').getBoundingClientRect();
  const rows = [...document.querySelectorAll('.ag-center-cols-container .ag-row')];
  const bottom = rows.reduce((m, r) => Math.max(m, r.getBoundingClientRect().bottom), 0);
  return {drawn: rows.length, gap: vp.bottom - bottom, height: vp.height};
}"""
#: Every test row scored: the table is complete.
ALL_SCORED = """(n) => window.DstfSuite && DstfSuite.api && (() => {
  let k = 0;
  DstfSuite.api.forEachNode(x => { if (!x.data._g && x.data.status !== '\u2026') k++; });
  return k === n;
})()"""


def test_the_rows_fill_the_table_on_a_second_load(tmp_path, playwright_browser):
    """A second load in one browser context drew the first 15 rows of a
    710 px table and left the rest blank until a scroll or a resize: the
    scripts came from the cache, so the grid was ready at NiceGUI's default
    256 px before the page's CSS (added after the handshake) gave it its
    height. Over as many tests as TRANSFORM's, whose load it took."""
    many = 326

    def setup(cfg: Path) -> None:
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"] += [
            {
                "model": f"FakeLib.Many.{LONG_NAME.rsplit('.', 1)[1]}_{i:03d}",
                "source": "../Examples/A.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
            for i in range(many)
        ]
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    cfg, work = _two_package_project(tmp_path)
    setup(cfg)
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        # The cause, whatever the race does: the grid's height is in the
        # page's FIRST HTML, there before the grid is built.
        with urllib.request.urlopen(url, timeout=10) as response:
            assert ".dstf-suite-grid { height:" in response.read().decode("utf-8")
        context = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        # Its symptom: TRANSFORM-length names (a larger first message) make
        # the race go the wrong way on every cached load here.
        for load in range(2):
            page.goto(url)
            page.wait_for_function(ALL_SCORED, arg=6 + many, timeout=60_000)
            page.wait_for_timeout(1000)  # what a scroll or resize would repaint
            drawn = page.evaluate(ROWS_DRAWN)
            # Rows reach the viewport's bottom (within the row buffer's
            # partial row), and there are as many as fit its height.
            assert drawn["gap"] < 30, (load, drawn)
            assert drawn["drawn"] >= drawn["height"] // 30, (load, drawn)
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


#: Every child of the header's row whose right edge passes the viewport.
HEADER_PAST = """() => {
  const out = [];
  for (const el of document.querySelector('.dstf-header-row').children) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > window.innerWidth) out.push([el.textContent, r.right]);
  }
  return out;
}"""

#: Every button in the header's row whose label is taller than `k` line
#: heights: `k` 1.5 finds a label wrapped to a second line, 2.5 one wrapped
#: to a third. At 900 px the row's buttons wrapped their labels and still
#: fitted, which is how Export left in the row passed; with an edit pending,
#: "1 pending edit" wrapped to three lines, and with a rerun running too and
#: the backend select still in the row, Save took four.
HEADER_TALL = """(k) => {
  const out = [];
  for (const b of document.querySelectorAll('.dstf-header-row .q-btn')) {
    const c = b.querySelector('.q-btn__content') || b;
    const s = getComputedStyle(c);
    const line = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2;
    const h = c.getBoundingClientRect().height;
    if (h > k * line) out.push([b.textContent, h, line]);
  }
  return out;
}"""


def _keep(page, tmp_path: Path, name: str) -> None:
    """A viewport screenshot, copied where the gate can read it when
    `DSTF_KEEP_SHOTS` is set."""
    path = tmp_path / f"task9-{name}.png"
    page.screenshot(path=str(path))
    keep = os.environ.get("DSTF_KEEP_SHOTS")
    if keep:
        import shutil

        Path(keep).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(keep) / path.name)


#: Save's longest one-edit caption. FakeLib cannot stage it (every test of
#: it has an entry), so the test writes it into the button's label in the
#: browser; the caption does not change while the header is measured, so no
#: refresh puts the server's back (asserted after).
NEW_ENTRY = "Save 1 spec edit (1 test, 1 new entry) to test_spec.json"
SAVE_TEXT = """(text) => {
  for (const b of document.querySelectorAll('.dstf-header-row .q-btn')) {
    const span = b.querySelector('.q-btn__content span.block');
    if (span && span.textContent.startsWith('Save ')) {
      if (text !== null) span.textContent = text;
      return span.textContent;
    }
  }
  return null;
}"""


def _fits(page, width: int, save: str | None = None) -> None:
    """Nothing in the header is cut off at `width`: the page does not scroll
    sideways and every child of the header row ends inside the viewport.
    At 1280 px no header button's label wraps; at 900 px a button may take
    two lines and no more (the owner's ruling, 2026-09-25, which Q13 A
    meets: one line at 900 needs 960 px even with the run indicator and
    Save's why-sentence hidden). ``save`` is written into Save's label
    first."""
    page.set_viewport_size({"width": width, "height": 900})
    if save is not None:
        assert page.evaluate(SAVE_TEXT, save) == save
    page.wait_for_function(
        "document.documentElement.scrollWidth == window.innerWidth",
        timeout=EXPECT_TIMEOUT,
    )
    row = page.locator(".dstf-header-row")
    for loc in (
        row.get_by_role("button", name=re.compile(r"^Save ")),
        row.get_by_role("button", name=re.compile(r"^Discard ")),
        page.locator(".dstf-header-menu"),
    ):
        box = loc.bounding_box()
        assert box is not None and box["x"] + box["width"] <= width, (width, box)
    assert page.evaluate(HEADER_PAST) == [], width
    assert page.evaluate(HEADER_TALL, 1.5 if width >= 1280 else 2.5) == [], width
    if save is not None:
        assert page.evaluate(SAVE_TEXT, None) == save  # no refresh undid it


def _sleeping(cfg: Path) -> None:
    """D's simulation sleeps, so a rerun of D stays running while the
    header is measured; the process is terminated on the way out."""
    (cfg.parent.parent / "Examples" / "D.py").write_text(
        "import time\n\n\ndef simulate(stop_time, tolerance):\n"
        "    time.sleep(120)\n    return {}\n",
        encoding="utf-8",
    )


def test_the_header_fits_900_px(tmp_path, playwright_browser):
    """The owner's ruling Q10 A: one header at every width, the rarely used
    actions in a ⋮ menu at its end. At 900 px the row cut off "dashboard"
    and "Stop server"; now nothing passes the viewport at 1280 or 900, no
    label wraps at 1280 and none takes a third line at 900, on the Suite
    page and on a Test page with its Variables drawer open, with nothing
    pending, with one edit pending, and in the busiest header: a rerun
    running, an edit pending and Save's caption naming a new entry. There,
    with the backend select in the row, Save took four lines at 900 px and
    two at 1280, which is why the select moved into the menu (the owner's
    ruling, Q13 A). Export left in the
    row is killed by `test_the_header_menu_holds_the_rarely_used_actions`
    (its ancestor check), not here."""
    cfg, work = _project(tmp_path)
    _sleeping(cfg)
    # An untracked variable in A's result: what the one pending edit makes
    # a watch plot of (x, the tracked one, has its plot already).
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["variables"]["w"] = [2 * v for v in payload["time"]]
    path.write_text(json.dumps(payload), encoding="utf-8")
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto(url)
        expect(page.locator(ROWS)).to_have_count(5, timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width)
            _keep(page, tmp_path, f"suite-{width}")
        page.locator(".dstf-header-menu").click()
        menu = page.locator(".q-menu")
        for text in ("Export 0 edits to spec_patch.json", "no pending edits"):
            expect(menu.get_by_text(text, exact=True)).to_be_visible(
                timeout=EXPECT_TIMEOUT
            )
        for text in ("Open in another backend…", "dashboard", "Stop server"):
            expect(menu.get_by_text(text, exact=True)).to_be_visible()
        _keep(page, tmp_path, "suite-900-menu")
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden(timeout=EXPECT_TIMEOUT)

        page.goto(f"{url}test/{A}")
        expect(page.locator(".dstf-subheader")).to_contain_text(
            A, timeout=EXPECT_TIMEOUT
        )
        page.locator(".dstf-rail .q-btn").nth(1).click()
        expect(page.locator(".dstf-var-target")).to_be_visible(timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width)
            _keep(page, tmp_path, f"test-{width}")
        page.locator(".dstf-header-menu").click()
        expect(menu.get_by_text("Stop server", exact=True)).to_be_visible(
            timeout=EXPECT_TIMEOUT
        )
        _keep(page, tmp_path, "test-900-menu")
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden(timeout=EXPECT_TIMEOUT)

        # One edit pending: a watch plot of w.
        page.set_viewport_size({"width": 1280, "height": 900})
        w = page.locator(".dstf-var-row").filter(
            has=page.locator(".dstf-var-text", has_text=re.compile("^w$"))
        )
        w.hover()
        w.locator(".dstf-var-new").click()
        header = page.locator(".dstf-header-row")
        expect(header).to_contain_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width)
            _keep(page, tmp_path, f"test-{width}-pending")

        # The busiest header: a rerun of D running as well.
        page.goto(url)
        expect(page.locator(ROWS)).to_have_count(5, timeout=EXPECT_TIMEOUT)
        _box(page, D).click()
        rerun = re.compile(r"^Queue 1$")
        page.get_by_role("button", name=rerun).first.click()
        page.locator(".q-dialog").get_by_role("button", name=rerun).click()
        expect(header).to_contain_text("1 running", timeout=EXPECT_TIMEOUT)
        expect(header).to_contain_text("1 pending edit")
        for width in (1280, 900):
            _fits(page, width, NEW_ENTRY)
            _keep(page, tmp_path, f"suite-{width}-busy")
        page.goto(f"{url}test/{A}")
        expect(header).to_contain_text("1 running", timeout=EXPECT_TIMEOUT)
        expect(page.locator(".dstf-var-target")).to_be_visible(timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width, NEW_ENTRY)
            _keep(page, tmp_path, f"test-{width}-busy")
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def test_the_dashboard_item_opens_the_dashboard_in_a_new_tab(
    tmp_path, playwright_browser
):
    """dashboard in the ⋮ menu is a link the browser follows into a new tab,
    not a `window.open` sent from the server, which a popup blocker may
    refuse (NiceGUI's `ui.navigate.to` says so), and the menu closes behind
    it. The ⋮ button names itself, says it opens a menu and whether the
    menu is open, and the menu's items are `menuitem`s (Quasar draws a
    `q-item` as a `listitem`)."""
    cfg, work = _project(tmp_path)
    (work / "dashboard.html").write_text("<h1>the dashboard</h1>", encoding="utf-8")
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto(url)
        expect(page.locator(ROWS)).to_have_count(5, timeout=EXPECT_TIMEOUT)
        more = page.locator(".dstf-header-menu")
        expect(more).to_have_attribute("aria-label", "More actions")
        expect(more).to_have_attribute("aria-haspopup", "menu")
        expect(more).to_have_attribute("aria-expanded", "false")
        more.click()
        menu = page.locator(".q-menu")
        item = menu.get_by_text("dashboard", exact=True)
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        expect(more).to_have_attribute("aria-expanded", "true", timeout=EXPECT_TIMEOUT)
        expect(menu).to_have_attribute("role", "menu")
        # Export, Change backend…, Open in another backend…, dashboard, the
        # other theme, Stop server.
        items = menu.locator(".q-item")
        expect(items).to_have_count(6)
        assert (
            items.evaluate_all("es => es.map(e => e.getAttribute('role'))")
            == ["menuitem"] * 6
        )
        with context.expect_page(timeout=EXPECT_TIMEOUT) as opened:
            item.click()
        tab = opened.value
        tab.wait_for_load_state()
        assert tab.url.endswith("/dashboard/dashboard.html"), tab.url
        expect(tab.locator("h1")).to_have_text("the dashboard")
        assert page.url == url  # the tool's own tab stays where it was
        expect(menu).to_be_hidden(timeout=EXPECT_TIMEOUT)
        expect(more).to_have_attribute("aria-expanded", "false", timeout=EXPECT_TIMEOUT)
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)


def _cpu_time_only(cfg: Path) -> None:
    """A's run kept no wall time and a result whose statistics carry a CPU
    time, as a Dymola result's do (a run_stats.json from a batch run: its
    walls null)."""
    (cfg.parent.parent.parent / "work" / "test_0001" / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": A,
                "translation_wall": None,
                "sim_wall": None,
                "elapsed": None,
                "statistics": {"CPUtime": 0.046},
            }
        ),
        encoding="utf-8",
    )


def test_a_cpu_time_in_sim_reads_its_tag_and_says_what_it_is(
    tmp_path, playwright_browser
):
    """Where no wall time was kept, Sim (s) shows the result file's CPU
    time with a small "cpu" after it, and its tooltip says so; a row with
    neither reads "—", no tag and no tooltip."""
    from dstf.reporting.rows import SIM_CPU_TIP

    proc, url, page, context, errors, _ = _served(
        tmp_path, playwright_browser, setup=_cpu_time_only
    )
    try:
        cell = page.locator(f".ag-row[row-id='{A}'] .ag-cell[col-id='sim']")
        expect(cell.locator(".dstf-cell-tag")).to_have_text(
            "cpu", timeout=EXPECT_TIMEOUT
        )
        expect(cell).to_have_text("0.05cpu")
        assert _tooltip(page, A, "sim") == SIM_CPU_TIP
        other = page.locator(f".ag-row[row-id='{D}'] .ag-cell[col-id='sim']")
        expect(other).to_have_text("—")
        expect(other.locator(".dstf-cell-tag")).to_have_count(0)
        page.mouse.move(0, 0)
        _keep(page, tmp_path, "suite-sim-cpu-1280")
        assert errors == [], errors
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
