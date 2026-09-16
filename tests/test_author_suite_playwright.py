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
        expect(page.locator(".dstf-test")).to_contain_text(
            "FakeLib.A", timeout=EXPECT_TIMEOUT
        )
        expect(page.locator(".dstf-test .dstf-badge")).to_have_text(
            "FAIL", timeout=EXPECT_TIMEOUT
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
        page.get_by_role("button", name="Stop server").click()
        proc.wait(15)
        assert proc.returncode is not None
        context.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved
