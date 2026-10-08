"""A Python-backend fixture project made LIVE (D151), for the Session's, the
page's and the browser's tests. Not a test module (no ``test_`` prefix).

No bundled backend that runs in CI has a result file that grows: Dymola and
OpenModelica do, and neither is there. So the smallest double: the Python
backend declares ``Capability.LIVE_RESULTS`` with ``live.mat`` as its
growing file (:func:`make_live`), and every test's ``simulate`` is
:data:`SIMULATE_LIVE`, which writes a real DSresult v4 file a row at a time
— `mat4_files`' writer, the header counting 0 as OpenModelica's does until
a run ends — before it returns the same series as its result.

A test steers a run through files beside its script:

* ``<name>.hold`` holding ``N``: the run writes rows ``0 … N-1`` and waits
  for the file to go — a run stopped at a known time, for as long as the
  test looks at it;
* ``<name>.rows`` (default 21), ``<name>.pause`` (seconds a row, default 0),
  ``<name>.scale`` (x = scale·t; default 1);
* ``<name>.boom``: the run raises after its last row.

The series: ``x = scale·t``, ``y = 2t`` and — tracked by no test —
``z = t²``.
"""

from __future__ import annotations

from pathlib import Path

from test_session import A, _two_var_project

TESTS = str(Path(__file__).parent)

SIMULATE_LIVE = f"""
import pathlib
import sys
import time

sys.path.insert(0, {TESTS!r})

import numpy as np  # noqa: E402
from mat4_files import write_result_mat  # noqa: E402

HERE = pathlib.Path(__file__)


def _read(suffix, default):
    try:
        return HERE.with_suffix(suffix).read_text().strip() or default
    except OSError:
        return default


def _held(row):
    hold = HERE.with_suffix(".hold")
    try:
        return hold.exists() and row >= int(hold.read_text() or 0)
    except (OSError, ValueError):
        return False


def simulate(stop_time, tolerance):
    rows = int(_read(".rows", "21"))
    pause = float(_read(".pause", "0"))
    scale = float(_read(".scale", "1"))
    t = np.linspace(0.0, stop_time, rows)
    series = {{"x": scale * t, "y": 2 * t, "z": t * t}}
    mat = write_result_mat(
        pathlib.Path("live.mat"),
        tool="openmodelica",
        dtype="<f8",
        time=t,
        trajectories=series,
        points=0,
        declared=0,
    )
    with open("live.mat", "ab") as f:
        for row in range(rows):
            while _held(row):
                time.sleep(0.01)
            f.write(mat.rows(row, row + 1))
            f.flush()
            time.sleep(pause)
    if HERE.with_suffix(".boom").exists():
        raise RuntimeError("boom in simulate")
    return {{"time": t.tolist(), "variables": {{n: v.tolist() for n, v in series.items()}}}}
"""


def make_live(patch=setattr) -> None:
    """The Python backend with a live view: ``patch(obj, name, value)`` is
    ``monkeypatch.setattr`` in a test, plain ``setattr`` in a server a
    browser test starts."""
    from dstf.backends.common import result_files
    from dstf.backends.python.backend import PythonBackend
    from dstf.backends.types import Capability

    finished = PythonBackend.result_names.__func__

    def result_names(cls, test_dir):
        # The contract of a backend with a live view: a growing file's
        # variables are listed too.
        if (test_dir / "result.json").exists():
            return finished(cls, test_dir)
        return result_files.mat_names(test_dir / "live.mat")

    patch(
        PythonBackend,
        "capabilities",
        PythonBackend.capabilities | {Capability.LIVE_RESULTS},
    )
    patch(
        PythonBackend,
        "live_result_path",
        classmethod(lambda cls, test_dir: test_dir / "live.mat"),
    )
    patch(PythonBackend, "result_names", classmethod(result_names))


def write_scripts(examples: Path) -> None:
    """`SIMULATE_LIVE` as every test's script; A's result differs from its
    baseline (x = t), as the fixture projects' does."""
    for script in examples.rglob("*.py"):
        script.write_text(SIMULATE_LIVE, encoding="utf-8")
    (examples / "A.scale").write_text("1.25", encoding="utf-8")


def live_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """`test_session._two_var_project` — A tracks x and y against a
    baseline of both and fails — with `SIMULATE_LIVE` as every script.
    Returns the config file, the work dir and the Examples directory."""
    cfg, work = _two_var_project(tmp_path)
    examples = tmp_path / "FakeLib" / "Examples"
    write_scripts(examples)
    return cfg, work, examples


def steer(examples: Path, model_id: str, **files: object) -> None:
    """Write ``<name>.<key>`` beside ``model_id``'s script for each of
    ``files`` (``hold=5``, ``pause=0.05`` …); None removes it."""
    short = model_id.rsplit(".", 1)[-1]
    for key, value in files.items():
        path = examples / f"{short}.{key}"
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(str(value), encoding="utf-8")


__all__ = ["A", "SIMULATE_LIVE", "live_project", "make_live", "steer", "write_scripts"]
