"""The one browser test (§4.7): the real page, served by `dstf author`, in
Chromium.

Everything else about the shell is driven by NiceGUI's in-process user
simulation, which never renders anything. This says the page a user opens
actually works end to end: the process serves on 127.0.0.1, the row button
selects a test, the leaf row selects a leaf, Edit criteria's form fires
`on_change` from a real keystroke, and the edit reaches the Session — the
header counts two pending ops and the leaf's verdict flips to PASS — while
the spec file on disk is untouched, because nothing was saved.

Selectors: NiceGUI's `.mark()` is server-side only (`Element._markers` is
read by `ElementFilter`, never serialized to the DOM), so the row buttons
the simulation finds by marker have no DOM handle here. They are located by
role and their short name inside the left column instead.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

import time  # noqa: E402

from playwright.sync_api import expect  # noqa: E402

from test_session import A, _project  # noqa: E402

READY_TIMEOUT = 30.0  # seconds to wait for the server's first 200
EXPECT_TIMEOUT = 15_000  # ms; a refresh is a websocket round trip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_serving(url: str, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"dstf author exited with {proc.returncode}\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    pytest.fail(f"no 200 from {url} within {READY_TIMEOUT}s\n{log.read_text()}")


def test_the_real_page_edits_one_tolerance_and_saves_nothing(
    tmp_path, playwright_browser
):
    cfg, work = _project(tmp_path)
    spec_file = cfg.parent / "test_spec.json"
    before = spec_file.read_bytes()
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    log = tmp_path / "author.log"
    # The server is a plain process, not pytest: NiceGUI's `ui.run` treats an
    # inherited PYTEST_CURRENT_TEST as "I am a screen test" and demands
    # NICEGUI_SCREEN_TEST_PORT, ignoring --port.
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}

    with log.open("wb") as sink:
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
    try:
        _wait_until_serving(url, proc, log)
        context = playwright_browser.new_context()
        page = context.new_page()
        page.goto(url)

        # The page is mounted and the websocket is up once the header draws.
        pending = page.get_by_role("button", name=re.compile(r"\d+ pending"))
        expect(pending).to_contain_text("0 pending", timeout=EXPECT_TIMEOUT)

        # The test list is the first `w-1/5` column; its rows are buttons
        # named by the model's short name ("A", not "FakeLib.A").
        tests_column = page.locator("div.w-1\\/5").first
        tests_column.get_by_role(
            "button", name=A.rsplit(".", 1)[-1], exact=True
        ).click()

        # The tree row lives in the centre column: Edit tree's panel carries
        # buttons naming the same path ("wrap /metrics/children/0"), so the
        # path alone is not unique on the page. And `filter(has_text=...)`
        # rather than `get_by_role(name=re.compile(...))`: Playwright
        # serializes a regex into the role selector delimited by slashes,
        # and the leaf's path is full of them.
        centre = page.locator("div.w-3\\/5").first
        leaf = centre.get_by_role("button").filter(has_text="/metrics/children/0")
        expect(leaf).to_contain_text("FAIL", timeout=EXPECT_TIMEOUT)
        leaf.click()

        page.get_by_role("tab", name="Edit criteria").click()
        tolerance = page.get_by_label("Tolerance", exact=True)
        expect(tolerance).to_be_visible(timeout=EXPECT_TIMEOUT)
        tolerance.fill("0.5")
        tolerance.press("Tab")  # blur: Quasar emits the change NiceGUI listens for

        # The materialization op + the replace, and the rescored verdict.
        expect(pending).to_contain_text("2 pending", timeout=EXPECT_TIMEOUT)
        expect(leaf).to_contain_text("PASS", timeout=EXPECT_TIMEOUT)

        context.close()
    finally:
        proc.terminate()
        proc.wait(10)

    assert spec_file.read_bytes() == before  # nothing was saved
