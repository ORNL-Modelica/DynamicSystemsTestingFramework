"""``dstf.pipeline.run_pipeline`` — the run as a library callable.

What ``cmd_run`` does between "these are the tests" and "here is the report",
with no CLI in it: no printing, no ``sys.exit``, no browser, no prompt. The
tool runs the same callable on a worker thread, so what it leaves on disk (the
batch manifest, ``status.json``, the dashboard, the per-test sim dirs) is the
contract, not only what it returns.

Most tests drive the real Python backend over a temporary copy of
``examples/python/PythonTestingLib``; ``SimpleRamp`` (scipy) runs exactly once
in this module, everything else uses ``ConstantCsv`` (~0.25 s).
"""

from __future__ import annotations

import pytest

from conftest import python_example_copy
from dstf.backends import get_backend
from dstf.backends.progress import read_status
from dstf.pipeline import run_pipeline
from dstf.storage.reference_store import ReferenceStore

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
SIMPLE_RAMP = "PythonTestingLib.Examples.SimpleRamp"


@pytest.fixture
def example(tmp_path):
    """``(config, all_tests, backend, store)`` over a runnable library copy."""
    config, tests = python_example_copy(tmp_path)
    return config, tests, get_backend(config), ReferenceStore(config)


def _pick(tests, model_id):
    match = [t for t in tests if t.model_id == model_id]
    assert match, f"{model_id} not discovered in the example library"
    return match


def test_run_pipeline_runs_a_test_and_leaves_the_run_on_disk(example):
    """One test in, one result out — and the work dir a UI (or a later
    ``compare``) can read the run back from."""
    config, all_tests, backend, store = example

    outcome = run_pipeline(
        config, _pick(all_tests, CONSTANT_CSV), backend=backend, store=store
    )

    assert list(outcome.results) == [CONSTANT_CSV]
    assert outcome.results[CONSTANT_CSV].success
    assert [t.model_id for t in outcome.scope_tests] == [CONSTANT_CSV]
    assert outcome.notices == []

    work = config.work_dir
    for name in ("batch_manifest.json", "status.json", "dashboard.html"):
        assert (work / name).exists(), name

    import json

    mapping = json.loads((work / "reference_manifest.json").read_text())
    assert sorted(mapping) == ["ref_0001", "ref_0002", "ref_0003"]
    assert CONSTANT_CSV in mapping.values()

    sim_dirs = [d for d in work.iterdir() if d.is_dir() and d.name.startswith("test_")]
    assert [d.name for d in sim_dirs] == ["test_0001"]
    assert (sim_dirs[0] / "result.json").exists()

    assert read_status(work)["counts"]["passed"] == 1


def test_merge_widens_the_read_scope_over_candidates(example):
    """``merge`` reads back every model the batch manifest knows, narrowed to
    the candidate list the caller passes — and re-runs none of them."""
    config, all_tests, backend, store = example
    constant = _pick(all_tests, CONSTANT_CSV)
    ramp = _pick(all_tests, SIMPLE_RAMP)

    run_pipeline(config, constant, backend=backend, store=store)

    # Same backend, same store — a second run on the live instance.
    second = run_pipeline(config, ramp, backend=backend, store=store, merge=True)
    # Without `candidates` the widening has only `tests` to widen over, so the
    # scope is the run even though the manifest now holds both models.
    assert [t.model_id for t in second.scope_tests] == [SIMPLE_RAMP]
    # And the results are the scope, not every model the work dir's manifest
    # has accumulated: ConstantCsv ran a moment ago and is not in this outcome.
    assert set(second.results) == {SIMPLE_RAMP}

    ramp_dir = next(
        d
        for d in config.work_dir.iterdir()
        if d.is_dir() and (d / "result.json").exists() and d.name != "test_0001"
    )
    ramp_mtime = (ramp_dir / "result.json").stat().st_mtime

    third = run_pipeline(
        config, constant, backend=backend, store=store, merge=True, candidates=all_tests
    )

    # Candidate order, not run order (which was SimpleRamp then ConstantCsv),
    # and both models read back.
    ran = {SIMPLE_RAMP, CONSTANT_CSV}
    in_candidate_order = [t.model_id for t in all_tests if t.model_id in ran]
    assert in_candidate_order != [SIMPLE_RAMP, CONSTANT_CSV]
    assert [t.model_id for t in third.scope_tests] == in_candidate_order
    assert sorted(third.results) == sorted([SIMPLE_RAMP, CONSTANT_CSV])
    assert third.results[SIMPLE_RAMP].success
    assert third.results[CONSTANT_CSV].success
    # The out-of-scope test was read, not re-run.
    assert (ramp_dir / "result.json").stat().st_mtime == ramp_mtime


def test_run_pipeline_prints_nothing_with_a_quiet_backend(example, capsys):
    """The callable itself narrates nothing; with a quiet backend the whole
    run is silent, which is what makes it usable inside a UI process."""
    config, all_tests, backend, store = example
    backend.quiet = True

    run_pipeline(config, _pick(all_tests, CONSTANT_CSV), backend=backend, store=store)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


class _RaisingBackend:
    """A backend whose run dies the way a dead worker pool does."""

    def __init__(self):
        self.ref_id_map: dict = {}
        self.boom = RuntimeError("all workers dead")

    def run_tests(self, tests):
        raise self.boom


def test_a_failing_run_propagates_unchanged(example):
    """Exceptions are the callable's error channel — the CLI's "Run aborted"
    exit stays in the CLI."""
    config, all_tests, _backend, store = example
    backend = _RaisingBackend()

    with pytest.raises(RuntimeError) as exc_info:
        run_pipeline(
            config,
            _pick(all_tests, CONSTANT_CSV),
            backend=backend,
            store=store,
        )

    # The instance, not a lookalike: a re-raise as a fresh RuntimeError fails.
    assert exc_info.value is backend.boom


def test_no_tests_is_an_empty_outcome_that_touches_nothing(example):
    """Whether "no tests matched" is a failure is a CLI decision; the callable
    just does nothing."""
    config, _all_tests, backend, store = example
    config.work_dir.mkdir(parents=True, exist_ok=True)
    before = sorted(config.work_dir.rglob("*"))

    outcome = run_pipeline(config, [], backend=backend, store=store)

    assert outcome.manifests == []
    assert outcome.results == {}
    assert outcome.scope_tests == []
    assert outcome.notices == []
    assert sorted(config.work_dir.rglob("*")) == before
