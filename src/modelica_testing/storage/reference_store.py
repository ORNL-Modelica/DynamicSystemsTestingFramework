"""JSON file-based storage for reference results with stable numeric test IDs.

Reference files (ref_NNNN.json) are the source of truth. Each file contains
model_id, test_id, status, date_added, and last_updated metadata. An in-memory
index is built by scanning ref files — no persistent manifest file needed.
"""

import csv
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from ..simulators import TestResult, VariableResult

logger = logging.getLogger(__name__)

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
        """Load reference data for a model. Returns None if not found."""
        ref_file = self._ref_file_for_model(model_id)
        if ref_file is None or not ref_file.exists():
            return None
        try:
            return json.loads(ref_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read reference %s: %s", ref_file, e)
            return None

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

        if result.statistics:
            ref_data["statistics"] = result.statistics

        # Diagnostic variables (CPUtime, EventCounter) — stored but not compared
        if result.diagnostics:
            diag_list = []
            for diag in result.diagnostics:
                _, values_list = _downsample(shared_time, diag.values)
                diag_list.append({
                    "name": diag.name,
                    "values": values_list,
                })
            ref_data["diagnostics"] = diag_list

        # Data fields last — keeps metadata readable at the top of the file
        ref_data["n_vars"] = len(variables)
        ref_data["time"] = shared_time_list
        ref_data["variables"] = variables

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
