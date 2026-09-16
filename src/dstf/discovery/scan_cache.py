"""The source scan, cached across tool restarts (brief §3.8).

Under ``$XDG_CACHE_HOME/dstf/scan/`` (default ``~/.cache/dstf/scan/``),
one JSON file per (library path, recognizer set) — a file another DSTF
version wrote is ignored and replaced — never in
the work dir, which is a run's artifact directory that other tools read,
and never validated per file: on a 9P mount (``/mnt/d``) a ``stat`` sweep
over TRANSFORM's 3 269 ``.mo`` files measured 27 s, longer than parsing
them. The Session opens from the cache and rescans in the background.
Only ``source_type == "modelica"`` scans anything, so only those cache.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from .. import __version__
from ..config import Config
from . import (
    mo_parser,  # noqa: F401  — registers the bundled recognizer; the key names it
)
from .recognizer import get_recognizers
from .test_registry import TestModel

logger = logging.getLogger(__name__)

CACHE_VERSION = 1


def cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "dstf" / "scan"


def _key(config: Config) -> str:
    recognizers = sorted(
        r.name
        for r in get_recognizers("modelica")
        if r.name not in config.disabled_bundled
    ) + sorted(r.name for r in config.recognizers if "modelica" in r.applies_to)
    payload = json.dumps(
        [str(config.library_dir.resolve()), recognizers, CACHE_VERSION], sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _path(config: Config) -> Path:
    return cache_dir() / f"{_key(config)}.json"


def _to_json(test: TestModel) -> dict:
    if test.metric_tree_spec is not None:
        raise ValueError(f"{test.model_id}: a scanned test carries no metric tree")
    data = dataclasses.asdict(test)
    data["source_file"] = str(test.source_file)
    return data


def _from_json(data: dict) -> TestModel:
    data = dict(data)
    data["source_file"] = Path(data["source_file"])
    return TestModel(**data)


def load_scan(config: Config) -> dict[str, TestModel] | None:
    """The cached scan for ``config``, or None (no cache, another DSTF
    version, unreadable, or not a Modelica source)."""
    if config.source_type != "modelica":
        return None
    path = _path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("dstf_version") != __version__:
            return None
        return {m: _from_json(d) for m, d in data["tests"].items()}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.warning("ignoring the scan cache %s: %s", path, e)
        return None


def store_scan(config: Config, scanned: dict[str, TestModel]) -> Path | None:
    """Write the scan; returns the path, or None for a non-Modelica source
    or when the cache cannot be written (a read-only home, a full disk).
    The cache is an accelerator: a failed write is a warning, never an
    error — an open that cannot cache still opens, and a rescan that cannot
    cache still re-merges and finishes."""
    if config.source_type != "modelica":
        return None
    path = _path(config)
    payload = {
        "dstf_version": __version__,
        "library_dir": str(config.library_dir.resolve()),
        "tests": {m: _to_json(t) for m, t in scanned.items()},
    }
    # Unique per thread as well as per process: a cached open's rescan
    # thread and a second Session's in the same process write the same
    # key, and with one name the second write replaced the first's file
    # from under it and its own rename failed.
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("not caching the scan at %s: %s", path, e)
        return None
    return path
