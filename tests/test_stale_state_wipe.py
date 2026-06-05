"""Tests for `cli._wipe_stale_state_for_scope` — the per-test cleanup
that prevents prior runs' artifacts from bleeding into a fresh run.

D91 — root cause was that `cmd_run` never wiped a test's prior
`reports/<report_dir>/comparison_data.json` sidecar, so
`_enrich_row_from_comparison` would override a fresh sim's PASS verdict
with the stale FAIL from the prior run. This module locks down the wipe
contract: only in-scope tests are touched; out-of-scope dirs are
preserved (so `--merge` and `--rerun` keep working); both layers (sim
work dir + report dir) get cleared by `cmd_run`; only the report dir
gets cleared by `cmd_compare` (compare reads sim outputs to
re-evaluate).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dstf.cli import _wipe_stale_state_for_scope


@dataclass
class _FakeTest:
    model_id: str


@dataclass
class _FakeConfig:
    work_dir: object


def _seed_batch_manifest(work_dir, mapping: dict[str, str]) -> None:
    """Write a batch_manifest.json mapping test_key -> {model_id, ref_id}."""
    manifest = {tk: {"model_id": mid, "ref_id": None} for tk, mid in mapping.items()}
    (work_dir / "batch_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _seed_dir(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "marker.txt").write_text("seed", encoding="utf-8")


def test_wipe_in_scope_test_clears_both_dirs(tmp_path):
    """A test in scope gets BOTH its sim dir AND report dir wiped."""
    _seed_batch_manifest(tmp_path, {"test_0001": "Lib.A"})
    _seed_dir(tmp_path / "test_0001")
    _seed_dir(tmp_path / "reports" / "ref_0042")

    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.A")],
        ref_id_map={"Lib.A": "ref_0042"},
        wipe_sim_dirs=True,
    )

    assert not (tmp_path / "test_0001").exists()
    assert not (tmp_path / "reports" / "ref_0042").exists()


def test_wipe_compare_mode_preserves_sim_dir(tmp_path):
    """cmd_compare reads sim outputs to re-evaluate them; the sim work
    dir must NOT be wiped. Only the report dir (with its stale sidecar)
    gets cleared so the next `compare --report` writes fresh sidecars.
    """
    _seed_batch_manifest(tmp_path, {"test_0001": "Lib.A"})
    _seed_dir(tmp_path / "test_0001")
    _seed_dir(tmp_path / "reports" / "ref_0042")

    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.A")],
        ref_id_map={"Lib.A": "ref_0042"},
        wipe_sim_dirs=False,
    )

    # Sim dir kept (compare needs to read it)
    assert (tmp_path / "test_0001").exists()
    assert (tmp_path / "test_0001" / "marker.txt").exists()
    # Report dir cleared
    assert not (tmp_path / "reports" / "ref_0042").exists()


def test_wipe_preserves_out_of_scope_dirs(tmp_path):
    """A test NOT in scope (e.g. when running with --filter X.*)
    keeps both its sim and report dirs. Out-of-scope preservation is
    what makes --merge work — partial reruns still produce a
    full-suite report covering the un-rerun tests.
    """
    _seed_batch_manifest(
        tmp_path,
        {
            "test_0001": "Lib.A",
            "test_0002": "Lib.B",
        },
    )
    _seed_dir(tmp_path / "test_0001")
    _seed_dir(tmp_path / "test_0002")
    _seed_dir(tmp_path / "reports" / "ref_0001")
    _seed_dir(tmp_path / "reports" / "ref_0002")

    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.A")],  # Only A in scope
        ref_id_map={"Lib.A": "ref_0001", "Lib.B": "ref_0002"},
        wipe_sim_dirs=True,
    )

    # Lib.A wiped
    assert not (tmp_path / "test_0001").exists()
    assert not (tmp_path / "reports" / "ref_0001").exists()
    # Lib.B preserved
    assert (tmp_path / "test_0002" / "marker.txt").exists()
    assert (tmp_path / "reports" / "ref_0002" / "marker.txt").exists()


def test_wipe_first_time_test_no_op(tmp_path):
    """A test running for the first time (no batch_manifest entry,
    no prior dirs) is a no-op — the wipe must not crash, and the
    runner will create the dir from scratch.
    """
    # No batch_manifest.json, no dirs at all
    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.New")],
        ref_id_map={},
        wipe_sim_dirs=True,
    )
    # Still nothing — wipe didn't create or crash
    assert not (tmp_path / "reports").exists()


def test_wipe_falls_back_to_test_key_for_no_baseline_tests(tmp_path):
    """Tests without a stored baseline have report_dir = test_key (not
    ref_NNNN). The wipe must follow that fallback so the comparison
    sidecar at <work_dir>/reports/test_NNNN/ is also cleared.
    """
    _seed_batch_manifest(tmp_path, {"test_0007": "Lib.NewTest"})
    _seed_dir(tmp_path / "test_0007")
    _seed_dir(tmp_path / "reports" / "test_0007")  # No baseline yet

    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.NewTest")],
        ref_id_map={},  # No baseline -> empty map -> fallback to test_key
        wipe_sim_dirs=True,
    )

    assert not (tmp_path / "test_0007").exists()
    assert not (tmp_path / "reports" / "test_0007").exists()


def test_wipe_only_sim_dir_when_only_sim_dir_exists(tmp_path):
    """If reports/ doesn't exist yet but the sim dir does, the wipe
    just clears the sim dir without crashing on the missing reports/.
    """
    _seed_batch_manifest(tmp_path, {"test_0001": "Lib.A"})
    _seed_dir(tmp_path / "test_0001")
    # reports/ doesn't exist

    _wipe_stale_state_for_scope(
        _FakeConfig(tmp_path),
        [_FakeTest("Lib.A")],
        ref_id_map={"Lib.A": "ref_0042"},
        wipe_sim_dirs=True,
    )

    assert not (tmp_path / "test_0001").exists()
