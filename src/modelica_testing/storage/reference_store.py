"""JSON file-based storage for reference results with stable numeric test IDs.

Reference files (ref_NNNN.json) are the source of truth. Each file contains
model_id, test_id, status, date_added, and last_updated metadata. An in-memory
index is built by scanning ref files — no persistent manifest file needed.

Phase 1.7a: a `Baseline` view is exposed alongside the raw-dict API. Today the
on-disk format is flat (one implicit baseline per file); the view synthesizes
a single baseline named ``"primary"`` from the flat fields. Phase 1.7b will
write the new format (``baselines: {name: {...}}``) and this reader will
present both formats through the same view API.
"""

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from ..simulators import TestResult, VariableResult

logger = logging.getLogger(__name__)

#: Name of the default (and, for legacy flat files, only) baseline.
PRIMARY_BASELINE = "primary"


@dataclass(frozen=True)
class Baseline:
    """A single named baseline extracted from a reference file.

    Phase 1.7a: populated either from the new ``baselines: {name: {...}}``
    schema, or synthesized from a legacy flat reference file. Readers can
    treat both the same way. See docs/architecture.md "Forward: multiple
    named baselines".
    """

    #: Baseline name (e.g. ``"primary"``, ``"experiment"``, ``"analytical"``).
    name: str
    #: Shared time vector (all variables in this baseline share one time grid).
    time: list[float]
    #: Variable entries: ``[{"index", "name", "values"}, ...]``.
    variables: list[dict]
    #: Diagnostic variable entries (same shape as ``variables``).
    diagnostics: list[dict] = field(default_factory=list)
    #: Simulation parameters that produced this baseline.
    simulation: dict = field(default_factory=dict)
    #: Comparison configuration (tolerance, variable_overrides).
    comparison: dict = field(default_factory=dict)
    #: Statistics captured at baseline-acceptance time.
    statistics: dict = field(default_factory=dict)
    #: Provenance metadata: origin, captured_at, simulator, os, notes,
    #: citation, plus arbitrary user metadata. For legacy flat files this
    #: is synthesized from ``date_added`` / ``last_updated``.
    provenance: dict = field(default_factory=dict)


def _baseline_from_flat(data: dict) -> Baseline:
    """Synthesize a ``"primary"`` baseline from a legacy flat reference dict.

    The legacy format has fields (``time``, ``variables``, ``comparison``, ...)
    at the top level of the ref JSON. This adapter lifts them into a Baseline
    view so readers can target a single API across schema versions.
    """
    provenance: dict[str, Any] = {"origin": "legacy-flat"}
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
    """Return all baselines in a reference file, keyed by name.

    Ref files use a **hybrid schema**:

    * The ``"primary"`` baseline is always stored as flat top-level fields
      (``time``, ``variables``, ``simulation``, ``comparison``,
      ``statistics``, ``diagnostics``). This preserves compatibility with
      every existing reader and every existing ref file.
    * Optional *additional* named baselines (``experiment``, ``analytical``,
      user-defined) live under a top-level ``baselines`` map whose entries
      have the same per-baseline shape as the flat primary.

    A file with only flat fields is read as ``{"primary": Baseline}``.
    A file that also carries ``baselines`` is read as
    ``{"primary": ..., <additional>: ..., ...}``. ``primary`` in a
    ``baselines`` map is not legal — the flat fields are primary.
    """
    out: dict[str, Baseline] = {PRIMARY_BASELINE: _baseline_from_flat(data)}

    extras = data.get("baselines")
    if isinstance(extras, dict):
        for name, entry in extras.items():
            if name == PRIMARY_BASELINE:
                # Flat fields are authoritative for primary; ignore any
                # accidental primary entry under baselines.
                logger.warning(
                    "Ref file has a 'primary' entry under 'baselines' — "
                    "ignoring; the flat top-level fields are the primary baseline."
                )
                continue
            out[name] = Baseline(
                name=name,
                time=entry.get("time", []),
                variables=entry.get("variables", []),
                diagnostics=entry.get("diagnostics", []),
                simulation=entry.get("simulation", {}),
                comparison=entry.get("comparison", {}),
                statistics=entry.get("statistics", {}),
                provenance=entry.get("provenance", {}),
            )
    return out

# Pattern matching ref_NNNN.json filenames
_REF_FILE_PATTERN = re.compile(r"^ref_(\d{4,})\.json$")


class RefIndex:
    """In-memory index mapping model IDs to ref files.

    Built by scanning ref_NNNN.json files in the reference directory.
    No persistent manifest file — the ref files are the source of truth.
    """

    def __init__(self, ref_dir: Path):
        self.ref_dir = ref_dir
        self._by_model: dict[str, str] = {}      # model_id -> test_id
        self._by_id: dict[str, dict] = {}         # test_id -> {model_id, status}
        self._loaded = False

    def _scan(self):
        """Scan ref files and build the in-memory index."""
        self._by_model.clear()
        self._by_id.clear()

        if not self.ref_dir.exists():
            self._loaded = True
            return

        for ref_file in sorted(self.ref_dir.glob("ref_*.json")):
            m = _REF_FILE_PATTERN.match(ref_file.name)
            if not m:
                continue
            test_id = m.group(1)
            try:
                # Read only the first few KB — metadata is at the top
                text = ref_file.read_text(encoding="utf-8")
                data = json.loads(text)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping unreadable ref file %s: %s", ref_file.name, e)
                continue

            model_id = data.get("model_id")
            if not model_id:
                logger.warning("Ref file %s has no model_id — skipping", ref_file.name)
                continue

            status = data.get("status", "active")
            self._by_id[test_id] = {"model_id": model_id, "status": status}
            if status != "obsolete":
                self._by_model[model_id] = test_id

        self._loaded = True

    def _ensure_loaded(self):
        if not self._loaded:
            self._scan()

    def get_id(self, model_id: str) -> Optional[str]:
        """Look up the numeric ID for a model. Returns None if not found."""
        self._ensure_loaded()
        return self._by_model.get(model_id)

    def get_model_id(self, test_id: str) -> Optional[str]:
        """Look up the model ID for a numeric test ID."""
        self._ensure_loaded()
        entry = self._by_id.get(test_id)
        if entry and entry["status"] != "obsolete":
            return entry["model_id"]
        return None

    def next_id(self) -> str:
        """Return the next available numeric ID."""
        self._ensure_loaded()
        if not self._by_id:
            return "0001"
        max_id = max(int(k) for k in self._by_id)
        return f"{max_id + 1:04d}"

    def register(self, model_id: str) -> str:
        """Register a model and return its ID. Reuses existing ID if already registered."""
        existing = self.get_id(model_id)
        if existing is not None:
            return existing
        test_id = self.next_id()
        self._by_id[test_id] = {"model_id": model_id, "status": "active"}
        self._by_model[model_id] = test_id
        return test_id

    def active_tests(self) -> dict[str, str]:
        """Return dict of test_id -> model_id for all active (non-obsolete, non-skip) tests."""
        self._ensure_loaded()
        return {
            tid: entry["model_id"]
            for tid, entry in self._by_id.items()
            if entry["status"] not in ("obsolete",)
        }

    def all_tests(self) -> dict[str, dict]:
        """Return dict of test_id -> {model_id, status} for all tests."""
        self._ensure_loaded()
        return dict(self._by_id)

    @staticmethod
    def ref_filename(test_id: str) -> str:
        """Reference filename for a given numeric test ID."""
        return f"ref_{test_id}.json"


class ReferenceStore:
    """Manages per-test JSON reference files."""

    def __init__(self, config: Config):
        self.config = config
        self.ref_dir = config.reference_dir
        self._index = RefIndex(self.ref_dir)

    @property
    def index(self) -> RefIndex:
        return self._index

    def _ensure_dir(self):
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def _ref_file_for_model(self, model_id: str) -> Optional[Path]:
        """Get the reference file path for a model. Returns None if not indexed."""
        test_id = self._index.get_id(model_id)
        if test_id is None:
            return None
        return self.ref_dir / RefIndex.ref_filename(test_id)

    def get_reference(self, model_id: str) -> Optional[dict]:
        """Load reference data for a model. Returns None if not found.

        Returns the raw dict as stored on disk. Most callers should prefer
        :meth:`get_baseline` or :meth:`get_baselines`, which present a
        schema-version-independent view.
        """
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            return None
        try:
            return json.loads(ref_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read reference %s: %s", ref_file, e)
            return None

    def get_baselines(self, model_id: str) -> dict[str, Baseline]:
        """Return all named baselines for a model.

        For legacy flat ref files, returns a single synthetic ``"primary"``
        baseline. For the new multi-baseline schema (Phase 1.7b), returns
        the authored map. Empty dict if the model has no reference file.
        """
        data = self.get_reference(model_id)
        if data is None:
            return {}
        return _extract_baselines(data)

    def get_baseline(
        self,
        model_id: str,
        name: str = PRIMARY_BASELINE,
    ) -> Optional[Baseline]:
        """Return one named baseline for a model, or None if not found.

        Defaults to ``"primary"`` — which is what every existing caller
        implicitly wants (there is only one baseline per file today).
        """
        return self.get_baselines(model_id).get(name)

    def add_named_baseline(
        self,
        model_id: str,
        name: str,
        time: list[float],
        variables: list[dict],
        *,
        provenance: Optional[dict] = None,
        simulation: Optional[dict] = None,
        statistics: Optional[dict] = None,
        overwrite: bool = True,
    ) -> bool:
        """Add or update a **non-primary** named baseline on an existing reference.

        Primary stays at the flat top-level (written by ``store_reference``
        on ``--accept``). Additional baselines live under the ``baselines``
        map. Use this for programmatic authoring of experiment / analytical
        / cross-backend baselines that don't come from running the framework
        itself. The model must already have a primary baseline on disk —
        this helper adds to an existing ref file, it does not create one.

        Args:
            model_id: Must already have a ref file (primary baseline).
            name: Baseline name (``"experiment"``, ``"analytical"``, ...).
                  ``"primary"`` is rejected — primary is written by
                  ``store_reference`` only.
            time: Shared time vector for all variables in this baseline.
            variables: ``[{"index", "name", "values"}, ...]``.
            provenance: Optional origin metadata (captured_at, source,
                citation, notes, ...). Stored verbatim under the baseline's
                ``provenance`` key.
            simulation: Optional simulation parameters that produced this
                baseline (stop_time, method, ...). Free-form dict.
            statistics: Optional statistics captured at baseline-acceptance
                time. Free-form dict.
            overwrite: When False, refuses to replace an existing baseline
                of the same name (returns False).

        Returns:
            True on write, False if overwrite=False and the name exists.
        """
        if name == PRIMARY_BASELINE:
            raise ValueError(
                "'primary' baselines are written by store_reference only; "
                "add_named_baseline is for non-primary baselines."
            )
        if not name:
            raise ValueError("baseline name must be a non-empty string")

        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            raise FileNotFoundError(
                f"No primary baseline for {model_id!r} — run the test with "
                f"--accept first to create the reference file, then add "
                f"non-primary baselines."
            )

        data = self.get_reference(model_id) or {}
        extras = data.setdefault("baselines", {})
        if not overwrite and name in extras:
            return False

        entry: dict[str, Any] = {
            "time": list(time),
            "variables": list(variables),
        }
        if provenance:
            entry["provenance"] = dict(provenance)
        if simulation:
            entry["simulation"] = dict(simulation)
        if statistics:
            entry["statistics"] = dict(statistics)
        extras[name] = entry

        ref_file.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )
        return True

    def list_baseline_names(self, model_id: str) -> list[str]:
        """Return the names of baselines available for a model."""
        return list(self.get_baselines(model_id).keys())

    def store_reference(
        self,
        test: TestModel,
        result: TestResult,
    ) -> bool:
        """Store simulation results as a new reference baseline."""
        if not result.success or not result.variables:
            return False

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
            variables.append({
                "index": var.index,
                "name": var.name,
                "values": values_list,
            })

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

        ref_data = {
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
        comparison = {}
        if test.comparison_tolerance is not None:
            comparison["tolerance"] = test.comparison_tolerance
        if test.variable_overrides:
            comparison["variable_overrides"] = test.variable_overrides
        # Preserve existing comparison settings if not overridden
        if existing and "comparison" in existing:
            existing_comp = existing["comparison"]
            if "tolerance" not in comparison and "tolerance" in existing_comp:
                comparison["tolerance"] = existing_comp["tolerance"]
            if "variable_overrides" not in comparison and "variable_overrides" in existing_comp:
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

        # Preserve any additional named baselines (experiment, analytical, ...).
        # Primary is always the flat top-level fields above; the ``baselines``
        # key only carries non-primary baselines. Accepting new primary results
        # must not wipe out user-supplied experiment/analytical baselines.
        if existing and isinstance(existing.get("baselines"), dict):
            extras = {
                name: entry
                for name, entry in existing["baselines"].items()
                if name != PRIMARY_BASELINE
            }
            if extras:
                ref_data["baselines"] = extras

        ref_file.write_text(
            json.dumps(ref_data, indent=2) + "\n", encoding="utf-8"
        )

        return True

    def accept_results(
        self,
        tests: list[TestModel],
        results: dict[str, TestResult],
    ) -> int:
        """Accept simulation results as new baselines. Returns count stored."""
        stored = 0
        for test in tests:
            result = results.get(test.model_id)
            if result and self.store_reference(test, result):
                stored += 1
        return stored

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
            ref_file.write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
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
        output_path.write_text(
            json.dumps(all_refs, indent=2) + "\n", encoding="utf-8"
        )

    def export_csv(self, output_path: Path):
        """Export a summary CSV of all references."""
        models = self.list_models()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "test_id", "model_id", "status", "n_vars", "stop_time",
                "tolerance", "method", "date_added", "last_updated",
            ])
            for model_id in models:
                ref = self.get_reference(model_id)
                if ref:
                    sim = ref.get("simulation", {})
                    writer.writerow([
                        ref.get("test_id", ""),
                        model_id,
                        ref.get("status", "active"),
                        ref.get("n_vars", ""),
                        sim.get("stop_time", ""),
                        sim.get("tolerance", ""),
                        sim.get("method", ""),
                        ref.get("date_added", ""),
                        ref.get("last_updated", ""),
                    ])

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
        return removed


def _downsample(
    time: np.ndarray, values: np.ndarray, max_points: int = 2000
) -> tuple[list[float], list[float]]:
    """Downsample a time series to at most max_points.

    Always preserves first, last, and event boundaries (duplicate time points).
    Evenly samples remaining points to fill up to max_points.
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
            event_indices.add(i)      # post-event

    # Fill remaining budget with evenly spaced indices
    remaining = max_points - len(event_indices)
    if remaining > 0:
        candidates = np.linspace(0, n - 1, remaining + len(event_indices), dtype=int)
        all_indices = sorted(event_indices | set(candidates.tolist()))
    else:
        all_indices = sorted(event_indices)

    # Trim to max_points if we overshot
    if len(all_indices) > max_points:
        all_indices = all_indices[:max_points]

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
