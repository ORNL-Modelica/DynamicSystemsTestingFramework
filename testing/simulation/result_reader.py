"""Read Dymola .mat result files and extract unitTests.x[i] trajectories."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from .dymola_runner import BatchManifest

logger = logging.getLogger(__name__)


@dataclass
class VariableResult:
    """Time series for a single tracked variable."""
    index: int  # 1-based
    time: np.ndarray
    values: np.ndarray


@dataclass
class TestResult:
    """Results from a single test simulation."""
    model_id: str
    success: bool
    variables: list[VariableResult] = field(default_factory=list)
    error_message: Optional[str] = None
    statistics: Optional[dict] = None


def _read_dymola_mat(mat_path: Path) -> Optional[dict]:
    """Read a Dymola-format .mat file.

    Dymola uses a specific MAT4 format with:
    - 'Aclass': metadata matrix
    - 'name': character matrix of variable names
    - 'data_1': parameter/constant values
    - 'data_2': trajectory values (time-varying)
    - 'dataInfo': mapping from name index to data matrix and column

    Returns dict with variable names as keys and (time, values) tuples.
    """
    try:
        from scipy.io import loadmat
    except ImportError:
        raise ImportError("scipy is required for reading .mat files: pip install scipy")

    try:
        mat = loadmat(str(mat_path), squeeze_me=False)
    except Exception as e:
        logger.error("Failed to read %s: %s", mat_path, e)
        return None

    # Extract variable names from the 'name' matrix
    # Names are stored as a character array (each row is a variable name)
    if 'name' not in mat:
        logger.error("No 'name' matrix in %s", mat_path)
        return None

    name_matrix = mat['name']
    # Each row is a null-padded character array
    var_names = []
    for row in name_matrix:
        name = ''.join(chr(c) for c in row).strip('\x00').strip()
        var_names.append(name)

    # dataInfo maps each variable to its data source
    # dataInfo[i] = [data_matrix_index, column_index, interpolation, ...]
    # data_matrix_index: 1 = data_1, 2 = data_2
    # column_index: 1-based, negative means negate values
    data_info = mat.get('dataInfo', None)
    data_1 = mat.get('data_1', None)
    data_2 = mat.get('data_2', None)

    if data_info is None or data_2 is None:
        logger.error("Missing dataInfo or data_2 in %s", mat_path)
        return None

    # Time is always the first row of data_2
    time = data_2[0, :]

    result = {}
    for i, name in enumerate(var_names):
        info = data_info[i]
        data_matrix_idx = int(info[0])
        col_idx = int(info[1])
        negate = col_idx < 0
        col = abs(col_idx) - 1  # Convert to 0-based

        if data_matrix_idx == 1 and data_1 is not None:
            # Constant/parameter value — replicate across time
            val = data_1[col, :]
            if len(val) == 1:
                values = np.full_like(time, val[0])
            else:
                values = val
        elif data_matrix_idx == 2:
            values = data_2[col, :]
        else:
            continue

        if negate:
            values = -values

        result[name] = (time, values)

    return result


def _extract_unit_test_vars(
    mat_data: dict, n_vars: int
) -> list[VariableResult]:
    """Extract unitTests.x[1..n] from parsed mat data."""
    results = []
    for i in range(1, n_vars + 1):
        var_name = f"unitTests.x[{i}]"
        if var_name in mat_data:
            time, values = mat_data[var_name]
            results.append(VariableResult(index=i, time=time, values=values))
        else:
            logger.warning("Variable %s not found in results", var_name)
    return results


def read_results(
    manifests: list[BatchManifest],
    tests: list[TestModel],
    config: Config,
) -> dict[str, TestResult]:
    """Read simulation results from .mat files produced by batch runs.

    Uses run results from manifests to skip tests that failed or timed out.
    Returns dict of model_id -> TestResult.
    """
    # Build lookup: model_id -> TestModel
    test_lookup = {t.model_id: t for t in tests}
    results: dict[str, TestResult] = {}

    # Build lookup of run results if available (from per-test execution)
    run_results = {}
    for manifest in manifests:
        for rr in manifest.results:
            run_results[rr.model_id] = rr

    for manifest in manifests:
        for test_key, model_id in manifest.manifest.items():
            # Check if we already know this test failed at the simulation stage
            rr = run_results.get(model_id)
            stats = rr.statistics if rr else None
            if rr and not rr.success:
                results[model_id] = TestResult(
                    model_id=model_id,
                    success=False,
                    error_message=rr.error_message or "Simulation failed",
                    statistics=stats,
                )
                continue

            mat_path = manifest.mat_file(test_key)
            test_model = test_lookup.get(model_id)
            n_vars = test_model.n_vars if test_model else 1

            if not mat_path.exists():
                results[model_id] = TestResult(
                    model_id=model_id,
                    success=False,
                    error_message=f"Result file not found: {mat_path}",
                    statistics=stats,
                )
                continue

            mat_data = _read_dymola_mat(mat_path)
            if mat_data is None:
                results[model_id] = TestResult(
                    model_id=model_id,
                    success=False,
                    error_message=f"Failed to parse: {mat_path}",
                    statistics=stats,
                )
                continue

            variables = _extract_unit_test_vars(mat_data, n_vars)
            results[model_id] = TestResult(
                model_id=model_id,
                success=True,
                variables=variables,
                statistics=stats,
            )

    return results


def read_last_results(
    tests: list[TestModel],
    config: Config,
) -> dict[str, TestResult]:
    """Read results from the most recent batch run in the work directory."""
    work_dir = config.work_dir
    if not work_dir.exists():
        return {}

    # Find all manifest files
    manifest_paths = sorted(work_dir.glob("batch_*_manifest.json"))
    if not manifest_paths:
        return {}

    manifests = [BatchManifest.load(p) for p in manifest_paths]
    return read_results(manifests, tests, config)
