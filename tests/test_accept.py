"""`accept_run_results`: `ReferenceStore.accept_results` plus the refusal of a
test whose last run never reached a verdict — the guard that used to live
only in `cmd_compare`, now the one function every accept path calls. Its
third value, the warning for a large baseline file, is tested in
tests/test_baseline_points.py."""

import json

import numpy as np

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.accept import accept_run_results
from dstf.storage.reference_store import ReferenceStore


def _result(model_id):
    t = np.linspace(0.0, 1.0, 5)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=t * 2, name="x")],
    )


def _env(tmp_path, manifest):
    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib\nend FakeLib;\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    config = Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=work)
    return config, ReferenceStore(config)


def test_a_pending_test_is_refused_with_the_reason_and_the_rest_are_stored(tmp_path):
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.Done",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            },
        },
    )
    tests = [
        make_test_model("FakeLib.Done", variable_patterns=["x"]),
        make_test_model("FakeLib.InFlight", variable_patterns=["x"]),
    ]
    results = {t.model_id: _result(t.model_id) for t in tests}

    stored, skipped, warnings, notes = accept_run_results(
        store, tests, results, config.work_dir
    )

    assert stored == 1 and warnings == [] and notes == []
    assert [m for m, _ in skipped] == ["FakeLib.InFlight"]
    assert "re-run before accepting" in skipped[0][1]
    assert str(config.work_dir) in skipped[0][1]
    assert store.get_reference("FakeLib.Done") is not None
    assert store.get_reference("FakeLib.InFlight") is None


def test_a_pending_test_outside_the_scope_is_not_refused(tmp_path):
    """`compare --accept --filter A` must not fail on B's aborted run: the
    refusal is scoped to the tests being accepted."""
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.Done",
                "ref_id": None,
                "last_run_status": "passed",
            },
            "test_0002": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            },
        },
    )
    tests = [make_test_model("FakeLib.Done", variable_patterns=["x"])]
    assert accept_run_results(
        store, tests, {"FakeLib.Done": _result("FakeLib.Done")}, config.work_dir
    ) == (1, [], [], [])


def test_no_manifest_means_no_refusal(tmp_path):
    config, store = _env(tmp_path, {})
    (config.work_dir / "batch_manifest.json").unlink()
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    assert accept_run_results(
        store, tests, {"FakeLib.A": _result("FakeLib.A")}, config.work_dir
    ) == (1, [], [], [])


def test_a_simulate_only_test_is_named_and_is_neither_stored_nor_skipped(tmp_path):
    """`Accepted 0 test baselines` with no word of the test: a test that
    declares nothing to score — what `dstf spec add <model>` writes — has no
    baseline to store, and the accept says that in one line."""
    config, store = _env(tmp_path, {})
    tests = [
        make_test_model("FakeLib.Tank"),
        make_test_model("FakeLib.A", variable_patterns=["x"]),
        make_test_model("FakeLib.Marked", variable_patterns=["x"], simulate_only=True),
    ]
    assert [t.is_simulate_only for t in tests] == [True, False, True]
    results = {t.model_id: _result(t.model_id) for t in tests}

    assert accept_run_results(store, tests, results, config.work_dir) == (
        1,
        [],
        [],
        ["Simulate-only, nothing to store: FakeLib.Tank, FakeLib.Marked"],
    )
    assert store.index.get_id("FakeLib.Tank") is None
    assert store.index.get_id("FakeLib.Marked") is None
    # Alone: nothing stored, nothing skipped, and said.
    assert accept_run_results(store, tests[:1], results, config.work_dir) == (
        0,
        [],
        [],
        ["Simulate-only, nothing to store: FakeLib.Tank"],
    )


def test_a_simulate_only_test_with_its_own_reason_is_not_named_twice(tmp_path):
    """One whose run failed, or did not finish, is skipped with that
    reason: the note names the tests passed over for having no baseline to
    store, and no other."""
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            }
        },
    )
    tests = [make_test_model(f"FakeLib.{name}") for name in ("Failed", "InFlight")]
    results = {
        "FakeLib.Failed": TestResult(model_id="FakeLib.Failed", success=False),
        "FakeLib.InFlight": _result("FakeLib.InFlight"),
    }

    stored, skipped, warnings, notes = accept_run_results(
        store, tests, results, config.work_dir
    )

    assert (stored, warnings, notes) == (0, [], [])
    assert [model_id for model_id, _ in skipped] == [
        "FakeLib.Failed",
        "FakeLib.InFlight",
    ]


def test_the_note_names_six_tests_and_counts_the_rest():
    """The list a lost-variable warning writes: six names, then `and N
    more`."""
    from dstf.storage.accept import simulate_only_note

    names = [f"Lib.T{i}" for i in range(1, 9)]
    assert simulate_only_note([]) is None
    assert simulate_only_note(names[:6]) == (
        "Simulate-only, nothing to store: "
        "Lib.T1, Lib.T2, Lib.T3, Lib.T4, Lib.T5, Lib.T6"
    )
    assert simulate_only_note(names) == (
        "Simulate-only, nothing to store: "
        "Lib.T1, Lib.T2, Lib.T3, Lib.T4, Lib.T5, Lib.T6 and 2 more"
    )


def test_store_skips_still_come_through(tmp_path):
    config, store = _env(tmp_path, {})
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    assert accept_run_results(store, tests, {}, config.work_dir) == (
        0,
        [("FakeLib.A", "no simulation result")],
        [],
        [],
    )


# ---------------------------------------------------------------------------
# A base tolerance that is no number, beside the store's own skips
# ---------------------------------------------------------------------------

PAIR = {"lower": 0.1, "upper": 0.2}


def _reference_file(config):
    (path,) = sorted(config.reference_root.rglob("ref_*.json"))
    return path


def test_a_failed_simulation_with_a_bad_base_is_skipped_as_a_failed_simulation(
    tmp_path,
):
    """Nothing ran, so nothing was refused — as `compare_one` says SIM_FAIL
    for it, not the refusal. The test is skipped ONCE, with the simulation's
    reason, and no reference is written: not for a bad base in the spec, and
    not over one the stored reference already holds."""
    config, store = _env(tmp_path, {})
    test = make_test_model("FakeLib.A", variable_patterns=["x"])
    test.comparison_tolerance = PAIR
    failed = TestResult(model_id="FakeLib.A", success=False, error_message="boom")

    assert accept_run_results(
        store, [test], {"FakeLib.A": failed}, config.work_dir
    ) == (
        0,
        [("FakeLib.A", "simulation failed")],
        [],
        [],
    )
    assert accept_run_results(store, [test], {}, config.work_dir) == (
        0,
        [("FakeLib.A", "no simulation result")],
        [],
        [],
    )
    assert store.get_reference("FakeLib.A") is None

    # The same over a stored reference whose own base is the bad one.
    test.comparison_tolerance = None
    assert accept_run_results(
        store, [test], {"FakeLib.A": _result("FakeLib.A")}, config.work_dir
    ) == (1, [], [], [])
    path = _reference_file(config)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["comparison"] = {"tolerance": PAIR}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    store = ReferenceStore(config)
    assert accept_run_results(
        store, [test], {"FakeLib.A": failed}, config.work_dir
    ) == (
        0,
        [("FakeLib.A", "simulation failed")],
        [],
        [],
    )
    assert path.read_bytes() == before


def test_an_unfinished_test_with_a_bad_base_is_listed_once(tmp_path):
    """Its run never finished: that is the reason it is skipped with, and
    the only one — the comparison's refusal is not said beside it."""
    config, store = _env(
        tmp_path,
        {
            "test_0001": {
                "model_id": "FakeLib.InFlight",
                "ref_id": None,
                "last_run_status": "pending",
            }
        },
    )
    test = make_test_model("FakeLib.InFlight", variable_patterns=["x"])
    test.comparison_tolerance = PAIR
    results = {"FakeLib.InFlight": _result("FakeLib.InFlight")}

    stored, skipped, *_ = accept_run_results(store, [test], results, config.work_dir)

    assert stored == 0
    assert [model_id for model_id, _ in skipped] == ["FakeLib.InFlight"]
    assert "re-run before accepting" in skipped[0][1]
    assert "base tolerance" not in skipped[0][1]
    assert store.get_reference("FakeLib.InFlight") is None


# ---------------------------------------------------------------------------
# A tree wrong in its file
# ---------------------------------------------------------------------------


def test_a_test_refused_for_its_broken_tree_is_skipped_with_the_sentence(tmp_path):
    """`compare_one` refuses a tree wrong in its file (`broken_roles`): no
    accept repairs that, so none stores a baseline for it — with a baseline
    and without — and it is skipped with the refusal's sentence, as a base
    tolerance that is no number is. The reference file is not touched."""
    from dstf.comparison.comparator import broken_roles
    from dstf.comparison.tree_spec import parse_metric_tree

    config, store = _env(tmp_path, {})
    test = make_test_model("FakeLib.A", variable_patterns=["x"])
    results = {"FakeLib.A": _result("FakeLib.A")}
    test.metric_tree_spec = parse_metric_tree(
        {"combinator": "warn", "children": [{"metric": "nrmse", "variable": "x"}]}
    )
    sentence = broken_roles(test)
    assert sentence is not None and sentence.startswith("metrics: Every tree needs")

    assert accept_run_results(store, [test], results, config.work_dir) == (
        0,
        [("FakeLib.A", sentence)],
        [],
        [],
    )
    assert store.get_reference("FakeLib.A") is None

    test.metric_tree_spec = None
    assert accept_run_results(store, [test], results, config.work_dir)[0] == 1
    path = _reference_file(config)
    before = path.read_bytes()
    test.metric_tree_spec = parse_metric_tree(
        {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "nrmse", "variable": "x", "against": "elsewhere"},
            ],
        }
    )
    stored, skipped, *_ = accept_run_results(
        ReferenceStore(config), [test], results, config.work_dir
    )
    assert (stored, skipped) == (0, [("FakeLib.A", broken_roles(test))])
    assert path.read_bytes() == before
