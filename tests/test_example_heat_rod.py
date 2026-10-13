"""The shipped Modelica example's array model, `HeatRodTest`: the one test
of the library whose variable is an array other than `unitTests.x`, so a
criterion over an array family — Indices in the add form, the folded
criteria row (D152) — can be tried on what ships.

Without a simulator: what the spec entry holds, what its two shipped
baselines hold, that each baseline passes its own criteria, and that the
criteria fold as the tool folds them. The model itself is run on Dymola and
OpenModelica by the real-simulator test at the end."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from dstf.authoring.family_rows import Fold, fold_rows
from dstf.backends.types import TestResult, VariableResult
from dstf.cli import main
from dstf.comparison.comparator import compare_test
from dstf.comparison.tree_spec import spec_to_view
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests
from dstf.reporting.tree_rows import criteria_rows, leaves_of

EXAMPLE = Path(__file__).parent.parent / "examples" / "modelica" / "ModelicaTestingLib"
REFS = EXAMPLE / "Resources" / "ReferenceResults"
ROD = "ModelicaTestingLib.Examples.HeatRodTest"
ELEMENTS = [f"T[{i}]" for i in range(1, 21)]
#: The partitions the baselines were run for (a Windows Dymola was not).
PARTITIONS = ["Dymola/linux", "OpenModelica/linux"]


def _test():
    config = Config(config_file=REFS / "testing.json", backend_name="OpenModelica")
    (test,) = [t for t in discover_tests(config) if t.model_id == ROD]
    return test


def _entry() -> dict:
    spec = json.loads((REFS / "test_spec.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == ROD]
    return entry


def _reference(partition: str) -> dict:
    (found,) = [
        data
        for path in (REFS / partition).glob("ref_*.json")
        if (data := json.loads(path.read_text(encoding="utf-8")))["model_id"] == ROD
    ]
    return found


def test_the_model_is_declared_by_the_spec_and_reads_its_annotation():
    """No UnitTests component: the spec declares it, tracks the array by a
    pattern, and its settings are the model's own `experiment(...)`."""
    test = _test()
    assert (test.source, test.variable_patterns) == ("spec", ["T[*]"])
    assert (test.stop_time, test.output_interval, test.tolerance) == (50.0, 0.5, 1e-6)
    assert "HeatRodTest" in (EXAMPLE / "Examples" / "package.order").read_text().split()
    assert "simulation" not in _entry()


def test_the_entry_is_one_tube_per_element_and_one_criterion_of_its_own():
    """What Indices `1:20` writes — literal leaves, identical but for the
    variable — and after them a criterion on one element alone."""
    children = _entry()["metrics"]["children"]
    tubes, own = children[:20], children[20:]
    assert [leaf["variable"] for leaf in tubes] == ELEMENTS
    assert {json.dumps({**leaf, "variable": ""}, sort_keys=True) for leaf in tubes} == {
        json.dumps(
            {
                "metric": "tube",
                "variable": "",
                "tube_width_mode": "rel",
                "tube_rel": 0.01,
                "tube_min_width": 0.05,
            },
            sort_keys=True,
        )
    }
    assert [(leaf["metric"], leaf["variable"]) for leaf in own] == [("scalar", "T[10]")]


def test_the_criteria_fold_into_one_row_and_the_own_one_follows():
    view = spec_to_view(_test().metric_tree_spec)
    rows = criteria_rows(view, [leaf["variable"] for leaf in leaves_of(view)])
    fold, own = fold_rows(rows)
    assert isinstance(fold, Fold) and fold.name == "tube · T[1:20]"
    assert fold.variables == ELEMENTS and fold.crumbs == "and ›"
    assert not isinstance(own, Fold)
    assert (own["leaf"]["metric"], own["leaf"]["variable"]) == ("scalar", "T[10]")


@pytest.mark.parametrize("partition", PARTITIONS)
def test_each_shipped_baseline_holds_the_array_and_passes_its_own_criteria(partition):
    reference = _reference(partition)
    assert [v["name"] for v in reference["variables"]] == ELEMENTS
    time = np.asarray(reference["time"])
    assert (time[0], time[-1], len(time)) == (0.0, 50.0, 102)
    assert reference["sampling"]["setting"] == "all"
    assert reference["sampling"]["precision"] in ("single", "double")
    assert reference["statistics"]["translation"]["continuous_time_states"] == 20
    # The physics: hot at the left end, cold at the right, monotone between,
    # and close to the straight line conduction settles on.
    final = np.array([v["values"][-1] for v in reference["variables"]])
    assert np.all(np.diff(final) < 0) and 96 < final[0] < 100 and 20 < final[-1] < 24
    steady = 100 - 80 * (np.arange(1, 21) - 0.5) / 20
    assert np.max(np.abs(final - steady)) < 0.5

    result = TestResult(
        ROD,
        True,
        variables=[
            VariableResult(v["index"], time, np.asarray(v["values"]), name=v["name"])
            for v in reference["variables"]
        ],
    )
    comp = compare_test(_test(), result, reference)
    assert comp.passed and len(comp.variables) == 21
    assert all(v.passed for v in comp.variables)
    assert comp.warnings == []


@pytest.mark.parametrize("partition", PARTITIONS)
def test_each_shipped_baseline_is_the_solution_of_the_model_as_written(partition):
    """No test a machine without a simulator runs read the MODEL: with
    `T_hot=90` in the `.mo` every one of them passed, against baselines of
    a rod held at 100. The model is linear, so its solution is written
    out here from the parameters and equations its file holds, and each
    shipped baseline lies on it to the solver's tolerance."""
    import re

    source = (EXAMPLE / "Examples" / "HeatRodTest.mo").read_text(encoding="utf-8")
    for line in (
        "der(T[1]) = a*(2*T_hot - 3*T[1] + T[2])/dx^2;",
        "der(T[i]) = a*(T[i - 1] - 2*T[i] + T[i + 1])/dx^2;",
        "der(T[n]) = a*(T[n - 1] - 3*T[n] + 2*T_cold)/dx^2;",
        "Real T[n](each start=T_cold, each fixed=true)",
        "final parameter Real dx=L/n",
    ):
        assert line in source, line
    given = {
        m[1]: float(m[2])
        for m in re.finditer(r"parameter (?:Integer|Real) (\w+)=([-+.\deE]+)", source)
    }
    assert set(given) == {"n", "a", "L", "T_hot", "T_cold"}
    n, hot, cold = int(given["n"]), given["T_hot"], given["T_cold"]
    k = given["a"] / (given["L"] / n) ** 2
    # T' = A T + b, the three equations above.
    A = k * (np.diag(np.full(n - 1, 1.0), -1) + np.diag(np.full(n - 1, 1.0), 1))
    A += np.diag(np.full(n, -2.0 * k))
    A[0, 0] = A[-1, -1] = -3.0 * k
    b = np.zeros(n)
    b[0], b[-1] = 2.0 * k * hot, 2.0 * k * cold
    steady = np.linalg.solve(A, -b)
    rates, modes = np.linalg.eigh(A)
    start = modes.T @ (np.full(n, cold) - steady)

    reference = _reference(partition)
    time = np.asarray(reference["time"])
    exact = steady[:, None] + modes @ (start[:, None] * np.exp(rates[:, None] * time))
    stored = np.array([v["values"] for v in reference["variables"]])
    assert stored.shape == exact.shape == (n, len(time))
    assert np.max(np.abs(stored - exact)) < 1e-3  # 6e-5 on both simulators


def test_the_windows_dymola_partition_has_no_baseline_of_it_yet():
    """Said, not hidden: the model was added with baselines from the two
    simulators run for it. On Windows Dymola it reads NO_REF until someone
    accepts one there."""
    held = {
        json.loads(p.read_text(encoding="utf-8"))["model_id"]
        for p in (REFS / "Dymola" / "windows").glob("ref_*.json")
    }
    assert ROD not in held and len(held) == 11


# ---------------------------------------------------------------------------
# Real simulators
# ---------------------------------------------------------------------------


def _dymola_available() -> bool:
    from test_live_results_real import _dymola_available as available

    return available()


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "OpenModelica",
            marks=[
                pytest.mark.ompython,
                pytest.mark.skipif(shutil.which("omc") is None, reason="needs omc"),
            ],
        ),
        pytest.param("Dymola", marks=[pytest.mark.dymola]),
    ],
)
def test_the_model_runs_and_passes_against_its_shipped_baseline(
    tmp_path, capsys, backend
):
    if backend == "Dymola" and not _dymola_available():
        pytest.skip("needs Dymola")
    pytest.importorskip("nicegui")
    lib = tmp_path / "ModelicaTestingLib"
    shutil.copytree(EXAMPLE, lib)
    cfg = lib / "Resources" / "ReferenceResults" / "testing.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    cfg.write_text(json.dumps({**data, "backend": backend}), encoding="utf-8")
    capsys.readouterr()
    rc = main(
        ["--config", str(cfg), "run", "--work-dir", str(tmp_path / "work")]
        + ["--no-browser", "--filter", ROD]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "Results: 1/1 passed" in out, out
    verdicts = json.loads((tmp_path / "work" / "verdicts.json").read_text("utf-8"))
    (rod,) = verdicts["tests"]
    assert (rod["model_id"], rod["status"]) == (ROD, "PASS")
