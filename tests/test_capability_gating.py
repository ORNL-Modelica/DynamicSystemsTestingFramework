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


class TestDatasetKindThreading:
    def test_single_declared_kind_is_used(self):
        class _Spectral(_ProbeBackend):
            produced_datasets = frozenset({DatasetType.SPECTRUM})

        assert cli_mod._dataset_kind_for(_Spectral(None)) is DatasetType.SPECTRUM

    def test_multi_kind_backend_falls_back_to_time_series(self):
        class _Both(_ProbeBackend):
            produced_datasets = frozenset({DatasetType.TIME_SERIES, DatasetType.EVENTS})

        assert cli_mod._dataset_kind_for(_Both(None)) is DatasetType.TIME_SERIES

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
        from pathlib import Path
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
            source_file=Path(""),
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
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
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
