"""What a Playwright test of the live view runs in the `dstf author` process
it starts, before `main_entry` (never imported by the tool): the Python
backend made live (`live_fake.make_live`). Not a test module (no ``test_``
prefix)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).parent))

from live_fake import make_live  # noqa: E402

make_live()
