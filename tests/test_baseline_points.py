"""``baseline_points`` (D153): a stored baseline keeps every point its run
wrote unless a number is given, and its reference JSON says what was done
under ``sampling``."""

from __future__ import annotations

import hashlib
import inspect
import json

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.cli.main import main
from dstf.comparison.comparator import compare_all
from dstf.config import Config, ConfigError
from dstf.storage.reference_store import ReferenceStore, _downsample

M = "Lib.Run"

#: What a refused value is told, after the key and the value given.
FORMS = 'the points a baseline keeps are "all" or a whole number of 2 or more'


def _config(tmp_path, **keys) -> Config:
    """A project whose ``testing.json`` holds ``keys``."""
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    (root / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "backend": "Python",
                "reference_root": "refs",
                **keys,
            }
        ),
        encoding="utf-8",
    )
    return Config(config_file=root / "testing.json", work_dir=tmp_path / "work")


def _run(rows: int, events: tuple[int, ...] = ()) -> TestResult:
    """``rows`` samples an eighth of a second apart. Each row in ``events``
    repeats the time of the row before it, as a Modelica event is written.
    ``i`` is the row's own number — the values a baseline stores ARE the rows
    it kept — and ``x`` jumps by 100 at every event. Every number is a few
    binary digits, so a stored value is the run's, bit for bit."""
    step = np.ones(rows)
    step[0] = 0.0
    step[list(events)] = 0.0
    time = np.cumsum(step) / 8.0
    index = np.arange(rows, dtype=float)
    x = index / 4.0 + 100.0 * (np.cumsum(step == 0.0) - 1)
    return TestResult(
        model_id=M,
        success=True,
        variables=[
            VariableResult(index=1, time=time, values=index, name="i"),
            VariableResult(index=2, time=time, values=x, name="x"),
        ],
    )


def _stored(config: Config, result: TestResult, test=None) -> dict:
    """``result`` accepted as ``test``'s baseline: the reference JSON."""
    store = ReferenceStore(config)
    test = test or make_test_model(M, variable_patterns=["i", "x"])
    assert store.store_reference(test, result) is True
    reference = store.get_reference(M)
    assert reference is not None
    return reference


def _kept(reference: dict) -> list[int]:
    """The rows of `_run` a reference kept, read off its ``i``."""
    (i,) = [v for v in reference["variables"] if v["name"] == "i"]
    return [int(v) for v in i["values"]]


def _holds_rows(reference: dict, result: TestResult, rows: list[int]) -> bool:
    """Whether the reference holds exactly ``rows`` of ``result``, in order,
    for its time and every variable."""
    time = result.variables[0].time
    return reference["time"] == [float(time[r]) for r in rows] and all(
        stored["values"] == [float(var.values[r]) for r in rows]
        for stored, var in zip(reference["variables"], result.variables, strict=True)
    )


EVENTS = tuple(range(100, 5000, 123))


# -- the default: every point -------------------------------------------------


def test_the_default_stores_every_point_in_order_and_scores_zero_against_itself(
    tmp_path,
):
    config = _config(tmp_path)
    result = _run(5000, EVENTS)
    test = make_test_model(M, variable_patterns=["i", "x"])

    reference = _stored(config, result, test)

    assert config.baseline_points == "all"
    assert len(reference["time"]) == 5000
    assert _holds_rows(reference, result, list(range(5000)))
    # The events are there: a time written twice, 40 times.
    time = reference["time"]
    assert sum(a == b for a, b in zip(time, time[1:])) == len(EVENTS) == 40

    (comp,) = compare_all([test], {M: result}, ReferenceStore(config))
    assert comp.passed
    assert [(v.name, v.nrmse) for v in comp.variables] == [("i", 0.0), ("x", 0.0)]


def test_all_written_out_is_the_default(tmp_path):
    reference = _stored(_config(tmp_path, baseline_points="all"), _run(5000, EVENTS))
    assert _kept(reference) == list(range(5000))


# -- a number: thinned as the cap of 2000 thinned ------------------------------

#: ``(rows, events, baseline_points)`` -> how many rows the function this
#: replaced kept, and the SHA-256 of their list as JSON. Made by running
#: ``_downsample(time, values, max_points=N)`` as it was BEFORE this key
#: existed — commit 53570e0, where its default was 2000 — over `_run`.
GOLDEN = {
    "no events": ((5000, (), 2000), 2000, "72860352a7c971c0"),
    "events": ((5000, EVENTS, 2000), 2000, "8cef8faac66529d4"),
    "more events than points": (
        (5000, tuple(range(2, 5000, 3)), 2000),
        2000,
        "010baaeded207033",
    ),
    "a small number": ((5000, (700, 701, 3000), 50), 50, "b1639682dc0b73d8"),
    "a run shorter than the number": (
        (1500, (300, 900), 2000),
        1500,
        "db4d61b3884f4ea3",
    ),
}


@pytest.mark.parametrize("case", list(GOLDEN))
def test_a_number_thins_exactly_as_the_cap_of_2000_did(tmp_path, case):
    (rows, events, points), count, digest = GOLDEN[case]
    result = _run(rows, events)

    reference = _stored(_config(tmp_path, baseline_points=points), result)

    kept = _kept(reference)
    assert len(kept) == count
    assert hashlib.sha256(json.dumps(kept).encode()).hexdigest()[:16] == digest
    # Both ends, and one set of rows for the time and every variable.
    assert (kept[0], kept[-1]) == (0, rows - 1)
    assert _holds_rows(reference, result, kept)


def test_a_thinned_baseline_keeps_its_events_while_they_fit(tmp_path):
    reference = _stored(_config(tmp_path, baseline_points=500), _run(5000, EVENTS))
    kept = _kept(reference)
    assert len(kept) == 500
    assert all({row - 1, row} <= set(kept) for row in EVENTS)


@pytest.mark.parametrize("points", [3000, 3001, 10**6])
def test_a_number_at_or_over_the_runs_rows_stores_them_all(tmp_path, points):
    """It never invents a point: a run with fewer rows is stored whole."""
    result = _run(3000, (300, 900))
    reference = _stored(_config(tmp_path, baseline_points=points), result)
    assert _holds_rows(reference, result, list(range(3000)))


@pytest.mark.parametrize("events", [(), (700,), EVENTS])
def test_two_keeps_the_two_ends(tmp_path, events):
    reference = _stored(_config(tmp_path, baseline_points=2), _run(5000, events))
    assert _kept(reference) == [0, 4999]


def test_every_thinning_says_its_number():
    """The cap of 2000 was a default argument nobody decided."""
    assert (
        inspect.signature(_downsample).parameters["max_points"].default
        is inspect.Parameter.empty
    )


# -- testing.json: "all" or a whole number of 2 or more ------------------------

REFUSED = [True, False, 2000.0, 1.5, 0, 1, -5, "ALL", "2000", "", None, [2000], {}]


@pytest.mark.parametrize("value", ["all", 2, 2000])
def test_testing_json_takes_all_or_a_whole_number_from_two(tmp_path, value):
    config = _config(tmp_path, baseline_points=value)
    assert config.baseline_points == value
    assert type(config.baseline_points) is type(value)


@pytest.mark.parametrize("value", REFUSED, ids=repr)
def test_testing_json_refuses_anything_else_with_one_sentence(tmp_path, value):
    with pytest.raises(ConfigError) as refused:
        _config(tmp_path, baseline_points=value)
    assert str(refused.value) == (
        f"testing.json baseline_points is {json.dumps(value)}: {FORMS}"
    )


def test_a_refused_testing_json_is_one_line_and_exit_two(tmp_path, capsys):
    with pytest.raises(ConfigError):
        _config(tmp_path, baseline_points=1)
    config_file = tmp_path / "project" / "testing.json"
    assert main(["--config", str(config_file), "discover"]) == 2
    assert capsys.readouterr().err == (
        f"Config error: testing.json baseline_points is 1: {FORMS}\n"
    )


# -- the reference JSON records what was done ---------------------------------


@pytest.mark.parametrize(
    ("keys", "rows", "sampling"),
    [
        ({}, 5000, {"setting": "all", "source_points": 5000, "stored_points": 5000}),
        (
            {"baseline_points": 2000},
            5000,
            {"setting": 2000, "source_points": 5000, "stored_points": 2000},
        ),
        (
            {"baseline_points": 2000},
            1500,
            {"setting": 2000, "source_points": 1500, "stored_points": 1500},
        ),
    ],
    ids=["all", "thinned", "a number over the rows"],
)
def test_the_reference_records_its_sampling(tmp_path, keys, rows, sampling):
    reference = _stored(_config(tmp_path, **keys), _run(rows, (300, 900)))
    assert reference["sampling"] == sampling
    assert len(reference["time"]) == sampling["stored_points"]
    # Metadata, so above the data: the file's last two keys stay its arrays.
    assert list(reference)[-2:] == ["time", "variables"]


def test_a_reference_without_sampling_loads_and_scores_as_before(tmp_path):
    """Every reference written before the key has none: old data, read as it
    always was."""
    config = _config(tmp_path, baseline_points=2000)
    result = _run(5000, EVENTS)
    test = make_test_model(M, variable_patterns=["i", "x"])
    assert "sampling" in _stored(config, result, test)
    drifted = _run(5000, EVENTS)
    drifted.variables[1].values = drifted.variables[1].values * 1.001

    def score() -> list[tuple]:
        (comp,) = compare_all([test], {M: drifted}, ReferenceStore(config))
        return [(comp.passed, v.name, v.passed, v.nrmse) for v in comp.variables]

    with_sampling = score()
    (path,) = config.reference_dir.glob("ref_*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["sampling"]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    assert "sampling" not in ReferenceStore(config).get_reference(M)
    assert score() == with_sampling
    assert with_sampling[1][3] > 0.0  # a comparison that measured something


def test_the_key_changes_an_existing_baseline_only_when_it_is_accepted_again(
    tmp_path,
):
    result = _run(5000, EVENTS)
    assert _stored(_config(tmp_path), result)["sampling"]["stored_points"] == 5000
    (path,) = _config(tmp_path).reference_dir.glob("ref_*.json")
    before = path.read_bytes()

    config = _config(tmp_path, baseline_points=50)
    test = make_test_model(M, variable_patterns=["i", "x"])
    (comp,) = compare_all([test], {M: result}, ReferenceStore(config))
    assert comp.passed and path.read_bytes() == before

    again = _stored(config, result, test)
    assert again["sampling"] == {
        "setting": 50,
        "source_points": 5000,
        "stored_points": 50,
    }
    assert len(again["time"]) == 50
