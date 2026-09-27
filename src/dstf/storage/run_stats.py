"""A test's run statistics, kept beside its result: ``<test_dir>/run_stats.json``.

The walls a run measured (translation, simulation, elapsed) and the
statistics its backend parsed live only on the in-process ``TestRunResult``.
A later ``compare``, a ``--merge`` over tests this run did not re-run, or a
reload in the tool holds none, and read the result back with no timing —
and, for Dymola, without the dslog statistics too. The file is written as
the run records the test's verdict (:meth:`BatchManifest.record_result`) and
lives in the test's own directory, which a run wipes exactly when it re-runs
the test: it is as fresh as the result file beside it.

Plain dicts only: the run result is rebuilt by the backends
(``backends.base.kept_run_result``), which keeps this module out of the
backends' import graph, as ``batch_manifest`` is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ref_index import _atomic_write_text

if TYPE_CHECKING:
    from ..backends.types import TestRunResult

RUN_STATS_FILENAME = "run_stats.json"


def write_run_stats(test_dir: Path, result: TestRunResult) -> None:
    """Write *result*'s walls, elapsed and statistics into *test_dir*.

    Never raises: it is called while the run records the test's verdict,
    which a failed write must not cost — one ``# WARN:`` line instead. A
    statistics value JSON cannot hold is written as its ``str``.
    """
    path = test_dir / RUN_STATS_FILENAME
    payload = {
        "model_id": result.model_id,
        "translation_wall": result.translation_wall,
        "sim_wall": result.sim_wall,
        "elapsed": result.elapsed,
        "statistics": result.statistics,
    }
    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, json.dumps(payload, default=str))
    except (OSError, ValueError, TypeError) as exc:
        print(f"# WARN: could not write {path}: {exc}", file=sys.stderr)


def read_run_stats(test_dir: Path, model_id: str) -> dict[str, Any] | None:
    """The run statistics kept for *model_id* in *test_dir*, or ``None``.

    ``None`` without a word when there is no file (a run from before the file
    existed); ``None`` with one warning line on stderr when the file cannot
    be read or was written for another model.
    """
    path = test_dir / RUN_STATS_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        if data.get("model_id") != model_id:
            raise ValueError(f"written for {data.get('model_id')!r}, not {model_id!r}")
        stats = data.get("statistics")
        return {
            "translation_wall": _number(data.get("translation_wall")),
            "sim_wall": _number(data.get("sim_wall")),
            "elapsed": _number(data.get("elapsed")) or 0.0,
            "statistics": stats if isinstance(stats, dict) else None,
        }
    except (OSError, ValueError) as exc:
        print(f"# WARN: ignoring {path}: {exc}", file=sys.stderr)
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
