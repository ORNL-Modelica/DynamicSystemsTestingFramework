"""`dstf plot` in a real headless Chromium: the images it writes from two
runs and the gallery that holds them — measured, never by the dict alone."""

import os
import struct
from pathlib import Path

import pytest

from conftest import python_example_copy, write_python_run
from dstf.cli.main import main
from dstf.reporting.baseline_gallery import GALLERY

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

#: Where Playwright's browsers are, read before conftest points
#: XDG_CACHE_HOME (where Playwright looks on Linux) at a per-test folder.
_BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
)

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"


@pytest.fixture(autouse=True)
def _browsers_where_they_are(monkeypatch):
    """The command under test launches its own Chromium inside the test."""
    if Path(_BROWSERS).is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", _BROWSERS)


def _size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def test_two_runs_are_drawn_and_the_page_holds_every_image(tmp_path, capsys):
    python_example_copy(tmp_path)
    config = tmp_path / "lib" / "Resources" / "ReferenceResults" / "testing.json"
    a = write_python_run(
        tmp_path / "main", {RAMP: {"x": [0, 2, 4, 6, 8]}, CSV: {"x": [1.0] * 5}}
    )
    b = write_python_run(
        tmp_path / "fix", {RAMP: {"x": [0, 2, 4, 7, 9]}, CSV: {"x": [1.0] * 5}}
    )
    out = tmp_path / "out"
    code = main(
        ["plot", f"main={a}", f"fix={b}", "-o", str(out), "--config", str(config)]
    )
    assert code == 0
    (png,) = out.rglob("*.png")
    assert png.relative_to(out).as_posix() == "SimpleRamp/Python-linux/01-x.png"
    # two runs and no notes: baseline plot's 1000 x 640
    assert _size(png.read_bytes()) == (1000, 640)
    page = (out / GALLERY).read_text(encoding="utf-8")
    assert page.count('src="data:image/png;base64,') == 1
    said = capsys.readouterr().out.splitlines()
    assert said[0] == "Nothing moved in 1 test: ConstantCsv."
    assert said[1].startswith(f"Wrote 1 image, 0 tables and {out / GALLERY} (")
