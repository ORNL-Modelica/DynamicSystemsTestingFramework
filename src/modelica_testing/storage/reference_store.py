"""JSON file-based storage for reference results with stable numeric test IDs."""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from ..simulators import TestResult, VariableResult

logger = logging.getLogger(__name__)


class TestManifest:
    """Manages the test_manifest.json file that maps stable numeric IDs to model IDs.

    The manifest lives at <reference_root>/test_manifest.json and is shared
    across all simulator/OS combinations. IDs are never reused — obsolete
    tests are marked with status "obsolete" rather than deleted.
    """

    def __init__(self, manifest_path: Path):
        self.path = manifest_path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        if self.path.exists():
            try:
                self._data = json.loads(
                    self.path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to read manifest %s: %s", self.path, e)
                self._data = {"version": 1, "tests": {}}
        else:
            self._data = {"version": 1, "tests": {}}
        return self._data

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2) + "\n", encoding="utf-8"
        )

    def get_id(self, model_id: str) -> Optional[str]:
        """Look up the numeric ID for a model. Returns None if not registered."""
        data = self._load()
        for test_id, entry in data["tests"].items():
            if entry["model_id"] == model_id and entry.get("status", "active") == "active":
                return test_id
        return None

    def get_model_id(self, test_id: str) -> Optional[str]:
        """Look up the model ID for a numeric test ID."""
        data = self._load()
        entry = data["tests"].get(test_id)
        if entry and entry.get("status", "active") == "active":
            return entry["model_id"]
        return None

    def register(self, model_id: str) -> str:
        """Register a model and return its numeric ID. Reuses existing ID if already registered."""
        existing = self.get_id(model_id)
        if existing is not None:
            return existing

        data = self._load()
        next_id = self._next_id(data)
        data["tests"][next_id] = {
            "model_id": model_id,
            "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "status": "active",
        }
        self._save()
        return next_id

    def mark_obsolete(self, test_id: str):
        """Mark a test as obsolete (ID is never reused)."""
        data = self._load()
        if test_id in data["tests"]:
            data["tests"][test_id]["status"] = "obsolete"
            self._save()

    def active_tests(self) -> dict[str, str]:
        """Return dict of test_id -> model_id for all active tests."""
        data = self._load()
        return {
            tid: entry["model_id"]
            for tid, entry in data["tests"].items()
            if entry.get("status", "active") == "active"
        }

    def exists(self) -> bool:
        """Whether the manifest file exists on disk."""
        return self.path.exists()

    def _next_id(self, data: dict) -> str:
        if not data["tests"]:
            return "0001"
        max_id = max(int(k) for k in data["tests"])
        return f"{max_id + 1:04d}"

    @staticmethod
    def ref_filename(test_id: str) -> str:
        """Reference filename for a given numeric test ID."""
        return f"ref_{test_id}.json"


class ReferenceStore:
    """Manages per-test JSON reference files using the test manifest."""

    def __init__(self, config: Config):
        self.config = config
        self.ref_dir = config.reference_dir
        self._manifest = TestManifest(config.manifest_file)

    @property
    def manifest(self) -> TestManifest:
        return self._manifest

    def _ensure_dir(self):
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def _ref_file_for_model(self, model_id: str) -> Optional[Path]:
        """Get the reference file path for a model. Returns None if not in manifest."""
        test_id = self._manifest.get_id(model_id)
        if test_id is None:
            return None
        return self.ref_dir / TestManifest.ref_filename(test_id)

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

        test_id = self._manifest.register(test.model_id)
        filename = TestManifest.ref_filename(test_id)
        ref_file = self.ref_dir / filename

        # Build the reference JSON
        # All variables share the same time vector (Modelica spec).
        # Downsample time once, then apply the same sampling to all variables.
        shared_time = result.variables[0].time
        shared_time_list, _ = _downsample(shared_time, shared_time)
        n_ds = len(shared_time_list)

        variables = []
        for var in result.variables:
            _, values_list = _downsample(shared_time, var.values)
            # Ensure same length as time (downsample is deterministic for same input time)
            variables.append({
                "index": var.index,
                "name": var.name,
                "values": values_list,
            })

        ref_data = {
            "model_id": test.model_id,
            "test_id": test_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "simulation": {
                "stop_time": test.stop_time,
                "tolerance": test.tolerance,
                "method": test.method,
                "number_of_intervals": test.number_of_intervals,
            },
        }

        if result.statistics:
            ref_data["statistics"] = result.statistics

        # Data fields last — keeps metadata readable at the top of the file
        ref_data["n_vars"] = len(variables)
        ref_data["time"] = shared_time_list
        ref_data["variables"] = variables

        ref_file.write_text(
            _compact_json(ref_data) + "\n", encoding="utf-8"
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

    def list_models(self) -> list[str]:
        """List all model IDs with stored references."""
        return sorted(self._manifest.active_tests().values())

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
                "test_id", "model_id", "n_vars", "stop_time", "tolerance",
                "method", "last_updated",
            ])
            for model_id in models:
                ref = self.get_reference(model_id)
                if ref:
                    sim = ref.get("simulation", {})
                    test_id = ref.get("test_id", "")
                    writer.writerow([
                        test_id,
                        model_id,
                        ref.get("n_vars", ""),
                        sim.get("stop_time", ""),
                        sim.get("tolerance", ""),
                        sim.get("method", ""),
                        ref.get("last_updated", ""),
                    ])

    def cleanup_obsolete(self) -> int:
        """Remove reference files for tests marked obsolete in the manifest."""
        data = self._manifest._load()
        removed = 0
        for test_id, entry in data["tests"].items():
            if entry.get("status") == "obsolete":
                ref_file = self.ref_dir / TestManifest.ref_filename(test_id)
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
    """Convert numpy array to JSON-serializable list of Python floats."""
    return [float(v) for v in arr]


def _compact_json(data: dict) -> str:
    """Serialize reference JSON with number arrays on single lines."""
    import re
    text = json.dumps(data, indent=2)

    def _collapse_array(m: re.Match) -> str:
        content = m.group(0)
        collapsed = re.sub(r'\s*\n\s*', ' ', content)
        return collapsed

    text = re.sub(
        r'\[\s*\n\s*-?[\d.][\s\S]*?\n\s*\]',
        _collapse_array,
        text,
    )
    return text
