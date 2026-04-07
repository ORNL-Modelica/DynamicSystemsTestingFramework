"""Parse runAll_Dymola.mos to extract simulation parameters for each test model."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SimParams:
    """Simulation parameters extracted from a simulateModel() call."""
    model_id: str
    stop_time: Optional[float] = None
    tolerance: Optional[float] = None
    method: Optional[str] = None
    number_of_intervals: Optional[int] = None
    output_interval: Optional[float] = None
    result_file: Optional[str] = None


# Pattern to match: simulateModel("Model.Path", key=value, ...)
_SIMULATE_RE = re.compile(
    r'simulateModel\(\s*"([^"]+)"(.*?)\)\s*;', re.DOTALL
)

# Patterns for individual named parameters
_PARAM_PATTERNS = {
    "stop_time": re.compile(r'stopTime\s*=\s*([0-9eE.+-]+)', re.IGNORECASE),
    "tolerance": re.compile(r'tolerance\s*=\s*([0-9eE.+-]+)', re.IGNORECASE),
    "method": re.compile(r'method\s*=\s*"([^"]+)"', re.IGNORECASE),
    "number_of_intervals": re.compile(
        r'numberOfIntervals\s*=\s*([0-9]+)', re.IGNORECASE
    ),
    "output_interval": re.compile(
        r'outputInterval\s*=\s*([0-9eE.+-]+)', re.IGNORECASE
    ),
    "result_file": re.compile(r'resultFile\s*=\s*"([^"]+)"', re.IGNORECASE),
}


def parse_mos_file(path: Path) -> dict[str, SimParams]:
    """Parse a .mos file and return a dict of model_id -> SimParams.

    Duplicate model_ids are deduplicated (last occurrence wins).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    results: dict[str, SimParams] = {}

    for match in _SIMULATE_RE.finditer(text):
        model_id = match.group(1)
        params_text = match.group(2)

        params = SimParams(model_id=model_id)

        for field_name, pattern in _PARAM_PATTERNS.items():
            m = pattern.search(params_text)
            if m:
                value = m.group(1)
                if field_name in ("stop_time", "tolerance", "output_interval"):
                    setattr(params, field_name, float(value))
                elif field_name == "number_of_intervals":
                    setattr(params, field_name, int(value))
                else:
                    setattr(params, field_name, value)

        results[model_id] = params

    return results
