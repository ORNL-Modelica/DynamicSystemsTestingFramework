"""`Capability` flags and `produced_datasets` as runtime gates, not decoration.

Before these, the declarations were validated at registration and then
consulted nowhere: `_get_backend` branched on whether `persistent_backend_cls()`
happened to return something, and the cross-backend chain branched on whether
`export_fmu` happened to raise. A backend could therefore be driven down a
path it had explicitly not declared. These tests pin the declaration as the
thing that decides.

The chain's own end-to-end tests (which need fmpy + the Reference-FMU
binaries) stay in ``test_cross_backend.py``; only the gate lives here, so it
runs everywhere.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dstf.backends import BackendUnavailableError, dataset_kind_for, select_backend
from dstf.backends.base import Backend
from dstf.backends.types import Capability, DatasetType
from dstf.cli import run_cmds as cli_mod


class _ProbeBackend(Backend):
    """A backend that ships a persistent variant but does NOT declare it.

    Mirrors a real situation: an experimental persistent path that exists in
    the tree but isn't ready to be the default. The flag is how the backend
    says so.
    """

    capabilities = frozenset({Capability.BATCH_FALLBACK})
    persistent_cls_asked = False

    def __init__(self, config):  # no super(): Config here is a stand-in
        self.config = config

    @classmethod
    def persistent_backend_cls(cls):
        cls.persistent_cls_asked = True
        return _ProbePersistentBackend

    def read_result(self, test, test_key, run_result):
        raise NotImplementedError


class _ProbePersistentBackend(_ProbeBackend):
    capabilities = frozenset({Capability.PERSISTENT_WORKERS})

    preflighted = False

    @classmethod
    def preflight(cls, config) -> None:
        cls.preflighted = True


def _use_probe(monkeypatch, cls=_ProbeBackend):
    monkeypatch.setattr("dstf.backends.get_backend_class", lambda config: cls)


def _all_registered_backends():
    """Every backend in the registry, builtins force-imported first.

    Iterating the registry rather than a hardcoded five-name list means a
    sixth registered backend is covered automatically (B2 review finding 6).
    """
    from types import SimpleNamespace

    from dstf.backends import _REGISTRY, get_backend_class

    for name in ("Dymola", "FMPy", "OpenModelica", "Julia", "Python"):
        get_backend_class(SimpleNamespace(backend=name))
    assert len(_REGISTRY) >= 5
    return dict(_REGISTRY)


class TestPersistentWorkersGate:
    def test_undeclared_backend_never_reaches_its_persistent_variant(self, monkeypatch):
        _ProbeBackend.persistent_cls_asked = False
        _ProbePersistentBackend.preflighted = False
        _use_probe(monkeypatch)

        backend = cli_mod._get_backend(SimpleNamespace(), persistent=True)

        assert type(backend) is _ProbeBackend
        assert _ProbeBackend.persistent_cls_asked is False
        assert _ProbePersistentBackend.preflighted is False

    def test_declared_backend_still_gets_the_persistent_variant(self, monkeypatch):
        class _Declared(_ProbeBackend):
            capabilities = frozenset(
                {Capability.BATCH_FALLBACK, Capability.PERSISTENT_WORKERS}
            )

        _ProbePersistentBackend.preflighted = False
        _use_probe(monkeypatch, _Declared)

        backend = cli_mod._get_backend(SimpleNamespace(), persistent=True)

        assert type(backend) is _ProbePersistentBackend
        assert _ProbePersistentBackend.preflighted is True

    def test_every_builtin_pairs_its_flag_with_a_real_hook(self):
        """The gate must not change behaviour for any shipped backend.

        For each builtin: declaring PERSISTENT_WORKERS <=> returning a
        persistent class. If this ever drifts, `--batch`-less runs silently
        change backend for whichever one drifted.
        """
        for name, cls in _all_registered_backends().items():
            declared = Capability.PERSISTENT_WORKERS in cls.capabilities
            assert declared is (cls.persistent_backend_cls() is not None), name


class TestSelectBackend:
    """The selection as a library call: it raises where the CLI exits.

    `_get_backend` is now the CLI's wrapper over `select_backend`; anything
    that is not a CLI (the authoring tool's rerun) needs the choice without
    a `sys.exit` in it.
    """

    def test_batch_request_returns_the_batch_backend(self, monkeypatch):
        _ProbeBackend.persistent_cls_asked = False
        _use_probe(monkeypatch)

        backend = select_backend(SimpleNamespace(), persistent=False)

        assert type(backend) is _ProbeBackend
        assert _ProbeBackend.persistent_cls_asked is False

    def test_undeclared_backend_stays_batch_when_persistent_asked(self, monkeypatch):
        _ProbeBackend.persistent_cls_asked = False
        _ProbePersistentBackend.preflighted = False
        _use_probe(monkeypatch)

        backend = select_backend(SimpleNamespace(), persistent=True)

        assert type(backend) is _ProbeBackend
        assert _ProbeBackend.persistent_cls_asked is False
        assert _ProbePersistentBackend.preflighted is False

    def test_python_backend_has_no_persistent_variant_to_select(self, tmp_path):
        """A real batch-only builtin: persistent=True is a no-op, not an error."""
        from dstf.backends.python.backend import PythonBackend
        from dstf.config import Config

        source = tmp_path / "lib"
        source.mkdir()
        config = Config(
            source_path=source,
            reference_root=tmp_path / "refs",
            source_type="python",
            backend_name="Python",
            work_dir=tmp_path / "work",
        )

        assert type(select_backend(config, persistent=True)) is PythonBackend

    def test_preflight_failure_raises_instead_of_exiting(self, monkeypatch):
        class _Declared(_ProbeBackend):
            capabilities = frozenset(
                {Capability.BATCH_FALLBACK, Capability.PERSISTENT_WORKERS}
            )

            @classmethod
            def persistent_backend_cls(cls):
                return _Unavailable

        class _Unavailable(_Declared):
            @classmethod
            def preflight(cls, config) -> None:
                raise RuntimeError("no omc")

        _use_probe(monkeypatch, _Declared)

        with pytest.raises(BackendUnavailableError) as excinfo:
            select_backend(SimpleNamespace(backend="Probe"), persistent=True)

        # The backends' sentence names nothing a caller may not have:
        # `dstf author`, whose Queue shows it, has no `--batch`.
        message = str(excinfo.value)
        assert "no omc" in message
        assert "--batch" not in message
        assert excinfo.value.batch_runs

    def test_the_cli_says_batch_where_its_script_mode_would_run(
        self, monkeypatch, capsys
    ):
        """`run` has `--batch`: its refusal of the workers names it, and only
        where the backend's script mode would run."""
        for batch_runs, said in ((True, True), (False, False)):

            def _raise(config, *, persistent, to_run, batch_runs=batch_runs):
                raise BackendUnavailableError(
                    "Persistent-worker Probe unavailable.", batch_runs=batch_runs
                )

            monkeypatch.setattr("dstf.backends.select_backend", _raise)
            with pytest.raises(SystemExit):
                cli_mod._get_backend(SimpleNamespace(), persistent=True)
            err = capsys.readouterr().err
            assert ("Or re-run with `--batch`" in err) is said, err

    def test_cli_wrapper_turns_the_error_into_exit_1(self, monkeypatch, capsys):
        """`_get_backend` is the only place the abort message is printed."""

        def _raise(config, *, persistent, to_run):
            raise BackendUnavailableError("Persistent-worker Probe unavailable.")

        monkeypatch.setattr("dstf.backends.select_backend", _raise)

        with pytest.raises(SystemExit) as excinfo:
            cli_mod._get_backend(SimpleNamespace(), persistent=True)

        assert excinfo.value.code == 1
        assert "Persistent-worker Probe unavailable." in capsys.readouterr().err

    def test_unregistered_backend_name_is_unavailable_not_a_value_error(self):
        with pytest.raises(BackendUnavailableError) as excinfo:
            select_backend(
                SimpleNamespace(backend="Nope", backend_name="Nope"), persistent=False
            )

        assert "Unsupported backend: Nope" in str(excinfo.value)

    def test_missing_executable_is_unavailable(self, monkeypatch):
        """A constructor's `RuntimeError` ("julia not found") is the same class
        of problem as a failed preflight: the backend cannot be built here."""

        class _NoBinary(_ProbeBackend):
            def __init__(self, config):
                raise RuntimeError("Julia binary not found.")

        _use_probe(monkeypatch, _NoBinary)

        with pytest.raises(BackendUnavailableError) as excinfo:
            select_backend(SimpleNamespace(), persistent=False)

        assert "Julia binary not found." in str(excinfo.value)

    def test_a_constructor_bug_still_tracebacks(self, monkeypatch):
        """The translation is narrow: only `RuntimeError` from the constructor
        means "unavailable". Anything else is a bug and must not be dressed up
        as a missing dependency."""

        class _Buggy(_ProbeBackend):
            def __init__(self, config):
                raise KeyError("work_dir")

        _use_probe(monkeypatch, _Buggy)

        with pytest.raises(KeyError):
            select_backend(SimpleNamespace(), persistent=False)

    def test_a_registry_bug_still_tracebacks(self):
        """The same fence on the other translation: only `get_backend_class`'s
        `ValueError` ("unsupported backend") means "unavailable". A config with
        no `backend` attribute is a caller bug — it must traceback, not be
        dressed up as a machine that can't run this backend. Widening that
        `except` to `Exception` would hide it."""
        with pytest.raises(AttributeError):
            select_backend(SimpleNamespace(), persistent=False)


class TestDatasetKindThreading:
    def test_single_declared_kind_is_used(self):
        class _Spectral(_ProbeBackend):
            produced_datasets = frozenset({DatasetType.SPECTRUM})

        assert dataset_kind_for(_Spectral(None)) is DatasetType.SPECTRUM

    def test_multi_kind_backend_falls_back_to_time_series(self):
        class _Both(_ProbeBackend):
            produced_datasets = frozenset({DatasetType.TIME_SERIES, DatasetType.EVENTS})

        assert dataset_kind_for(_Both(None)) is DatasetType.TIME_SERIES

    def test_every_builtin_declares_time_series_only(self):
        """All five declare {TIME_SERIES}, so threading is behaviour-neutral."""
        for name, cls in _all_registered_backends().items():
            assert cls.produced_datasets == frozenset({DatasetType.TIME_SERIES}), name


class TestFmuExportGate:
    def test_chain_skips_backend_without_fmu_export_capability(self, tmp_path, caplog):
        """The gate is the declared capability, checked BEFORE any work.

        Pre-fix the chain called ``export_fmu`` unconditionally and relied on
        catching ``NotImplementedError``. That made "this backend cannot
        export" — a fact knowable from the class — look like a runtime
        failure, and it silently no-op'd for batch-mode Dymola, which
        declared FMU_EXPORT it did not have (fixed alongside this).

        No fmpy needed: the gate returns before the FMPy half is reached.
        """
        import logging
        from unittest.mock import MagicMock

        from dstf.backends.cross_backend import produce_dymola_via_fmpy_baseline
        from dstf.config import Config
        from dstf.discovery.test_registry import TestModel
        from dstf.storage.reference_store import ReferenceStore

        source = tmp_path / "fmu_examples"
        source.mkdir()
        config = Config(
            source_path=source,
            reference_root=tmp_path / "refs",
            source_type="fmu",
            backend_name="FMPy",
            work_dir=tmp_path / "work",
        )
        test = TestModel(
            model_id="BouncingBall",
            source_file=None,
            source_package="",
            short_name="BouncingBall",
            n_vars=1,
            variable_patterns=["h"],
            stop_time=1.0,
            tolerance=1e-6,
            method="Dassl",
        )

        primary_backend = MagicMock()
        primary_backend.capabilities = frozenset()  # e.g. batch-mode Dymola

        with caplog.at_level(logging.WARNING):
            ok = produce_dymola_via_fmpy_baseline(
                test, primary_backend, config, ReferenceStore(config)
            )

        assert ok is False
        primary_backend.export_fmu.assert_not_called()
        assert "Capability.FMU_EXPORT" in caplog.text

    def test_batch_dymola_does_not_claim_fmu_export(self):
        """Honesty: only the class that implements export_fmu declares the flag.

        `_validate_capabilities` can't police this — it runs on
        `@register`-ed classes only, and PersistentDymolaBackend is reached via
        `DymolaBackend.persistent_backend_cls`, never the registry.
        """
        from dstf.backends.dymola.backend import DymolaBackend
        from dstf.backends.dymola.persistent_backend import PersistentDymolaBackend

        assert Capability.FMU_EXPORT not in DymolaBackend.capabilities
        assert DymolaBackend.export_fmu is Backend.export_fmu

        assert Capability.FMU_EXPORT in PersistentDymolaBackend.capabilities
        assert PersistentDymolaBackend.export_fmu is not Backend.export_fmu


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75).
#
# Finding 28 — the capability-honesty check must compare classmethod
# ``__func__``, otherwise a declared PERSISTENT_WORKERS backend with no
# persistent backend sails through registration.
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 28 — capability-honesty check must compare classmethod __func__
# ---------------------------------------------------------------------------


class TestCapabilityHonesty:
    def test_persistent_workers_without_override_trips(self):
        from dstf.backends import _REGISTRY, register
        from dstf.backends.base import Backend

        try:
            with pytest.raises(TypeError, match="PERSISTENT_WORKERS"):

                @register("BogusPersistentBackend")
                class Bogus(Backend):
                    capabilities = frozenset({Capability.PERSISTENT_WORKERS})

                    def read_result(self, test, test_key, run_result):
                        raise NotImplementedError

        finally:
            _REGISTRY.pop("BogusPersistentBackend", None)

    def test_persistent_workers_with_override_passes(self):
        from dstf.backends import _REGISTRY, register
        from dstf.backends.base import Backend

        try:

            @register("HonestPersistentBackend")
            class Honest(Backend):
                capabilities = frozenset({Capability.PERSISTENT_WORKERS})

                @classmethod
                def persistent_backend_cls(cls):
                    return cls

                def read_result(self, test, test_key, run_result):
                    raise NotImplementedError

            assert _REGISTRY["HonestPersistentBackend"] is Honest
        finally:
            _REGISTRY.pop("HonestPersistentBackend", None)

    def test_fmpy_backend_dropped_aspirational_persistent_flag(self):
        """FmpyBackend has no persistent backend; the flag was aspirational
        and now trips the (repaired) honesty check."""
        from dstf.backends.fmpy.backend import FmpyBackend

        assert Capability.PERSISTENT_WORKERS not in FmpyBackend.capabilities
        assert FmpyBackend.persistent_backend_cls() is None


# ---------------------------------------------------------------------------
# LIVE_RESULTS — the result file can be read while the run writes it
# ---------------------------------------------------------------------------


class TestLiveResultsGate:
    """Which backends a run in progress can be watched on, and where.

    `LIVE_RESULTS` and `live_result_path` are one promise: the flag says the
    result file grows during the run, the method says which file. The
    authoring tool gates its live view on the flag and builds a
    `LiveMatReader` for the path.
    """

    LIVE = {"Dymola": "dsres.mat", "OpenModelica": "result_res.mat"}

    def _classes(self):
        """Every registered backend and the persistent variant it selects —
        the tool holds an instance of either."""
        for name, cls in _all_registered_backends().items():
            yield name, cls
            persistent = cls.persistent_backend_cls()
            if persistent is not None:
                yield name, persistent

    def test_the_flag_and_the_path_go_together_on_every_builtin(self, tmp_path):
        seen = set()
        for name, cls in self._classes():
            path = cls.live_result_path(tmp_path)
            declared = Capability.LIVE_RESULTS in cls.capabilities
            assert declared is (path is not None), cls.__name__
            assert declared is (name in self.LIVE), cls.__name__
            if declared:
                assert path == tmp_path / self.LIVE[name], cls.__name__
                seen.add(cls.__name__)
        assert seen == {
            "DymolaBackend",
            "PersistentDymolaBackend",
            "OpenModelicaBackend",
            "PersistentOpenModelicaBackend",
        }

    def test_backends_that_write_their_result_once_do_not_declare_it(self, tmp_path):
        """Python and Julia return a result at the end; FMPy saves its
        ``.npz`` after the simulation. None has a file to follow."""
        undeclared = {
            cls.__name__
            for _, cls in self._classes()
            if Capability.LIVE_RESULTS not in cls.capabilities
        }
        assert undeclared == {
            "FmpyBackend",
            "JuliaBackend",
            "PersistentJuliaBackend",
            "PythonBackend",
        }

    def test_the_live_path_is_the_file_the_result_is_read_from(self, tmp_path):
        """The growing file and the finished result are one file: what the
        live reader follows is what `result_names` lists afterwards."""
        import numpy as np

        from dstf.backends.common.live_mat import LiveMatReader
        from mat4_files import write_result_mat

        for name, cls in self._classes():
            if name not in self.LIVE:
                continue
            test_dir = tmp_path / cls.__name__
            test_dir.mkdir()
            t = np.linspace(0.0, 1.0, 5)
            write_result_mat(
                cls.live_result_path(test_dir), time=t, trajectories={"x": 2 * t}
            )
            assert cls.result_names(test_dir) == ["x"], cls.__name__
            snap = LiveMatReader(cls.live_result_path(test_dir)).poll(["x"])
            assert snap.rows == 5 and len(snap.series["x"].time) == 5

    def test_declaring_it_without_a_path_trips_at_registration(self):
        from dstf.backends import _REGISTRY, register

        try:
            with pytest.raises(TypeError, match="LIVE_RESULTS"):

                @register("BogusLiveBackend")
                class Bogus(Backend):
                    capabilities = frozenset({Capability.LIVE_RESULTS})

                    def read_result(self, test, test_key, run_result):
                        raise NotImplementedError

        finally:
            _REGISTRY.pop("BogusLiveBackend", None)

    def test_declaring_it_with_a_path_registers(self):
        from dstf.backends import _REGISTRY, register

        try:

            @register("HonestLiveBackend")
            class Honest(Backend):
                capabilities = frozenset({Capability.LIVE_RESULTS})

                @classmethod
                def live_result_path(cls, test_dir):
                    return test_dir / "growing.mat"

                def read_result(self, test, test_key, run_result):
                    raise NotImplementedError

            assert _REGISTRY["HonestLiveBackend"] is Honest
        finally:
            _REGISTRY.pop("HonestLiveBackend", None)
