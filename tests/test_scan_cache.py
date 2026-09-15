"""The library scan cached across restarts (brief §3.8), under the user's
cache dir — never the work dir. Keyed by the library path, the recognizer
set and the DSTF version. It validates nothing per file: on the owner's
/mnt/d mount a stat sweep costs more than the scan (plan spike 4), so a
cached open trusts the cache and rescans in the background (Session)."""

from __future__ import annotations

import json

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
