"""A test's BASE tolerance is one finite number, judged once, before anything
else a comparison decides — through the CLI, over a real Python-backend
project: every shape a JSON file can hold there, with and without a stored
baseline, on the tree path, the flat path and a baseline-free flat test."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET

import pytest

from dstf.cli import main

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"

#: What a JSON file can hold as a base tolerance that is no finite number.
SHAPES = {
    "pair": {"lower": 0.1, "upper": 0.2},
    "one-sided": {"lower": 0.1},
    "digits": "0.5",
    "string": "abc",
    "bool": True,
    "list": [1],
}
#: Python's ``json`` reads these three literals; no other shape is non-finite.
NON_FINITE = {"nan": "NaN", "inf": "Infinity", "huge-int": "1" + "0" * 400}

#: A test's criteria, as its spec entry holds them: (model, entry fields).
KINDS = {
    "tree": (RAMP, {"metrics": {"metric": "nrmse", "variable": "x"}}),
    "flat": (RAMP, {}),
    "baseline-free flat": (CSV, {}),
    "baseline-free tree": (
        CSV,
        {"metrics": {"metric": "range", "variable": "x", "min_value": 0.99}},
    ),
}


def sentence(source: str, value: object) -> str:
    text = repr(value)
    if len(text) > 60:
        text = text[:60] + "…"
    return (
        "the base tolerance must be a number; a lower/upper pair belongs on a "
        f"criterion ({source} is {text})"
    )


SPEC = "test_spec.json comparison.tolerance"
BASELINE = "the baseline's comparison.tolerance"


class Project:
    """The example library, run once; each test starts from its files as
    they were after that run."""

    def __init__(self, config, pristine):
        self.config = config
        self.ref_root = config.config_file.parent
        self.spec = self.ref_root / "test_spec.json"
        self._pristine = pristine

    def reset(self) -> None:
        shutil.rmtree(self.ref_root)
        shutil.copytree(self._pristine, self.ref_root)

    def dstf(self, command: str, *extra: str) -> int:
        return main(
            ["--config", str(self.config.config_file), command]
            + ["--work-dir", str(self.config.work_dir), *extra]
        )

    def reference(self, model_id: str):
        for path in sorted(self.ref_root.rglob("ref_*.json")):
            if json.loads(path.read_text(encoding="utf-8"))["model_id"] == model_id:
                return path
        return None

    def edit_spec(self, model_id: str, raw_tolerance: str | None, **fields) -> None:
        """Set ``model_id``'s entry: ``fields`` over it, and its base
        tolerance as the JSON text ``raw_tolerance`` (a literal such as
        ``NaN`` has no Python value that ``json.dumps`` writes back)."""
        data = json.loads(self.spec.read_text(encoding="utf-8"))
        (entry,) = [e for e in data["tests"] if e["model"] == model_id]
        entry.update(fields)
        if raw_tolerance is not None:
            entry.setdefault("comparison", {})["tolerance"] = "@@BASE@@"
        text = json.dumps(data, indent=2)
        if raw_tolerance is not None:
            text = text.replace('"@@BASE@@"', raw_tolerance)
        self.spec.write_text(text, encoding="utf-8")

    def edit_reference(self, model_id: str, raw_tolerance: str) -> None:
        path = self.reference(model_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("comparison", {})["tolerance"] = "@@BASE@@"
        path.write_text(
            json.dumps(data).replace('"@@BASE@@"', raw_tolerance), encoding="utf-8"
        )

    def junit(self, tmp_path) -> tuple[int, dict[str, tuple[str, str | None]]]:
        """``compare`` to a JUnit file: the exit code and, per test, what its
        case holds — ``("failure", message)``, or ``("pass", None)``."""
        out = tmp_path / "junit.xml"
        rc = self.dstf("compare", "--report-format", "junit", "--output", str(out))
        cases = {}
        for case in ET.parse(out).getroot().iter("testcase"):
            child = next(iter(case), None)
            name = f"{case.get('classname')}.{case.get('name')}"
            cases[name] = (
                ("pass", None) if child is None else (child.tag, child.get("message"))
            )
        return rc, cases


@pytest.fixture(scope="module")
def _ran(tmp_path_factory):
    from conftest import python_example_copy

    root = tmp_path_factory.mktemp("base_tolerance")
    config, _ = python_example_copy(root)
    pristine = root / "pristine"
    project = Project(config, pristine)
    with pytest.MonkeyPatch.context() as m:
        m.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
        assert project.dstf("run") == 0
    shutil.copytree(project.ref_root, pristine)
    return project


@pytest.fixture
def project(_ran, monkeypatch):
    monkeypatch.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
    _ran.reset()
    yield _ran
    _ran.reset()


def _case(cases: dict, model_id: str):
    (found,) = [v for k, v in cases.items() if k.endswith(model_id.split(".")[-1])]
    return found


def test_the_project_compares_clean_as_it_is(project, tmp_path):
    rc, cases = project.junit(tmp_path)
    assert rc == 0 and set(cases.values()) == {("pass", None)}


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_spec_base_that_is_no_number_refuses_the_test(
    project, tmp_path, shape, baseline, kind
):
    model_id, fields = KINDS[kind]
    bad = SHAPES[shape]
    project.edit_spec(model_id, json.dumps(bad), **fields)
    if not baseline:
        project.reference(model_id).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(SPEC, bad))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("shape", list(NON_FINITE))
def test_a_spec_base_that_is_not_finite_refuses_the_test(
    project, tmp_path, shape, baseline, kind
):
    model_id, fields = KINDS[kind]
    raw = NON_FINITE[shape]
    project.edit_spec(model_id, raw, **fields)
    if not baseline:
        project.reference(model_id).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(SPEC, json.loads(raw)))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("shape", [*SHAPES, *NON_FINITE])
def test_a_baselines_base_that_is_no_number_refuses_the_test(
    project, tmp_path, shape, kind
):
    model_id, fields = KINDS[kind]
    raw = NON_FINITE.get(shape) or json.dumps(SHAPES[shape])
    project.edit_spec(model_id, None, **fields)
    project.edit_reference(model_id, raw)
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(BASELINE, json.loads(raw)))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_number_in_the_spec_shadows_whatever_the_baseline_holds(
    project, tmp_path, kind
):
    """The base in effect is the one judged: the spec's number is what the
    test is scored with, so the baseline's is never read."""
    model_id, fields = KINDS[kind]
    project.edit_spec(model_id, "0.5", **fields)
    project.edit_reference(model_id, '{"lower": 0.1, "upper": 0.2}')
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("pass", None) and rc == 0


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("raw", ["0", "-1", "0.5", "null"])
def test_a_number_or_no_base_is_never_refused(project, tmp_path, raw, baseline):
    """A baseline-free test reads no tolerance: zero, a negative number and
    an unset base score it exactly as a base of 0.5 does."""
    project.edit_spec(CSV, raw)
    if not baseline:
        project.reference(CSV).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, CSV) == ("pass", None) and rc == 0
