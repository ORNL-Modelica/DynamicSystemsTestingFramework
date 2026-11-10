"""`dstf manifest cleanup`: what a work dir holds that no test uses.

Each test builds a real work dir and reference folder, runs the command,
and reads the directory and the manifest FILE afterwards. A code is written
as a literal or computed here with hashlib, never by the code under test.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from conftest import PROVENANCE, python_example_copy
from dstf.backends.types import TestResult, VariableResult
from dstf.cli.main import main
from dstf.storage.reference_store import ReferenceStore

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"
POINTS = "PythonTestingLib.Examples.PointsCheck"
GONE = "PythonTestingLib.Examples.Gone"
#: sha256(RAMP)[:12], from `sha256sum` outside this suite.
RAMP_STEM = "ref_9bbf24e6f8aa"


def _stem(model_id: str) -> str:
    return "ref_" + hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]


def _project(tmp_path):
    """The Python example with ONE stored baseline (SimpleRamp's, through
    the real writer) and a work dir holding live, orphan, old-name and
    unrelated things. Returns ``(config, argv)``."""
    config, tests = python_example_copy(tmp_path, baselines=False)
    (ramp,) = [t for t in tests if t.model_id == RAMP]
    t = np.linspace(0.0, 1.0, 5)
    result = TestResult(
        model_id=RAMP,
        success=True,
        variables=[VariableResult(index=1, time=t, values=t, name="y")],
    )
    assert ReferenceStore(config).store_reference(ramp, result, provenance=PROVENANCE)

    work = config.work_dir
    manifest = {
        "test_0001": {"model_id": RAMP, "ref_id": RAMP_STEM},
        "test_0002": {"model_id": CSV, "ref_id": None},
        "test_0003": {"model_id": POINTS, "ref_id": None},
        "test_0004": {"model_id": GONE, "ref_id": None},
    }
    work.mkdir(parents=True)
    (work / "batch_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    for key in manifest:
        (work / key).mkdir()
        (work / key / "result.mat").write_bytes(b"x")
    reports = work / "reports"
    for name in (
        RAMP_STEM,  # live: a declared model with a baseline
        "test_0002",  # live: a declared model without one
        "test_0003",  # live
        "test_0004",  # the orphan's, by its key
        _stem(GONE),  # the orphan's, by its code
        _stem(CSV),  # a declared model's, which has no baseline (any more)
        "ref_0007",  # an old numbered name
        "ref_0123456789ab",  # the shape of a code, of no declared model
        "test_0009",  # a key no manifest entry has
        "ref_123",  # too short to be either name
        "ref_0123456789abc",  # 13 hex: not a code
        "my_notes",  # a user's
        "PythonTestingLib_Examples_SimpleRamp",  # a model-named page
    ):
        (reports / name).mkdir(parents=True)
        (reports / name / "interactive.html").write_text("<html/>", encoding="utf-8")
    (reports / "plotly.min.js").write_text("//", encoding="utf-8")
    argv = [
        "manifest",
        "cleanup",
        "--config",
        str(config.config_file),
        "--work-dir",
        str(work),
    ]
    return config, argv


def _listing(root) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


#: What the dry run lists, and `--apply` removes.
STALE = sorted(
    [
        f"batch_manifest.json: test_0004 ({GONE})",
        "reports/ref_0007/",
        "reports/ref_0123456789ab/",
        f"reports/{_stem(CSV)}/",
        f"reports/{_stem(GONE)}/",
        "reports/test_0004/",
        "reports/test_0009/",
        "test_0004/",
    ]
)


def test_a_dry_run_lists_exactly_what_no_test_uses_and_removes_nothing(
    tmp_path, capsys
):
    config, argv = _project(tmp_path)
    before = _listing(tmp_path)

    rc = main(argv)

    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == [
        f"In {config.work_dir}, no test uses:",
        *(f"  {line}" for line in STALE),
        "Dry run: nothing was removed. Re-run with --apply to remove them.",
    ]
    assert _listing(tmp_path) == before


def test_apply_removes_exactly_what_the_dry_run_lists(tmp_path, capsys):
    config, argv = _project(tmp_path)
    work = config.work_dir
    before = _listing(work)

    rc = main([*argv, "--apply"])

    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[-1] == "Removed 7 directories and 1 manifest entry."
    gone = {line for line in STALE if line.endswith("/")}
    after = _listing(work)
    assert sorted(set(before) - set(after)) == _under(before, gone)
    assert set(after) <= set(before)
    manifest = json.loads((work / "batch_manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest) == ["test_0001", "test_0002", "test_0003"]
    assert manifest["test_0001"]["model_id"] == RAMP


def _under(listing: list[str], dirs: set[str]) -> list[str]:
    """Every path of ``listing`` that is one of ``dirs`` or inside one."""
    return sorted(
        p for p in listing if any(p == d.rstrip("/") or p.startswith(d) for d in dirs)
    )


def test_nothing_stale_is_said_in_one_line(tmp_path, capsys):
    config, argv = _project(tmp_path)
    assert main([*argv, "--apply"]) == 0
    capsys.readouterr()
    after = _listing(tmp_path)

    assert main(argv) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"Nothing to clean up in {config.work_dir}: every entry and directory "
        "belongs to a test."
    ]
    assert _listing(tmp_path) == after


def _refused(tmp_path, capsys, argv) -> str:
    before = _listing(tmp_path)
    rc = main([*argv, "--apply"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert _listing(tmp_path) == before
    return captured.err.strip()


def test_a_missing_reference_folder_is_refused_and_nothing_removed(tmp_path, capsys):
    config, argv = _project(tmp_path)
    ref_dir = config.reference_dir
    for f in ref_dir.iterdir():
        f.unlink()
    ref_dir.rmdir()

    err = _refused(tmp_path, capsys, argv)

    assert err == (
        f"Config error: the reference folder {ref_dir} is not there — "
        "nothing was removed"
    )


@pytest.mark.parametrize("state", ["missing", "unreadable"])
def test_a_batch_manifest_missing_or_unreadable_is_refused_and_nothing_removed(
    state, tmp_path, capsys
):
    config, argv = _project(tmp_path)
    path = config.work_dir / "batch_manifest.json"
    if state == "missing":
        path.unlink()
        said = "is not there"
    else:
        path.write_text('{"test_0001": ', encoding="utf-8")
        said = "cannot be read: "

    err = _refused(tmp_path, capsys, argv)

    assert err.startswith(f"Config error: {path} {said}")
    assert err.endswith(" — nothing was removed")
    if state == "unreadable":
        # Not quarantined to .corrupt, as a run's read of it would.
        assert path.read_text(encoding="utf-8") == '{"test_0001": '


def test_a_reference_folder_of_old_names_is_refused_and_nothing_removed(
    tmp_path, capsys
):
    config, argv = _project(tmp_path)
    (config.reference_dir / "ref_0001.json").write_text("{}", encoding="utf-8")

    err = _refused(tmp_path, capsys, argv)

    assert err == (
        f"Config error: {config.reference_dir} holds baselines under the old "
        "numbered names (ref_0001.json) — convert them with "
        "scripts/convert_baseline_names.py, see CHANGELOG"
    )
