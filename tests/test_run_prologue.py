"""The shared ``run_tests`` prologue (``Backend._prepare_run``).

The prologue used to be copy-pasted into three ``run_tests`` bodies (default
batch loop, persistent template, batch Dymola) and drifted: C-9/D96 found the
batch Dymola copy had lost the run-provenance stamp. These tests pin the
single implementation and the parity of what the three call sites write.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_test_model
from dstf.backends.base import Backend
from dstf.backends.persistent import PersistentBackendBase, Worker
from dstf.backends.types import TestRunResult
from dstf.config import Config
from dstf.discovery.test_registry import TestModel


def _tm(model_id: str, field_sources: dict | None = None) -> TestModel:
    """conftest's builder plus the per-field provenance these tests assert on."""
    return make_test_model(
        model_id,
        variable_patterns=["x"],
        method="dassl",
        field_sources=field_sources or {},
    )


def _config(tmp_path: Path, **kwargs) -> Config:
    lib = tmp_path / "Lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "package.mo").write_text("package Lib end Lib;")
    return Config(
        source_path=lib,
        reference_root=tmp_path / "refs",
        backend_name="Dymola",
        work_dir=tmp_path / "work",
        **kwargs,
    )


class _FakeBatchBackend(Backend):
    """Site A: the default batch loop, with the simulation stubbed out."""

    def describe_tool_version(self) -> str | None:
        return "FakeTool 1.2.3"

    def run_single_test(self, test, test_key, index, total) -> TestRunResult:
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=0.5,
        )

    def read_result(self, test, test_key, run_result):  # pragma: no cover — unused
        raise AssertionError


class _FakeWorker(Worker):
    def start(self) -> None:
        self._up = True

    def is_alive(self) -> bool:
        return getattr(self, "_up", False)

    def run_test(self, test, test_key, progress=None) -> TestRunResult:
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=0.5,
        )

    def close(self, grace: float = 5.0) -> None:
        self._up = False


class _FakePersistentBackend(PersistentBackendBase):
    """Site B: the persistent template driving workers that never spawn."""

    worker_cls = _FakeWorker
    backend_label = "Fake"

    def describe_tool_version(self) -> str | None:
        return "FakeTool 1.2.3"

    def read_result(self, test, test_key, run_result):  # pragma: no cover — unused
        raise AssertionError


def _dymola_backend(config: Config, monkeypatch):
    """Site C: batch Dymola with the ``.mos`` execution stubbed out."""
    from dstf.backends.dymola.backend import DymolaBackend

    monkeypatch.setattr(
        DymolaBackend,
        "describe_tool_version",
        lambda self: "FakeTool 1.2.3",
    )
    monkeypatch.setattr(
        DymolaBackend,
        "_run_batch",
        lambda self, batch, *a, **k: [
            TestRunResult(
                model_id=test.model_id, test_key=test_key, success=True, elapsed=0.5
            )
            for test, test_key in batch
        ],
    )
    return DymolaBackend(config)


class TestPrepareRun:
    def test_manifest_saved_with_a_key_per_test(self, tmp_path):
        cfg = _config(tmp_path)
        backend = _FakeBatchBackend(cfg)

        setup = backend._prepare_run([_tm("Lib.A"), _tm("Lib.B")])

        saved = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        assert {k: v["model_id"] for k, v in saved.items()} == {
            "test_0001": "Lib.A",
            "test_0002": "Lib.B",
        }
        assert setup.manifest.manifest == saved
        assert setup.total == 2

    def test_keyed_tests_follow_the_caller_order(self, tmp_path):
        cfg = _config(tmp_path)
        backend = _FakeBatchBackend(cfg)
        tests = [_tm("Lib.C"), _tm("Lib.A"), _tm("Lib.B")]

        setup = backend._prepare_run(tests)

        assert [t for t, _ in setup.keyed_tests] == tests
        assert [k for _, k in setup.keyed_tests] == [
            "test_0001",
            "test_0002",
            "test_0003",
        ]

    def test_progress_registers_every_test_with_ref_id_report_dir(self, tmp_path):
        cfg = _config(tmp_path)
        backend = _FakeBatchBackend(cfg)
        backend.ref_id_map = {"Lib.A": "ref_0042"}  # Lib.B has no baseline yet

        setup = backend._prepare_run(
            [_tm("Lib.A", {"stop_time": "spec"}), _tm("Lib.B")]
        )

        status = json.loads((cfg.work_dir / "status.json").read_text())
        entries = {e["model_id"]: e for e in status["tests"]}
        assert set(entries) == {"Lib.A", "Lib.B"}
        assert entries["Lib.A"]["report_dir"] == "ref_0042"
        assert entries["Lib.B"]["report_dir"] == "test_0002"  # falls back to test_key
        assert entries["Lib.A"]["field_sources"] == {"stop_time": "spec"}
        assert setup.progress is backend.progress

    def test_run_metadata_carries_the_tool_version(self, tmp_path):
        cfg = _config(tmp_path)
        setup = _FakeBatchBackend(cfg)._prepare_run([_tm("Lib.A")])

        assert setup.run_metadata["tool_version"] == "FakeTool 1.2.3"
        assert setup.run_metadata["backend"] == cfg.backend
        assert setup.run_metadata["os"] == cfg.os_name
        assert setup.run_metadata["dstf_version"]

        status = json.loads((cfg.work_dir / "status.json").read_text())
        assert status["metadata"] == setup.run_metadata


class TestAllThreeSitesAgree:
    """Batch loop, persistent template and batch Dymola write the same
    manifest and the same run provenance for the same test list."""

    def _run(self, backend, tests) -> tuple[dict, dict]:
        backend.run_tests(tests)
        work_dir = backend.config.work_dir
        manifest = json.loads((work_dir / "batch_manifest.json").read_text())
        metadata = json.loads((work_dir / "status.json").read_text())["metadata"]
        return manifest, metadata

    def test_manifest_and_metadata_are_identical(self, tmp_path, monkeypatch):
        tests = [_tm("Lib.A"), _tm("Lib.B")]
        sites = {
            "batch": _FakeBatchBackend(_config(tmp_path / "a")),
            "persistent": _FakePersistentBackend(_config(tmp_path / "b")),
            "dymola-batch": _dymola_backend(_config(tmp_path / "c"), monkeypatch),
        }

        outputs = {}
        for name, backend in sites.items():
            manifest, metadata = self._run(backend, tests)
            # Drop what is legitimately per-run: the manifest's run timestamps
            # and the provenance generation time.
            outputs[name] = (
                {k: v["model_id"] for k, v in manifest.items()},
                {k: v for k, v in metadata.items() if k != "generated_at"},
            )

        assert len(set(map(repr, outputs.values()))) == 1, outputs
        manifest_ids, metadata = outputs["batch"]
        assert manifest_ids == {"test_0001": "Lib.A", "test_0002": "Lib.B"}
        assert metadata["tool_version"] == "FakeTool 1.2.3"
        assert metadata["backend"] == "Dymola"


class TestRunSummary:
    def test_batch_dymola_summary_reports_wall_clock(
        self, tmp_path, monkeypatch, capsys
    ):
        """The batch Dymola summary used to print total work only; it now goes
        through the shared ``_print_run_summary`` and reports wall clock too."""
        backend = _dymola_backend(_config(tmp_path), monkeypatch)

        backend.run_tests([_tm("Lib.A"), _tm("Lib.B")])

        err = capsys.readouterr().err
        assert "Simulations complete: 2 ok, 0 failed, 0 timed out" in err
        assert "s wall" in err
        assert "total work" in err
        assert "parallel speedup" in err

    def test_persistent_summary_keeps_its_backend_phrase(self, tmp_path, capsys):
        backend = _FakePersistentBackend(_config(tmp_path))

        backend.run_tests([_tm("Lib.A")])

        assert "Persistent Fake run complete: 1 ok, 0 failed, 0 timed out" in (
            capsys.readouterr().err
        )


# =========================================================================
# Review K1 — the prologue stamps this run's scope pending.
#
# _prepare_run saves the manifest before a single test starts, and
# assign_test_keys has just carried every prior entry forward. Without this
# stamp, an aborted run leaves the PREVIOUS run's verdict in place and a
# later `compare` trusts it (see tests/test_compare_accept.py::TestAbortWindow).
# =========================================================================


class TestPrepareRunMarksScopePending:
    def test_in_scope_entries_are_pending_before_any_test_runs(self, tmp_path):
        cfg = _config(tmp_path)
        backend = _FakeBatchBackend(cfg)

        setup = backend._prepare_run([_tm("Lib.A"), _tm("Lib.B")])

        saved = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        assert {e["last_run_status"] for e in saved.values()} == {"pending"}
        assert setup.manifest.manifest == saved

    def test_a_prior_verdict_does_not_survive_into_the_next_run(self, tmp_path):
        """The abort window, at the prologue: run 1 passes, run 2 re-runs the
        same test — the manifest it opens must not still say 'passed'."""
        cfg = _config(tmp_path)
        backend = _FakeBatchBackend(cfg)
        backend.run_tests([_tm("Lib.A")])
        after_run_1 = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        assert after_run_1["test_0001"]["last_run_status"] == "passed"

        _FakeBatchBackend(cfg)._prepare_run([_tm("Lib.A")])

        after_prologue = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        assert after_prologue["test_0001"]["last_run_status"] == "pending"

    def test_out_of_scope_entries_keep_their_verdict(self, tmp_path):
        """--filter / --rerun: tests this run isn't touching keep the record
        their own last run left, which is what --merge reports over."""
        cfg = _config(tmp_path)
        _FakeBatchBackend(cfg).run_tests([_tm("Lib.A"), _tm("Lib.B")])

        _FakeBatchBackend(cfg)._prepare_run([_tm("Lib.A")])  # filtered rerun

        saved = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        assert saved["test_0001"]["last_run_status"] == "pending"  # in scope
        assert saved["test_0002"]["last_run_status"] == "passed"  # untouched

    def test_the_batch_loop_records_each_verdict_as_it_lands(self, tmp_path):
        """Half (b): the record must be on disk per result, not at the end —
        an abort skips the end."""
        cfg = _config(tmp_path)
        seen: list[dict] = []

        class _ObservingBackend(_FakeBatchBackend):
            def run_single_test(self, test, test_key, index, total):
                # Snapshot what is ALREADY persisted when this test starts.
                seen.append(
                    json.loads((cfg.work_dir / "batch_manifest.json").read_text())
                )
                return super().run_single_test(test, test_key, index, total)

        _ObservingBackend(cfg).run_tests([_tm("Lib.A"), _tm("Lib.B")])

        # Before test 2 ran, test 1's verdict was already durable on disk.
        assert seen[1]["test_0001"]["last_run_status"] == "passed"
        assert seen[1]["test_0002"]["last_run_status"] == "pending"


# =========================================================================
# Review L1 — the batch-Dymola lane records verdicts too.
#
# _prepare_run stamps every in-scope entry "pending"; only record_result
# clears it. This lane owns its own run_tests and extended `all_results`
# without recording, so a COMPLETED `run --batch` left the whole scope
# pending and the manifest-wins protection never materialized for it.
# =========================================================================


class TestBatchDymolaRecordsVerdicts:
    def _statuses(self, cfg):
        saved = json.loads((cfg.work_dir / "batch_manifest.json").read_text())
        return {k: v.get("last_run_status") for k, v in saved.items()}

    def test_sequential_batches_record_every_verdict(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path, parallel=1)
        backend = _dymola_backend(cfg, monkeypatch)

        backend.run_tests([_tm("Lib.A"), _tm("Lib.B"), _tm("Lib.C")])

        statuses = self._statuses(cfg)
        assert set(statuses.values()) == {"passed"}
        assert "pending" not in statuses.values()

    def test_pooled_batches_record_every_verdict(self, tmp_path, monkeypatch):
        """The parallel path lands results through a different call site."""
        cfg = _config(tmp_path, parallel=3, batch_size=1)
        backend = _dymola_backend(cfg, monkeypatch)

        backend.run_tests([_tm("Lib.A"), _tm("Lib.B"), _tm("Lib.C")])

        statuses = self._statuses(cfg)
        assert set(statuses.values()) == {"passed"}

    def test_failures_and_timeouts_are_recorded_not_left_pending(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.dymola.backend import DymolaBackend

        cfg = _config(tmp_path, parallel=1)
        backend = _dymola_backend(cfg, monkeypatch)

        def _mixed(self, batch, *a, **k):
            out = []
            for i, (test, test_key) in enumerate(batch):
                out.append(
                    TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=i == 0,
                        timed_out=i == 2,
                        elapsed=0.5,
                    )
                )
            return out

        monkeypatch.setattr(DymolaBackend, "_run_batch", _mixed)

        backend.run_tests([_tm("Lib.A"), _tm("Lib.B"), _tm("Lib.C")])

        assert self._statuses(cfg) == {
            "test_0001": "passed",
            "test_0002": "failed",
            "test_0003": "timed_out",
        }

    def test_a_verdict_is_durable_before_the_next_batch_runs(
        self, tmp_path, monkeypatch
    ):
        """Recording is per result as each batch lands, not in an epilogue an
        abort would skip."""
        from dstf.backends.dymola.backend import DymolaBackend

        cfg = _config(tmp_path, parallel=1, batch_size=1)
        seen: list[dict] = []

        def _observing(self, batch, *a, **k):
            seen.append(json.loads((cfg.work_dir / "batch_manifest.json").read_text()))
            return [
                TestRunResult(
                    model_id=t.model_id, test_key=tk, success=True, elapsed=0.1
                )
                for t, tk in batch
            ]

        backend = _dymola_backend(cfg, monkeypatch)
        monkeypatch.setattr(DymolaBackend, "_run_batch", _observing)

        backend.run_tests([_tm("Lib.A"), _tm("Lib.B")])

        assert seen[1]["test_0001"]["last_run_status"] == "passed"
        assert seen[1]["test_0002"]["last_run_status"] == "pending"
