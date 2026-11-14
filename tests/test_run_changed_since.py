"""`dstf run --changed-since REV` through `main`: the run simulates only the
tests that use what changed, and says so on the console and in its files.

Over tests/test_uses_cli.py's project — a git copy of the bundled Modelica
library whose testing.json names a fake backend — with that fake's table of
what each test uses, and a subclass of it that "simulates" at the
simulator's boundary, as the bundled DecayBackend plug-in does: it writes a
result file per test and reads it back."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import PROJECT_ROOT
from dstf.backends import _REGISTRY, register
from dstf.backends.types import Capability, TestResult, TestRunResult, VariableResult
from dstf.cli import main
from dstf.config import Config
from dstf.discovery.unit_tests import unit_test_variables
from test_uses_cli import (  # noqa: F401  (the fixtures, re-used)
    CANNOT,
    FAKE,
    HEAT,
    LIB,
    SIMPLE,
    _touch,
    env,
    nothing_installed,
    repo,
)

RUNS = "FakeRuns"
CONSTANT = f"{LIB}.Examples.ConstantTest"
NO_WORKERS = "FakeNoWorkers"


@pytest.fixture
def proj(repo, tmp_path):  # noqa: F811
    """`repo` run by `FakeRuns`: `FakeUses`'s answers, and a simulation
    that writes each tracked variable as sin(t). ``proj.simulated`` the
    tests simulated, ``proj.order`` "asked" and "simulated" as they came;
    ``proj.work`` the work directory each run is given."""
    proj = repo
    proj.simulated, proj.order = [], []
    proj.work = tmp_path / "work"

    class FakeRuns(_REGISTRY[FAKE]):
        def uses(self, tests):
            proj.order.append("asked")
            return super().uses(tests)

        def run_single_test(self, test, test_key, index, total):
            proj.order.append("simulated")
            proj.simulated.append(test.model_id)
            began = time.monotonic()
            directory = self.config.work_dir / test_key
            directory.mkdir(parents=True, exist_ok=True)
            t = np.linspace(0.0, 1.0, 11)
            names = [v.name for v in unit_test_variables(test)]
            result = {"time": t.tolist(), **{n: np.sin(t).tolist() for n in names}}
            (directory / "result.json").write_text(json.dumps(result))
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=time.monotonic() - began,
            )

        def read_result(self, test, test_key, run_result):
            path = self.config.work_dir / test_key / "result.json"
            try:
                result = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                return TestResult(test.model_id, False, error_message=f"{path}: {exc}")
            t = np.asarray(result.pop("time"), dtype=float)
            return TestResult(
                test.model_id,
                True,
                variables=[
                    VariableResult(index=i, time=t, values=np.asarray(v), name=n)
                    for i, (n, v) in enumerate(result.items(), start=1)
                ],
            )

    class FakeNoWorkers(FakeRuns):
        """Declares workers that stay alive, whose interface is missing
        here: what --batch is for."""

        capabilities = frozenset({Capability.USES, Capability.PERSISTENT_WORKERS})

        @classmethod
        def persistent_backend_cls(cls):
            return NoInterface

    class NoInterface(FakeNoWorkers):
        @classmethod
        def preflight(cls, config):
            raise RuntimeError("The fake's worker interface is not installed.")

    register(RUNS)(FakeRuns)
    register(NO_WORKERS)(FakeNoWorkers)
    proj.data["backend"] = RUNS
    proj.config.write_text(json.dumps(proj.data), encoding="utf-8")
    yield proj
    del _REGISTRY[RUNS], _REGISTRY[NO_WORKERS]


def _run(proj, capsys, *argv):
    rc = main(
        ["run", "--config", str(proj.config), "--work-dir", str(proj.work)]
        + ["--no-browser", *argv]
    )
    out, err = capsys.readouterr()
    return rc, out, err


def _files(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under ``root`` but git's, with its time and size."""
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if ".git" not in p.relative_to(root).parts
    }


def _read(work: Path, name: str) -> dict:
    return json.loads((work / name).read_text(encoding="utf-8"))


def _accept_both(proj, capsys) -> None:
    """Baselines for SimpleTest and ConstantTest in the fake's partition,
    committed: the run that follows scores them PASS."""
    rc, out, err = _run(proj, capsys, "--accept", "--filter", f"{SIMPLE},{CONSTANT}")
    assert rc == 0, out + err
    proj.git.commit("baselines")
    proj.short = proj.git.git("rev-parse", "--short", "HEAD")
    proj.simulated.clear()
    proj.order.clear()


# --------------------------------------------------------------------------
# The selected run
# --------------------------------------------------------------------------


def test_only_the_tests_that_use_what_changed_are_simulated(proj, capsys):
    _accept_both(proj, capsys)
    _touch(proj, "Examples", "SimpleTest.mo")
    _touch(proj, "Examples", "ConstantTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert rc == 0, out + err
    assert sorted(proj.simulated) == [CONSTANT, SIMPLE]
    # the simulator is asked once, before the run simulates anything
    assert proj.order == ["asked", "simulated", "simulated"]
    assert out.startswith(
        f"Changed since HEAD ({proj.short} baselines): 2 files\n"
        f"  Examples/ConstantTest.mo  {CONSTANT}\n"
        f"  Examples/SimpleTest.mo    {SIMPLE}\n"
        "Selected 2 of 14 tests: the ones that use what changed. Not a full run.\n"
    ), out
    assert "\nResults: 2/2 passed (2 of 14 tests selected; not a full run)\n" in out
    assert "Could not be asked" not in out
    # the progress lines on stderr, as `dstf uses` says them
    assert err.startswith(
        "Comparing 25 files with HEAD ...\nAsking FakeRuns what 14 tests use ...\n"
    ), err


def test_the_run_s_files_say_what_it_was_selected_by(proj, capsys):
    _accept_both(proj, capsys)
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert rc == 0, out + err
    block = {
        "changed_since": "HEAD",
        "commits": [proj.short],
        "files": 1,
        "selected": 1,
        "of": 14,
        "could_not_be_asked": 0,
    }
    assert _read(proj.work, "status.json")["selection"] == block
    verdicts = _read(proj.work, "verdicts.json")
    assert verdicts["selection"] == block
    assert [t["model_id"] for t in verdicts["tests"]] == [SIMPLE]

    # a compare over the work dir still says the last run was a selection,
    # in its files and on its pages
    page = proj.cwd / "page.html"
    rc = main(
        ["compare", "--config", str(proj.config), "--work-dir", str(proj.work)]
        + ["--no-browser", "--report-format", "html", "-o", str(page)]
    )
    capsys.readouterr()
    assert rc == 0
    assert _read(proj.work, "status.json")["selection"] == block
    assert _read(proj.work, "verdicts.json")["selection"] == block
    said = (
        "Not a full run. 1 of 14 tests was run: the one that uses what changed "
        "since HEAD (1 file). The other 13 were not run."
    )
    assert _painted(proj.work / "dashboard.html") == _painted(page) == said

    # a later run without the flag: null, and nothing said of a selection
    rc, out, err = _run(proj, capsys, "--filter", SIMPLE)
    assert rc == 0, out + err
    assert _read(proj.work, "status.json")["selection"] is None
    assert _read(proj.work, "verdicts.json")["selection"] is None
    assert "\nResults: 1/1 passed\n" in out
    assert _painted(proj.work / "dashboard.html") is None


def test_the_filter_narrows_first_and_all_of_it_selected_is_no_partial_run(
    proj, capsys
):
    _accept_both(proj, capsys)
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD", "--filter", SIMPLE)
    assert rc == 0, out + err
    assert "Selected the 1 test: it uses what changed.\n" in out
    assert "\nResults: 1/1 passed\n" in out
    selection = _read(proj.work, "status.json")["selection"]
    assert (selection["selected"], selection["of"]) == (1, 1)


def test_with_merge_the_counts_are_the_merged_scope_s(proj, capsys):
    rc, full, err = _run(proj, capsys)
    assert proj.simulated and len(proj.simulated) == 14, full + err
    results = [line for line in full.splitlines() if line.startswith("Results: ")]
    assert len(results) == 1 and results[0].endswith("/14 passed")
    proj.simulated.clear()
    _touch(proj, "Examples", "SimpleTest.mo")

    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD", "--merge")
    assert proj.simulated == [SIMPLE], out + err
    # every test scored as the full run scored it; the run was of one
    assert f"\n{results[0]} (1 of 14 tests selected; not a full run)\n" in out
    assert len(_read(proj.work, "verdicts.json")["tests"]) == 14
    status = _read(proj.work, "status.json")
    assert [t["model_id"] for t in status["tests"]] == [SIMPLE]
    assert (status["selection"]["selected"], status["selection"]["of"]) == (1, 14)


def test_a_test_that_could_not_be_asked_is_run_and_never_said_to_use_it(proj, capsys):
    answer = proj.answer
    proj.answer = lambda m: (
        "Error: class\n  HeatRodTest not found." if m == HEAT else answer(m)
    )
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert sorted(proj.simulated) == [HEAT, SIMPLE]
    assert (
        "Selected 2 of 14 tests: 1 uses what changed, 1 could not be asked "
        "what it uses and is run. Not a full run.\n"
        "Could not be asked:\n"
        f"  {HEAT} — Error: class HeatRodTest not found.\n"
    ) in out, out
    assert _read(proj.work, "status.json")["selection"]["could_not_be_asked"] == 1


def test_an_answer_given_as_a_list_selects_as_a_frozenset_does(proj, capsys):
    """A plug-in's `uses` may answer with any collection of names."""
    answer = proj.answer
    proj.answer = lambda m: sorted(answer(m))
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert rc == 0, out + err
    assert proj.simulated == [SIMPLE]


def test_when_no_selected_test_could_be_asked_none_is_said_to_use_it(proj, capsys):
    proj.answer = lambda m: "Error: class not found."
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(
        proj, capsys, "--changed-since", "HEAD", "--filter", f"{SIMPLE},{HEAT}"
    )
    assert (
        "Selected all 2 tests: none could be asked what it uses, so each is run.\n"
        "Could not be asked:\n"
    ) in out, out
    assert "use what changed" not in out and "uses what changed" not in out


def test_past_twenty_the_tests_not_asked_are_counted(proj, capsys):
    spec = proj.lib / "Resources" / "ReferenceResults" / "test_spec.json"
    data = json.loads(spec.read_text(encoding="utf-8"))
    missing = [f"{LIB}.Examples.Missing{i:02d}" for i in range(22)]
    data["tests"] += [{"model": m} for m in missing]
    spec.write_text(json.dumps(data), encoding="utf-8")
    answer = proj.answer
    proj.answer = lambda m: "Error: class not found." if "Missing" in m else answer(m)
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert (
        "Selected 23 of 36 tests: 1 uses what changed, 22 could not be asked what "
        "they use and are run. Not a full run.\n"
    ) in out, out
    listed = [
        line for line in out.splitlines() if line.endswith("— Error: class not found.")
    ]
    assert len(listed) == 20
    assert f"  {missing[19]} — Error: class not found.\n  … and 2 more\n" in out


def test_the_block_counts_the_files_that_differ_not_the_classes(proj, capsys):
    image = proj.lib / "Resources" / "Images" / "ThermostatBuildingTest.png"
    image.write_bytes(image.read_bytes() + b"\0")
    _touch(proj, "Examples", "SimpleTest.mo")
    _run(proj, capsys, "--changed-since", "HEAD", "--filter", SIMPLE)
    assert _read(proj.work, "status.json")["selection"]["files"] == 2


def test_a_file_no_class_stores_runs_every_test_and_asks_no_simulator(proj, capsys):
    image = proj.lib / "Resources" / "Images" / "ThermostatBuildingTest.png"
    image.write_bytes(image.read_bytes() + b"\0")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert proj.asked == [] and len(proj.simulated) == 14
    assert "1 file that no class stores changed: every test is run (14 tests).\n" in out
    assert "not a full run" not in out
    selection = _read(proj.work, "status.json")["selection"]
    assert (selection["selected"], selection["of"]) == (14, 14)


def _painted(page: Path) -> str | None:
    """The run notice of the page, as text; None when it has none."""
    import re

    import markupsafe

    found = re.search(
        r'<div class="run-notice" id="run-notice">(.*?)</div>',
        page.read_text(encoding="utf-8"),
        re.S,
    )
    return markupsafe.Markup(found.group(1)).striptags() if found else None


def test_the_first_paint_says_this_run_s_selection_not_the_last_run_s(
    proj, capsys, monkeypatch
):
    """The page a browser tab opens on, and the one the run paints as it
    starts, are written before this run's status.json: each says what THIS
    run is, at both sites (the command's, the pipeline's)."""
    painted = []
    # the browser the command opens, and the moment the run starts
    monkeypatch.setattr(
        "dstf.reporting.report_suite._launch_opener",
        lambda page: painted.append(("opened", _painted(page))),
    )
    fake = _REGISTRY[RUNS]
    run_tests = fake.run_tests

    def started(self, tests):
        painted.append(("started", _painted(self.config.work_dir / "dashboard.html")))
        return run_tests(self, tests)

    monkeypatch.setattr(fake, "run_tests", started)
    _touch(proj, "Examples", "SimpleTest.mo")
    argv = ["run", "--config", str(proj.config), "--work-dir", str(proj.work)]
    assert main([*argv, "--changed-since", "HEAD"]) == 0
    selected = painted[:]
    painted.clear()
    main([*argv, "--filter", f"{LIB}.Examples.S*"])
    assert painted == [("opened", None), ("started", None)]
    painted.clear()
    assert main([*argv, "--changed-since", "HEAD"]) == 0
    capsys.readouterr()
    said = (
        "Not a full run. 1 of 14 tests is being run: the one that uses what "
        "changed since HEAD (1 file). The other 13 are not run."
    )
    assert selected == painted == [("opened", said), ("started", said)]


# --------------------------------------------------------------------------
# Nothing to run: exit 0, and nothing is written
# --------------------------------------------------------------------------


def test_nothing_changed_runs_nothing_and_creates_no_work_dir(proj, capsys):
    # a baseline in the run's partition: a run that went on would write
    # reference_manifest.json into the work dir before simulating
    _accept_both(proj, capsys)
    proj.work = proj.cwd.parent / "another-work-dir"
    before = _files(proj.lib.parent)
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD", "--report")
    assert (rc, out) == (
        0,
        f"Nothing changed since HEAD ({proj.short} baselines).\nNo test to run.\n",
    ), err
    assert not proj.work.exists()
    assert proj.asked == [] and proj.simulated == []
    assert _files(proj.lib.parent) == before
    assert list(proj.cwd.iterdir()) == []


def test_a_change_no_test_uses_runs_nothing_and_writes_no_report_file(proj, capsys):
    _accept_both(proj, capsys)
    proj.work = proj.cwd.parent / "another-work-dir"
    (proj.lib / "Scratch").mkdir()
    (proj.lib / "Scratch" / "Try.mo").write_text("model Try end Try;\n")
    before = _files(proj.lib.parent)
    rc, out, err = _run(
        proj, capsys, "--changed-since", "HEAD", "--report-format", "junit"
    )
    assert rc == 0, err
    assert out.endswith("No test uses what changed: no test to run.\n")
    assert len(proj.asked) == 1 and proj.simulated == []
    assert not proj.work.exists()
    assert _files(proj.lib.parent) == before
    assert list(proj.cwd.iterdir()) == []


def test_nothing_to_run_leaves_an_earlier_run_s_files_as_they_are(proj, capsys):
    _touch(proj, "Examples", "SimpleTest.mo")
    assert _run(proj, capsys, "--changed-since", "HEAD")[0] == 0
    proj.git.commit("edited")
    before = _files(proj.work)
    assert "verdicts.json" in before
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert rc == 0 and out.endswith("No test to run.\n"), out + err
    assert _files(proj.work) == before


def test_nothing_to_run_leaves_a_corrupt_manifest_where_it_is(proj, capsys):
    """The choice comes before the manifest's check of orphans, which moves
    a corrupt batch_manifest.json aside."""
    _touch(proj, "Examples", "SimpleTest.mo")
    assert _run(proj, capsys, "--changed-since", "HEAD")[0] == 0
    proj.git.commit("edited")
    manifest = proj.work / "batch_manifest.json"
    manifest.write_text("{ not json", encoding="utf-8")
    before = _files(proj.work)
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert rc == 0 and out.endswith("No test to run.\n"), out + err
    assert _files(proj.work) == before
    assert manifest.read_text(encoding="utf-8") == "{ not json"


# --------------------------------------------------------------------------
# Refused: one sentence, nothing simulated, nothing written
# --------------------------------------------------------------------------


def test_a_filter_that_matches_nothing_is_said_before_git_is_read(proj, capsys):
    rc, out, err = _run(proj, capsys, "--filter", "Nope.*", "--changed-since", "nope")
    assert (rc, out, err) == (1, "No tests matched the filter.\n", "")
    assert not proj.work.exists()


def test_git_that_cannot_answer_is_exit_2(proj, capsys):
    rc, out, err = _run(proj, capsys, "--changed-since", "nope")
    assert (rc, out) == (2, "")
    assert err == f"Error: unknown revision 'nope' in {proj.lib.parent}\n"
    assert not proj.work.exists()


def test_a_backend_that_cannot_say_what_a_test_uses_is_a_config_error(proj, capsys):
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD", "--backend", CANNOT)
    assert (rc, out) == (2, "")
    assert err == (
        "Config error: --changed-since does not apply to the FakeCannot backend: "
        "it cannot say what a test uses. Run without --changed-since.\n"
    )
    assert not proj.work.exists() and proj.simulated == []


def test_a_simulator_that_could_not_be_asked_is_exit_1(proj, capsys):
    _touch(proj, "Examples", "SimpleTest.mo")
    proj.fails = RuntimeError("the simulator stopped")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    assert (rc, out) == (1, "")
    assert err.endswith("FakeRuns could not be asked: the simulator stopped\n")
    assert not proj.work.exists() and proj.simulated == []


def test_beside_batch_the_simulator_is_still_asked_through_its_workers(proj, capsys):
    _touch(proj, "Examples", "SimpleTest.mo")
    rc, out, err = _run(
        proj, capsys, "--changed-since", "HEAD", "--backend", NO_WORKERS, "--batch"
    )
    assert (rc, out) == (1, "")
    assert err.split("\n", 1)[1] == (
        f"Persistent-worker {NO_WORKERS} unavailable.\n\n"
        "The fake's worker interface is not installed.\n\n"
        "--changed-since asks through the backend's workers that stay alive, "
        "also beside --batch: run without --changed-since.\n"
    )
    assert not proj.work.exists() and proj.simulated == []
    # without --batch, the sentence `dstf uses` says, and no more
    rc, out, err = _run(
        proj, capsys, "--changed-since", "HEAD", "--backend", NO_WORKERS
    )
    assert (rc, out) == (1, "")
    assert err.endswith("The fake's worker interface is not installed.\n"), err


def test_beside_batch_git_s_refusal_is_git_s_sentence_alone(proj, capsys):
    rc, out, err = _run(
        proj, capsys, "--changed-since", "nope", "--backend", NO_WORKERS, "--batch"
    )
    assert (rc, out) == (2, "")
    assert err == f"Error: unknown revision 'nope' in {proj.lib.parent}\n"


def test_a_project_that_is_not_modelica_is_a_config_error(tmp_path, capsys):
    """Said before the capability: the Python backend cannot answer
    either, and its sentence would be beside the point."""
    import shutil

    lib = tmp_path / "py"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    assert Config(config_file=config).source_type == "python"
    work = tmp_path / "work"
    rc = main(
        ["run", "--config", str(config), "--work-dir", str(work), "--no-browser"]
        + ["--changed-since", "HEAD"]
    )
    out, err = capsys.readouterr()
    assert (rc, out) == (2, "")
    assert err == (
        "Config error: --changed-since reads a Modelica library's files; this "
        "project's source_type is python.\n"
    )
    assert not work.exists()


# --------------------------------------------------------------------------
# The library call
# --------------------------------------------------------------------------


def test_one_backend_run_twice_records_null_the_second_time(proj):
    from dstf.backends import select_backend
    from dstf.discovery.test_registry import discover_tests
    from dstf.pipeline import run_pipeline
    from dstf.storage.reference_store import ReferenceStore

    config = Config(config_file=proj.config, work_dir=proj.work)
    tests = [t for t in discover_tests(config) if t.model_id == SIMPLE]
    backend = select_backend(config, persistent=True, to_run=True)
    backend.quiet = True
    store = ReferenceStore(config)
    block = {
        "changed_since": "main",
        "commits": ["abc1234"],
        "files": 1,
        "selected": 1,
        "of": 14,
        "could_not_be_asked": 0,
    }
    run_pipeline(config, tests, backend=backend, store=store, selection=block)
    assert _read(proj.work, "status.json")["selection"] == block
    run_pipeline(config, tests, backend=backend, store=store)
    assert _read(proj.work, "status.json")["selection"] is None
