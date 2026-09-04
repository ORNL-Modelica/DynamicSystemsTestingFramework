"""JSON file-based storage for reference results with stable numeric test IDs.

Reference files (ref_NNNN.json) are the source of truth. Each file contains
model_id, test_id, status, date_added, and last_updated metadata. An in-memory
index is built by scanning ref files — no persistent manifest file needed
(:mod:`dstf.storage.ref_index`).

A :class:`~dstf.storage.baseline.Baseline` view is exposed alongside the raw-dict
API. The on-disk format is flat — one baseline per file — and the view
synthesizes a single baseline named ``"primary"`` from those fields. Additional
named baselines are separate files under ``soft_checks/ref_NNNN/<name>.json``
(D66); that CRUD lives in :mod:`dstf.storage.roles`.
"""

import csv
import io
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..backends import TestResult
from ..config import Config
from ..discovery.test_registry import TestModel
from .baseline import PRIMARY_BASELINE, Baseline
from .ref_index import RefIndex, _atomic_write_text
from .roles import _companion_dir, _RolesMixin, _soft_check_dir

logger = logging.getLogger(__name__)


def _baseline_from_flat(data: dict) -> Baseline:
    """Synthesize the ``"primary"`` baseline from a flat reference dict.

    A ref file keeps its baseline fields (``time``, ``variables``,
    ``comparison``, ...) at the top level. This adapter lifts them into a
    Baseline view so readers target one API for primary and soft_checks alike.
    """
    provenance: dict[str, Any] = {"origin": "flat"}
    if "date_added" in data:
        provenance["captured_at"] = data["date_added"]
    if "last_updated" in data and data.get("last_updated") != data.get("date_added"):
        provenance["last_updated"] = data["last_updated"]

    return Baseline(
        name=PRIMARY_BASELINE,
        time=data.get("time", []),
        variables=data.get("variables", []),
        diagnostics=data.get("diagnostics", []),
        simulation=data.get("simulation", {}),
        comparison=data.get("comparison", {}),
        statistics=data.get("statistics", {}),
        provenance=provenance,
    )


def _extract_baselines(data: dict) -> dict[str, Baseline]:
    """Return the ``"primary"`` baseline of a reference file, keyed by name.

    A ref file carries exactly one baseline: the primary, stored as flat
    top-level fields (``time``, ``variables``, ``simulation``,
    ``comparison``, ``statistics``, ``diagnostics``). Named baselines live
    in the store's ``soft_checks/ref_NNNN/<name>.json`` subdir (D66) and are
    merged in by :meth:`ReferenceStore.get_baselines`, not read from here.
    """
    return {PRIMARY_BASELINE: _baseline_from_flat(data)}


class ReferenceStore(_RolesMixin):
    """Manages per-test JSON reference files.

    The soft_check / companion CRUD (``add_soft_check``, ``get_companions``,
    ``freeze_companion``, ...) is inherited from
    :class:`~dstf.storage.roles._RolesMixin`; it reaches back only through
    ``ref_dir`` and :meth:`_ref_file_for_model`.
    """

    def __init__(self, config: Config):
        self.config = config
        self.ref_dir = config.reference_dir
        self._index = RefIndex(self.ref_dir)

    @property
    def index(self) -> RefIndex:
        return self._index

    def _ensure_dir(self):
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def _ref_file_for_model(self, model_id: str) -> Path | None:
        """Get the reference file path for a model. Returns None if not indexed."""
        test_id = self._index.get_id(model_id)
        if test_id is None:
            return None
        return self.ref_dir / RefIndex.ref_filename(test_id)

    def get_reference(self, model_id: str) -> dict | None:
        """Load reference data for a model. Returns None if not found.

        Returns the raw dict as stored on disk. Most callers should prefer
        :meth:`get_baseline` or :meth:`get_baselines`, which present a
        schema-version-independent view.
        """
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            return None
        try:
            data = json.loads(ref_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read reference %s: %s", ref_file, e)
            return None
        if isinstance(data.get("baselines"), dict):
            # Old data, not old code: named baselines are read only from
            # soft_checks/ now. The key is ignored here and dropped the next
            # time this file is written.
            logger.warning(
                "%s carries a top-level 'baselines' key — it is ignored. "
                "Re-add those baselines as soft_checks: "
                "`dstf import-baseline %s soft_check <name> <source>`.",
                ref_file,
                model_id,
            )
        return data

    def get_baselines(self, model_id: str) -> dict[str, Baseline]:
        """Return the primary baseline plus any registered soft_checks.

        Companions are **not** returned here — they are plot-only
        overlays and cannot be scored against (D66). Use
        :meth:`get_companions` for those.

        Empty dict if the model has no reference file.
        """
        data = self.get_reference(model_id)
        if data is None:
            return {}
        out = _extract_baselines(data)
        out.update(self.get_soft_checks(model_id))
        return out

    def get_baseline(
        self,
        model_id: str,
        name: str = PRIMARY_BASELINE,
    ) -> Baseline | None:
        """Return one named baseline for a model, or None if not found.

        Defaults to ``"primary"`` — which is what every existing caller
        implicitly wants (there is only one baseline per file today).
        """
        return self.get_baselines(model_id).get(name)

    def list_baseline_names(self, model_id: str) -> list[str]:
        """Return the names of baselines available for a model."""
        return list(self.get_baselines(model_id).keys())

    def store_reference(
        self,
        test: TestModel,
        result: TestResult,
    ) -> bool:
        """Store simulation results as a new reference baseline.

        Raises :class:`ValueError` when the trajectory contains non-finite
        values — callers that accept in bulk should catch it per test.
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
        existing = None
        if ref_file.exists():
            try:
                existing = json.loads(ref_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Build the reference JSON
        # All variables share the same time vector (Modelica spec).
        shared_time = result.variables[0].time
        shared_time_list, _ = _downsample(shared_time, shared_time)

        variables = []
        for var in result.variables:
            _, values_list = _downsample(shared_time, var.values)
            variables.append(
                {
                    "index": var.index,
                    "name": var.name,
                    "values": values_list,
                }
            )

        now = datetime.now(timezone.utc).isoformat()
        date_added = now
        if existing and "date_added" in existing:
            date_added = existing["date_added"]

        # Derive numberOfIntervals from actual result if not explicitly set
        n_intervals = test.number_of_intervals
        out_interval = test.output_interval
        if n_intervals is None and out_interval is None:
            # Count unique time points (exclude event duplicates)
            unique_times = len(np.unique(shared_time))
            n_intervals = max(unique_times - 1, 1)

        ref_data: dict[str, Any] = {
            "model_id": test.model_id,
            "test_id": test_id,
            "status": "active",
            "date_added": date_added,
            "last_updated": now,
            "simulation": {
                "stop_time": test.stop_time,
                "tolerance": test.tolerance,
                "method": test.method,
                "number_of_intervals": n_intervals,
                "output_interval": out_interval,
            },
        }

        # Comparison settings (per-test and per-variable tolerances)
        comparison: dict[str, Any] = {}
        if test.comparison_tolerance is not None:
            comparison["tolerance"] = test.comparison_tolerance
        if test.variable_overrides:
            comparison["variable_overrides"] = test.variable_overrides
        # Preserve existing comparison settings if not overridden
        if existing and "comparison" in existing:
            existing_comp = existing["comparison"]
            if "tolerance" not in comparison and "tolerance" in existing_comp:
                comparison["tolerance"] = existing_comp["tolerance"]
            if (
                "variable_overrides" not in comparison
                and "variable_overrides" in existing_comp
            ):
                comparison["variable_overrides"] = existing_comp["variable_overrides"]
        if comparison:
            ref_data["comparison"] = comparison

        if result.statistics:
            ref_data["statistics"] = result.statistics

        # Diagnostic variables (CPUtime, EventCounter) — stored as a scalar
        # summary, not a full trajectory. The trajectory value (especially
        # CPUtime) is nondeterministic — storing it caused every re-accept
        # to produce a spurious git diff and bloated the baseline files.
        # Users who want a diagnostic's full trajectory can add its name to
        # the test's variables/variable_patterns list — it'll be tracked
        # like any other variable at that point.
        if result.diagnostics:
            diag_list = []
            for diag in result.diagnostics:
                values = np.asarray(diag.values)
                entry: dict = {"name": diag.name}
                if values.size > 0:
                    entry["final"] = float(values[-1])
                    entry["min"] = float(values.min())
                    entry["max"] = float(values.max())
                diag_list.append(entry)
            ref_data["diagnostics"] = diag_list

        # Data fields last — keeps metadata readable at the top of the file
        ref_data["n_vars"] = len(variables)
        ref_data["time"] = shared_time_list
        ref_data["variables"] = variables

        # review 2026-07-06 (finding 31): atomic — a Ctrl-C mid-accept must
        # not corrupt the baseline (a corrupt file is silently dropped by the
        # next scan and its ID re-allocated to a different model).
        _atomic_write_text(ref_file, json.dumps(ref_data, indent=2) + "\n")

        return True

    def accept_results(
        self,
        tests: list[TestModel],
        results: dict[str, TestResult],
    ) -> tuple[int, list[tuple[str, str]]]:
        """Accept simulation results as new baselines.

        Returns ``(stored, skipped)`` where ``skipped`` lists
        ``(model_id, reason)`` for tests that should have produced a
        baseline but didn't — review 2026-07-06 finding 30: the CLI exits
        nonzero (and says why) when an --accept run stores fewer baselines
        than it attempted. Simulate-only tests never store a baseline by
        design, so a successful simulate-only run is neither stored nor
        skipped.
        """
        stored = 0
        skipped: list[tuple[str, str]] = []
        for test in tests:
            result = results.get(test.model_id)
            if result is None:
                skipped.append((test.model_id, "no simulation result"))
                continue
            if not result.success:
                skipped.append((test.model_id, "simulation failed"))
                continue
            if test.simulate_only:
                continue
            try:
                ok = self.store_reference(test, result)
            except ValueError as exc:
                # review 2026-07-06 leftover B: one poisoned trajectory must
                # not abort accepting the rest — skip it, warn, keep going.
                logger.warning("Not accepting %s: %s", test.model_id, exc)
                skipped.append((test.model_id, str(exc)))
                continue
            if ok:
                stored += 1
            else:
                skipped.append((test.model_id, "no tracked variables in result"))
        return stored, skipped

    def set_status(self, model_id: str, status: str) -> bool:
        """Update the status field of a reference file.

        Valid statuses: 'active', 'skip', 'obsolete'.
        """
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            return False
        try:
            data = json.loads(ref_file.read_text(encoding="utf-8"))
            data["status"] = status
            # review 2026-07-06 (finding 31): atomic status flip.
            _atomic_write_text(ref_file, json.dumps(data, indent=2) + "\n")
            # Update in-memory index
            test_id = self._index.get_id(model_id)
            if test_id and test_id in self._index._by_id:
                self._index._by_id[test_id]["status"] = status
                if status == "obsolete":
                    self._index._by_model.pop(model_id, None)
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to update status for %s: %s", ref_file, e)
            return False

    def list_models(self) -> list[str]:
        """List all model IDs with stored references (excluding obsolete)."""
        return sorted(self._index.active_tests().values())

    def export_json(self, output_path: Path):
        """Export all references as a single JSON file."""
        models = self.list_models()
        all_refs = {}
        for model_id in models:
            ref = self.get_reference(model_id)
            if ref:
                all_refs[model_id] = ref

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
            if ref:
                sim = ref.get("simulation", {})
                writer.writerow(
                    [
                        ref.get("test_id", ""),
                        model_id,
                        ref.get("status", "active"),
                        ref.get("n_vars", ""),
                        sim.get("stop_time", ""),
                        sim.get("tolerance", ""),
                        sim.get("method", ""),
                        ref.get("date_added", ""),
                        ref.get("last_updated", ""),
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
                        logger.info("Removed orphaned role dir: %s", role_dir)
        return removed


def _downsample(
    time: np.ndarray, values: np.ndarray, max_points: int = 2000
) -> tuple[list[float], list[float]]:
    """Downsample a time series to at most max_points.

    Always preserves the first and last samples. Event boundaries (duplicate
    time points) are preserved whenever they fit within the budget; if event
    indices alone exceed max_points they are evenly thinned (review
    2026-07-06, finding 3). Evenly samples remaining points to fill up to
    max_points.
    """
    n = len(time)
    if n <= max_points:
        return _to_json_list(time), _to_json_list(values)

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
    return _to_json_list(time[idx]), _to_json_list(values[idx])


def _to_json_list(arr: np.ndarray) -> list[float]:
    """Convert numpy array to JSON-serializable list of Python floats.

    Rounds to significant digits matching the source precision:
    - float32 (older Dymola): 7 significant digits
    - float64 (newer Dymola): 15 significant digits
    This avoids noise from float32→float64 promotion while preserving
    full precision for native float64 data.
    """
    sig = 7 if arr.dtype == np.float32 else 15
    return [float(f"%.{sig}g" % v) for v in arr]
