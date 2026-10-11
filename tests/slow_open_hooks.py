"""What a Playwright test of an OPENING project runs in the `dstf author`
process it starts, before `main_entry` (never imported by the tool): a cold
scan that waits for as long as a file ``hold-open`` stands beside the
config, so the page can be read while every row is known and none is
scored. Not a test module (no ``test_`` prefix)."""

import sys
import time
from pathlib import Path

from dstf.authoring import session as _session

_HOLD = Path(sys.argv[sys.argv.index("--config") + 1]).parent / "hold-open"
_scan = _session.scan_source_tests


def _held(config, **kw):
    while _HOLD.exists():
        time.sleep(0.02)
    return _scan(config, **kw)


_session.load_scan = lambda config: None
_session.scan_source_tests = _held
