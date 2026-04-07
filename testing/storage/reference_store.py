"""JSON file-based storage for reference results."""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel, generate_reference_filename
from ..simulation.result_reader import TestResult, VariableResult

logger = logging.getLogger(__name__)


class ReferenceStore:
    """Manages per-test JSON reference files and the index."""

    def __init__(self, config: Config):
        self.config = config
        self.ref_dir = config.reference_dir
        self._index: Optional[dict] = None

    def _ensure_dir(self):
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> dict:
        """Load or initialize the index.json file."""
        if self._index is not None:
            return self._index

        if self.config.index_file.exists():
            self._index = json.loads(
                self.config.index_file.read_text(encoding="utf-8")
            )
        else:
            self._index = {}
        return self._index

    def _save_index(self):
        """Write the index.json file."""
        self._ensure_dir()
        self.config.index_file.write_text(
            json.dumps(self._index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _ref_file_for_model(self, model_id: str) -> Path:
        """Get the reference file path for a model, using index or generating."""
        index = self._load_index()
        if model_id in index:
            return self.ref_dir / index[model_id]["filename"]
        filename = generate_reference_filename(
            model_id,
            library_name=self.config.library_name,
            path_abbreviations=self.config.path_abbreviations or None,
        )
        return self.ref_dir / filename

    def get_reference(self, model_id: str) -> Optional[dict]:
        """Load reference data for a model. Returns None if not found."""
        ref_file = self._ref_file_for_model(model_id)
        if not ref_file.exists():
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
        index = self._load_index()

        filename = generate_reference_filename(
            test.model_id,
            library_name=self.config.library_name,
            path_abbreviations=self.config.path_abbreviations or None,
        )
        ref_file = self.ref_dir / filename

        # Build the reference JSON
        # All variables share the same time vector (Modelica spec).
        # Use the time from the first variable and downsample once.
        shared_time = result.variables[0].time
        shared_time_list, _ = _downsample(shared_time, shared_time)
        # Compute downsample indices so all variables are sampled identically
        ds_indices = _downsample_indices(len(shared_time))

        variables = []
        for var in result.variables:
            values_ds = var.values[ds_indices] if ds_indices is not None else var.values
            values_list = _to_json_list(values_ds)
            expr = ""
            if var.index - 1 < len(test.x_expressions):
                expr = test.x_expressions[var.index - 1]

            variables.append({
                "index": var.index,
                "expression": expr,
                "values": values_list,
            })

        ref_data = {
            "model_id": test.model_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "simulation": {
                "stop_time": test.stop_time,
                "tolerance": test.tolerance,
                "method": test.method,
                "number_of_intervals": test.number_of_intervals,
            },
            "n_vars": test.n_vars,
            "time": shared_time_list,
            "variables": variables,
        }

        # Include simulation statistics if available
        if result.statistics:
            ref_data["statistics"] = result.statistics

        ref_file.write_text(
            _compact_json(ref_data) + "\n", encoding="utf-8"
        )

        # Update index
        index[test.model_id] = {
            "filename": filename,
            "n_vars": test.n_vars,
            "last_updated": ref_data["last_updated"],
        }
        self._index = index
        self._save_index()

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
        index = self._load_index()
        return sorted(index.keys())

    def export_json(self, output_path: Path):
        """Export all references as a single JSON file."""
        index = self._load_index()
        all_refs = {}
        for model_id in sorted(index.keys()):
            ref = self.get_reference(model_id)
            if ref:
                all_refs[model_id] = ref

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(all_refs, indent=2) + "\n", encoding="utf-8"
        )

    def export_csv(self, output_path: Path):
        """Export a summary CSV of all references."""
        index = self._load_index()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "model_id", "n_vars", "stop_time", "tolerance",
                "method", "last_updated", "filename",
            ])
            for model_id in sorted(index.keys()):
                ref = self.get_reference(model_id)
                if ref:
                    sim = ref.get("simulation", {})
                    writer.writerow([
                        model_id,
                        ref.get("n_vars", ""),
                        sim.get("stop_time", ""),
                        sim.get("tolerance", ""),
                        sim.get("method", ""),
                        ref.get("last_updated", ""),
                        index[model_id]["filename"],
                    ])


def _downsample_indices(n: int, max_points: int = 2000) -> Optional[np.ndarray]:
    """Compute downsample indices. Returns None if no downsampling needed."""
    if n <= max_points:
        return None
    indices = np.linspace(0, n - 1, max_points, dtype=int)
    indices[0] = 0
    indices[-1] = n - 1
    return indices


def _downsample(
    time: np.ndarray, values: np.ndarray, max_points: int = 2000
) -> tuple[list[float], list[float]]:
    """Downsample a time series to at most max_points, always keeping first/last."""
    indices = _downsample_indices(len(time), max_points)
    if indices is None:
        return _to_json_list(time), _to_json_list(values)
    return _to_json_list(time[indices]), _to_json_list(values[indices])


def _to_json_list(arr: np.ndarray) -> list[float]:
    """Convert numpy array to JSON-serializable list of Python floats."""
    return [float(v) for v in arr]


def _compact_json(data: dict) -> str:
    """Serialize reference JSON with number arrays on single lines.

    Keeps the structural indentation (indent=2) but collapses lists of
    numbers (time, values) onto one line for readability.
    """
    import re
    text = json.dumps(data, indent=2)
    # Match JSON arrays that contain only numbers (int, float, scientific notation)
    # These are the "time" and "values" arrays.
    def _collapse_array(m: re.Match) -> str:
        # Remove internal newlines and extra whitespace
        content = m.group(0)
        collapsed = re.sub(r'\s*\n\s*', ' ', content)
        return collapsed
    # Pattern: [ followed by lines of numbers/commas, ending with ]
    text = re.sub(
        r'\[\s*\n\s*-?[\d.][\s\S]*?\n\s*\]',
        _collapse_array,
        text,
    )
    return text
