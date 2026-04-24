"""Stub — full implementation lands in Task 4."""
from __future__ import annotations

from .. import register
from ..base import Capability, DatasetType, SimulatorRunner


@register("Python")
class PythonRunner(SimulatorRunner):
    """Placeholder; run_single_test / read_result arrive in Task 4."""

    capabilities = frozenset({Capability.BATCH_FALLBACK})
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = ()

    def run_single_test(self, test, test_key, index, total):
        raise NotImplementedError("PythonRunner under construction — see Task 4")

    def read_result(self, test, test_key, run_result):
        raise NotImplementedError("PythonRunner under construction — see Task 4")
