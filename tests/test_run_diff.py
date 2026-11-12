"""`dstf diff`: every variable of two runs of one tool, read from the runs'
work dirs with no configuration — the classes of a group (identical,
rounding, near zero, derivative, changed; a parameter identical, rounding
or differs), the statistics never scored, the tests not compared, and each
refusal, read from the Markdown and the JSON the command prints. The
result files are synthetic DSresult files (`mat4_files`), written as
Dymola and OpenModelica write theirs."""

import hashlib
import json
import os

import numpy as np
import pytest

from dstf.cli.main import main
from mat4_files import write_result_mat

A = "Lib.Examples.A"
B = "Lib.Examples.B"
TIME = [0.0, 1.0, 2.0, 3.0, 4.0]
RAMP = [0.0, 1.0, 2.0, 3.0, 4.0]


def _run(
    where,
    tests,
    *,
    backend="Dymola",
    failed=(),
    pending=(),
    stats=None,
):
    """A run's work dir as the backend leaves one: ``tests`` maps a model
    id to its result file's ``write_result_mat`` arguments (None: no file);
    a model in ``failed`` is recorded as failed, its file holding values;
    one in ``pending`` was started and never finished. ``stats`` maps a
    model id to the statistics its run_stats.json keeps."""
    where.mkdir(parents=True)
    manifest, rows = {}, []
    for i, (model_id, mat) in enumerate(tests.items(), start=1):
        key = f"test_{i:04d}"
        status = (
            "failed"
            if model_id in failed
            else "pending"
            if model_id in pending
            else "passed"
        )
        manifest[key] = {
            "model_id": model_id,
            "ref_id": None,
            "last_run_at": 1.0,
            "last_run_status": status,
        }
        rows.append({"test_key": key, "model_id": model_id, "status": status})
        (where / key).mkdir()
        if mat is not None:
            om = backend == "OpenModelica"
            name = "result_res.mat" if om else "dsres.mat"
            mat = {"time": TIME, **mat}
            write_result_mat(
                where / key / name, tool="openmodelica" if om else "dymola", **mat
            )
        if stats and model_id in stats:
            (where / key / "run_stats.json").write_text(
                json.dumps(
                    {
                        "model_id": model_id,
                        "translation_wall": 1.0,
                        "sim_wall": 1.0,
                        "elapsed": 2.0,
                        "statistics": stats[model_id],
                        "run_metadata": None,
                    }
                )
            )
    (where / "batch_manifest.json").write_text(json.dumps(manifest))
    meta = {"backend": backend, "backend_name": backend, "os": "linux"}
    (where / "status.json").write_text(json.dumps({"metadata": meta, "tests": rows}))
    return where


def _diff(capsys, *argv):
    """The command's stdout, as lines; it must have exited 0."""
    code = main(["diff", *map(str, argv)])
    out = capsys.readouterr().out
    assert code == 0, out
    return out.splitlines()


def _refused(capsys, *argv):
    code = main(["diff", *map(str, argv)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    return captured.err.strip()


def _parse_refused(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        main(["diff", *map(str, argv)])
    assert e.value.code == 2
    return capsys.readouterr().err.splitlines()[-1]


def _pair(tmp_path, old, new, **kw):
    """Two Dymola runs of test A, its result files ``old`` and ``new``."""
    a = _run(tmp_path / "main", {A: old}, **kw)
    b = _run(tmp_path / "fix", {A: new}, **kw)
    return f"main={a}", f"fix={b}"


def _row(lines):
    """The overview's one row."""
    (row,) = [line for line in lines if line.startswith("| Dymola/linux |")]
    return row


def _f32_step(x):
    return float(np.spacing(np.float32(x)))


# --- identical ---------------------------------------------------------------


def test_two_runs_whose_files_hold_the_same_values_are_all_identical(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP, "y": [5.0] * 5}, "parameters": {"k": 2.0}}
    lines = _diff(capsys, *_pair(tmp_path, mat, mat))
    assert lines[0] == "**Every variable, main → fix** (1 test)"
    assert _row(lines) == "| Dymola/linux | 1 of 1 | all identical | all identical |"
    assert "Nothing differs in 1 test." in lines


def test_a_later_run_over_a_longer_span_differs_in_either_order(tmp_path, capsys):
    """Another stop time: equal over the reference's span, yet the runs hold
    other points. Neither order reads identical, and the detail gives each
    run's points and span."""
    t8 = [float(t) for t in range(9)]
    short, long = {"trajectories": {"x": RAMP}}, {"time": t8, "trajectories": {"x": t8}}
    runs = _pair(tmp_path, short, long)
    lines = _diff(capsys, *runs, "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (time points differ) | none |"
    )
    assert "Differs in 1 of 1 test: `A`." in lines
    assert (
        "- Variables: 1 in main, 1 in fix; points: 5 in main (0–4 s), 9 in fix "
        "(0–8 s); a Change is measured on main's points only"
    ) in lines
    lines = _diff(capsys, f"fix={runs[1][4:]}", f"main={runs[0][5:]}", "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (time points differ) | none |"
    )
    assert "| `x` | 0 %; 4 old points outside the new time span |" in lines


def test_a_finer_grid_moved_between_the_reference_s_points_is_not_identical(
    tmp_path, capsys
):
    fine = [t / 2 for t in range(9)]
    spiked = [100.0 if t == 0.5 else t for t in fine]
    old = {"trajectories": {"x": RAMP}}
    new = {"time": fine, "trajectories": {"x": spiked}}
    lines = _diff(capsys, *_pair(tmp_path, old, new))
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (time points differ) | none |"
    )


def test_the_same_point_count_on_a_shifted_grid_is_not_identical(tmp_path, capsys):
    old = {"trajectories": {"x": RAMP}}
    new = {"time": [0.0, 1.5, 2.0, 3.0, 4.0], "trajectories": {"x": RAMP}}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (8.3 % of range) (time points differ) "
        "| none |"
    )
    assert (
        "- Variables: 1 in main, 1 in fix; points: 5 in main (0–4 s), 5 in fix "
        "(0–4 s); a Change is measured on main's points only"
    ) in lines


# --- rounding ----------------------------------------------------------------


def test_a_move_of_one_float32_step_is_rounding_in_a_single_precision_file(
    tmp_path, capsys
):
    moved = [0.0, 1.0, 2.0 + _f32_step(2.0), 3.0, 4.0]
    lines = _diff(
        capsys,
        *_pair(tmp_path, {"trajectories": {"x": RAMP}}, {"trajectories": {"x": moved}}),
    )
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 rounding; changed: none | none |"
    )


def test_the_same_move_in_a_double_precision_file_is_a_change(tmp_path, capsys):
    """One float32 step is many float64 steps: a float64 file's rounding is
    its own, far finer."""
    moved = [0.0, 1.0, 2.0 + _f32_step(2.0), 3.0, 4.0]
    old = {"dtype": "<f8", "trajectories": {"x": RAMP}}
    new = {"dtype": "<f8", "trajectories": {"x": moved}}
    lines = _diff(capsys, *_pair(tmp_path, old, new))
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (6.0e-06 % of range) | none |"
    )
    one_step = [0.0, 1.0, float(np.nextafter(2.0, 3.0)), 3.0, 4.0]
    new = {"dtype": "<f8", "trajectories": {"x": one_step}}
    lines = _diff(capsys, *_pair(tmp_path / "f8", old, new))
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 rounding; changed: none | none |"
    )


def test_a_float64_file_against_a_float32_one_rounds_at_float32(tmp_path, capsys):
    """The coarser file's step: 0.1 saved at single precision is 0.1 within
    rounding, a signal's and a parameter's."""
    wave = [0.1, 0.2, 0.3, 0.4, 0.5]
    old = {"dtype": "<f8", "trajectories": {"x": wave}, "parameters": {"k": 0.1}}
    new = {"dtype": "<f4", "trajectories": {"x": wave}, "parameters": {"k": 0.1}}
    lines = _diff(capsys, *_pair(tmp_path, old, new))
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 rounding; changed: none | 1 rounding |"
    )


def test_float64_and_float32_copies_of_one_run_are_rounding_in_either_order(
    tmp_path, capsys
):
    """One run saved at double and at single precision: the float32 times
    are not the float64 ones, so the later run must not be read on the
    other's grid. Point by point, time and value each agree within one
    float32 step."""
    t = [i * 0.1 for i in range(11)]
    data = {"x": [1000.0 * v + 3.0 for v in t], "y": [float(np.sin(10 * v)) for v in t]}
    f8 = {"dtype": "<f8", "time": t, "trajectories": data}
    f4 = {"dtype": "<f4", "time": t, "trajectories": data}
    runs = _pair(tmp_path, f8, f4)
    for argv in (runs, (f"fix={runs[1][4:]}", f"main={runs[0][5:]}")):
        lines = _diff(capsys, *argv, "--details")
        assert _row(lines) == (
            "| Dymola/linux | 1 of 1 | 2 rounding; changed: none | none |"
        )
        # rounding is no difference: nothing to fold out either
        assert "Nothing differs beyond rounding in 1 test." in lines
        assert "<details>" not in lines


def test_times_within_rounding_and_no_varying_signal_differ_in_nothing(
    tmp_path, capsys
):
    t = [i * 0.1 for i in range(11)]
    held = {"time": t, "trajectories": {"c": [5.0] * 11}}
    runs = _pair(tmp_path, {"dtype": "<f8", **held}, {"dtype": "<f4", **held})
    lines = _diff(capsys, *runs)
    assert _row(lines) == "| Dymola/linux | 1 of 1 | none | all identical |"
    assert "Nothing differs beyond rounding in 1 test." in lines


# --- near zero, derivatives ----------------------------------------------------


def test_near_zero_is_a_class_only_with_the_flag(tmp_path, capsys):
    small = [0.0, 1e-5, 2e-5, 3e-5, 4e-5]
    moved = [0.0, 1e-5, 2.5e-5, 3e-5, 4e-5]
    runs = _pair(
        tmp_path, {"trajectories": {"x": small}}, {"trajectories": {"x": moved}}
    )
    lines = _diff(capsys, *runs)
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (12 % of range) | none |"
    )
    lines = _diff(capsys, *runs, "--near-zero", "1e-4", "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 near zero; changed: none | none |"
    )
    assert "- Near zero, the largest |fix − main|: 5e-06 (`x`, 2 s)" in lines
    (key,) = [line for line in lines if line.startswith("A group = ")]
    assert "near zero = every value smaller in size than 0.0001;" in key
    # under a cut the values reach, it is a change again
    lines = _diff(capsys, *runs, "--near-zero", "3e-5")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (12 % of range) | none |"
    )


def test_a_group_is_a_derivative_when_any_of_its_names_is_one(tmp_path, capsys):
    """Its alias der(pos) makes the group a derivative, and is the name it
    is shown by, not the shorter `v`."""
    alias = {"der(pos)": ("v", False)}
    old = {"trajectories": {"v": RAMP}, "aliases": alias}
    new = {"trajectories": {"v": [0.0, 1.0, 2.5, 3.0, 4.0]}, "aliases": alias}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 derivative; changed: none | none |"
    )
    assert "- Derivatives, the largest: `der(pos)` (+1) 12 % of range (2 s)" in lines


# --- changed ---------------------------------------------------------------------


def test_a_value_not_finite_in_one_run_is_changed_and_listed_first(tmp_path, capsys):
    """Changed whatever else it is — here a derivative, under a near-zero
    cut it does not reach — and ranked above a larger move."""
    old = {"trajectories": {"big": RAMP, "der(x)": RAMP}}
    new = {
        "trajectories": {
            "big": [0.0, 1.0, 9.0, 3.0, 4.0],
            "der(x)": [0.0, 1.0, float("nan"), 3.0, 4.0],
        }
    }
    runs = _pair(tmp_path, old, new)
    lines = _diff(capsys, *runs, "--details", "--near-zero", "100")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 near zero; 1 changed (not finite) | none |"
    )
    lines = _diff(capsys, *runs, "--details")
    assert _row(lines) == ("| Dymola/linux | 1 of 1 | 2 changed (not finite) | none |")
    table = lines[lines.index("| Changed | Change |") + 2 :][:2]
    assert table == [
        "| `der(x)` | 0 %; 1 point not finite from 2 s |",
        "| `big` | 175 % of range (2 s) |",
    ]


def test_aliases_that_split_in_the_later_run_are_two_groups(tmp_path, capsys):
    """`a` and `b` are one in main; in fix `b` moved and `a` did not. A group
    is equal in BOTH runs: `a` is identical, `b` changed."""
    old = {"trajectories": {"a": RAMP}, "aliases": {"b": ("a", False)}}
    new = {"trajectories": {"a": RAMP, "b": [0.0, 1.0, 2.5, 3.0, 4.0]}}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert "- Signals (2 groups): 1 identical, 1 changed" in lines
    assert "| `b` | 12 % of range (2 s) |" in lines


# --- parameters ------------------------------------------------------------------


def test_parameters_identical_rounding_and_differing_are_counted(tmp_path, capsys):
    """A NaN parameter in both runs is identical; one float32 step is
    rounding; 1 → 2 differs, its aliases counted beside its name."""
    one_step = 3.0 + _f32_step(3.0)
    old = {
        "trajectories": {"x": RAMP},
        "parameters": {"p": float("nan"), "q": 3.0, "r": 1.0},
        "aliases": {"r_alias": ("r", False)},
    }
    new = {
        "trajectories": {"x": RAMP},
        "parameters": {"p": float("nan"), "q": one_step, "r": 2.0},
        "aliases": {"r_alias": ("r", False)},
    }
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert (
        _row(lines)
        == "| Dymola/linux | 1 of 1 | all identical | 1 differs, 1 rounding |"
    )
    assert (
        "<summary><code>A</code> · Dymola/linux — signals all identical; "
        "parameters: 1 differs, 1 rounding</summary>"
    ) in lines
    assert "- Parameters (3 groups): 1 identical, 1 rounding, 1 differs" in lines
    assert "- Parameters that differ, main → fix: `r` (+1) 1 → 2" in lines


# --- names, statistics -----------------------------------------------------------


def test_a_name_one_run_holds_is_counted_and_listed(tmp_path, capsys):
    old = {"trajectories": {"x": RAMP}}
    new = {"trajectories": {"x": RAMP, "y": RAMP}}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | all identical (1 name only in fix) | none |"
    )
    assert "- Only in fix: `y`" in lines


def test_the_solver_s_statistics_are_listed_and_never_scored(tmp_path, capsys):
    """CPUtime, EventCounter and NonlinearSystems.* in the file are no
    variables: their finals are listed old → new beside the run's own
    statistics, and the signals stay identical."""
    stat = "NonlinearSystems.simulation[1].Iterations"
    old = {
        "trajectories": {"x": RAMP, "CPUtime": RAMP, "EventCounter": [0, 1, 1, 2, 3]},
        "parameters": {stat: 10.0},
    }
    new = {
        "trajectories": {"x": RAMP, "CPUtime": RAMP, "EventCounter": [0, 1, 2, 4, 5]},
        "parameters": {stat: 30.0},
    }
    a = _run(
        tmp_path / "main", {A: old}, stats={A: {"simulation": {"accepted_steps": 10}}}
    )
    b = _run(
        tmp_path / "fix", {A: new}, stats={A: {"simulation": {"accepted_steps": 20}}}
    )
    lines = _diff(capsys, f"main={a}", f"fix={b}", "--details", "A")
    assert _row(lines) == "| Dymola/linux | 1 of 1 | all identical | none |"
    assert "- Variables: 1 in main, 1 in fix; points: 5 in main, 5 in fix" in lines
    assert lines[lines.index("- Statistics, main → fix (not scored):") + 1] == (
        f"  - Changed: EventCounter 3 → 5; {stat} 10 → 30; Accepted steps 10 → 20"
    )


def test_a_statistic_whose_value_differs_at_all_is_listed_as_changed(tmp_path, capsys):
    """1000 → 1005 is under the scorer's 1 %, and a counter one run lacks is
    no criterion: both differ, so neither is "unchanged"."""
    s1 = "NonlinearSystems.simulation[1].Iterations"
    s2 = "NonlinearSystems.simulation[2].Iterations"
    old = {"trajectories": {"x": RAMP}, "parameters": {s1: 1000.0}}
    new = {"trajectories": {"x": RAMP}, "parameters": {s1: 1005.0, s2: 450.0}}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details", "A")
    at = lines.index("- Statistics, main → fix (not scored):")
    assert lines[at + 1 : at + 3] == [
        f"  - Changed: {s1} 1000 → 1005; {s2} – → 450",
        "",
    ]


def test_the_statistics_lists_are_capped(tmp_path, capsys):
    names = [f"NonlinearSystems.simulation[{i:02d}].Calls" for i in range(25)]
    old = {"trajectories": {"x": RAMP}, "parameters": {n: 1.0 for n in names}}
    moved = {"trajectories": {"x": RAMP}, "parameters": {n: 2.0 for n in names}}
    lines = _diff(capsys, *_pair(tmp_path, old, old), "--details", "A")
    (same,) = [line for line in lines if line.startswith("  - Unchanged: ")]
    assert same.endswith(f"; {names[19]} 1; and 5 more")
    lines = _diff(capsys, *_pair(tmp_path / "moved", old, moved), "--details", "A")
    (changed,) = [line for line in lines if line.startswith("  - Changed: ")]
    assert changed.endswith(f"; {names[19]} 1 → 2; and 5 more")


# --- tests not compared ----------------------------------------------------------


def test_a_failed_a_pending_and_a_one_run_test_are_named_not_compared(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    c, d = "Lib.Examples.C", "Lib.Examples.D"
    a = _run(tmp_path / "main", {A: mat, B: mat, c: mat}, pending=(c,))
    b = _run(tmp_path / "fix", {A: mat, B: mat, c: mat, d: mat}, failed=(B,))
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert _row(lines) == "| Dymola/linux | 1 of 4 | all identical | none |"
    assert lines[-3:] == [
        "Nothing differs in 1 test.",
        "Only in fix: `D`.",
        "Not compared: `B` (fix: the run failed), `C` (main: not run).",
    ]


def test_a_test_with_no_result_file_is_not_run(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    a = _run(tmp_path / "main", {A: mat, B: mat})
    b = _run(tmp_path / "fix", {A: mat, B: None})
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert lines[-1] == "Not compared: `B` (fix: not run)."


def test_a_timed_out_and_a_cancelled_result_are_not_compared(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    moved = {"trajectories": {"x": RAMP[::-1]}}
    a = _run(tmp_path / "main", {A: mat, B: mat})
    b = _run(tmp_path / "fix", {A: moved, B: moved})
    manifest = json.loads((b / "batch_manifest.json").read_text())
    manifest["test_0001"]["last_run_status"] = "timed_out"
    manifest["test_0002"]["last_run_status"] = "cancelled"
    (b / "batch_manifest.json").write_text(json.dumps(manifest))
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert _row(lines) == "| Dymola/linux | 0 of 2 | – | – |"
    assert lines[-1] == ("Not compared: `A` (fix: the run failed), `B` (fix: not run).")


_PARTIAL = (
    "partial result: dsres.mat holds 3 time points up to t=2, its header counts "
    "5 — the run did not finish writing it"
)


def _partial(run):
    """``run``'s test A's file cut after 3 of its 5 points, as a killed run
    leaves it."""
    write_result_mat(
        run / "test_0001" / "dsres.mat",
        time=TIME,
        trajectories={"x": [0.0, 1.0, 2.0, 9.0, 9.0]},
        points=3,
        declared=5,
    )


def test_a_partial_result_file_is_never_compared(tmp_path, capsys):
    """Whatever the manifest says: here, passed."""
    a = _run(tmp_path / "main", {A: {"trajectories": {"x": RAMP}}})
    b = _run(tmp_path / "fix", {A: None})
    _partial(b)
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert lines[-1] == f"Not compared: `A` (fix: {_PARTIAL})."


def test_a_partial_result_under_an_old_manifest_is_never_compared(tmp_path, capsys):
    """An old manifest names its models alone and records no verdict."""
    a = _run(tmp_path / "main", {A: {"trajectories": {"x": RAMP}}})
    b = _run(tmp_path / "fix", {A: None})
    _partial(b)
    for run in (a, b):
        held = json.loads((run / "batch_manifest.json").read_text())
        bare = {key: entry["model_id"] for key, entry in held.items()}
        (run / "batch_manifest.json").write_text(json.dumps(bare))
        status = json.loads((run / "status.json").read_text())
        (run / "status.json").write_text(json.dumps({**status, "tests": []}))
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert lines[-1] == f"Not compared: `A` (fix: {_PARTIAL})."


# --- the reference ---------------------------------------------------------------


def test_the_first_label_given_is_the_reference(tmp_path, capsys):
    """A Change is a percent of the reference's range: main's 4 against
    fix's 8."""
    a = _run(tmp_path / "main", {A: {"trajectories": {"x": RAMP}}})
    b = _run(tmp_path / "fix", {A: {"trajectories": {"x": [2 * t for t in RAMP]}}})
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert lines[0] == "**Every variable, main → fix** (1 test)"
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (100 % of range) | none |"
    )
    lines = _diff(capsys, f"fix={b}", f"main={a}")
    assert lines[0] == "**Every variable, fix → main** (1 test)"
    assert _row(lines) == (
        "| Dymola/linux | 1 of 1 | 1 changed (50 % of range) | none |"
    )


def test_a_tool_that_saves_only_the_tracked_signals_says_so(tmp_path, capsys):
    mat = {"dtype": "<f8", "trajectories": {"x": RAMP}}
    a = _run(tmp_path / "main", {A: mat}, backend="OpenModelica")
    b = _run(tmp_path / "fix", {A: mat}, backend="OpenModelica")
    lines = _diff(capsys, f"main={a}", f"fix={b}")
    assert lines[4] == (
        "| OpenModelica/linux (this tool saves only the tracked signals; parameters "
        "are kept) | 1 of 1 | all identical | none |"
    )


def _hashes(*dirs):
    out = {}
    for d in dirs:
        for root, names, files in os.walk(d):
            for name in names + files:
                p = os.path.join(root, name)
                st = os.lstat(p)
                body = b""
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        body = f.read()
                out[p] = (st.st_mtime_ns, hashlib.sha256(body).hexdigest())
    return out


def test_a_run_is_only_read(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    a = _run(tmp_path / "main", {A: mat, B: mat}, pending=(B,))
    b = _run(tmp_path / "fix", {A: {"trajectories": {"x": RAMP[::-1]}}, B: mat})
    before = _hashes(a, b)
    _diff(capsys, f"main={a}", f"fix={b}", "--details")
    _diff(capsys, f"main={a}", f"fix={b}", "--format", "json")
    assert _hashes(a, b) == before


# --- JSON ----------------------------------------------------------------------


def test_the_json_holds_every_group_that_is_not_identical_with_all_its_names(
    tmp_path, capsys
):
    old = {
        "trajectories": {"x": RAMP, "same": RAMP},
        "parameters": {"k": 1.0},
        "aliases": {"x_alias": ("x", False)},
    }
    new = {
        "trajectories": {"x": [0.0, 1.0, 2.5, 3.0, 4.0], "same": RAMP},
        "parameters": {"k": 2.0},
        "aliases": {"x_alias": ("x", False)},
    }
    data = json.loads(
        "\n".join(_diff(capsys, *_pair(tmp_path, old, new), "--format", "json"))
    )
    assert data["labels"] == ["main", "fix"]
    (folder,) = data["folders"]
    assert (folder["old"]["label"], folder["new"]["label"]) == ("main", "fix")
    (test,) = folder["tests"]
    assert test["signals"]["counts"] == {
        "identical": 1,
        "rounding": 0,
        "near zero": 0,
        "derivative": 0,
        "changed": 1,
    }
    (group,) = test["signals"]["groups"]
    assert (group["names"], group["kind"]) == (["x", "x_alias"], "changed")
    assert group["change"]["largest"] == pytest.approx(12.5)
    assert group["change"]["at"] == 2.0
    assert test["parameters"]["groups"] == [
        {"names": ["k"], "kind": "differs", "old": 1.0, "new": 2.0}
    ]


# --- what is asked for, and refusals ---------------------------------------------


def test_test_narrows_the_tests_and_details_with_no_name_folds_the_ones_that_differ(
    tmp_path, capsys
):
    mat = {"trajectories": {"x": RAMP}}
    moved = {"trajectories": {"x": RAMP[::-1]}}
    a = _run(tmp_path / "main", {A: mat, B: mat})
    b = _run(tmp_path / "fix", {A: moved, B: mat})
    lines = _diff(capsys, f"main={a}", f"fix={b}", "--details")
    summaries = [line for line in lines if line.startswith("<summary>")]
    assert summaries == [
        "<summary><code>A</code> · Dymola/linux — 1 changed (100 % of range)</summary>"
    ]
    lines = _diff(capsys, f"main={a}", f"fix={b}", "--test", "B")
    assert _row(lines) == "| Dymola/linux | 1 of 1 | all identical | none |"


def test_a_name_details_does_not_compare_is_refused(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    runs = _pair(tmp_path, mat, mat)
    assert _refused(capsys, *runs, "--details", "Nope") == (
        "Error: --details: no test this diff compares is named Nope (a short name "
        "such as A, or a full model id)"
    )
    assert _refused(capsys, *runs, "--test", "Nope") == (
        "Error: --test: no run given is of a test named Nope (a short name such as "
        "A, or a full model id)"
    )


def test_a_third_label_is_refused(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    runs = [_run(tmp_path / n, {A: mat}) for n in "abc"]
    assert _refused(capsys, *(f"{n}={r}" for n, r in zip("abc", runs))) == (
        "Error: diff compares two runs; give two labels (a third: c)"
    )


def test_two_labels_over_two_tools_are_two_runs(tmp_path, capsys):
    """Labels are counted, not arguments: each label once per tool."""
    mat = {"trajectories": {"x": RAMP}}
    om = {"dtype": "<f8", "trajectories": {"x": RAMP}}
    a = _run(tmp_path / "a", {A: mat})
    b = _run(tmp_path / "b", {A: mat})
    a_om = _run(tmp_path / "a_om", {A: om}, backend="OpenModelica")
    b_om = _run(tmp_path / "b_om", {A: om}, backend="OpenModelica")
    lines = _diff(capsys, f"main={a}", f"main={a_om}", f"fix={b}", f"fix={b_om}")
    assert lines[0] == "**Every variable, main → fix** (1 test)"
    assert [line.split(" |")[0] for line in lines if line.startswith("| ")][2:] == [
        "| Dymola/linux",
        "| OpenModelica/linux (this tool saves only the tracked signals; parameters "
        "are kept)",
    ]


def test_runs_with_no_test_in_common_are_refused(tmp_path, capsys):
    mat = {"trajectories": {"x": RAMP}}
    a = _run(tmp_path / "main", {A: mat})
    b = _run(tmp_path / "fix", {B: mat})
    assert _refused(capsys, f"main={a}", f"fix={b}") == (
        "Error: the runs of Dymola/linux hold no test in common"
    )


@pytest.mark.parametrize("size", ["0", "-1", "inf", "nan"])
def test_near_zero_takes_a_finite_size_above_zero(tmp_path, capsys, size):
    assert _parse_refused(capsys, "a=x", "b=y", "--near-zero", size) == (
        f"dstf diff: error: argument --near-zero: {size}: must be a finite number "
        "above 0 (a size, in the variables' units)"
    )


def test_details_before_the_runs_does_not_take_them_as_names(capsys):
    assert _parse_refused(capsys, "--details", "a=1", "b=2") == (
        "dstf diff: error: argument --details: a=1: a test name holds no '=' (a "
        "LABEL=DIR after --details is read as one of its values)"
    )


def test_a_test_name_holding_an_equals_sign_after_the_runs_is_refused_plainly(capsys):
    assert _parse_refused(capsys, "a=1", "b=2", "--test", "Lib.M=1") == (
        "dstf diff: error: argument --test: Lib.M=1: a test name holds no '=' (a "
        "LABEL=DIR after --test is read as one of its values)"
    )


def test_markdown_over_what_a_pull_request_holds_is_noted_on_stderr(tmp_path, capsys):
    long = {
        f"v{i}_" + "x" * 3500: [0.0, 1.0, 2.0, 3.0, float(i + 5)] for i in range(20)
    }
    old = {"trajectories": {n: RAMP for n in long}}
    new = {"trajectories": long}
    code = main(["diff", *_pair(tmp_path, old, new), "--details"])
    captured = capsys.readouterr()
    assert code == 0
    assert len(captured.out) > 65_536
    assert captured.err == (
        f"Note: this Markdown is {len(captured.out):,} characters; a GitHub "
        "pull-request body or comment holds 65,536.\n"
    )


# --- what the reports hold besides --------------------------------------------------


def test_an_event_stored_another_number_of_times_is_said(tmp_path, capsys):
    """Main stores the event at 2 s three times, fix twice: the middle sample
    has nothing to meet."""
    old = {
        "time": [0.0, 1.0, 2.0, 2.0, 2.0, 3.0],
        "trajectories": {"x": [0, 1, 2, 3, 4, 5]},
    }
    new = {"time": [0.0, 1.0, 2.0, 2.0, 3.0], "trajectories": {"x": [0, 1, 2, 4, 5]}}
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    assert (
        "| `x` | 0 %; 1 old points inside an event the new run stores another "
        "number of times |"
    ) in lines


def test_the_json_is_strict_with_a_value_not_finite(tmp_path, capsys):
    old = {"dtype": "<f8", "trajectories": {"x": RAMP}, "parameters": {"p": 1.0}}
    new = {
        "dtype": "<f8",
        "trajectories": {"x": [0.0, float("inf"), 2.0, 3.0, 4.0]},
        "parameters": {"p": float("nan")},
    }
    text = "\n".join(_diff(capsys, *_pair(tmp_path, old, new), "--format", "json"))

    def refuse(literal):
        raise ValueError(literal)

    (test,) = json.loads(text, parse_constant=refuse)["folders"][0]["tests"]
    assert test["parameters"]["groups"] == [
        {
            "names": ["p"],
            "kind": "differs",
            "old": 1.0,
            "new": None,
            "new_non_finite": "nan",
        }
    ]
    (group,) = test["signals"]["groups"]
    assert (group["change"]["non_finite"], group["change"]["non_finite_at"]) == (1, 1.0)


def test_near_zero_lists_the_largest_absolute_move_first(tmp_path, capsys):
    """`a` moves 10 % of its tiny range, `b` 1 % of a range a hundred times
    larger: `b` moved more, in the variables' own units."""
    old = {
        "trajectories": {
            "a": [0.0, 1e-7, 2e-7, 3e-7, 4e-7],
            "b": [0.0, 1e-5, 2e-5, 3e-5, 4e-5],
        }
    }
    new = {
        "trajectories": {
            "a": [0.0, 1e-7, 2.4e-7, 3e-7, 4e-7],
            "b": [0.0, 1e-5, 2.04e-5, 3e-5, 4e-5],
        }
    }
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details", "--near-zero", "1")
    (largest,) = [line for line in lines if line.startswith("- Near zero")]
    assert largest == "- Near zero, the largest |fix − main|: 4e-07 (`b`, 2 s)"


def test_the_differing_parameters_are_listed_by_name_and_capped(tmp_path, capsys):
    names = [f"p{i:02d}" for i in range(24, -1, -1)]  # written last-first
    old = {"trajectories": {"x": RAMP}, "parameters": {n: int(n[1:]) for n in names}}
    new = {
        "trajectories": {"x": RAMP},
        "parameters": {n: -int(n[1:]) - 1 for n in names},
    }
    lines = _diff(capsys, *_pair(tmp_path, old, new), "--details")
    (said,) = [line for line in lines if line.startswith("- Parameters that differ")]
    shown = "; ".join(f"`p{i:02d}` {i} → {-i - 1}" for i in range(20))
    assert said == f"- Parameters that differ, main → fix: {shown}; and 5 more"
