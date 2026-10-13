"""Live results in the Session (D151): a running test keeps its layout and
reads its growing result file; a run that did not finish shows what it
wrote and is never scored. Real runs of the Python backend made live
(`live_fake`): a real pool, a real subprocess, a real DSresult file growing
a row at a time."""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from dstf.authoring import capabilities as caps
from dstf.authoring import live_rows
from dstf.authoring.builtin import register_builtins
from dstf.authoring.builtin.accept import PARTIAL, Accept
from dstf.authoring.layout import layout_op, new_watch
from dstf.authoring.live_rows import NOT_SCORED, WAITING, Partial
from dstf.authoring.queue_rows import queue_row
from dstf.authoring.session import Selection, Session, SessionView
from dstf.authoring.situation import strip_for
from dstf.authoring.spec_document import PatchOp
from dstf.backends.common.live_mat import GONE, RUNNING, LiveMatReader
from dstf.backends.pool import ItemState
from dstf.reporting.status import CANCELLED, FAIL, NOT_RUN, SIM_FAIL, classify
from dstf.reporting.tree_rows import leaves_of
from live_fake import live_project, make_live, steer
from test_session import A, C, D
from test_session_queue import Loop


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


class Harness:
    def __init__(self, tmp_path) -> None:
        self.cfg, self.work, self.examples = live_project(tmp_path)
        self.loop = Loop()
        self.session = Session(post=self.loop.post)
        self.session.open(self.cfg, work_dir=self.work)
        self.session.rescan_done.wait(30)
        self.loop.pump()
        #: What the test's one page draws, by model: asked again before
        #: every poll, as a page asks every second (`ask`).
        self.drawn: dict[str, set[str]] = {}

    @property
    def runs(self):
        return self.session.live_runs

    def ask(self, model_id: str, *names: str) -> None:
        self.drawn[model_id] = set(names)

    def poll(self) -> None:
        """One second of the shell's: the page asks, the Session reads."""
        for model_id, names in self.drawn.items():
            self.runs.ask(model_id, "page", names)
        self.runs.poll()

    def steer(self, model_id: str, **files) -> None:
        steer(self.examples, model_id, **files)

    def polled(self, pred, what: str = "condition") -> None:
        """Poll as the shell does, once a look, until ``pred``."""

        def look() -> bool:
            self.poll()
            return bool(pred())

        self.loop.until(look, what=what)

    def running(self, model_id: str) -> None:
        s = self.session
        self.loop.until(
            lambda: (
                s.queue_state(model_id) is ItemState.RUNNING
                and model_id not in s.project.results
            ),
            what=f"{model_id} running",
        )

    def at(self, model_id: str, time: float) -> None:
        """Until the reader has seen ``model_id``'s file reach ``time``."""
        trace = self.runs._runs[model_id].trace
        self.polled(
            lambda: trace.time is not None and abs(trace.time - time) < 1e-9,
            f"{model_id} at t = {time}",
        )

    def ended(self, model_id: str) -> None:
        self.loop.until(
            lambda: (
                self.session.queue_state(model_id)
                not in (ItemState.WAITING, ItemState.RUNNING)
            ),
            what=f"{model_id} ended",
        )

    def view(self, *models: str) -> SessionView:
        return SessionView(self.session, Selection(tests=frozenset(models)))


@pytest.fixture
def h(tmp_path, monkeypatch):
    make_live(monkeypatch.setattr)
    harness = Harness(tmp_path)
    yield harness
    for hold in harness.examples.glob("*.hold"):
        hold.unlink()
    harness.session.stop_queue()
    harness.session.wait_background(30)
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]


@pytest.fixture
def plain(tmp_path):
    """The same project on the Python backend as it is: no live view."""
    harness = Harness(tmp_path)
    yield harness
    for hold in harness.examples.glob("*.hold"):
        hold.unlink()
    harness.session.stop_queue()
    harness.session.wait_background(30)


def _steps(count: int) -> list[float]:
    """The first ``count`` times of a 21-row run over one second."""
    return np.linspace(0.0, 1.0, 21)[:count].tolist()


def _subjects(session: Session, model_id: str) -> list[tuple[str, ...]]:
    return [plot.subject for plot in session.plots(model_id)]


def _polls(monkeypatch) -> list[list[str]]:
    """Every name list a live reader is polled with, in order."""
    seen: list[list[str]] = []
    poll = LiveMatReader.poll

    def spy(self, names=()):
        seen.append(list(names))
        return poll(self, names)

    monkeypatch.setattr(LiveMatReader, "poll", spy)
    return seen


# -- the running test ----------------------------------------------------------


def test_a_running_test_keeps_its_layout_and_says_waiting(h):
    s = h.session
    before = _subjects(s, A)
    assert before == [("x",), ("y",)]
    paths = [leaf["path"] for leaf in leaves_of(s.tree_view(A))]
    assert classify(s.comparisons[A]) == FAIL

    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)

    # its result is gone, and with it every verdict — not its plots
    assert classify(s.comparisons[A]) == NOT_RUN
    assert _subjects(s, A) == before
    assert s.tracked(A) == ["x", "y"]
    view = s.tree_view(A)
    assert view["unscored"] == WAITING
    leaves = leaves_of(view)
    assert [leaf["path"] for leaf in leaves] == paths
    assert all(leaf["criterion"] == WAITING for leaf in leaves)
    assert not any("passed" in leaf for leaf in leaves)
    assert s.partial(A) is None and s.live_runs.word(A) == WAITING


def test_the_trace_grows_and_the_words_say_how_far(h):
    s = h.session
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    row = queue_row(s, A)
    assert row.progress == "starting" and row.words == "running · starting"

    h.ask(A, "x")
    h.at(A, 0.2)  # rows 0 … 4 of 21 over one second
    row = queue_row(s, A)
    assert row.progress == "t = 0.2 / 1 s"
    assert row.words == "running · t = 0.2 / 1 s"

    view = s.view(A, "x")
    assert view.act_time.tolist() == _steps(5)
    assert np.allclose(view.act_values, 1.25 * view.act_time)
    # the baseline as always, and no criterion's geometry
    assert view.ref_values.tolist() == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert view.leaves == () and view.full_samples["act"] == 5
    # the figure: the Actual so far, on an axis that spans the whole run
    figure = s.plot_figure(A, 0)
    actual = next(tr for tr in figure["data"] if tr["meta"]["id"] == "actual")
    assert list(actual["x"]) == _steps(5)
    assert figure["layout"]["xaxis"]["range"] == [0.0, 1.0]
    assert not any(sh.get("name") == live_rows.STOPPED for sh in _shapes(figure))

    h.steer(A, hold=12)
    h.at(A, 0.55)
    assert s.view(A, "x").act_time.tolist() == _steps(12)
    assert len(s.view(A, "x").act_time) == 12


def _shapes(figure: dict) -> list[dict]:
    return figure["layout"].get("shapes", [])


def test_only_the_names_a_page_asked_for_are_read(h, monkeypatch):
    seen = _polls(monkeypatch)
    s = h.session
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    h.polled(lambda: h.runs._runs[A].trace.state == RUNNING, "the file's header")
    assert seen and all(names == [] for names in seen)  # nobody asked

    del seen[:]
    h.runs.ask(A, "one page", {"y"})
    h.runs.ask(A, "another", {"x", "y"})
    h.runs.poll()
    assert seen == [["x", "y"]]  # one read for both pages
    assert s.view(A, "y").act_time is not None

    # a page that stopped asking costs two more reads, then nothing
    for _ in range(3):
        h.runs.poll()
    assert seen[-1] == []


def test_no_file_is_read_on_a_backend_without_a_live_view(plain, monkeypatch):
    seen = _polls(monkeypatch)
    s = plain.session
    before = _subjects(s, A)
    plain.steer(A, hold=5)
    s.enqueue([A])
    plain.running(A)
    plain.ask(A, "x")
    for _ in range(3):
        plain.poll()

    assert seen == [] and plain.runs._runs[A].reader is None
    assert plain.runs.polled(A) is False
    row = queue_row(s, A)
    assert row.progress is None and row.words.endswith(" s")
    # the layout and the waiting rows are every backend's
    assert _subjects(s, A) == before
    assert s.tree_view(A)["unscored"] == WAITING
    view = s.view(A, "x")
    assert view.act_time is None and view.ref_values is not None
    assert "range" not in s.plot_figure(A, 0)["layout"]["xaxis"]


def test_the_end_swaps_to_the_scored_result_and_keeps_no_sample(h):
    s = h.session
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.2)

    h.steer(A, hold=None)
    h.ended(A)

    assert s.queue_state(A) is ItemState.FINISHED
    assert h.runs.kept(A) is None and h.runs.word(A) is None
    assert h.runs.polled(A) is False and s.partial(A) is None
    view = s.tree_view(A)
    assert "unscored" not in view
    assert all(leaf["passed"] is False for leaf in leaves_of(view))
    assert classify(s.comparisons[A]) == FAIL
    finished = s.view(A, "x")
    assert len(finished.act_time) == 21 and finished.leaves
    assert "range" not in s.plot_figure(A, 0)["layout"]["xaxis"]


def test_the_growing_file_s_variables_are_listed_and_a_watch_plot_draws_live(h):
    s = h.session
    events: list[dict] = []
    s.on("queue", events.append)
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    assert s.variable_names(A) == ["x", "y"]  # no file yet: the tracked ones

    h.polled(lambda: h.runs._runs[A].trace.state == RUNNING, "the file's header")
    # said once, so the page lists the file again
    assert [e["state"] for e in events if e["state"] == "live"] == ["live"]
    assert s.variable_names(A) == ["x", "y", "z"]

    extras = new_watch(s.layout(A), "z")
    s.stage([layout_op(A, s.spec.entry(A), extras)])
    assert _subjects(s, A) == [("x",), ("y",), ("z",)]
    assert s.plots(A)[2].watch and s.plots(A)[2].missing == ()
    h.ask(A, "z")
    h.at(A, 0.2)
    h.poll()
    view = s.view(A, "z")
    assert np.allclose(view.act_values, np.asarray(view.act_time) ** 2)
    assert len(view.act_time) == 5


def test_a_test_s_first_run_is_laid_out_by_what_it_names(h):
    """C never ran: there is no result to keep a layout of. Its spec names
    `x`, so its page has that plot from the run's first second — and after
    a cancel, the partial trace on it."""
    s = h.session
    assert C not in s.project.results and _subjects(s, C) == []
    assert s.tree_view(C) is None
    h.steer(C, hold=5)
    (item,) = s.enqueue([C])
    h.running(C)

    assert _subjects(s, C) == [("x",)] and s.tracked(C) == ["x"]
    h.ask(C, "x")
    h.at(C, 0.2)
    assert s.view(C, "x").act_time.tolist() == _steps(5)
    # no baseline and nothing ever compared: no criterion to wait on
    assert s.tree_view(C) is None

    s.cancel(item)
    h.ended(C)
    assert s.partial(C).sentence == "Cancelled at t = 0.2 of 1 s — not scored"
    assert _subjects(s, C) == [("x",)]
    assert s.view(C, "x").act_time.tolist() == _steps(5)


def test_a_criterion_edited_while_its_test_runs_shows_in_the_waiting_tree(h):
    """D150: a running test takes its criteria's edits. Its page's tree is
    the criteria as they stand, so the edit is a row at once — and the
    variable it names a plot, drawn live."""
    s = h.session
    s.stage([PatchOp(D, "add", "/metrics", {"metric": "nrmse", "variable": "x"})])
    assert _subjects(s, D) == [("x",)]
    h.steer(D, hold=5)
    s.enqueue([D])
    h.running(D)
    assert [leaf["variable"] for leaf in leaves_of(s.tree_view(D))] == ["x"]

    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "y", "min_value": 0.0},
        ],
    }
    s.stage([PatchOp(D, "replace", "/metrics", tree)])

    view = s.tree_view(D)
    assert view["unscored"] == WAITING and view["combinator"] == "and"
    assert [(leaf["metric"], leaf["variable"]) for leaf in leaves_of(view)] == [
        ("nrmse", "x"),
        ("range", "y"),
    ]
    assert all(leaf["criterion"] == WAITING for leaf in leaves_of(view))
    assert _subjects(s, D) == [("x",), ("y",)]
    h.ask(D, "y")
    h.at(D, 0.2)
    assert np.allclose(s.view(D, "y").act_values, 2 * np.asarray(_steps(5)))


def test_the_tool_s_pool_asks_for_prompt_rows_and_nothing_else_does(h):
    """`Config.live_results` makes OpenModelica write every row promptly
    (`-mat_sync=1`), at a cost: set for the pool the tool opens, by nobody
    for a CLI run."""
    config = h.session.project.config
    assert config.live_results is False
    h.steer(D, hold=0)
    h.session.enqueue([D])
    assert config.live_results is True


# -- a run that did not finish ---------------------------------------------------


def test_a_cancelled_run_shows_what_it_wrote_and_scores_nothing(h):
    s = h.session
    before = _subjects(s, A)
    h.steer(A, hold=7)
    (item,) = s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.3)

    assert s.cancel(item) is True
    h.ended(A)

    assert s.queue_state(A) is ItemState.CANCELLED
    assert s.partial(A) == Partial(live_rows.CANCELLED, _steps(7)[-1], 1.0)
    assert s.partial(A).sentence == "Cancelled at t = 0.3 of 1 s — not scored"
    # never scored: no result, no verdict, the Suite row's word unchanged
    assert A not in s.project.results
    assert classify(s.comparisons[A]) == NOT_RUN
    assert queue_row(s, A).status == CANCELLED
    view = s.tree_view(A)
    assert view["unscored"] == NOT_SCORED
    assert all(leaf["criterion"] == NOT_SCORED for leaf in leaves_of(view))
    assert not any("passed" in leaf for leaf in leaves_of(view))
    # shown: the plots it had, the file's rows by the size-based reader
    assert _subjects(s, A) == before
    shown = s.view(A, "x")
    assert shown.act_time.tolist() == _steps(7)
    assert shown.leaves == () and shown.ref_values is not None
    figure = s.plot_figure(A, 0, theme="dark")
    (line,) = [sh for sh in _shapes(figure) if sh.get("name") == live_rows.STOPPED]
    assert line["x0"] == _steps(7)[-1] and line["line"]["color"] == "#9e9e9e"
    (note,) = figure["layout"]["annotations"]
    assert note["text"] == "stopped at t = 0.3 s"
    assert figure["layout"]["xaxis"]["range"] == [0.0, 1.0]
    # no reader is left on it
    assert h.runs.polled(A) is False and h.runs._runs[A].reader is None


def _header_parses(monkeypatch) -> list[int]:
    """One entry — the count of names — each time a result file's name
    matrix is decoded: what a header parse costs (0.3 s for 20,000
    variables), whichever reader asked."""
    from dstf.backends.common import mat_reader

    parsed: list[int] = []
    real = mat_reader._parse_name_matrix

    def counting(name_matrix):
        names = real(name_matrix)
        parsed.append(len(names))
        return names

    monkeypatch.setattr(mat_reader, "_parse_name_matrix", counting)
    return parsed


def test_a_partial_result_s_file_is_parsed_once_for_every_name_its_page_draws(
    h, monkeypatch
):
    """The page asks a name at a time (a 20-member family is twenty asks),
    on the UI loop. Each ask parsed the whole header again and kept its own
    copy of the time: one parse and one time array now, for as long as the
    partial result stands."""
    s = h.session
    h.steer(A, hold=7)
    (item,) = s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.3)
    s.cancel(item)
    h.ended(A)
    assert s.partial(A) is not None

    parsed = _header_parses(monkeypatch)
    views = {name: s.view(A, name) for name in ("x", "y", "z")}
    assert s.variable_names(A) == ["x", "y", "z"]  # the pickers' listing too
    assert len(parsed) == 1
    times = [h.runs.series(A, name)[0][0] for name in ("x", "y", "z", "x")]
    assert all(time is times[0] for time in times)
    assert len(parsed) == 1  # asked again: nothing is read again
    assert times[0].tolist() == _steps(7)
    assert np.allclose(views["x"].act_values, 1.25 * times[0])
    assert np.allclose(views["z"].act_values, times[0] ** 2)
    # a name the file lacks is asked of it once, and stays absent
    assert h.runs.series(A, "nope") == (None, 0) and len(parsed) == 1


def test_a_running_file_s_header_is_parsed_once_for_its_rows_and_its_names(
    h, monkeypatch
):
    """The first poll parses the header; listing the file's variables for
    the pickers parsed it a second time, on the loop too."""
    s = h.session
    parsed = _header_parses(monkeypatch)
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.2)
    assert s.variable_names(A) == ["x", "y", "z"]
    assert s.variable_names(A) == ["x", "y", "z"]
    assert len(parsed) == 1


def test_a_partial_result_is_refused_as_a_baseline_with_a_sentence(h):
    s = h.session
    accept = Accept()
    assert accept.available(h.view(A)) is True
    h.steer(A, hold=7)
    (item,) = s.enqueue([A])
    h.running(A)
    h.at(A, 0.3)
    s.cancel(item)
    h.ended(A)

    assert accept.available(h.view(A)) == PARTIAL
    assert accept.caption(h.view(A)) == "Accept 0 results as baselines"
    # ... and beside a test that can be accepted, it is the one left out
    both = h.view(A, D)
    assert accept.available(both) is True
    assert accept.caption(both) == "Accept 1 result as baselines"


def test_a_timed_out_run_shows_what_it_wrote_as_a_failure(h):
    s = h.session
    spec_path = h.cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            # Long enough for a loaded machine to start Python and write
            # four rows: the run is held there until this kills it.
            entry["simulation"]["timeout"] = 8
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s.reload()
    h.loop.pump()
    h.steer(A, hold=4)  # held until the timeout kills it
    s.enqueue([A])
    h.running(A)
    h.at(A, 0.15)
    h.ended(A)

    assert s.queue_state(A) is ItemState.FAILED
    assert s.partial(A) == Partial(live_rows.TIMED_OUT, _steps(4)[-1], 1.0)
    assert s.partial(A).sentence == "Timed out at t = 0.15 of 1 s — not scored"
    assert classify(s.comparisons[A]) == SIM_FAIL
    assert queue_row(s, A).status == SIM_FAIL
    assert s.tree_view(A)["unscored"] == NOT_SCORED
    # the failed run has no plots of its own (O2); the page keeps the last
    assert _subjects(s, A) == [("x",), ("y",)]
    assert len(s.view(A, "x").act_time) == 4
    assert Accept().available(h.view(A)) == PARTIAL

    # Its reason, in the run's words — and for a tool started later, which
    # holds no run result, the verdict the run recorded and then what its
    # file is: "No Python result at …" alone said nothing of a timeout.
    said = s.comparisons[A].error_message
    assert said.startswith("Python execution exceeded 8.0s timeout (")
    s.stop_queue()
    s.wait_background(30)
    loop = Loop()
    later = Session(post=loop.post)
    later.open(h.cfg, work_dir=h.work)
    later.rescan_done.wait(30)
    loop.pump()
    read_back = later.comparisons[A]
    assert classify(read_back) == SIM_FAIL
    assert read_back.error_message.startswith(
        "Run recorded timed_out — No Python result at "
    )
    assert strip_for(
        status=SIM_FAIL, has_result=True, failure=read_back.error_message
    ).sentence.startswith("Run recorded timed_out — No Python result at ")


def test_a_reread_that_raises_at_a_run_s_end_leaves_the_result_that_was_read(
    h, monkeypatch, caplog
):
    """A criterion staged while the test ran names a variable its file
    holds and the run's read did not extract: the end reads the result
    again. That read raising (the mount went away) left a FINISHED run
    reading NOT RUN, said no `ended`, wrote no status.json — and the
    exception left the tick. It is logged in one line; the result the run
    read is scored and everything after it happens."""
    s = h.session
    events = []
    for kind in ("queue", "results"):
        s.on(kind, lambda e: events.append((e["event"], e.get("state"))))
    h.steer(A, hold=5)
    s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.2)
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "range", "variable": "z", "min_value": -1, "max_value": 5},
        ],
    }
    s.stage([PatchOp(A, "add", "/metrics", tree)])
    h.loop.pump()

    def gone(*args, **kwargs):
        raise OSError("the mount went away")

    monkeypatch.setattr(s.project.backend, "read_last_results", gone)
    events.clear()
    caplog.clear()
    h.steer(A, hold=None)
    with caplog.at_level("ERROR", logger="dstf.authoring.session"):
        h.ended(A)  # a raise out of the posted call fails here

    assert s.queue_state(A) is ItemState.FINISHED
    assert A in s.project.results and s.project.results[A].success
    assert classify(s.comparisons[A]) != NOT_RUN
    assert ("results", None) in events and events[-1] == ("queue", "ended")
    (line,) = [r for r in caplog.records if "again" in r.getMessage()]
    assert "the mount went away" in line.getMessage() and A in line.getMessage()
    assert line.exc_info is None  # one line: no traceback
    s.wait_background(30)
    status = json.loads((h.work / "status.json").read_text(encoding="utf-8"))
    assert A in [row["model_id"] for row in status["tests"]]


def test_a_run_that_raised_after_its_rows_is_a_failed_partial(h):
    s = h.session
    h.steer(D, boom="")
    s.enqueue([D])
    h.ended(D)
    assert s.queue_state(D) is ItemState.FAILED
    assert s.partial(D) == Partial(live_rows.FAILED, 1.0, 1.0)
    assert s.partial(D).sentence == "Failed at t = 1 of 1 s — not scored"
    assert len(s.view(D, "x").act_time) == 21


def test_a_second_run_shows_no_sample_of_the_first(h):
    """A rerun wipes the test's directory when it starts: the page says
    `starting` and then draws the new run — the layout it keeps is the one
    the FIRST run kept, since the cancelled one left it no result."""
    s = h.session
    h.steer(A, hold=9, scale=1)
    (first,) = s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.4)
    s.cancel(first)
    h.ended(A)
    assert len(s.view(A, "x").act_time) == 9

    h.steer(A, hold=3, scale=7)
    s.enqueue([A])
    h.running(A)
    # started, nothing read yet: no partial, no sample, the plots still there
    assert s.partial(A) is None and s.tree_view(A)["unscored"] == WAITING
    assert queue_row(s, A).progress == "starting"
    assert len(s.view(A, "x").act_time) == 0
    assert _subjects(s, A) == [("x",), ("y",)]

    h.ask(A, "x")
    h.at(A, 0.1)
    view = s.view(A, "x")
    assert view.act_time.tolist() == _steps(3)
    assert np.allclose(view.act_values, 7 * view.act_time)  # none of scale 1


def _revisions(figure: dict) -> tuple:
    """What the browser keeps a plot's zoom and legend choices under."""
    layout = figure["layout"]
    return tuple(layout[key].get("uirevision") for key in ("xaxis", "yaxis")) + (
        layout.get("uirevision"),
    )


def test_every_run_s_figures_are_their_own_revision_of_the_plot(h):
    """The plot starts over whole at a run's first figure, whatever stood
    there before — the last result's or a PARTIAL one's: a zoom made on a
    cancelled run's trace carried into the next run's. A partial result's
    figure is its own run's revision (the same trace, stopped), and a
    result's is the figure layer's."""
    s = h.session
    result = _revisions(s.plot_figure(A, 0, theme="default"))
    assert set(result) == {"keep"}

    h.steer(A, hold=7)
    (item,) = s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.3)
    first = _revisions(s.plot_figure(A, 0, theme="default"))
    assert len(set(first)) == 1 and first != result
    s.cancel(item)
    h.ended(A)
    assert s.partial(A) is not None
    assert _revisions(s.plot_figure(A, 0, theme="default")) == first

    h.steer(A, hold=3)
    s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.1)
    second = _revisions(s.plot_figure(A, 0, theme="default"))
    assert len(set(second)) == 1 and second not in (first, result)

    h.steer(A, hold=None)
    h.ended(A)
    assert _revisions(s.plot_figure(A, 0, theme="default")) == result


def test_a_file_that_goes_away_under_the_reader_starts_the_trace_over(h):
    """The wipe as the reader sees it: the file removed and written again
    under a poller is another run — `gone`, then its rows from the start,
    and never an exception."""
    s = h.session
    h.steer(A, hold=6)
    s.enqueue([A])
    h.running(A)
    h.ask(A, "x")
    h.at(A, 0.25)
    run = h.runs._runs[A]
    path = run.reader.path
    kept = path.read_bytes()

    path.unlink()
    h.poll()
    assert run.trace.state == GONE and run.trace.series("x") is None
    assert queue_row(s, A).progress == "starting"
    assert len(s.view(A, "x").act_time) == 0

    path.write_bytes(kept[: len(kept) - 2 * 4 * 8])  # its first four rows
    h.poll()
    assert run.trace.series("x")[0].tolist() == _steps(4)
    assert queue_row(s, A).progress == "t = 0.15 / 1 s"


def test_a_project_that_closes_keeps_nothing_of_its_runs(h):
    s = h.session
    h.steer(A, hold=7)
    (item,) = s.enqueue([A])
    h.running(A)
    h.at(A, 0.3)
    s.cancel(item)
    h.ended(A)
    assert s.partial(A) is not None
    s.open(h.cfg, work_dir=h.work)
    s.rescan_done.wait(30)
    h.loop.pump()
    assert s.partial(A) is None and h.runs.kept(A) is None
