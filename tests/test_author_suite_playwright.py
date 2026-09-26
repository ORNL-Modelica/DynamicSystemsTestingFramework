"""The Suite page and the Test page stub in Chromium, served by `dstf author`
(brief §3.2, §3.3, §3.8; roadmap §2's gate predicates over FakeLib). NiceGUI's
user simulation renders nothing, so this says the page a user opens works
end to end: the process serves on 127.0.0.1 within seconds, the rows fill in
with verdicts, a check survives a click into a test and back within the tab,
the buttons say their counts, the font token reaches the body, nothing
scrolls sideways, and Stop server ends the process.

Selectors: NiceGUI's `.mark()` is server-side only, so elements are located
by the `dstf-*` classes and by role and name."""

from __future__ import annotations

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
from test_session import A, D, G, _project  # noqa: E402

READY_TIMEOUT = 30.0  # seconds to wait for the server's first 200
EXPECT_TIMEOUT = 15_000  # ms; a refresh is a websocket round trip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(cfg: Path, work: Path, log: Path) -> tuple[subprocess.Popen, str]:
    """`dstf author` as a plain process: NiceGUI's `ui.run` treats an
    inherited PYTEST_CURRENT_TEST as "I am a screen test" and demands
    NICEGUI_SCREEN_TEST_PORT, ignoring --port."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    sink = log.open("wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dstf",
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


def _row(page, short_name: str):
    """The row whose name button reads `short_name` exactly."""
    return page.locator("div.dstf-tests .dstf-row").filter(
        has=page.get_by_role("button", name=short_name, exact=True)
    )


def test_the_suite_page_fills_in_keeps_its_checks_across_a_click_and_stops(
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
        rows = page.locator("div.dstf-tests .dstf-row")
        expect(rows).to_have_count(5, timeout=EXPECT_TIMEOUT)
        badges = page.locator("div.dstf-tests .dstf-badge")
        expect(badges.filter(has_text="PASS")).to_have_count(2, timeout=EXPECT_TIMEOUT)
        for word, n in (("^FAIL$", 1), ("SIM_FAIL", 1), ("NO_REF", 1)):
            expect(badges.filter(has_text=re.compile(word))).to_have_count(
                n, timeout=EXPECT_TIMEOUT
            )
        # Each badge carries its verdict's fill (`theme.STATUS`: fail
        # #f44336, pass #4CAF50). A Quasar badge is `bg-primary` unless it
        # is built with `color=None`, and that `!important` beats the inline
        # background: every verdict came out Quasar blue (the Task 4 review).
        fill = "e => getComputedStyle(e).backgroundColor"
        assert (
            badges.filter(has_text=re.compile("^FAIL$")).first.evaluate(fill)
            == "rgb(244, 67, 54)"
        )
        assert badges.filter(has_text="PASS").first.evaluate(fill) == "rgb(76, 175, 80)"
        # The chips carry the classifier's counts.
        chips = page.locator(".dstf-chip")
        expect(chips.filter(has_text="PASS 2")).to_have_count(1, timeout=EXPECT_TIMEOUT)
        expect(chips.filter(has_text=re.compile(r"^FAIL 1$"))).to_have_count(
            1, timeout=EXPECT_TIMEOUT
        )  # not SIM_FAIL 1

        # The font token reaches the body (roadmap §1 predicate 2's last gap):
        # the computed stack STARTS with the token's first family — Quasar's
        # own default also ends in a generic sans-serif, so the tail proves
        # nothing (the Task 9 review).
        font = page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert font.strip().startswith(FONT_STACK.split(",")[0]), font
        # Nothing scrolls sideways (the old shell's tab strip did).
        layout = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth,"
            " client: document.documentElement.clientWidth, x: window.scrollX})"
        )
        assert layout["scroll"] <= layout["client"] and layout["x"] == 0, layout

        # Check D and G; the batch buttons say their object and count. Two,
        # so the Test page's "1 selected test" below cannot be the Suite's
        # selection leaking through (the Task 9 review).
        _row(page, D.rsplit(".", 1)[-1]).get_by_role("checkbox").click()
        _row(page, G.rsplit(".", 1)[-1]).get_by_role("checkbox").click()
        accept = page.get_by_role(
            "button", name=re.compile(r"^Accept 2 results as baselines")
        )
        expect(accept).to_be_enabled(timeout=EXPECT_TIMEOUT)
        rerun = page.get_by_role(
            "button", name=re.compile(r"^Rerun 2 selected tests \(Python\)")
        )
        expect(rerun).to_be_enabled(timeout=EXPECT_TIMEOUT)

        # A click on A's name views A: the Test page, in this tab.
        page.get_by_role("button", name=A.rsplit(".", 1)[-1], exact=True).click()
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
        expect(
            page.get_by_role(
                "button", name=re.compile(r"^Rerun 1 selected test \(Python\)")
            )
        ).to_be_visible(timeout=EXPECT_TIMEOUT)

        # Back to the Suite: D and G are still checked, A is not (viewing ≠
        # selecting).
        page.get_by_role("link", name="← Suite").click()
        expect(page.locator("div.dstf-tests .dstf-row")).to_have_count(
            5, timeout=EXPECT_TIMEOUT
        )
        for checked in (D, G):
            expect(
                _row(page, checked.rsplit(".", 1)[-1]).get_by_role("checkbox")
            ).to_have_attribute("aria-checked", "true", timeout=EXPECT_TIMEOUT)
        expect(
            _row(page, A.rsplit(".", 1)[-1]).get_by_role("checkbox")
        ).to_have_attribute("aria-checked", "false", timeout=EXPECT_TIMEOUT)
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
#: "1 pending edit" wrapped to three lines.
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


def _fits(page, width: int) -> None:
    """Nothing in the header is cut off at `width`: the page does not scroll
    sideways and every child of the header row ends inside the viewport.
    At 1280 px no header button's label wraps either; at 900 px the pending
    count, Save and Discard may take two lines and no more (the owner's
    ruling, 2026-09-25: one line needs ~950 px once the backend select, the
    run indicator and Save's why-sentence are in the row)."""
    page.set_viewport_size({"width": width, "height": 900})
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


def _count_lines(page) -> float:
    """The pending count's label height in line heights. It keeps one line
    at 900 px (`no-wrap`): squeezed, it took three beside Save's longest
    one-edit caption, "(1 test, 1 new entry)", which FakeLib cannot stage
    (every test of it has an entry), so this pins it instead."""
    return page.locator(".dstf-header-row .q-btn", has_text="pending edit").evaluate(
        """b => { const c = b.querySelector('.q-btn__content');
                  return c.getBoundingClientRect().height
                         / parseFloat(getComputedStyle(c).lineHeight); }"""
    )


def test_the_header_fits_900_px(tmp_path, playwright_browser):
    """The owner's ruling Q10 A: one header at every width, the rarely used
    actions in a ⋮ menu at its end. At 900 px the row cut off "dashboard"
    and "Stop server"; now nothing passes the viewport at 1280 or 900, no
    label wraps at 1280 and none takes a third line at 900, on the Suite
    page and on a Test page with its Variables drawer open, with nothing
    pending and again with one edit pending (the row then carries the
    backend's why-sentence, and "1 pending edit" took three lines at 900).
    Export left in the row is killed by
    `test_the_header_menu_holds_the_rarely_used_actions` (its ancestor
    check), not here: at 900 px the row's buttons may wrap and still fit."""
    cfg, work = _project(tmp_path)
    log = tmp_path / "author.log"
    proc, url = _start(cfg, work, log)
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto(url)
        expect(page.locator("div.dstf-tests .dstf-row")).to_have_count(
            5, timeout=EXPECT_TIMEOUT
        )
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

        # One edit pending: x on a plot of its own.
        page.set_viewport_size({"width": 1280, "height": 900})
        x = page.locator(".dstf-var-row").filter(has_text="x").first
        x.hover()
        x.locator(".dstf-var-new").click()
        header = page.locator(".dstf-header-row")
        expect(header).to_contain_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width)
            _keep(page, tmp_path, f"test-{width}-pending")
        assert _count_lines(page) <= 1.5
        page.goto(url)
        expect(page.locator("div.dstf-tests .dstf-row")).to_have_count(
            5, timeout=EXPECT_TIMEOUT
        )
        expect(header).to_contain_text("1 pending edit", timeout=EXPECT_TIMEOUT)
        for width in (1280, 900):
            _fits(page, width)
            _keep(page, tmp_path, f"suite-{width}-pending")
        assert _count_lines(page) <= 1.5
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
    refuse (NiceGUI's `ui.navigate.to` says so). The ⋮ button names itself
    and says it opens a menu."""
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
        expect(page.locator("div.dstf-tests .dstf-row")).to_have_count(
            5, timeout=EXPECT_TIMEOUT
        )
        more = page.locator(".dstf-header-menu")
        expect(more).to_have_attribute("aria-label", "More actions")
        expect(more).to_have_attribute("aria-haspopup", "menu")
        more.click()
        item = page.locator(".q-menu").get_by_text("dashboard", exact=True)
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        with context.expect_page(timeout=EXPECT_TIMEOUT) as opened:
            item.click()
        tab = opened.value
        tab.wait_for_load_state()
        assert tab.url.endswith("/dashboard/dashboard.html"), tab.url
        expect(tab.locator("h1")).to_have_text("the dashboard")
        assert page.url == url  # the tool's own tab stays where it was
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
