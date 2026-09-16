"""The library scan cached across restarts (brief §3.8), under the user's
cache dir — never the work dir. Keyed by the library path and the
recognizer set; a cache from another DSTF version is ignored. It validates nothing per file: on the owner's
/mnt/d mount a stat sweep costs more than the scan (plan spike 4), so a
cached open trusts the cache and rescans in the background (Session)."""

from __future__ import annotations

import json
import logging
import os
import threading

import pytest

from dstf.discovery import scan_cache
from dstf.discovery.test_registry import scan_source_tests
from test_discovery import _write_lib


@pytest.fixture(autouse=True)
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_cache_dir_is_under_xdg_cache_home(tmp_path):
    assert scan_cache.cache_dir() == tmp_path / "cache" / "dstf" / "scan"


def test_store_then_load_round_trips_every_test_model(tmp_path):
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    path = scan_cache.store_scan(config, scanned)
    assert path.parent == scan_cache.cache_dir()
    assert scan_cache.load_scan(config) == scanned


def test_a_missing_cache_loads_none(tmp_path):
    config = _write_lib(tmp_path)
    assert scan_cache.load_scan(config) is None


def test_the_key_changes_with_the_library_path_and_the_recognizers(tmp_path):
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    scan_cache.store_scan(config, scanned)
    (tmp_path / "elsewhere").mkdir()
    other = _write_lib(tmp_path / "elsewhere")
    assert scan_cache.load_scan(other) is None
    config.disabled_bundled.append("modelica:bundled-unit-tests")
    assert scan_cache.load_scan(config) is None


def test_a_cache_from_another_dstf_version_is_ignored(tmp_path, monkeypatch):
    config = _write_lib(tmp_path)
    path = scan_cache.store_scan(config, scan_source_tests(config))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dstf_version"] = "0.0.0+other"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert scan_cache.load_scan(config) is None


def test_an_unreadable_cache_loads_none(tmp_path):
    config = _write_lib(tmp_path)
    path = scan_cache.store_scan(config, scan_source_tests(config))
    path.write_text("{not json", encoding="utf-8")
    assert scan_cache.load_scan(config) is None


def test_a_non_modelica_source_is_never_cached(tmp_path):
    config = _write_lib(tmp_path)
    config.source_type = "python"
    assert scan_cache.store_scan(config, {}) is None
    assert scan_cache.load_scan(config) is None


def test_a_fresh_process_finds_the_cache(tmp_path):
    """The bundled recognizer registers only when `mo_parser` is imported,
    which `scan_source_tests` does lazily; a process that loads the cache
    BEFORE scanning anything (every warm `dstf author`) must compute the
    same key, or the cache never hits outside the test that wrote it."""
    import os
    import subprocess
    import sys

    config = _write_lib(tmp_path)
    scan_cache.store_scan(config, scan_source_tests(config))
    probe = (
        "from pathlib import Path\n"
        "from dstf.config import Config\n"
        "from dstf.discovery.scan_cache import load_scan\n"
        f"cfg = Config(config_file={str(tmp_path / 'testing.json')!r})\n"
        "print('HIT' if load_scan(cfg) else 'MISS')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "XDG_CACHE_HOME": os.environ["XDG_CACHE_HOME"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "HIT", out.stderr


def test_an_unwritable_cache_dir_is_a_warning_and_stores_nothing(
    tmp_path, monkeypatch, caplog
):
    """The cache is an accelerator: a home it cannot be written to (here the
    `dstf` segment is a FILE, so `mkdir -p` raises NotADirectoryError, an
    OSError) is logged and skipped, never raised at whoever wanted a scan."""
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "dstf").write_text("not a directory", encoding="utf-8")
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    with caplog.at_level(logging.WARNING, logger="dstf.discovery.scan_cache"):
        assert scan_cache.store_scan(config, scanned) is None
    assert any("not caching the scan" in r.getMessage() for r in caplog.records)
    assert scan_cache.load_scan(config) is None


def test_two_threads_can_store_the_same_key_at_once(tmp_path, monkeypatch):
    """A cached open's rescan thread and a second Session's in one process
    write the same key. Both are held at the rename until both temporaries
    exist: with a per-process temporary name the second write replaced the
    first thread's file from under it and its own rename raised
    FileNotFoundError (which the OSError guard would turn into a silent
    None — either way, not the path)."""
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    gate = threading.Barrier(2, timeout=10)
    real_replace = os.replace

    def held_replace(src, dst):
        gate.wait()
        return real_replace(src, dst)

    monkeypatch.setattr(scan_cache.os, "replace", held_replace)
    outcomes: list = []

    def store():
        try:
            outcomes.append(scan_cache.store_scan(config, scanned))
        except BaseException as e:  # a failure is an outcome too
            outcomes.append(e)

    threads = [threading.Thread(target=store) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert outcomes == [scan_cache._path(config)] * 2
    assert scan_cache.load_scan(config) == scanned
    assert not list(scan_cache.cache_dir().glob("*.tmp"))
