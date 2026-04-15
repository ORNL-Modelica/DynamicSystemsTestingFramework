"""FMPy-backed simulator runner.

Phase 2.2 (current): skeleton only. Registers under ``"FMPy"``, declares
capabilities, surfaces a clear ``NotImplementedError`` if someone tries to
simulate. Phase 2.3 fills in ``run_single_test`` and ``read_result`` against
FMPy's Python API.
"""

from __future__ import annotations

import logging

from ...config import Config
from ...discovery.test_registry import TestModel
from .. import register
from ..base import (
    Capability,
    DatasetType,
    SimulatorRunner,
    TestResult,
    TestRunResult,
)

logger = logging.getLogger(__name__)


@register("FMPy")
class FmpyRunner(SimulatorRunner):
    """Runs FMU simulations using the FMPy Python library.

    Consumes prebuilt FMUs — does not compile from Modelica sources. For
    Modelica → FMU conversion, a future ``supports_fmu_export`` capability
    on a Modelica backend (Dymola / OMC) will chain into this runner for
    cross-backend verification.
    """

    capabilities = frozenset({
        Capability.PERSISTENT_WORKERS,  # FMPy instances are cheap to keep alive
        # Deliberately absent:
        #   BATCH_FALLBACK — FMPy *is* the Python path; no script fallback exists
        #   FMU_EXPORT — FMPy consumes FMUs, doesn't produce them
        #   EXPERIMENT_INGEST — not applicable
    })
    produced_datasets = frozenset({DatasetType.TIME_SERIES})

    def __init__(self, config: Config):
        super().__init__(config)
        # Defer the fmpy import so a bare import of this module doesn't error
        # when fmpy isn't installed — the runner only errors if actually used.
        try:
            import fmpy  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The FMPy backend requires the optional 'fmpy' extra: "
                "uv pip install -e \".[fmpy]\""
            ) from exc

    def run_single_test(
        self,
        test: TestModel,
        test_key: str,
        index: int,
        total: int,
    ) -> TestRunResult:
        raise NotImplementedError(
            "FmpyRunner.run_single_test is not implemented yet (Phase 2.3 scope)."
        )

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
    ) -> TestResult:
        raise NotImplementedError(
            "FmpyRunner.read_result is not implemented yet (Phase 2.3 scope)."
        )
