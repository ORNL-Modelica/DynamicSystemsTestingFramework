"""Unit tests for the Dymola Python interface loader's wheel-extract cache.

Regression test for the bug where a stale or partial wheel extraction
(seen in the wild as a 32-byte `dymola_interface.py` stub) was reused
indefinitely because the `.extracted` marker file only stored the source
path, not a content signature. The marker now stores `path|size|mtime`
of the source archive; a mismatch wipes and re-extracts.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def _write_minimal_wheel(path: Path, payload: bytes) -> None:
    """Write a wheel-shaped zip containing one file: dymola/dymola_interface.py."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("dymola/__init__.py", b"")
        z.writestr("dymola/dymola_interface.py", payload)


@pytest.fixture
def fake_wheel(tmp_path: Path) -> Path:
    archive = tmp_path / "dymola-test-py3-none-any.whl"
    _write_minimal_wheel(archive, b"# generation 1: real content " * 100)
    return archive


def test_extracts_wheel_on_first_call(monkeypatch, tmp_path, fake_wheel):
    from dstf.simulators.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    entry = il._prepare_sys_path(fake_wheel)

    extracted = entry / "dymola" / "dymola_interface.py"
    assert extracted.exists()
    assert extracted.read_bytes().startswith(b"# generation 1")
    marker = entry / ".extracted"
    assert marker.exists()
    # Signature is path|size|mtime — verify all three present so a stale
    # path-only marker (the pre-fix shape) would mismatch and re-extract.
    parts = marker.read_text().split("|")
    assert len(parts) == 3
    assert parts[0] == str(fake_wheel)


def test_reuses_cache_when_signature_matches(monkeypatch, tmp_path, fake_wheel):
    from dstf.simulators.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    entry1 = il._prepare_sys_path(fake_wheel)
    extracted = entry1 / "dymola" / "dymola_interface.py"
    mtime_before = extracted.stat().st_mtime_ns

    entry2 = il._prepare_sys_path(fake_wheel)

    assert entry1 == entry2
    assert extracted.stat().st_mtime_ns == mtime_before, (
        "second call should not re-extract when the source archive is unchanged"
    )


def test_reextracts_when_wheel_signature_changes(monkeypatch, tmp_path, fake_wheel):
    """The original bug: a wheel was replaced in place (e.g. Dymola refresh)
    but our cache held a now-stale extraction. The path-only marker never
    invalidated. Now the marker records size+mtime so any wheel change is
    detected and the cache is wiped."""
    from dstf.simulators.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    il._prepare_sys_path(fake_wheel)

    # Replace the wheel with different content; bump mtime explicitly so this
    # test isn't a flaky race-with-filesystem-resolution.
    _write_minimal_wheel(fake_wheel, b"# generation 2: different content " * 200)
    import os
    import time

    new_mtime = time.time() + 5
    os.utime(fake_wheel, (new_mtime, new_mtime))

    entry = il._prepare_sys_path(fake_wheel)
    extracted = entry / "dymola" / "dymola_interface.py"
    assert extracted.read_bytes().startswith(b"# generation 2"), (
        "cache should re-extract when the source wheel changes"
    )


def test_reextracts_when_cache_marker_uses_old_path_only_format(
    monkeypatch,
    tmp_path,
    fake_wheel,
):
    """A cache populated by a pre-fix DSTF version has a marker containing
    just `str(archive)` — no size or mtime. Treat it as a mismatch and
    re-extract so users upgrading don't keep using broken caches."""
    from dstf.simulators.dymola import interface_loader as il

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(il, "_cache_root", lambda: cache_root)

    # Simulate a pre-fix cache: extract dir + path-only marker + a stub file.
    extract_dir = cache_root / fake_wheel.stem
    (extract_dir / "dymola").mkdir(parents=True)
    (extract_dir / "dymola" / "dymola_interface.py").write_bytes(b"# stub")
    (extract_dir / ".extracted").write_text(str(fake_wheel))  # old format

    il._prepare_sys_path(fake_wheel)

    extracted = extract_dir / "dymola" / "dymola_interface.py"
    assert extracted.read_bytes().startswith(b"# generation 1"), (
        "stub from a pre-fix cache should be replaced by a real extraction"
    )
