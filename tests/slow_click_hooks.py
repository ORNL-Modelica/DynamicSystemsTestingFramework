"""What a Playwright test of a Queue click runs in the `dstf author` process
it starts, before `main_entry` (never imported by the tool): a click's read
of the library that waits for as long as a file ``hold-click`` stands
beside the config, so the page can be read between the click and the read
— a tenth of a second on a small library, seconds on a large one on a slow
mount. Not a test module (no ``test_`` prefix)."""

import sys
import time
from pathlib import Path

from dstf.authoring import session as _session

_HOLD = Path(sys.argv[sys.argv.index("--config") + 1]).parent / "hold-click"
_read = _session._read_click


def _held(config, asks):
    while _HOLD.exists():
        time.sleep(0.02)
    return _read(config, asks)


_session._read_click = _held
