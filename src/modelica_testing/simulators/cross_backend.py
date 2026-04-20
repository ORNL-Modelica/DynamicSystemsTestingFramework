"""Cross-backend orchestration (4.B.3). **EXPERIMENTAL** (D65).

Helpers that chain backends to produce a **soft_check** baseline (D66).
Today the only chain is **dymola-via-fmpy**: Dymola exports the model as
an FMU, FMPy simulates the FMU, and the FMPy result is stored as a
soft_check (`"dymola-via-fmpy"` by convention) that MetricTree leaves
can score against via ``"against": "dymola-via-fmpy"`` — always inside
a ``warn`` combinator, enforced by the validator.

EXPERIMENTAL — scope limits (D65):
  - **Semantics**: the chain is only meaningful for *autonomous* tests —
    models with no external inputs, no python driver stepping the FMU, no
    need to choose between co-simulation and model-exchange. For a test
    that is fundamentally "a python script driving an FMU with a scheduled
    input sequence", this chain will produce a baseline whose values do
    NOT reflect what the test actually does — the comparison against the
    primary run becomes meaningless. Opt-in via ``requested_baselines``
    should be reserved for autonomous tests until generalization lands.
  - **Validation**: the Dymola export step requires Windows + Dymola + the
    FMI export license option. Cannot be exercised in CI on Linux WSL —
    tests mock the `export_fmu` step. End-to-end validation on real Dymola
    is deferred to a dedicated phase.

Generalization (input schedules, CS/ME choice, start-value overrides,
python-driver tests) is deferred to a future "FMU-path semantic gap
closure" phase.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Optional

from ..config import Config
from ..discovery.test_registry import TestModel
from ..storage.reference_store import ReferenceStore
from .base import SimulatorRunner, TestResult

logger = logging.getLogger(__name__)


# Convention name for the cross-backend baseline. Users reference this via
# ``{"metric": ..., "against": "dymola-via-fmpy"}`` in a MetricTree leaf.
CROSS_BACKEND_BASELINE_NAME = "dymola-via-fmpy"


def produce_dymola_via_fmpy_baseline(
    test: TestModel,
    primary_runner: SimulatorRunner,
    config: Config,
    store: ReferenceStore,
    *,
    fmu_dir: Optional[Path] = None,
) -> bool:
    """Run the Dymola → FMPy chain for one test and store the result.

    Steps:
      1. ``primary_runner.export_fmu(test, fmu_dir)`` produces a .fmu.
      2. Build a clone of the test pointing at the FMU.
      3. Construct an :class:`FmpyRunner` (config tweaked to source_type="fmu").
      4. Run FMPy on the FMU + read back the result.
      5. Persist the FMPy result as a soft_check via
         :meth:`ReferenceStore.add_soft_check` (D66).

    Returns True on success, False if any step fails (logs the reason).
    Requires the model to already have a primary baseline on disk —
    soft_checks augment primary, they do not replace it.
    """
    work = fmu_dir or (config.work_dir / f"chain_{test.model_id.replace('.', '_')}")
    work.mkdir(parents=True, exist_ok=True)

    # Step 1 — export the FMU via the primary backend.
    try:
        fmu_path = primary_runner.export_fmu(test, work)
    except (NotImplementedError, RuntimeError) as exc:
        logger.warning(
            "cross-backend chain: export_fmu failed for %s: %s",
            test.model_id, exc,
        )
        return False

    # Step 2 — clone the test with source_file pointing at the new FMU.
    chain_test = replace(test, source_file=fmu_path)

    # Step 3 — construct an FmpyRunner. We don't go through get_runner()
    # because that uses config.simulator_backend; we just want a temporary
    # FMPy runner regardless of what the primary is.
    from .fmpy.runner import FmpyRunner
    fmpy_config = replace(
        config,
        simulator="FMPy",
        source_type="fmu",
        # Reuse work_dir; FmpyRunner persists result.npz under <work_dir>/<test_key>.
    )
    try:
        fmpy_runner = FmpyRunner(fmpy_config)
    except ImportError as exc:
        logger.warning(
            "cross-backend chain: FMPy not installed (%s); skipping %s",
            exc, test.model_id,
        )
        return False

    # Step 4 — run FMPy on the exported FMU. Use a deterministic test_key
    # that doesn't collide with the primary run's keys.
    test_key = f"chain_{test.model_id.replace('.', '_')}"
    run_result = fmpy_runner.run_single_test(
        chain_test, test_key, index=1, total=1,
    )
    if not run_result.success:
        logger.warning(
            "cross-backend chain: FMPy sim failed for %s: %s",
            test.model_id, run_result.error_message,
        )
        return False

    fmpy_result: TestResult = fmpy_runner.read_result(chain_test, test_key, run_result)
    if not fmpy_result.success:
        logger.warning(
            "cross-backend chain: FMPy read_result failed for %s: %s",
            test.model_id, fmpy_result.error_message,
        )
        return False

    # Step 5 — store FMPy result as a named baseline.
    if not fmpy_result.variables:
        logger.warning(
            "cross-backend chain: FMPy produced no variables for %s; "
            "skipping baseline write", test.model_id,
        )
        return False

    # All FMPy variables share the same time vector; pull from the first.
    time_vec = list(fmpy_result.variables[0].time)
    variables_payload = [
        {
            "index": v.index,
            "name": v.name or "",
            "values": list(v.values),
        }
        for v in fmpy_result.variables
    ]
    try:
        store.add_soft_check(
            test.model_id,
            CROSS_BACKEND_BASELINE_NAME,
            time=time_vec,
            variables=variables_payload,
            provenance={
                "source": "cross-backend chain",
                "primary_backend": type(primary_runner).__name__,
                "secondary_backend": "FMPy",
                "fmu_path": str(fmu_path),
            },
        )
    except FileNotFoundError as exc:
        logger.warning(
            "cross-backend chain: cannot store baseline for %s: %s",
            test.model_id, exc,
        )
        return False
    return True
