"""``baseline_points`` (D153): a stored baseline keeps every point its run
wrote unless a number is given — in ``testing.json`` for the project, on a
``test_spec.json`` entry for one test — its reference JSON says what was
done under ``sampling``, and an accept says so when the file it wrote is
large."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_test_model
from dstf.authoring.session import Session
from dstf.backends.types import TestResult, VariableResult
from dstf.cli.main import main
from dstf.comparison.comparator import compare_all
from dstf.config import Config, ConfigError
from dstf.discovery.test_registry import discover_tests, merge_tests
from dstf.storage.accept import LARGE_BASELINE_BYTES, accept_run_results
from dstf.storage.baseline import ReferenceFile
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


def _view(reference: ReferenceFile) -> dict:
    """What a reader of a reference gets, keyed as its file is: the numbers
    the codec hands on (not the digits the file spells)."""
    return {
        "time": reference.baseline.time,
        "sampling": dataclasses.asdict(reference.sampling)
        if reference.sampling is not None
        else None,
        "variables": [
            {"name": v.name, "values": v.values} for v in reference.baseline.variables
        ],
    }


def _stored(config: Config, result: TestResult, test=None) -> dict:
    """``result`` accepted as ``test``'s baseline, as a reader gets it."""
    store = ReferenceStore(config)
    test = test or make_test_model(M, variable_patterns=["i", "x"])
    assert store.store_reference(test, result) is True
    reference = store.get_reference(M)
    assert reference is not None
    return _view(reference)


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
    path = tmp_path / "project" / "testing.json"
    assert str(refused.value) == (
        f"{path}: baseline_points is {json.dumps(value)}: {FORMS}"
    )


def test_a_refused_testing_json_is_one_line_and_exit_two(tmp_path, capsys):
    with pytest.raises(ConfigError):
        _config(tmp_path, baseline_points=1)
    config_file = tmp_path / "project" / "testing.json"
    assert main(["--config", str(config_file), "discover"]) == 2
    assert capsys.readouterr().err == (
        f"Config error: {config_file}: baseline_points is 1: {FORMS}\n"
    )


# -- the reference JSON records what was done ---------------------------------


@pytest.mark.parametrize(
    ("keys", "rows", "sampling"),
    [
        (
            {},
            5000,
            {
                "setting": "all",
                "source_points": 5000,
                "stored_points": 5000,
                "precision": "double",
            },
        ),
        (
            {"baseline_points": 2000},
            5000,
            {
                "setting": 2000,
                "source_points": 5000,
                "stored_points": 2000,
                "precision": "double",
            },
        ),
        (
            {"baseline_points": 2000},
            1500,
            {
                "setting": 2000,
                "source_points": 1500,
                "stored_points": 1500,
                "precision": "double",
            },
        ),
    ],
    ids=["all", "thinned", "a number over the rows"],
)
def test_the_reference_records_its_sampling(tmp_path, keys, rows, sampling):
    config = _config(tmp_path, **keys)
    reference = _stored(config, _run(rows, (300, 900)))
    assert reference["sampling"] == sampling
    assert len(reference["time"]) == sampling["stored_points"]
    # Metadata, so above the data: the file's last two keys stay its arrays.
    (path,) = config.reference_dir.glob("ref_*.json")
    assert list(json.loads(path.read_text(encoding="utf-8")))[-2:] == [
        "time",
        "variables",
    ]


def test_a_reference_without_sampling_loads_and_scores_as_before(tmp_path):
    """Every reference written before the key has none: old data, read as it
    always was."""
    config = _config(tmp_path, baseline_points=2000)
    result = _run(5000, EVENTS)
    test = make_test_model(M, variable_patterns=["i", "x"])
    assert _stored(config, result, test)["sampling"] is not None
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

    assert ReferenceStore(config).get_reference(M).sampling is None
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
        "precision": "double",
    }
    assert len(again["time"]) == 50


# -- test_spec.json: a test's own value ---------------------------------------

# -- a baseline holds its run's own numbers -----------------------------------
#
# Dymola writes single-precision result files. A float32 written at 7
# significant digits is a different float32 more often than not, and read
# as a double it is a different number than the run holds every time:
# a baseline accepted from a run scored up to 6e-2 against that same run.


def _single(time, **variables) -> TestResult:
    """A single-precision run: every array float32, as a Dymola file's."""
    time = np.asarray(time, dtype=np.float32)
    return TestResult(
        model_id=M,
        success=True,
        variables=[
            VariableResult(
                index=i, time=time, values=np.asarray(v, dtype=np.float32), name=name
            )
            for i, (name, v) in enumerate(variables.items(), start=1)
        ],
    )


def _bits(values) -> bytes:
    return np.asarray(values, dtype=np.float32).tobytes()


def _awkward(rows: int = 4000) -> TestResult:
    """Random single-precision numbers of every size, with the ones a
    round trip gets wrong first: subnormals, both zeros, the largest and
    the smallest; and a time vector with event rows (a time written
    twice)."""
    rng = np.random.default_rng(20261012)
    spread = (rng.standard_normal(rows) * 10.0 ** rng.uniform(-30, 30, rows)).astype(
        np.float32
    )
    tiny = np.float32(1e-45)  # the smallest subnormal
    edge = np.array(
        [0.0, -0.0, tiny, -tiny, 7e-42, 1.17549435e-38, 3.4028235e38, -3.4028235e38],
        dtype=np.float32,
    )
    spread[: edge.size] = edge
    # the measured case: a level of 130450 over a range of 0.4
    level = (130450.0 + 0.4 * rng.random(rows)).astype(np.float32)
    step = rng.uniform(1e-4, 3.0, rows).astype(np.float32)
    step[::97] = 0.0  # an event: this row repeats the time of the last
    time = np.cumsum(step, dtype=np.float32)
    return _single(time, spread=spread, level=level, i=np.arange(rows))


def test_a_single_precision_run_round_trips_bit_for_bit(tmp_path):
    result = _awkward()
    reference = _stored(
        _config(tmp_path), result, make_test_model(M, variable_patterns=["*"])
    )
    assert reference["sampling"]["precision"] == "single"
    time = result.variables[0].time
    assert sum(a == b for a, b in zip(time, time[1:])) >= 40  # events are in it
    assert _bits(reference["time"]) == time.tobytes()
    for stored, var in zip(reference["variables"], result.variables, strict=True):
        assert _bits(stored["values"]) == var.values.tobytes(), var.name
        # ... and what is handed on IS that number, as a Python float:
        # not the double its 9 digits spell
        assert stored["values"] == var.values.astype(np.float64).tolist(), var.name
        assert all(type(v) is float for v in stored["values"][:5])


@pytest.mark.parametrize("points", ["all", 300], ids=["every point", "thinned"])
def test_a_single_precision_baseline_scores_exactly_zero_against_its_run(
    tmp_path, points
):
    config = _config(tmp_path, baseline_points=points)
    result = _awkward()
    test = make_test_model(M, variable_patterns=["spread", "level", "i"])
    _stored(config, result, test)
    (comp,) = compare_all([test], {M: result}, ReferenceStore(config))
    assert comp.passed
    assert [(v.name, v.nrmse) for v in comp.variables] == [
        ("spread", 0.0),
        ("level", 0.0),
        ("i", 0.0),
    ]


def test_a_single_precision_value_is_written_with_nine_digits(tmp_path):
    """The fewest that identify a float32. `0.1f` is 0.100000001490116…:
    seven digits wrote `0.1`."""
    config = _config(tmp_path)
    _stored(config, _single([0.0, 1.0], x=[0.1, 16777217.0]))
    (path,) = config.reference_dir.glob("ref_*.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["variables"][0]["values"] == [0.100000001, 16777216.0]


def test_a_double_precision_run_is_stored_as_its_own_numbers(tmp_path):
    third = 1.0 / 3.0
    result = _run(8)
    result.variables[1].values = result.variables[1].values + third
    config = _config(tmp_path)
    reference = _stored(config, result)
    assert reference["sampling"]["precision"] == "double"
    # the double itself — 15 digits wrote another one — and no trip through
    # float32 on the way back
    assert reference["variables"][1]["values"][0] == third != float("%.15g" % third)
    assert reference["variables"][1]["values"][0] != float(np.float32(third))


def _awkward_doubles() -> TestResult:
    """Random doubles of every size, with the one a review measured: a
    signal of range 0.4 at a level of 130450 — where 15 digits put a
    stored value 2e-10 from the run's — and a time grid that is no round
    number (`0.1 + 0.2`)."""
    rng = np.random.default_rng(11)
    rows = 400
    time = np.cumsum(np.full(rows, 0.1)) + 0.2
    spread = rng.standard_normal(rows) * 10.0 ** rng.integers(-12, 13, rows)
    level = 130450.0 + 0.4 * rng.random(rows)
    return TestResult(
        model_id=M,
        success=True,
        variables=[
            VariableResult(index=i, time=time, values=v, name=name)
            for i, (name, v) in enumerate(
                {"spread": spread, "level": level}.items(), start=1
            )
        ],
    )


def test_a_double_precision_run_round_trips_bit_for_bit(tmp_path):
    result = _awkward_doubles()
    reference = _stored(
        _config(tmp_path), result, make_test_model(M, variable_patterns=["*"])
    )
    assert reference["sampling"]["precision"] == "double"
    assert reference["time"] == result.variables[0].time.tolist()
    for stored, var in zip(reference["variables"], result.variables, strict=True):
        assert np.asarray(stored["values"]).tobytes() == var.values.tobytes(), var.name


def test_a_double_precision_baseline_scores_exactly_zero_against_its_run(tmp_path):
    """It scored 6.7e-10 on the level signal: written at 15 digits, a
    stored double is another one than the run's."""
    config = _config(tmp_path)
    result = _awkward_doubles()
    test = make_test_model(M, variable_patterns=["spread", "level"])
    _stored(config, result, test)
    (comp,) = compare_all([test], {M: result}, ReferenceStore(config))
    assert comp.passed
    assert [(v.name, v.nrmse) for v in comp.variables] == [
        ("spread", 0.0),
        ("level", 0.0),
    ]


def test_one_double_array_among_single_ones_is_a_double_baseline(tmp_path):
    """The reader takes EVERY array of a `single` file through float32,
    which would move a double: the mark is for a run with none."""
    time = np.array([0.0, 0.5, 1.0])  # float64
    result = TestResult(
        model_id=M,
        success=True,
        variables=[
            VariableResult(
                index=1,
                time=time,
                values=np.array([0.1, 0.2, 0.3], dtype=np.float32),
                name="x",
            )
        ],
    )
    reference = _stored(
        _config(tmp_path), result, make_test_model(M, variable_patterns=["x"])
    )
    assert reference["sampling"]["precision"] == "double"
    # its single-precision values: nine digits, read as the doubles they spell
    assert reference["variables"][0]["values"] == [
        0.100000001,
        0.200000003,
        0.300000012,
    ]


def test_a_reference_without_the_precision_is_read_as_it_always_was(tmp_path):
    """Old data: every baseline stored before the field has none, holds
    seven digits, and is the JSON's own doubles."""
    config = _config(tmp_path)
    _stored(config, _single([0.0, 1.0], x=[0.1, 0.2]))
    (path,) = config.reference_dir.glob("ref_*.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["sampling"].pop("precision") == "single"
    raw["variables"][0]["values"] = [0.1, 0.2]  # as seven digits wrote them
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    reference = _view(ReferenceStore(config).get_reference(M))
    assert reference["variables"][0]["values"] == [0.1, 0.2]
    assert reference["variables"][0]["values"] != [
        float(np.float32(0.1)),
        float(np.float32(0.2)),
    ]


def test_every_reader_of_a_reference_file_reads_it_through_the_one_function(
    tmp_path,
):
    """The sibling-partition overlays and `soft-check add` read a
    reference file too: a single-precision one gives them the same
    numbers."""
    from dstf.reporting.overlay_loader import _baseline_to_overlay_vars
    from dstf.storage.baseline_codec import read_reference

    config = _config(tmp_path)
    _stored(config, _single([0.0, 1.0], x=[0.1, 0.2]))
    (path,) = config.reference_dir.glob("ref_*.json")
    baseline = read_reference(path).baseline
    assert baseline.variables[0].values == [
        float(np.float32(0.1)),
        float(np.float32(0.2)),
    ]
    overlay = _baseline_to_overlay_vars(baseline)["x"]
    assert list(overlay.values) == baseline.variables[0].values


#: What a single-precision run holds for 0.1 and 0.2, as Python floats —
#: not the doubles the nine digits a `single` file writes spell.
_AS_SINGLE = [float(np.float32(0.1)), float(np.float32(0.2))]


def _single_reference(tmp_path) -> tuple[Config, Path]:
    """A project with one `single` baseline of `M`, and that file."""
    config = _config(tmp_path)
    _stored(config, _single([0.0, 1.0], x=[0.1, 0.2]))
    (path,) = config.reference_dir.glob("ref_*.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["sampling"]["precision"] == "single"
    assert raw["variables"][0]["values"] == [0.100000001, 0.200000003] != _AS_SINGLE
    return config, path


def test_a_soft_check_file_is_read_through_the_one_function(tmp_path):
    """A `single` reference file copied in as a soft check was read as the
    doubles its digits spell: up to 5e-4 off the run on a signal at 1e5."""
    config, path = _single_reference(tmp_path)
    soft = config.reference_dir / "soft_checks" / path.stem / "other.json"
    soft.parent.mkdir(parents=True)
    soft.write_bytes(path.read_bytes())
    (baseline,) = ReferenceStore(config).get_soft_checks(M).values()
    assert baseline.variables[0].values == _AS_SINGLE
    assert baseline.time == [0.0, 1.0]


def test_a_companion_that_is_a_reference_file_is_read_through_it_too(tmp_path):
    """The Modelica example's `julia-mtk-*` companions point at another
    library's `ref_NNNN.json`: a `single` one drew the doubles."""
    from dstf.reporting.overlay_loader import load_overlays

    config, path = _single_reference(tmp_path)
    store = ReferenceStore(config)
    other = tmp_path / "other-library-ref.json"
    other.write_bytes(path.read_bytes())
    assert store.add_companion(M, "other", other, format="json")
    (overlay,) = [o for o in load_overlays(store, M) if o.role == "companion"]
    assert overlay.status == "loaded"
    assert overlay.variables["x"].values == _AS_SINGLE
    assert overlay.variables["x"].time == [0.0, 1.0]


def test_import_baseline_stores_the_runs_own_numbers(tmp_path, capsys):
    """`soft-check add` of a `single` reference file: the soft check it
    stores holds what the run held."""
    config, path = _single_reference(tmp_path)
    source = tmp_path / "from-elsewhere.json"
    source.write_bytes(path.read_bytes())
    assert (
        main(
            ["--config", str(config.config_file), "soft-check", "add", M, "other"]
            + [str(source)]
        )
        == 0
    )
    (baseline,) = ReferenceStore(config).get_soft_checks(M).values()
    assert baseline.variables[0].values == _AS_SINGLE


def test_a_sibling_partitions_reference_overlays_the_runs_own_numbers(tmp_path):
    from dstf.reporting.overlay_loader import (
        _sibling_backend_index,
        load_sibling_backend_overlays,
    )

    config, _path = _single_reference(tmp_path)
    assert config.reference_dir.parent.name == "Python"
    elsewhere = Config(
        config_file=config.config_file,
        work_dir=tmp_path / "work",
        backend_name="OpenModelica",
    )
    _sibling_backend_index.cache_clear()
    (overlay,) = load_sibling_backend_overlays(elsewhere, M)
    assert overlay.variables["x"].values == _AS_SINGLE


# -- a run with no rows -------------------------------------------------------


def test_a_result_with_no_rows_is_not_stored_and_the_accept_says_so(tmp_path):
    """It was stored: a baseline of no points, which nothing can be scored
    against."""
    config = _config(tmp_path)
    empty = np.array([], dtype=float)
    result = TestResult(
        model_id=M,
        success=True,
        variables=[VariableResult(index=1, time=empty, values=empty, name="x")],
    )
    test = make_test_model(M, variable_patterns=["x"])
    store = ReferenceStore(config)
    with pytest.raises(ValueError, match="no rows to store"):
        store.store_reference(test, result)
    stored, skipped, _ = store.accept_results([test], {M: result})
    assert stored == []
    assert skipped == [
        (M, f"refusing to store baseline for {M}: the result has no rows to store")
    ]
    assert list(config.reference_dir.glob("ref_*.json")) == []


def test_thinning_no_rows_gives_no_rows():
    """`_downsample` returns a run within its budget whole — a run of none
    included (its thinning indexes the first and the last row)."""
    empty = np.array([], dtype=float)
    assert _downsample(empty, empty, 5) == ([], [])


#: A key that is not written at all.
ABSENT = object()


def _spec_config(tmp_path, *, project=ABSENT, entry=ABSENT) -> Config:
    """`_config` with a ``test_spec.json`` holding M; ``project`` is
    testing.json's ``baseline_points``, ``entry`` the test's own."""
    keys = {} if project is ABSENT else {"baseline_points": project}
    config = _config(tmp_path, test_spec="test_spec.json", **keys)
    test = {"model": M, "variables": ["i", "x"]}
    if entry is not ABSENT:
        test["baseline_points"] = entry
    config.spec_path.write_text(json.dumps({"tests": [test]}), encoding="utf-8")
    return config


@pytest.mark.parametrize(
    ("project", "entry", "setting", "stored"),
    [
        (ABSENT, ABSENT, "all", 5000),
        (50, ABSENT, 50, 50),
        (ABSENT, 50, 50, 50),
        (50, "all", "all", 5000),
        ("all", 50, 50, 50),
        (100, 50, 50, 50),
        (50, 100, 100, 100),
    ],
    ids=[
        "neither: all",
        "the project's",
        "the test's",
        "the test's all over the project's number",
        "the test's number over the project's all",
        "the test's smaller number",
        "the test's larger number",
    ],
)
def test_the_tests_key_beats_the_projects(tmp_path, project, entry, setting, stored):
    config = _spec_config(tmp_path, project=project, entry=entry)
    (test,) = discover_tests(config)
    assert test.baseline_points == (None if entry is ABSENT else entry)

    result = _run(5000, EVENTS)
    reference = _stored(config, result, test)

    assert len(reference["time"]) == stored
    assert reference["sampling"] == {
        "setting": setting,
        "source_points": 5000,
        "stored_points": stored,
        "precision": "double",
    }
    # The VALUES are thinned by the same number as the time — the same
    # rows, for every variable: a baseline whose values were thinned by the
    # project's number under a time thinned by the test's would pass the
    # length of its time alone.
    kept = _kept(reference)
    assert len(kept) == stored
    assert _holds_rows(reference, result, kept)


def test_a_test_found_in_the_source_takes_the_key_from_its_spec_entry():
    """A model both scanned and in the spec: the entry's value is the test's,
    and an entry without the key leaves the project's to apply."""
    scanned = {M: make_test_model(M, source="unit_tests")}
    with_key = {M: make_test_model(M, baseline_points=50)}
    without = {M: make_test_model(M)}
    assert merge_tests(scanned, with_key)[0].baseline_points == 50
    assert merge_tests(scanned, without)[0].baseline_points is None


@pytest.mark.parametrize("value", REFUSED, ids=repr)
def test_a_spec_entry_refuses_anything_else_with_one_sentence(tmp_path, value):
    config = _spec_config(tmp_path, entry=value)
    with pytest.raises(ConfigError) as refused:
        discover_tests(config)
    assert str(refused.value) == (
        f"{config.spec_path}: tests[{M}].baseline_points is "
        f"{json.dumps(value)}: {FORMS}"
    )


def test_a_refused_spec_entry_is_one_line_and_exit_two(tmp_path, capsys):
    config = _spec_config(tmp_path, entry=2000.0)
    assert main(["--config", str(config.config_file), "discover"]) == 2
    assert capsys.readouterr().err == (
        f"Config error: {config.spec_path}: tests[{M}].baseline_points is "
        f"2000.0: {FORMS}\n"
    )


def test_spec_schema_lists_the_key_with_its_two_forms(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    out = tmp_path / "schema.json"
    assert main(["spec", "schema", "--output", str(out)]) == 0
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert "baseline_points" in schema["$defs"]["test_entry"]["properties"]

    def valid(value) -> bool:
        spec = {"tests": [{"model": M, "baseline_points": value}]}
        return jsonschema.Draft202012Validator(schema).is_valid(spec)

    assert all(valid(v) for v in ("all", 2, 2000))
    # 2000.0 is an integer to JSON Schema; the parser is the one that refuses it.
    assert not any(valid(v) for v in REFUSED if v != 2000.0)


# -- the authoring tool ---------------------------------------------------------


def test_the_tool_keeps_the_key_when_it_saves_an_edited_entry(tmp_path):
    from test_session import LOOSEN, A, _session_with_entry

    session = _session_with_entry(tmp_path, {"baseline_points": 3})
    assert _session_test(session, A).baseline_points == 3
    session.stage([LOOSEN])
    session.save()

    spec = json.loads(session.spec.path.read_text(encoding="utf-8"))
    (entry,) = [e for e in spec["tests"] if e["model"] == A]
    assert entry["baseline_points"] == 3
    assert entry["metrics"]["children"][0]["tolerance"] == 0.5  # the edit, saved
    assert _session_test(session, A).baseline_points == 3


def test_the_tools_accept_stores_the_points_the_tests_key_says(tmp_path):
    """The tool's Accept is the CLI's: a five-row result under the test's
    ``baseline_points: 3`` is stored as three points, beside one stored
    whole."""
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.session import Selection, SessionView
    from test_session import A, B, _session_with_entry

    session = _session_with_entry(tmp_path, {"baseline_points": 3})
    view = SessionView(session, Selection(tests=frozenset({A, B})))
    session.apply(Accept().act(view))

    store = session.project.store
    assert _view(store.get_reference(A))["sampling"] == {
        "setting": 3,
        "source_points": 5,
        "stored_points": 3,
        "precision": "double",
    }
    assert _view(store.get_reference(A))["time"] == [0.0, 0.5, 1.0]
    assert _view(store.get_reference(B))["sampling"] == {
        "setting": "all",
        "source_points": 5,
        "stored_points": 5,
        "precision": "double",
    }


def test_the_key_is_written_by_hand_a_patch_may_not_touch_it(tmp_path):
    """`spec update` and the tool edit `comparison`, `metrics` and `plots`:
    the key is outside what a patch may write, so neither stores a value
    the parser would refuse."""
    from dstf.discovery.patch_apply import PatchError, apply_patch

    config = _spec_config(tmp_path, entry=50)
    before = config.spec_path.read_bytes()
    for op in ("replace", "remove"):
        with pytest.raises(PatchError, match="baseline_points"):
            apply_patch(
                config.spec_path,
                M,
                [{"op": op, "path": "/baseline_points", "value": 1}],
            )
    assert config.spec_path.read_bytes() == before


def _session_test(session, model_id):
    (test,) = [t for t in session.project.tests if t.model_id == model_id]
    return test


# -- a large baseline is said at accept ----------------------------------------


def _said(name: str, model_id: str, megabytes: str, points: str, variables: str) -> str:
    return (
        f"{name} ({model_id}) is {megabytes} MB ({points} points × {variables}) — "
        'set "baseline_points" for this test to store fewer.'
    )


def _megabytes(path) -> str:
    return f"{path.stat().st_size / 1e6:.1f}"


def _tracking_all(model_id: str, **fields):
    """A test that tracks every variable of its result: an accept stores no
    baseline for one that tracks none of them (nothing would be scored)."""
    return make_test_model(model_id, variable_patterns=["*"], **fields)


def test_a_baseline_file_over_five_megabytes_is_said_and_is_stored(tmp_path):
    """The real threshold, on a run the size of the two that prompted it:
    75,000 rows of three variables at full precision."""
    assert LARGE_BASELINE_BYTES == 5_000_000
    config = _config(tmp_path)
    rng = np.random.default_rng(153)
    time = np.arange(75_000) / 8.0
    big = TestResult(
        model_id="Lib.Big",
        success=True,
        variables=[
            VariableResult(index=i, time=time, values=rng.normal(size=75_000), name=n)
            for i, n in enumerate("abc", 1)
        ],
    )
    tests = [_tracking_all("Lib.Big"), _tracking_all(M)]
    results = {"Lib.Big": big, M: _run(5000, EVENTS)}
    store = ReferenceStore(config)

    stored, skipped, warnings, _ = accept_run_results(
        store, tests, results, config.work_dir
    )

    assert (stored, skipped) == (2, [])
    path = config.reference_dir / "ref_0001.json"
    assert path.stat().st_size > LARGE_BASELINE_BYTES
    assert warnings == [
        _said("ref_0001.json", "Lib.Big", _megabytes(path), "75,000", "3 variables")
    ]
    assert len(_view(store.get_reference("Lib.Big"))["time"]) == 75_000  # stored whole


def test_the_warning_is_one_per_file_over_the_threshold(tmp_path, monkeypatch):
    """With the threshold between the files' sizes, each larger one is said
    once — with the points its file HOLDS, not the rows its run had — and a
    smaller one is not: a short run, and a long one its test's key thins."""
    config = _config(tmp_path)
    small = TestResult(
        model_id="Lib.Small",
        success=True,
        variables=[
            VariableResult(
                index=1, time=np.arange(5.0), values=np.arange(5.0), name="i"
            )
        ],
    )

    def long_run(model_id: str) -> TestResult:
        result = _run(5000, EVENTS)
        result.model_id = model_id
        return result

    tests = [
        _tracking_all("Lib.Small"),
        _tracking_all(M),
        _tracking_all("Lib.Thin", baseline_points=2),
        _tracking_all("Lib.Half", baseline_points=500),
    ]
    results = {"Lib.Small": small} | {
        model_id: long_run(model_id) for model_id in (M, "Lib.Thin", "Lib.Half")
    }
    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 5_000)

    stored, skipped, warnings, _ = accept_run_results(
        ReferenceStore(config), tests, results, config.work_dir
    )

    assert (stored, skipped) == (4, [])
    whole, half = (config.reference_dir / f"ref_000{n}.json" for n in (2, 4))
    assert warnings == [
        _said(whole.name, M, _megabytes(whole), "5,000", "2 variables"),
        _said(half.name, "Lib.Half", _megabytes(half), "500", "2 variables"),
    ]


def test_a_file_exactly_at_the_threshold_is_not_said(tmp_path, monkeypatch):
    """Larger than, not at."""
    config = _config(tmp_path)
    store = ReferenceStore(config)
    test, results = _tracking_all(M), {M: _run(50)}
    accept_run_results(store, [test], results, config.work_dir)
    (path,) = config.reference_dir.glob("ref_*.json")
    size = path.stat().st_size

    def warnings(threshold: int) -> list[str]:
        monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", threshold)
        return accept_run_results(store, [test], results, config.work_dir)[2]

    # An accept writes the same bytes but its timestamp: the size holds.
    assert warnings(size) == [] and path.stat().st_size == size
    assert warnings(size - 1) == [
        _said(path.name, M, _megabytes(path), "50", "2 variables")
    ]


def test_a_baseline_of_one_variable_reads_in_the_singular(tmp_path, monkeypatch):
    config = _config(tmp_path)
    result = _run(50)
    del result.variables[1]
    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)
    warnings = accept_run_results(
        ReferenceStore(config), [_tracking_all(M)], {M: result}, config.work_dir
    )[2]
    (path,) = config.reference_dir.glob("ref_*.json")
    assert warnings == [_said(path.name, M, _megabytes(path), "50", "1 variable")]


@pytest.fixture
def example(tmp_path, monkeypatch):
    """The Python example library, run once without baselines: ``dstf(...)``
    runs a command on it, ``refs()`` lists its reference files by model."""
    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path, baselines=False)

    def dstf(command: str, *extra: str) -> int:
        return main(
            ["--config", str(config.config_file), command]
            + ["--work-dir", str(config.work_dir), *extra]
        )

    def refs() -> dict[str, object]:
        paths = sorted(config.reference_dir.glob("ref_*.json"))
        return {json.loads(p.read_text(encoding="utf-8"))["model_id"]: p for p in paths}

    return SimpleNamespace(dstf=dstf, refs=refs, config=config)


def _warning_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "baseline_points" in line]


@pytest.mark.parametrize("command", ["run", "compare"])
def test_the_cli_prints_the_warning_once_per_file_and_exits_as_before(
    example, capsys, monkeypatch, command
):
    assert example.dstf("run", "--accept") == 0
    first = capsys.readouterr().out
    assert "Accepted 3 test baselines" in first
    assert _warning_lines(first) == []  # three small files, the real threshold

    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)
    assert example.dstf(command, "--accept") == 0
    out = capsys.readouterr().out
    assert "Accepted 3 test baselines" in out
    refs = example.refs()
    assert len(refs) == 3
    assert sorted(_warning_lines(out)) == sorted(
        "Warning: "
        + _said(
            path.name,
            model_id,
            _megabytes(path),
            f"{len(json.loads(path.read_text(encoding='utf-8'))['time']):,}",
            "1 variable",
        )
        for model_id, path in refs.items()
    )


def test_the_warning_does_not_hide_a_skipped_test_or_change_its_exit_code(
    example, capsys, monkeypatch
):
    """Two warnings beside one skip: the accept exits 1 for the skip, as it
    did, and names all three."""
    assert example.dstf("run", "--accept") == 0
    ramp = "PythonTestingLib.Examples.SimpleRamp"
    spec = json.loads(example.config.spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == ramp:
            entry["comparison"] = {"tolerance": {"lower": 0.1, "upper": 0.2}}
    example.config.spec_path.write_text(json.dumps(spec), encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)

    assert example.dstf("compare", "--accept") == 1
    out = capsys.readouterr().out
    assert "Accepted 2 test baselines" in out
    assert f"Skipped 1 test(s):\n  {ramp}: " in out
    assert len(_warning_lines(out)) == 2 and ramp not in "".join(_warning_lines(out))


def test_the_interactive_review_prints_the_warning_under_its_accept(
    example, capsys, monkeypatch
):
    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    assert example.dstf("run", "-i", "all") == 0
    out = capsys.readouterr().out
    refs = example.refs()
    assert len(refs) == 3 and out.count("Accepted (1 variables).") == 3
    lines = _warning_lines(out)
    assert len(lines) == 3
    for model_id, path in refs.items():
        assert sum(f"{path.name} ({model_id}) is " in line for line in lines) == 1


def test_the_tools_accept_notice_carries_the_warning(tmp_path, monkeypatch):
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.session import Selection, SessionView
    from test_session import A, B, C, _project

    cfg, work = _project(tmp_path)
    session = Session()
    session.open(cfg, work_dir=work)
    view = SessionView(session, Selection(tests=frozenset({A, B, C})))

    session.apply(Accept().act(view))
    assert session.notices[-1].startswith("Accept: stored 2 baseline(s) to ")
    assert "baseline_points" not in session.notices[-1]  # two small files

    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)
    session.apply(Accept().act(view))
    notice = session.notices[-1]
    reference_dir = session.project.config.reference_dir
    said = [
        _said(path.name, model_id, _megabytes(path), "5", "1 variable")
        for model_id, path in (
            (A, reference_dir / "ref_0001.json"),
            (B, reference_dir / "ref_0004.json"),
        )
    ]
    assert notice.startswith("Accept: stored 2 baseline(s) to ")
    assert f"skipped {C}: " in notice  # the refusal is still said
    assert notice.endswith("".join(f"; {line}" for line in said))


# -- a baseline that no longer holds a variable ------------------------------


def _of(*names: str) -> TestResult:
    time = np.arange(5.0)
    return TestResult(
        model_id=M,
        success=True,
        variables=[
            VariableResult(index=i, time=time, values=time, name=n)
            for i, n in enumerate(names, 1)
        ],
    )


def test_an_accept_says_which_variables_its_baseline_no_longer_holds(
    tmp_path, monkeypatch
):
    """One line per test whose new baseline dropped a variable the stored
    one held — before the size warning of the same file — and none for a
    first baseline, an unchanged one, or one that only gained."""
    config = _config(tmp_path)
    store, test = ReferenceStore(config), _tracking_all(M)

    def accept(*names: str) -> list[str]:
        stored, skipped, warnings, _ = accept_run_results(
            store, [test], {M: _of(*names)}, config.work_dir
        )
        assert (stored, skipped) == (1, [])
        return warnings

    assert accept("a", "b") == []  # the first baseline replaced nothing
    assert accept("a", "b") == []
    assert accept("a", "b", "c") == []
    assert accept("a", "c") == [
        f"ref_0001.json ({M}) no longer holds b — the accepted result does not hold it."
    ]
    assert accept("a", "c", *"defghijk") == []
    monkeypatch.setattr("dstf.storage.accept.LARGE_BASELINE_BYTES", 1)
    lost, large = accept("z")
    assert lost == (
        f"ref_0001.json ({M}) no longer holds a, c, d, e, f, g and 4 more — "
        "the accepted result does not hold them."
    )
    assert " MB (" in large
