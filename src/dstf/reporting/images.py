"""PNG images of Plotly figure dicts (``dstf baseline plot``), drawn by the
VENDORED plotly.js (``templates/vendor/plotly.min.js``) in one headless
Chromium for the whole run: no network, one image at a time.

Playwright is imported here only, inside :class:`Renderer`: the rest of
DSTF runs without it. Without Playwright, or without its Chromium, entering
a renderer raises :class:`ImagesUnavailable`, one sentence naming the
install — before the caller has written anything.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType
from typing import Any

#: The plotly.js a report page reads, shipped in the wheel.
PLOTLY_JS = Path(__file__).parent / "templates" / "vendor" / "plotly.min.js"

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'></head>"
    "<body style='margin:0;background:white'><div id='p'></div></body></html>"
)
_DRAW = """async (f) => {
    const gd = document.getElementById('p');
    gd.style.width = f.layout.width + 'px';
    gd.style.height = f.layout.height + 'px';
    await Plotly.react(gd, f.data, f.layout, f.config);
    await document.fonts.ready;
}"""


class ImagesUnavailable(Exception):
    """No browser to draw with; the text is one sentence naming the install."""


def _install() -> str:
    return f'"{sys.executable}" -m playwright install chromium'


class Renderer:
    """One headless Chromium with plotly.js loaded, for as long as the
    ``with``: :meth:`png` draws one figure."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> Renderer:
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as e:
            raise ImagesUnavailable(
                "drawing images needs Playwright, which is not installed here: "
                "install the browser extra (pip install "
                "'dynamic-systems-testing-framework[browser]'), then run "
                f"{_install()}"
            ) from e
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch()
        except Error as e:
            self._playwright.stop()
            raise ImagesUnavailable(
                f"Playwright has no Chromium here: run {_install()}"
            ) from e
        try:
            self._page = self._browser.new_page()
            # Nothing is fetched: the page and plotly.js are inline.
            self._page.route("**/*", lambda route: route.abort())
            self._page.set_content(_PAGE)
            self._page.add_script_tag(content=PLOTLY_JS.read_text(encoding="utf-8"))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def png(self, figure: dict) -> bytes:
        """The figure as a PNG, ``layout.width`` × ``layout.height``."""
        layout = figure["layout"]
        self._page.set_viewport_size(
            {"width": int(layout["width"]), "height": int(layout["height"])}
        )
        self._page.evaluate(_DRAW, figure)
        return self._page.locator("#p").screenshot(type="png")

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
