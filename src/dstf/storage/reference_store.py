"""JSON file-based storage for reference results with stable numeric test IDs.

Reference files (ref_NNNN.json) are the source of truth. Each file contains
model_id, test_id, status, date_added, and last_updated metadata. An in-memory
index is built by scanning ref files — no persistent manifest file needed
(:mod:`dstf.storage.ref_index`).

Every file is read and written by :mod:`dstf.storage.baseline_codec`; this
store hands out :class:`~dstf.storage.baseline.ReferenceFile`s. Additional
named baselines are separate files under ``soft_checks/ref_NNNN/<name>.json``
(D66); that CRUD lives in :mod:`dstf.storage.roles`.
"""

import csv
import io
import json
import logging
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..backends import TestResult
from ..config import Config
from ..discovery.test_registry import TestModel
from .baseline import (
    PRIMARY_BASELINE,
    Baseline,
    DiagnosticSummary,
    ReferenceFile,
    Sampling,
    StoredVariable,
)
from .baseline_codec import (
    DOUBLE,
    SINGLE,
    read_reference,
    reference_to_json,
    stored_numbers,
    write_reference,
)
from .ref_index import RefIndex, _atomic_write_text
from .roles import _companion_dir, _RolesMixin, _soft_check_dir

logger = logging.getLogger(__name__)


class ReferenceStore(_RolesMixin):
    """A project's stored baselines, one file per test under
    ``<reference_root>/<Backend>/<os>/``.

    Args:
        config: The project's configuration, which says where they are.
    """

    def __init__(self, config: Config):
        self.config = config
        self.ref_dir = config.reference_dir
        self._index = RefIndex(self.ref_dir)

    @property
    def index(self) -> RefIndex:
        return self._index

    @property
    def folder(self) -> str:
        """The folder this store reads, as a sentence names it: ``FMPy/macos``
        for ``<reference_root>/FMPy/macos/``."""
        return f"{self.config.backend}/{self.config.os_name}"

    def _ensure_dir(self):
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def _ref_file_for_model(self, model_id: str) -> Path | None:
        """Get the reference file path for a model. Returns None if not indexed."""
        test_id = self._index.get_id(model_id)
        if test_id is None:
            return None
        return self.ref_dir / RefIndex.ref_filename(test_id)

    def reference_file(self, model_id: str) -> Path | None:
        """The file a model's baseline is read from — what an error about
        its contents names — or None for a model with none."""
        return self._ref_file_for_model(model_id)

    def get_reference(self, model_id: str) -> ReferenceFile | None:
        """A model's reference file, decoded; None when it has none or it
        cannot be read (the error is logged)."""
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            return None
        try:
            return read_reference(ref_file)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read reference %s: %s", ref_file, e)
            return None

    def store_reference(
        self,
        test: TestModel,
        result: TestResult,
        *,
        provenance: dict,
    ) -> bool:
        """Store simulation results as a new reference baseline.

        ``provenance`` is what produced *result* (``accept._provenance``),
        written as the file's ``provenance``: required, so no writer can
        store a baseline without saying where it came from.

        Raises :class:`ValueError` when the trajectory contains non-finite
        values or no rows at all — callers that accept in bulk should catch
        it per test.
        """
        if not result.success or not result.variables:
            return False

        # review 2026-07-06 leftover B: refuse to store a poisoned baseline.
        # A trajectory containing NaN/Inf can never pass a later comparison
        # (the metric math propagates NaN → guaranteed FAIL forever) and
        # would serialize as non-strict JSON (`NaN`/`Infinity` literals)
        # that strict parsers reject. Name the model + variable so accept
        # flows can skip it and tell the user which signal is bad.
        time_arr = np.asarray(result.variables[0].time, dtype=float)
        if time_arr.size == 0:
            # A baseline of no points is one nothing can be scored against:
            # said here, where an accept skips the test with the reason.
            raise ValueError(
                f"refusing to store baseline for {test.model_id}: the result "
                "has no rows to store"
            )
        if time_arr.size and not np.all(np.isfinite(time_arr)):
            raise ValueError(
                f"refusing to store baseline for {test.model_id}: the time "
                "vector contains non-finite values (NaN/Inf)"
            )
        for var in result.variables:
            vals = np.asarray(var.values, dtype=float)
            if vals.size and not np.all(np.isfinite(vals)):
                raise ValueError(
                    f"refusing to store baseline for {test.model_id}: variable "
                    f"'{var.name or var.index}' contains non-finite values (NaN/Inf)"
                )

        self._ensure_dir()

        test_id = self._index.register(test.model_id)
        filename = RefIndex.ref_filename(test_id)
        ref_file = self.ref_dir / filename

        # Check if this is a new file or an update
        existing: ReferenceFile | None = None
        if ref_file.exists():
            try:
                existing = read_reference(ref_file)
            except (json.JSONDecodeError, OSError):
                pass

        # Build the reference file
        # All variables share the same time vector (Modelica spec).
        shared_time = result.variables[0].time
        # The points kept (D153): every row the run wrote, unless
        # `baseline_points` gives a number — the test's own, else the
        # project's. "all" is the run's own row count — a budget
        # `_downsample` returns every row within.
        setting = (
            test.baseline_points
            if test.baseline_points is not None
            else self.config.baseline_points
        )
        max_points = setting if isinstance(setting, int) else len(shared_time)
        shared_time_list, _ = _downsample(shared_time, shared_time, max_points)

        variables = []
        for var in result.variables:
            _, values_list = _downsample(shared_time, var.values, max_points)
            variables.append(
                StoredVariable(
                    index=var.index,
                    name=var.name,
                    # Always written, "" included: what the model's ``x={...}``
                    # said when this baseline was accepted, soft-checked on
                    # every compare.
                    expression=var.expression,
                    values=values_list,
                )
            )

        now = datetime.now(timezone.utc).isoformat()
        date_added = now
        if existing is not None and existing.date_added is not None:
            date_added = existing.date_added

        # Derive numberOfIntervals from actual result if not explicitly set
        n_intervals = test.number_of_intervals
        out_interval = test.output_interval
        if n_intervals is None and out_interval is None:
            # Count unique time points (exclude event duplicates)
            unique_times = len(np.unique(shared_time))
            n_intervals = max(unique_times - 1, 1)

        simulation = {
            "stop_time": test.stop_time,
            "tolerance": test.tolerance,
            "method": test.method,
            "number_of_intervals": n_intervals,
            "output_interval": out_interval,
        }
        # What was done to the run's rows, always written: a reference
        # WITHOUT the key was stored before it existed, under a cap of
        # 2000 points that no key set.
        sampling = Sampling(
            setting=setting,
            source_points=len(shared_time),
            stored_points=len(shared_time_list),
            # Always written. "single" only when the time vector and EVERY
            # value array were single precision: the codec then reads each
            # stored number back through float32, which would move a double.
            precision=SINGLE
            if all(
                np.asarray(a).dtype == np.float32
                for a in (shared_time, *(v.values for v in result.variables))
            )
            else DOUBLE,
        )

        # Comparison settings (per-test and per-variable tolerances)
        # A base tolerance is written only when it is ONE finite number —
        # the test's, or else the old reference's: whatever calls this, a
        # reference JSON never holds a base the comparison refuses.
        # Imported here: at module level it closes a cycle through
        # `dstf.comparison` -> comparator -> this module.
        from ..comparison.sides import is_base_tolerance

        comparison: dict[str, Any] = {}
        if is_base_tolerance(test.comparison_tolerance):
            comparison["tolerance"] = test.comparison_tolerance
        if test.variable_overrides:
            comparison["variable_overrides"] = test.variable_overrides
        # Preserve existing comparison settings if not overridden
        if existing is not None and existing.comparison:
            existing_comp = existing.comparison
            if "tolerance" not in comparison and is_base_tolerance(
                existing_comp.get("tolerance")
            ):
                comparison["tolerance"] = existing_comp["tolerance"]
            if (
                "variable_overrides" not in comparison
                and "variable_overrides" in existing_comp
            ):
                comparison["variable_overrides"] = existing_comp["variable_overrides"]

        # The listed names (`diagnostic_names`: CPUtime, EventCounter, a
        # test's own) — stored as their summary, not a trajectory: CPUtime's
        # is nondeterministic, and storing it gave every re-accept a spurious
        # git diff. A criterion that needs a listed name's curve has it
        # extracted, so it is among the variables above and stored as one
        # (D162); a `variables` pattern does the same.
        diagnostics = []
        for diag in result.diagnostics or []:
            values = np.asarray(diag.values)
            if values.size > 0:
                diagnostics.append(
                    DiagnosticSummary(
                        name=diag.name,
                        final=float(values[-1]),
                        min=float(values.min()),
                        max=float(values.max()),
                    )
                )
            else:
                diagnostics.append(DiagnosticSummary(name=diag.name))

        ref = ReferenceFile(
            model_id=test.model_id,
            test_id=test_id,
            status="active",
            date_added=date_added,
            last_updated=now,
            sampling=sampling,
            comparison=comparison,
            baseline=Baseline(
                name=PRIMARY_BASELINE,
                time=shared_time_list,
                variables=variables,
                diagnostics=diagnostics,
                simulation=simulation,
                statistics=result.statistics or {},
                provenance=provenance,
            ),
        )

        # review 2026-07-06 (finding 31): atomic — a Ctrl-C mid-accept must
        # not corrupt the baseline (a corrupt file is silently dropped by the
        # next scan and its ID re-allocated to a different model).
        write_reference(ref_file, ref)

        return True

    def accept_results(
        self,
        tests: list[TestModel],
        results: dict[str, TestResult],
        *,
        provenance: Mapping[str, dict],
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        """Accept simulation results as new baselines, each with the
        ``provenance`` of its model's result.

        Returns ``(stored, skipped, simulate_only)``: the model ids whose
        baseline was written, in the order given; ``(model_id, reason)`` for
        tests that should have produced a baseline but didn't — review
        2026-07-06 finding 30: the CLI exits nonzero (and says why) when an
        --accept run stores fewer baselines than it attempted; and the model
        ids of the simulate-only tests passed over, in the order given.
        Simulate-only tests never store a baseline by design, so a
        successful simulate-only run is neither stored nor skipped — and is
        named, so that an accept can say why it stored nothing for it.
        """
        stored: list[str] = []
        skipped: list[tuple[str, str]] = []
        simulate_only: list[str] = []
        for test in tests:
            result = results.get(test.model_id)
            if result is None:
                skipped.append((test.model_id, "no simulation result"))
                continue
            if not result.success:
                skipped.append((test.model_id, "simulation failed"))
                continue
            if test.is_simulate_only:
                simulate_only.append(test.model_id)
                continue
            try:
                ok = self.store_reference(
                    test, result, provenance=provenance[test.model_id]
                )
            except ValueError as exc:
                # review 2026-07-06 leftover B: one poisoned trajectory must
                # not abort accepting the rest — skip it, warn, keep going.
                logger.warning("Not accepting %s: %s", test.model_id, exc)
                skipped.append((test.model_id, str(exc)))
                continue
            if ok:
                stored.append(test.model_id)
            else:
                skipped.append((test.model_id, "no tracked variables in result"))
        return stored, skipped, simulate_only

    def list_models(self) -> list[str]:
        """List all model IDs with stored references (excluding obsolete)."""
        return sorted(self._index.active_tests().values())

    def export_json(self, output_path: Path):
        """Export all references as a single JSON file."""
        models = self.list_models()
        all_refs = {}
        for model_id in models:
            ref = self.get_reference(model_id)
            if ref is not None:
                all_refs[model_id] = reference_to_json(ref)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # review 2026-07-06 (finding 31): atomic like every other JSON write.
        _atomic_write_text(output_path, json.dumps(all_refs, indent=2) + "\n")

    def export_csv(self, output_path: Path):
        """Export a summary CSV of all references."""
        models = self.list_models()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(
            [
                "test_id",
                "model_id",
                "status",
                "n_vars",
                "stop_time",
                "tolerance",
                "method",
                "date_added",
                "last_updated",
            ]
        )
        for model_id in models:
            ref = self.get_reference(model_id)
            if ref is not None:
                sim = ref.baseline.simulation
                writer.writerow(
                    [
                        ref.test_id or "",
                        model_id,
                        ref.active_status,
                        len(ref.baseline.variables),
                        sim.get("stop_time", ""),
                        sim.get("tolerance", ""),
                        sim.get("method", ""),
                        ref.date_added or "",
                        ref.last_updated or "",
                    ]
                )
        # review 2026-07-06 (finding 31): atomic like every other export —
        # an interrupted write must not leave a truncated summary behind.
        _atomic_write_text(output_path, buf.getvalue(), newline="")

    def list_obsolete(self) -> list[tuple[str, str, Path]]:
        """``(test_id, model_id, ref_file)`` for every obsolete reference that
        still has a file on disk.

        The listing half of :meth:`cleanup_obsolete`, so ``manifest cleanup``
        can show what it would delete before it deletes anything (dry-run is
        the default for every destructive path).
        """
        out = []
        for test_id, entry in self._index.all_tests().items():
            if entry.get("status") != "obsolete":
                continue
            ref_file = self.ref_dir / RefIndex.ref_filename(test_id)
            if ref_file.exists():
                out.append((test_id, entry.get("model_id", ""), ref_file))
        return sorted(out)

    def cleanup_obsolete(self) -> int:
        """Remove reference files with status 'obsolete'."""
        all_tests = self._index.all_tests()
        removed = 0
        for test_id, entry in all_tests.items():
            if entry.get("status") == "obsolete":
                ref_file = self.ref_dir / RefIndex.ref_filename(test_id)
                if ref_file.exists():
                    ref_file.unlink()
                    removed += 1
                    logger.info("Removed obsolete reference: %s", ref_file.name)
                # review 2026-07-06 (finding 32): also remove the deleted
                # ref's soft_checks/ and companions/ dirs — a later reuse of
                # this ID must not inherit another model's role data.
                for role_dir in (
                    _soft_check_dir(self.ref_dir, ref_file),
                    _companion_dir(self.ref_dir, ref_file),
                ):
                    if role_dir.is_dir():
                        shutil.rmtree(role_dir, ignore_errors=True)
                        # Said only of a folder that is gone: one the
                        # removal could not delete is still there.
                        if not role_dir.exists():
                            logger.info("Removed orphaned role dir: %s", role_dir)
        return removed


def _downsample(
    time: np.ndarray, values: np.ndarray, max_points: int
) -> tuple[list[float], list[float]]:
    """Downsample a time series to at most max_points; one of at most
    max_points rows is returned whole, so no point is ever invented.

    Always preserves the first and last samples. Event boundaries (duplicate
    time points) are preserved whenever they fit within the budget; if event
    indices alone exceed max_points they are evenly thinned (review
    2026-07-06, finding 3). Evenly samples remaining points to fill up to
    max_points.
    """
    n = len(time)
    if n <= max_points:
        return stored_numbers(time), stored_numbers(values)

    # Find event boundary indices (duplicate time values — keep both)
    event_indices = set()
    event_indices.add(0)
    event_indices.add(n - 1)
    for i in range(1, n):
        if time[i] == time[i - 1]:
            event_indices.add(i - 1)  # pre-event
            event_indices.add(i)  # post-event

    if len(event_indices) > max_points:
        # review 2026-07-06 (finding 3): event indices alone exceed the
        # budget. Keep first/last and evenly thin the *interior* event
        # indices to fit — the old tail-trim (`all_indices[:max_points]`)
        # silently dropped the final samples, making tail regressions
        # invisible to every future comparison.
        interior = sorted(event_indices - {0, n - 1})
        budget = max(max_points - 2, 0)
        if budget and interior:
            pick = np.unique(np.linspace(0, len(interior) - 1, budget).astype(int))
            kept = {interior[i] for i in pick}
        else:
            kept = set()
        all_indices = sorted({0, n - 1} | kept)
    else:
        # Fill remaining budget with evenly spaced indices
        remaining = max_points - len(event_indices)
        candidates = np.linspace(0, n - 1, remaining + len(event_indices), dtype=int)
        extras = sorted(set(candidates.tolist()) - event_indices)
        # review 2026-07-06 (finding 3): when the union overshoots
        # max_points, drop interior linspace candidates — never the
        # endpoints or event boundaries — so the stored baseline always
        # reaches the final time sample.
        overshoot = len(event_indices) + len(extras) - max_points
        if overshoot > 0:
            keep_n = len(extras) - overshoot
            if keep_n > 0:
                pick = np.unique(np.linspace(0, len(extras) - 1, keep_n).astype(int))
                extras = [extras[i] for i in pick]
            else:
                extras = []
        all_indices = sorted(event_indices | set(extras))

    idx = np.array(all_indices)
    return stored_numbers(time[idx]), stored_numbers(values[idx])
