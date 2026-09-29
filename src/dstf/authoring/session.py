"""The Session — the authoring tool with no UI (two-tier design, §4.5).

Opens a project, scores its tests from the last run on disk, holds the
spec with the ops staged against it, and applies the outcomes capabilities
return. It writes only through ``save`` (the spec) and through the outcomes
it applies (the store, a job's work dir). Nothing here imports a UI.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from ..backends import BackendUnavailableError, dataset_kind_for, select_backend
from ..backends.base import Backend
from ..backends.common.result_variables import tracked_results
from ..backends.progress import recorded_backend
from ..backends.types import (
    DatasetType,
    TestResult,
    VariableText,
)
from ..comparison.comparator import DEFAULT_TOLERANCE, compare_one
from ..comparison.types import TestComparison
from ..config import Config, ConfigError
from ..discovery.patch_apply import PatchError
from ..discovery.plots import (
    PlotExtra,
    ResolvedPlot,
    plot_leaves,
    plots_of_test,
    tracked_names,
)
from ..discovery.scan_cache import load_scan, store_scan
from ..discovery.spec_parser import spec_models_from_document
from ..discovery.spec_update import (
    apply_validated_patches,
    implicit_tree_op,
    is_literal_variable,
    needs_materialization,
    unknown_override_modes,
    validate_entry_metric_tree,
)
from ..discovery.test_registry import (
    TestModel,
    merge_tests,
    scan_source_tests,
    spec_tests_by_model,
)
from ..figures.assemble import leaves_by_variable, view_from_trajectory
from ..figures.view import VariableView
from ..storage.baseline import PRIMARY_BASELINE
from ..storage.reference_store import ReferenceStore
from .capabilities import (
    Artifact,
    Job,
    JobContext,
    OpenProject,
    Outcome,
    Patch,
    StoreWrite,
)
from .situation import unrecorded_variables
from .spec_document import PatchOp, SpecDocument

if TYPE_CHECKING:
    import numpy as np

    from ..comparison.tree_spec import LeafSpec
    from ..figures.plotly import Visibility

Event = Literal["spec", "results", "job", "loading", "known", "overlays"]
Listener = Callable[[dict[str, Any]], None]

logger = logging.getLogger(__name__)

#: How long a job waits, before its run, for an earlier job's
#: ``after_reload`` (a Rerun's dashboard render: 1.6 s over TRANSFORM's work
#: dir with --report sidecars). Past it the run starts and one line says so.
BACKGROUND_WAIT = 60.0


@dataclass
class Project:
    config: Config
    store: ReferenceStore
    backend: Backend
    scanned: dict[str, TestModel]  # the source scan, merged against every spec edit
    tests: list[TestModel]
    results: dict[str, TestResult]
    dataset_kind: DatasetType
    #: Set once this project's sibling-backend overlay index is built (or
    #: its build failed): the project's, so a read asks the flag of the
    #: project it reads — a reopen has no window where a read of the new
    #: project sees the old one's set flag.
    overlays_ready: threading.Event = field(default_factory=threading.Event)

    def test(self, model_id: str) -> TestModel | None:
        return next((t for t in self.tests if t.model_id == model_id), None)


@dataclass(frozen=True)
class Selection:
    """What a page has selected, handed to a capability through its
    SessionView (D129): the checked tests, the variable and the criterion's
    tree path. A page builds a new view when it changes; two browser tabs
    keep two. Not the Session's — that was the old shell's one-tab shape.
    ``node_path``: the tree node whose controls are being drawn (NODE) — a
    leaf or a combinator; ``leaf_path`` only ever names a criterion.

    ``texts`` is the page's ONE read of the selected test's label scope
    (:meth:`Session.variable_texts`), handed down with the selection so a
    plot's ``+`` can say what the variable READS as without deriving the
    scope for itself. A page with forty cards did exactly that once per card:
    eighty scope builds per rebuild, each re-resolving the test's plots and
    its tracked names — 0.37 s at forty cards against 0.03 s at five. A
    selection carrying no texts reads the variable's NAME; nothing here
    picks a test to read a label off, which is also why a selection of two
    tests (refused at VARIABLE anyway) cannot show one of them's
    expression.
    """

    tests: frozenset[str] = frozenset()
    variable: str | None = None
    leaf_path: str | None = None
    node_path: str | None = None
    texts: Mapping[str, VariableText] = MappingProxyType({})


@dataclass
class _ModelReads:
    """What one click re-read from disk before this cache (brief §2.2): the
    primary reference and the overlays; plus the decimated base view per
    variable — trajectories, overlays and error series, no leaves — held
    here and shared by `figure` and `plot_figure` through `_editable_view`,
    so an edit re-decimates only the criterion's geometry and a plot's
    several series draw over the bases held here, one at a time."""

    ref: dict | None
    overlays: list
    bases: dict[str, VariableView] = field(default_factory=dict)
    #: The scored list the bases were built from (`_scored_key`). A rescore
    #: that changes it — a staged edit that starts or stops scoring a
    #: variable, a discard, a save, the background rescan — may leave any
    #: base of the model stale; the key does not say which, so
    #: `_editable_view` drops them all when it moves — for both figure reads
    #: (one extra decimation per variable).
    scored: tuple[tuple[str | None, int], ...] = ()
    #: Every name in the result file (`Backend.result_names`), once
    #: `names_read`: `None` when the backend cannot list the file, `()` when
    #: the file lists nothing.
    names: tuple[str, ...] | None = None
    names_read: bool = False
    #: Untracked series read from the result file, by name; `None` is a miss
    #: (the file lacks the name, or holds nothing drawable under it), cached
    #: so a redraw does not re-open the file for it.
    series: dict[str, tuple[np.ndarray, np.ndarray] | None] = field(
        default_factory=dict
    )
    #: Whether the overlays include the sibling-backend ones: False for a
    #: read taken before the project's sibling index was ready; the first
    #: read after it reloads the overlays (`_reads_for`).
    siblings: bool = True


def _scored_key(comp: TestComparison | None) -> tuple[tuple[str | None, int], ...]:
    """What `view` hands `build_trajectories`, reduced to what the base
    traces depend on: the (name, index) of every scored variable, in order;
    empty when the test was not evaluated. The WHOLE list, not "is this
    variable in it": with no baseline, an empty scored list draws a variable
    through the no-baseline path (overlays attached) and a non-empty one (a
    baseline-free leaf on ANOTHER variable) draws it from the result file,
    with none — or as the empty state when the file cannot be read."""
    # `evaluated` is `metric_tree is not None or bool(variables)`
    # (comparison/types.py), so an unevaluated comparison has no variables
    # and the tuple is empty by construction — no separate check.
    if comp is None:
        return ()
    return tuple((vc.name, vc.index) for vc in comp.variables)


@dataclass
class RunningJob:
    label: str
    models: frozenset[str]
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None
    #: Epoch seconds at start. The job panel ignores a ``status.json`` whose
    #: ``start_wall`` is older, so the previous run's snapshot is never shown
    #: as this job's progress.
    started_wall: float = field(default_factory=time.time)
    #: Set once ``_finish_job``'s reload has run. The job panel shows the
    #: results' rows only then: a job that asked for no reload, or one that
    #: failed, would otherwise show the previous run's rows as its own.
    reloaded: bool = False


class LockedError(RuntimeError):
    def __init__(self, models: Iterable[str], label: str):
        self.models = frozenset(models)
        super().__init__(
            f"{', '.join(sorted(self.models))}: {label} is running on them — the run "
            "pipeline clears their simulation directories and rewrites the batch manifest"
        )


class StaleSpecError(RuntimeError):
    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            f"{path} changed on disk since it was loaded; reload before saving"
        )


class JobRunningError(RuntimeError):
    pass


class PendingEditsError(RuntimeError):
    def __init__(self, count: int):
        self.count = count
        super().__init__(
            f"{count} staged edit(s) would be lost; save or discard them first"
        )


class _Background:
    """One daemon thread that runs what the Session hands it off the UI
    loop — a job's ``after_reload`` — one at a time and in the order
    handed. Started when there is work, ended when there is none. What it
    runs is logged when it raises, whatever it raises (a hook's SystemExit
    too), never re-raised: it writes files beside a reload that already
    stands, and a thread that died would leave ``wait`` hanging on it."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._queue: list[tuple[str, Callable[[], None]]] = []
        self._busy = False

    def submit(self, what: str, fn: Callable[[], None]) -> None:
        with self._cond:
            self._queue.append((what, fn))
            if self._busy:
                return
            self._busy = True
        threading.Thread(
            target=self._drain, name="dstf-background", daemon=True
        ).start()

    def _drain(self) -> None:
        while True:
            with self._cond:
                if not self._queue:
                    self._busy = False
                    self._cond.notify_all()
                    return
                what, fn = self._queue.pop(0)
            try:
                fn()
            except BaseException:
                logger.exception("%s failed", what)

    def wait(self, timeout: float | None = None) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: not self._busy, timeout)


class Session:
    def __init__(self, post: Callable[[Callable[[], None]], None] | None = None):
        """``post`` hands a job's completion to the UI loop; without one the
        completion runs on the worker thread (tests wait on ``RunningJob.done``)."""
        self.project: Project | None = None
        self.spec: SpecDocument = SpecDocument(
            Path("test_spec.json"), {"tests": []}, None
        )
        self.running: RunningJob | None = None
        #: The job that ran last, whatever its outcome — the job panel's
        #: outcome line. Project-scoped: LibA's outcome is not LibB's.
        self.last_job: RunningJob | None = None
        self.comparisons: dict[str, TestComparison] = {}
        self.artifacts: list[Artifact] = []
        self.notices: list[str] = []
        self._post = post or (lambda fn: fn())
        self._listeners: dict[str, list[Listener]] = {}
        self.rescan_done = threading.Event()
        self._reads: dict[str, _ModelReads] = {}
        self._background = _Background()

    # -- project -----------------------------------------------------------

    def open(
        self,
        config_path: Path,
        *,
        backend: str | None = None,
        work_dir: Path | None = None,
    ) -> None:
        """Open the project ``config_path`` describes. Without an explicit
        ``backend`` the one the last run recorded wins, as ``compare`` does.
        Refused while a job runs or while edits are pending — save or
        discard first; a swap must never drop them silently."""
        self._refuse_if_busy()
        config_path = Path(config_path)
        config = Config(
            config_file=config_path, backend_name=backend, work_dir=work_dir
        )
        warning: str | None = None
        if backend is None:
            assert config.work_dir is not None  # resolved in __post_init__
            recorded, warning = recorded_backend(config.work_dir)
            if recorded and recorded != config.backend_name:
                config = Config(
                    config_file=config_path, backend_name=recorded, work_dir=work_dir
                )
        try:
            # The Session's backend reads results; it is not the rerun
            # backend, so the batch class is what it wants. `select_backend`
            # is the seam that turns every "this machine can't build it"
            # failure — an unregistered name, a missing executable, an
            # absent optional dependency — into one class, so `dstf author`
            # prints a line where a RuntimeError tracebacked.
            backend_obj = select_backend(config, persistent=False)
        except BackendUnavailableError as e:
            raise ConfigError(str(e)) from e
        # A UI process has no console to narrate a run or a read to.
        backend_obj.quiet = True
        store = ReferenceStore(config)
        spec = SpecDocument.load(config.spec_path)
        # The models this open can name before the scan — the spec's and the
        # last run's — so a page draws their rows while a cold scan runs.
        self._emit("known", models=_known_models(config, spec.effective()))
        try:
            started = time.monotonic()
            # "scanning library…" before the first file — and the only scan
            # event for a source type that scans nothing (Python, FMU, Julia).
            self._emit("loading", phase="scan", done=0, total=0)
            # The reference index, read here — off the UI loop, after the
            # known rows, before the commit point: a page reads it as soon
            # as `project` is set (its baseline's name), and over TRANSFORM
            # the lazy scan (every ref file read whole) held the loop ~4 s
            # from that first read — with the sibling-index wait
            # (`_reads_for`), past the browser's ping budget: the socket
            # dropped and the reconnect left the page stale.
            store.index.ensure_loaded()
            cached = load_scan(config)
            if cached is not None:
                scanned = cached
                self._emit("loading", phase="scan", cached=True, total=len(cached))
            else:
                scanned = scan_source_tests(
                    config,
                    progress=lambda done, total: self._emit(
                        "loading", phase="scan", done=done, total=total
                    ),
                )
                store_scan(config, scanned)
            tests = self._merge(config, scanned, spec.effective())
            self.project = Project(
                config=config,
                store=store,
                backend=backend_obj,
                scanned=scanned,
                tests=tests,
                results={},
                dataset_kind=dataset_kind_for(backend_obj),
            )
            self.spec = spec
            self.comparisons = {}
            self._reads = {}
            # A notice is project-scoped text ("stored 2 baselines for LibA.X"), and
            # so is an artifact's path; a swap calls open, never close, and must not
            # carry either into LibB's panel. Assigned here, at the commit point,
            # and not on entry: an open that fails leaves the live project's panel
            # as it was. The recorded-backend warning is this project's first
            # notice, so the job panel shows it.
            self.notices = [warning] if warning else []
            self.artifacts = []
            self.last_job = None
            # Started at the commit point, not after the reload: the warm reads the
            # SIBLING backends' reference trees and the reload reads this one's, so
            # neither waits on the other and the warm gets its head start inside
            # the reload's window (15.6 s over TRANSFORM) instead of after it. How
            # much of that window it converts is NOT measured and is less than all
            # of it — both sides are json.loads, whose C scanner holds the GIL, so
            # they overlap on file IO and contend on parsing. Not earlier than this:
            # everything above can still raise, and an open that fails must leave
            # the live session's state as it was.
            threading.Thread(
                target=self._warm_overlay_index,
                args=(self.project,),
                name="dstf-overlay-index",
                daemon=True,
            ).start()
            self._emit("spec", models={t.model_id for t in tests})
            done_event = threading.Event()
            self.rescan_done = done_event
            try:
                self._reload_results()
            finally:
                # Whether or not one model's scoring raised: the project is open
                # and its results complete (`_reload_results`), so `done` fires
                # and the rescan starts — a shell waiting on either must not wait
                # forever. The failure still propagates to the caller, after both.
                self._emit("loading", phase="done", seconds=time.monotonic() - started)
                if cached is not None:
                    threading.Thread(
                        target=self._rescan,
                        args=(self.project, cached, done_event),
                        name="dstf-rescan",
                        daemon=True,
                    ).start()
                else:
                    done_event.set()
        except BaseException:
            # An open that fails takes back what it named: a page draws the
            # open project's rows (or none) again.
            self._emit("known", models=[])
            raise

    @property
    def overlay_index_ready(self) -> threading.Event:
        """The open project's `Project.overlays_ready`; with none open, an
        Event nothing sets."""
        project = self.project
        return project.overlays_ready if project is not None else threading.Event()

    def _warm_overlay_index(self, project: Project) -> None:
        from ..reporting.overlay_loader import warm_sibling_backend_index

        ready = project.overlays_ready
        try:
            warm_sibling_backend_index(project.config)
        except Exception:  # a broken sibling file must not kill the thread silently
            logger.exception("warming the sibling-backend overlay index failed")
            ready.set()
            return
        ready.set()
        try:
            self._post(lambda: self._overlays_ready(project))
        except Exception:  # the UI loop is gone (a shutdown): no page to redraw
            logger.debug("the sibling index is ready and the UI loop is gone")

    def _overlays_ready(self, project: Project) -> None:
        """The sibling index is built: name the models read without it
        (`_reads_for`) in ONE ``overlays`` event, so a page redraws them
        and their next read gains the sibling-backend overlays. The reads
        are kept, not dropped: they hold what was read from the result file,
        which a model a job holds cannot read again until the job's reload.
        Nothing for a project no longer open."""
        if self.project is not project:
            return
        stale = {m for m, reads in self._reads.items() if not reads.siblings}
        if stale:
            self._emit("overlays", models=stale)

    def _rescan(
        self, project: Project, cached: dict[str, TestModel], done: threading.Event
    ) -> None:
        """A cached open trusts the cache; this finds out whether it should
        have. Runs the full scan off the UI thread; everything that touches
        the Session — the re-merge AND the events — goes through ``_post``
        (the UI loop, where every other mutation happens)."""
        try:
            fresh = scan_source_tests(project.config)
        except Exception:
            logger.exception("background rescan failed")

            def failed() -> None:
                try:
                    self._emit("loading", phase="rescan", changed=False, error=True)
                finally:
                    done.set()

            self._post_or_run(failed)
            return
        changed_models = {
            m for m in set(fresh) | set(cached) if fresh.get(m) != cached.get(m)
        }
        if changed_models:
            store_scan(project.config, fresh)

        def apply() -> None:
            try:
                # The project may have been swapped or closed meanwhile.
                if changed_models and self.project is project:
                    # Only what changed is rescored: a one-file edit over
                    # TRANSFORM must not run 331 comparisons on the UI loop.
                    # A model a running job has locked is left to that job's
                    # completion reload, as `stage` and `rescore` leave it.
                    # The changed models' results are re-read with their new
                    # TestModels before they are rescored: a model the cache
                    # did not name was read as "not found in discovery"
                    # (SIM_FAIL where a cold open says NO_REF), and a model
                    # whose tracked variables changed was read with the old
                    # list.
                    locked = (
                        self.running.models if self.running is not None else frozenset()
                    )
                    self._rebuild_tests(changed_models - locked, scanned=fresh)
                self._emit("loading", phase="rescan", changed=bool(changed_models))
            finally:
                done.set()

        self._post_or_run(apply)

    def _post_or_run(self, fn: Callable[[], None]) -> None:
        """Hand ``fn`` to the UI loop; when the loop is gone (``post``
        raises — the case ``_start_job`` handles the same way) run it here,
        so a waiter on the Event is never left hanging."""
        try:
            self._post(fn)
        except BaseException:
            fn()

    def close(self) -> None:
        self._refuse_if_busy()
        self.project = None
        self.artifacts = []
        self.notices = []
        self.last_job = None
        self.spec = SpecDocument(Path("test_spec.json"), {"tests": []}, None)
        self.comparisons = {}
        self._reads = {}
        self._emit("spec", models=set())

    def reload(self) -> None:
        """Re-read the spec from disk, dropping pending ops (what a refused
        save offers). Refused while a job runs, as Discard is: the job's
        reload scores the spec."""
        project = self._require_project()
        self._refuse_if_running()
        self.spec = SpecDocument.load(project.config.spec_path)
        self._rebuild_tests(set(self.comparisons))

    def wait_background(self, timeout: float | None = None) -> bool:
        """Wait until every ``after_reload`` hook the Session ran off the UI
        loop has finished; False on a timeout."""
        return self._background.wait(timeout)

    def on(self, event: Event, cb: Listener) -> None:
        self._listeners.setdefault(event, []).append(cb)

    # -- scoring and figures ----------------------------------------------

    def rescore(self, model_id: str) -> TestComparison:
        """Score ``model_id`` against the spec with pending ops applied. A
        flat-override test first gets the same ``add /metrics`` op
        ``spec-update`` prepends, so the document the tool edits and the one
        it saves never differ in shape — but only when that tree scores
        exactly what the flat path scored (Conventions #7); otherwise the
        test is scored as loaded and nothing is staged."""
        self._refuse_if_locked([model_id])
        op = self._materialization(model_id)
        if op is not None:
            # Marked as `stage` marks its own: the drawer previews it as
            # nothing changed, with the note that Save writes it.
            self.stage(
                [
                    PatchOp(
                        model_id,
                        op["op"],
                        op["path"],
                        op["value"],
                        materializes=self._saved_scoring_tree(model_id),
                    )
                ]
            )
            return self.comparisons[model_id]
        comp = self._score(model_id)
        self._emit("results", models={model_id})
        return comp

    def view(self, model_id: str, variable: str) -> VariableView:
        """The variable's view: its trajectories, overlays and every leaf
        scoring it. A test with no result, a failed simulation or no baseline
        gets the empty state — trajectories present or not, no leaves."""
        from ..reporting.context_builder import build_trajectories
        from ..reporting.overlay_loader import attach_overlays_to_trajectories

        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)
        comp = self.comparisons.get(model_id)
        result = project.results.get(model_id)
        reads = self._reads_for(model_id)
        ref = reads.ref
        scored = list(comp.variables) if comp is not None and comp.evaluated else []
        texts = self.variable_texts(model_id)
        trajectories, _diag, nobaseline = build_trajectories(scored, result, ref, texts)
        overlays = reads.overlays
        attach_overlays_to_trajectories(trajectories, overlays)
        attach_overlays_to_trajectories(nobaseline, overlays)
        traj = next((t for t in trajectories if t["name"] == variable), None)
        if traj is None:
            nb = next((t for t in nobaseline if t["name"] == variable), None)
            if nb is not None:
                traj = {
                    "text": nb.get("text") or variable,
                    "act_time": nb["time"],
                    "act_values": nb["values"],
                    "overlays": nb["overlays"],
                }
            else:
                series = self._file_series(model_id, variable)
                # A series read straight out of the result file carries no
                # expression — the result in memory is where one lives —
                # but it is still in the page's label scope, so a
                # plot-only name that collides with a tracked variable's
                # expression reads the same here as in its plot note.
                traj = (
                    {
                        "text": texts[variable].text if variable in texts else variable,
                        "act_time": series[0],
                        "act_values": series[1],
                    }
                    if series is not None
                    else {}
                )
        leaves = leaves_by_variable(comp.metric_tree if comp is not None else None).get(
            variable, []
        )
        return view_from_trajectory(variable, traj, leaves)

    def _reads_for(self, model_id: str) -> _ModelReads:
        from ..reporting.overlay_loader import load_overlays

        project = self._require_project()
        reads = self._reads.get(model_id)
        if reads is not None and not reads.siblings and project.overlays_ready.is_set():
            # Read before the index was ready: the overlays again, now with
            # the sibling-backend ones; the decimated bases carry the
            # overlays and go with them. What came from the result file stays.
            reads.overlays = load_overlays(
                project.store, model_id, config=project.config
            )
            reads.siblings = True
            reads.bases.clear()
        if reads is None:
            # Never wait on the sibling-backend index: while the open's warm
            # thread builds it, it holds the index's lock, and a read on the
            # UI loop waited as long (over TRANSFORM, with the reference
            # index's scan, past the browser's ping budget). Until it is
            # ready the read has no sibling overlays; `_overlays_ready`
            # names the model then, and its `overlays` event redraws the page.
            siblings = project.overlays_ready.is_set()
            reads = _ModelReads(
                ref=project.store.get_reference(model_id),
                overlays=load_overlays(
                    project.store,
                    model_id,
                    config=project.config if siblings else None,
                ),
                siblings=siblings,
            )
            self._reads[model_id] = reads
        return reads

    def _test_dir(self, model_id: str) -> Path | None:
        from ..backends.base import last_test_keys

        project = self._require_project()
        work_dir = project.config.work_dir
        key = last_test_keys(work_dir).get(model_id) if work_dir is not None else None
        return work_dir / key if key is not None and work_dir is not None else None

    def tracked(self, model_id: str) -> list[str]:
        """The test's TRACKED names, in order: the scored variables first,
        then the rest of what the test tracks of the loaded result
        (`tracked_results`: its UnitTests variables and patterns — not a
        variable a discarded criterion had re-read, which the result keeps),
        through `tracked_names`, the one rule. With no tree the plots derive
        from them; the Variables drawer groups them "Tracked"; and a
        variable a criterion may be added for is one of them — the scored
        names alone would lose a variable whose last criterion was just
        removed, and with it the way back to one."""
        project = self._require_project()
        comp = self.comparisons.get(model_id)
        scored = [vc.name for vc in comp.variables] if comp is not None else []
        result = project.results.get(model_id)
        test = project.test(model_id)
        if result is not None and test is not None:
            result = replace(result, variables=tracked_results(test, result))
        return tracked_names(scored, result)

    def variable_texts(self, model_id: str) -> dict[str, VariableText]:
        """``{name: VariableText}`` for this test — both readings of every
        variable its page can show, from the ONE scope builder the report
        uses (:func:`~dstf.reporting.context_builder.test_texts`).

        The scope is the whole TEST: the loaded result's variables, the
        scored comparisons' and every series of the test's resolved plots,
        plot-only names included. Nothing narrower: a card title, its
        criteria rows, its pills and its legend entries each disambiguated
        over their own little set, and the same variable read four
        different ways on one page (the b147918 review).

        A name the scope does not carry is its own label, and the page
        reads it that way. Nothing is ever keyed by either reading —
        titles, rows and captions read them; markers and ops do not."""
        from ..reporting.context_builder import test_texts

        project = self._require_project()
        if project.test(model_id) is None:
            return {}
        comp = self.comparisons.get(model_id)
        return test_texts(
            list(comp.variables) if comp is not None else [],
            project.results.get(model_id),
            [name for plot in self.plots(model_id) for name in plot.series],
        )

    def labels(self, model_id: str) -> dict[str, str]:
        """``{name: what every human-facing surface of this test's page
        reads for it}`` — the ``text`` of :meth:`variable_texts`, so the
        criteria rows, the pills, the focus bar, the "Add a criterion for
        x" captions and the add form's options all say the same thing.

        NOT the plain label: a surface that shows the muted name beside
        its title (the card title) reads ``variable_texts(...)[name].label``
        instead — the name is already there, so the title does not repeat
        it in parentheses."""
        return {
            name: entry.text for name, entry in self.variable_texts(model_id).items()
        }

    def variable_names(self, model_id: str) -> list[str]:
        """Every variable the last result holds: the tracked ones first, in
        the page's order, then the rest of the result file in its order."""
        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)
        tracked = self.tracked(model_id)
        have = set(tracked)
        return tracked + [n for n in self._file_names(model_id) if n not in have]

    def _file_names(self, model_id: str) -> tuple[str, ...]:
        """Every name in the last result FILE, listed once and kept with the
        model's reads; ``()`` when the backend keeps no file it can list, or
        a job has the model locked and it was not listed before."""
        project = self._require_project()
        reads = self._reads_for(model_id)
        if not reads.names_read and not self._is_locked(model_id):
            test_dir = self._test_dir(model_id)
            listed = (
                type(project.backend).result_names(test_dir)
                if test_dir is not None
                else None
            )
            reads.names = None if listed is None else tuple(listed)
            reads.names_read = True
        return reads.names or ()

    def _unread_in_file(
        self, models: Iterable[str], tests: Sequence[TestModel]
    ) -> set[str]:
        """The ``models`` whose criteria — as ``tests`` merge them — name a
        variable the loaded result lacks and its result FILE holds — a
        backend that records every variable (Dymola, the JSON driver) has
        it already, and a re-read with the merged TestModel extracts it
        (the owner's O2). A name the file lacks (OpenModelica, FMPy, a
        typo) only a rerun records; the strip asks for that
        (`situation.new_variables`)."""
        project = self._require_project()
        by_id = {t.model_id: t for t in tests}
        out: set[str] = set()
        for model_id in models:
            missing = unrecorded_variables(
                by_id.get(model_id),
                project.results.get(model_id),
                project.config.diagnostic_variables,
            )
            if missing and not set(missing).isdisjoint(self._file_names(model_id)):
                out.add(model_id)
        return out

    def _is_locked(self, model_id: str) -> bool:
        """A job holds this model: its work dir is being rewritten, so the
        file reads serve what they already hold and open nothing — as `view`
        and `figure` always have, from memory."""
        return self.running is not None and model_id in self.running.models

    def _file_series(
        self, model_id: str, variable: str
    ) -> tuple[np.ndarray, np.ndarray] | None:
        reads = self._reads_for(model_id)
        if variable not in reads.series:
            if self._is_locked(model_id):
                return None  # not cached: the job's reload answers it
            test_dir = self._test_dir(model_id)
            got = (
                type(self._require_project().backend).result_series(
                    test_dir, [variable]
                )
                if test_dir is not None
                else {}
            )
            reads.series[variable] = got.get(variable)
        return reads.series[variable]

    def layout(self, model_id: str) -> tuple[PlotExtra, ...]:
        """The extras a gesture edits (`authoring.layout`): the effective
        ``plots`` key with pending ops applied, ``()`` without one. It keeps
        a name the result lacks — it is what the spec says."""
        test = self._require_project().test(model_id)
        if test is None:
            raise KeyError(model_id)
        return tuple(test.plots or ())

    def _plot_leaves(self, model_id: str) -> list[LeafSpec] | None:
        """:func:`~dstf.discovery.plots.plot_leaves` for this test: the
        leaves its plots derive from, ``None`` for one plot per tracked
        name. From the tree SPEC and what was compared — no baseline read."""
        test = self._require_project().test(model_id)
        if test is None:
            raise KeyError(model_id)
        comp = self.comparisons.get(model_id)
        compared = [vc.name for vc in comp.variables] if comp is not None else []
        return plot_leaves(test, compared)

    def plots_from_tree(self, model_id: str) -> bool:
        """Whether the test's plots derive from a tree — the authored one,
        or the implicit tree of what was compared — rather than one per
        tracked variable or array. NOT whether there is a tree VIEW: a test
        with no baseline compares nothing, and an authored tree still says
        what its plots are."""
        return self._plot_leaves(model_id) is not None

    def plots(self, model_id: str) -> tuple[ResolvedPlot, ...]:
        """The test's plots as the page draws them
        (:func:`~dstf.discovery.plots.plots_of_test`, the rule the report draws
        by): one per scored variable or array family, in the order the tree
        first names it, decorated by the effective ``plots`` extras. The
        leaves come from the tree SPEC with pending ops applied — never
        :meth:`tree`, which materializes the tree and reads the baseline;
        no tree is one plot per tracked name or array. A UnitTests element
        never folds (O3); a failed run has none. While a job holds a model
        whose names were never read, nothing is missing."""
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        self.variable_names(model_id)  # reads the file's names, once
        reads = self._reads_for(model_id)
        comp = self.comparisons.get(model_id)
        plots = plots_of_test(
            test,
            [vc.name for vc in comp.variables] if comp is not None else [],
            project.results.get(model_id),
            listed=reads.names,
        )
        if reads.names_read:
            return plots
        # A job holds the model and its names were never read: a listing
        # not taken is not a file without the context and watch series, so
        # nothing is missing until the job's reload lists it.
        return tuple(replace(plot, missing=()) for plot in plots)

    def plot_figure(
        self,
        model_id: str,
        index: int,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
    ) -> dict:
        """The editable figure of plot ``index`` (IndexError past the end).
        A subject of one variable with no context is :meth:`figure`; a
        family, or context, is drawn over the same cached bases. A context
        view draws as a trace only — a context variable scored on another
        plot signs no leaf onto this one. ``editing`` and ``visibility`` as
        in :meth:`figure`."""
        from ..figures.plotly import plot_figure

        if index < 0:
            raise IndexError(index)
        plot = self.plots(model_id)[index]
        if len(plot.subject) == 1 and not plot.context:
            return self.figure(
                model_id, plot.subject[0], editing=editing, visibility=visibility
            )
        return plot_figure(
            [self._editable_view(model_id, name) for name in plot.subject],
            None,
            context=[
                self._editable_view(model_id, name)
                for name in plot.context
                if name not in plot.missing
            ],
            editable=True,
            rings_for=editing,
            visibility=visibility,
        )

    def figure(
        self,
        model_id: str,
        variable: str,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
    ) -> dict:
        """The tool's figure is the editable one — handle rings a user can
        drag (``variable_figure(..., editable=True)``); the report's is not.
        ``editing``: the leaf paths whose handles get a ring, ``None`` meaning
        every leaf; the Plotly ``config`` never depends on it. ``visibility``:
        what the page chose to show (legend b); the cached base does not
        depend on it."""
        from ..figures.plotly import variable_figure

        if self._require_project().test(model_id) is None:
            raise KeyError(model_id)  # before the cache: never cache a read for it
        return variable_figure(
            self._editable_view(model_id, variable),
            None,
            editable=True,
            rings_for=editing,
            visibility=visibility,
        )

    def _editable_view(self, model_id: str, variable: str) -> VariableView:
        """The decimated base (cached) joined to the leaves decimated on
        their own — what both figure reads draw, WITHOUT a budget.
        The base traces are decimated once per model and variable and
        cached until the model's scored list moves (`_ModelReads.scored`);
        only the leaves' geometry is decimated per call. The exception is a
        model a job holds: nothing is cached for it, so every call there
        re-decimates the base."""
        from ..figures.view import build_view, decimate_view

        project = self._require_project()
        budget = project.config.max_embedded_samples
        reads = self._reads_for(model_id)
        scored = _scored_key(self.comparisons.get(model_id))
        if reads.scored != scored:
            reads.bases.clear()
            reads.scored = scored
        base = reads.bases.get(variable)
        if base is None:
            base = decimate_view(
                replace(self.view(model_id, variable), leaves=()), budget
            )
            # Not cached under a lock, for `_file_series`' reason: it answers
            # None there and caches nothing, so an untracked series' base is
            # EMPTY — and a job that ends without dropping `_reads` (a
            # successful `Job(reloads_results=False)`) would leave it behind.
            if not self._is_locked(model_id):
                reads.bases[variable] = base
        comp = self.comparisons.get(model_id)
        leaves = leaves_by_variable(comp.metric_tree if comp is not None else None).get(
            variable, []
        )
        # The leaves are decimated on their own (no arrays but their
        # geometry) and joined to the cached base; the figure is then built
        # WITHOUT a budget, or `variable_figure` would decimate the base a
        # second time. The base's counts win the merge: a leaves-only view
        # stamps "act"/"ref" as 0.
        leaves_view = decimate_view(build_view(variable, leaves=leaves), budget)
        full = dict(leaves_view.full_samples)
        full.update(base.full_samples)
        return replace(base, leaves=leaves_view.leaves, full_samples=full)

    def tree_view(self, model_id: str) -> dict | None:
        """The read-only tree the report shows for this test, built by the
        report's own function; ``None`` when nothing was compared."""
        from ..reporting.context_builder import build_tree_view

        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        comp = self.comparisons.get(model_id)
        if comp is None or not comp.evaluated:
            return None
        return build_tree_view(
            list(comp.variables),
            test,
            comp.metric_tree,
            self.variable_texts(model_id),
            # the editor's "in effect" hints (K3); the report shows none
            comp.tolerance_source,
        )

    def tree(self, model_id: str) -> dict | None:
        """The raw tree a Patch edits: the entry's ``metrics`` block, else the
        tree ``rescore`` would materialize (the report's leaves), else None.
        A copy: a capability may build its next op from it without touching
        the document. An unknown model is a KeyError, as for ``tree_view``."""
        if self._require_project().test(model_id) is None:
            raise KeyError(model_id)
        entry = self.spec.entry(model_id) or {}
        if "metrics" in entry:
            return copy.deepcopy(entry["metrics"])
        op = self._materialization(model_id)
        return op["value"] if op is not None else None

    # -- the spec ----------------------------------------------------------

    def stage(self, ops: Sequence[PatchOp]) -> None:
        """Stage ``ops`` and rescore their models. Materializes the implicit
        tree where a per-leaf op needs one; validates each touched entry
        (paths, the parsed tree, the D66 roles). On any error the Session
        is unchanged."""
        project = self._require_project()
        models = {op.model_id for op in ops}
        self._refuse_if_locked(models)
        staged: list[PatchOp] = []
        for model_id in _in_order(op.model_id for op in ops):
            raw = [op.raw() for op in ops if op.model_id == model_id]
            entry = self.spec.entry(model_id) or {"model": model_id}
            if needs_materialization(entry, raw):
                # The same tree rescore stages — from what the flat path
                # scored — never one from the entry's list: on both tiers
                # leaf i is /metrics/children/i of the report's tree.
                tree_op = self._materialization(model_id)
                if tree_op is None:
                    raise PatchError(
                        f"{model_id}: a per-leaf op needs the implicit tree "
                        "materialized, and this test has no scored variables "
                        "a tree could be built from (or one of the flat/tree "
                        "differences applies) — author the tree explicitly"
                    )
                # Marked: the drawer diffs against the saved entry with this
                # tree in it (`SpecDocument.preview_base`) — but only when it
                # is the tree the SAVED entry scores. Built from overrides an
                # earlier pending op edited, it is not: unmarked, it shows as
                # added, and the note does not say "they score the same".
                staged.append(
                    PatchOp(
                        model_id,
                        tree_op["op"],
                        tree_op["path"],
                        tree_op.get("value"),
                        materializes=self._saved_scoring_tree(model_id),
                    )
                )
            # The ops as given: `rescore`'s materialization carries its mark.
            # Each value a copy: the caller's dict stays the caller's, and
            # editing it after staging changes neither export nor Save.
            staged += [
                replace(op, value=copy.deepcopy(op.value))
                for op in ops
                if op.model_id == model_id
            ]
        # Convention 8: an edit that lands on the path the last pending op
        # wrote supersedes it, so the pending count and the envelope carry
        # one op, never a keystroke log (the drawer shows the entry's diff
        # either way): a replace after an add or a
        # replace of the same path is that op with the new value, and a
        # remove after a replace is the remove. A remove after an ADD is
        # kept as two: when the member existed, the add replaced it, and
        # dropping both would leave it. A batch is never collapsed: its ops
        # belong together. A replace at an append path (``/-``) names no
        # member and is left for `effective` to refuse, never folded in. A
        # fold onto the op that materialized the tree is no longer that
        # tree: the folded op is not marked (`PatchOp.materializes`).
        pending = list(self.spec.pending)
        if len(staged) == 1 and pending:
            new, last = staged[0], pending[-1]
            same = (new.model_id, new.path) == (last.model_id, last.path)
            if same and not new.path.endswith("/-"):
                if new.op == "replace" and last.op in ("add", "replace"):
                    pending.pop()
                    staged = [PatchOp(new.model_id, last.op, new.path, new.value)]
                elif new.op == "remove" and last.op == "replace":
                    pending.pop()
        candidate = SpecDocument(
            self.spec.path,
            self.spec.loaded,
            self.spec.content_hash,
            [*pending, *staged],
        )
        effective = candidate.effective()  # PatchError on a bad path
        for model_id in models:
            validate_entry_metric_tree(
                effective, model_id, project.config, project.store
            )  # PatchError
        tests = self._merge(
            project.config, project.scanned, effective
        )  # ConfigError on a bad tree
        # A criterion on a variable the result file holds but the last read
        # did not extract: read again with the merged TestModel, which now
        # tracks it, so it scores at once — no rerun (the owner's O2). Read
        # BEFORE anything is replaced: a read that raises (the disk went
        # away) leaves the Session as it was, as the docstring promises.
        # Read directly, not through `_rebuild_tests(scanned=...)`: the
        # effective spec is merged just above (on TRANSFORM a merge is most
        # of a stage's cost; that would do it twice), and it would re-read
        # AND rescore only the models it is handed — the other staged models
        # must be rescored whether or not theirs is re-read.
        reread = self._unread_in_file(models, tests)
        fresh = self._read_again(reread, tests) if reread else {}
        self.spec = candidate
        project.tests = tests
        if reread:
            self._keep_read(reread, fresh)
        self._emit("spec", models=models)
        self._score_all(models)

    def discard(self, models: set[str] | None = None) -> None:
        """Drop the pending ops of ``models`` — every pending op when
        ``None`` — and restore their loaded verdicts; the other models' ops
        stay, in order. Refused while a job runs, as Save is: the job's
        reload scores the spec. ``models`` none of which has a pending op
        change nothing, and emit nothing."""
        self._refuse_if_running()
        pending = self.spec.pending
        gone = {op.model_id for op in pending}
        if models is not None:
            gone &= models
            if not gone:
                return
        kept = [op for op in pending if op.model_id not in gone]
        self.spec = SpecDocument(
            self.spec.path, self.spec.loaded, self.spec.content_hash, kept
        )
        self._rebuild_tests(gone)

    def saved_comparison(self, model_id: str) -> TestComparison | None:
        """The test's comparison as SAVED — the spec as loaded, no pending
        op applied — beside ``comparisons``, which is the edited spec's.
        With nothing pending for the model it IS ``comparisons[model_id]``;
        otherwise it is scored afresh, as `_score` scores, writing nothing:
        not ``comparisons``, not ``project.tests``. ``None`` for a model the
        saved spec does not make a test (an entry a pending op creates).
        Uncached: the strip asks for its one test, per paint."""
        return self.saved_comparisons({model_id})[model_id]

    def saved_comparisons(
        self, models: Collection[str]
    ) -> dict[str, TestComparison | None]:
        """`saved_comparison` of each of ``models``, the loaded spec merged
        ONCE for all of them (the pending drawer asks for every edited
        test's; a merge of TRANSFORM's spec is most of a call's cost)."""
        edited = {op.model_id for op in self.spec.pending}
        out: dict[str, TestComparison | None] = {
            m: self.comparisons.get(m) for m in models if m not in edited
        }
        rest = [m for m in models if m in edited]
        if rest:
            project = self._require_project()
            tests = {
                t.model_id: t
                for t in self._merge(project.config, project.scanned, self.spec.loaded)
            }
            for m in rest:
                test = tests.get(m)
                out[m] = None if test is None else self._compare(test)
        return out

    def save(self) -> None:
        """Apply exactly the pending ops through ``spec-update``'s
        read-modify-write, then reload. Refuses when the file changed on
        disk since it was loaded, and while a job runs: a rerun runs the
        spec with the pending ops applied, and its reload scores it."""
        project = self._require_project()
        self._refuse_if_running()
        if self.spec.on_disk_changed():
            raise StaleSpecError(self.spec.path)
        groups = self.spec.grouped()
        if groups:
            apply_validated_patches(
                self.spec.path, groups, project.config, project.store
            )
        models = {model_id for model_id, _ in groups}
        self.spec = SpecDocument.load(self.spec.path)
        self._rebuild_tests(models)

    def export(self) -> dict:
        from ..discovery.patch_envelope import ModelPatch, build_envelope

        return build_envelope([ModelPatch(m, ops) for m, ops in self.spec.grouped()])

    # -- outcomes ----------------------------------------------------------

    def apply(self, outcome: Outcome | Sequence[Outcome]) -> None:
        """Apply what a capability returned. ``None`` is nothing; a sequence
        is applied in order."""
        if outcome is None:
            return
        if isinstance(outcome, (list, tuple)):
            for one in outcome:
                self.apply(one)
            return
        if isinstance(outcome, Patch):
            # A capability's op is never the Session's materialization: only
            # `stage` and `rescore` know the tree they built scores the same.
            self.stage([replace(op, materializes=False) for op in outcome.ops])
        elif isinstance(outcome, StoreWrite):
            self._refuse_if_locked(outcome.models)
            message = outcome.run(self._require_project().store)
            self._reload_results()
            # The reload first, so a listener that reads verdicts on
            # ``written`` sees what the write did to them.
            if message:
                self.notices.append(message)
                self._emit("job", state="written", message=message)
        elif isinstance(outcome, Job):
            self._start_job(outcome)
        elif isinstance(outcome, Artifact):
            self.artifacts.append(outcome)
            self._emit("job", state="artifact", artifact=outcome)
        elif isinstance(outcome, OpenProject):
            self.open(outcome.config_path, work_dir=outcome.work_dir)
        else:
            raise TypeError(f"not an Outcome: {outcome!r}")

    def _start_job(self, outcome: Job) -> None:
        project = self._require_project()
        self._refuse_if_running()
        job = RunningJob(label=outcome.label, models=outcome.models)
        self.running = job
        self._emit("job", state="started", job=job)
        ctx = JobContext(project=project, models=outcome.models)

        def work() -> None:
            try:
                # A job's `after_reload` still queued (the last Rerun's
                # dashboard render) lands before this run starts, never on
                # its live page. Waited here, off the UI loop.
                if not self._background.wait(BACKGROUND_WAIT):
                    logger.warning(
                        "%s: starting with an earlier job's after_reload still "
                        "running after %.0f s",
                        outcome.label,
                        BACKGROUND_WAIT,
                    )
                outcome.run(ctx)
            except (
                BaseException
            ) as e:  # a job's failure is reported, not raised on its thread
                job.error = e
            finally:
                finish = lambda: self._finish_job(job, outcome)  # noqa: E731
                try:
                    self._post(finish)
                except BaseException as e:  # the loop is gone: finish here, never wedge
                    job.error = job.error or e
                    finish()

        threading.Thread(
            target=work, name=f"dstf-job-{outcome.label}", daemon=True
        ).start()

    def _finish_job(self, job: RunningJob, outcome: Job) -> None:
        """Release the lock, record the job as this project's last, reload if
        the job asked and succeeded, tell the listeners, mark done — the last
        two whatever the reload or a listener does, so a waiting shell never
        sees a job that neither finished nor failed."""
        self.running = None
        self.last_job = job
        if job.error is not None:
            # No reload follows a failed job, and its half-run rewrote its
            # models' work dirs: what was read of them is no longer the file.
            for model_id in job.models:
                self._reads.pop(model_id, None)
        try:
            if (
                outcome.reloads_results
                and job.error is None
                and self.project is not None
            ):
                self._reload_results()
                job.reloaded = True
                hook = outcome.after_reload
                if hook is not None:
                    # Off the UI loop (Rerun's writes the dashboard), in the
                    # order the jobs finished; on a snapshot of the
                    # comparisons this reload scored. A hook that raises is
                    # logged: the reload stands.
                    project, comparisons = self.project, dict(self.comparisons)
                    self._background.submit(
                        f"{job.label}: after_reload",
                        lambda: hook(project, comparisons),
                    )
        except BaseException as e:
            job.error = e
        finally:
            try:
                self._emit(
                    "job",
                    state="failed" if job.error is not None else "finished",
                    job=job,
                )
            finally:
                job.done.set()

    # -- internals ---------------------------------------------------------

    def _require_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("no project is open")
        return self.project

    def _merge(
        self, config: Config, scanned: dict[str, TestModel], document: dict
    ) -> list[TestModel]:
        models = spec_models_from_document(
            document,
            base_dir=config.spec_path.parent,
            label=f"{config.spec_path} (in memory)",
        )
        return merge_tests(scanned, spec_tests_by_model(models))

    def _rebuild_tests(
        self, models: set[str], *, scanned: dict[str, TestModel] | None = None
    ) -> None:
        """Re-merge the scan against the effective spec; models the spec no
        longer names drop out of ``comparisons``, models it newly names are
        scored (with whatever result the last reload holds for them).

        ``scanned`` is the rescan's new scan: it is merged in place of the
        project's, and the ``models`` are re-read from the last run with
        their merged TestModels. The read comes BEFORE anything is
        replaced — the scan, the tests, the comparisons — so a read that
        raises (the disk went away) leaves the Session as it was."""
        project = self._require_project()
        scan = project.scanned if scanned is None else scanned
        tests = self._merge(project.config, scan, self.spec.effective())
        current = {t.model_id for t in tests}
        reread = models & current if scanned is not None else set()
        fresh = self._read_again(reread, tests) if reread else {}
        project.scanned, project.tests = scan, tests
        gone = set(self.comparisons) - current
        new = current - set(self.comparisons)
        for model_id in gone:
            del self.comparisons[model_id]
        # Keyed off the reads, not the comparisons: a read cached for a model
        # that has a result but no comparison (a scoring failure leaves one)
        # must not outlive the model either.
        for model_id in set(self._reads) - current:
            del self._reads[model_id]
        if reread:
            self._keep_read(reread, fresh)
        self._emit("spec", models=models | gone | new)
        self._score_all((models | new) & current)

    def _read_again(
        self, models: set[str], tests: Sequence[TestModel]
    ) -> dict[str, TestResult]:
        """``models`` read from the last run with their TestModels in
        ``tests``; nothing is stored. Only they are kept: ``read_results``
        reports every manifest entry, and the others are not being re-read.
        A read that raises leaves every result and read as it was."""
        project = self._require_project()
        to_read = [t for t in tests if t.model_id in models]
        fresh: dict[str, TestResult] = {}

        def on_result(model_id: str, result: TestResult) -> None:
            if model_id in models:
                fresh[model_id] = result

        project.backend.read_last_results(to_read, on_result=on_result)
        return fresh

    def _keep_read(self, models: set[str], fresh: dict[str, TestResult]) -> None:
        """``fresh`` (`_read_again`) in place of what an earlier read stored
        for ``models``. A model it has no result for (no manifest entry)
        ends with none and is scored NO_REF / SIM_FAIL by the caller, as
        after a full reload. The result FILE did not change, so its listing
        (`_ModelReads.names`) is kept, and the next `variable_names` does
        not list it again; what came from the old result goes."""
        project = self._require_project()
        for model_id in models:
            if model_id in fresh:
                project.results[model_id] = fresh[model_id]
            else:
                project.results.pop(model_id, None)
            old = self._reads.pop(model_id, None)
            if old is not None and old.names_read:
                reads = self._reads_for(model_id)
                reads.names, reads.names_read = old.names, True

    def _score(self, model_id: str) -> TestComparison:
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        comp = self._compare(test)
        self.comparisons[model_id] = comp
        return comp

    def _compare(self, test: TestModel) -> TestComparison:
        """Score ``test`` against the last results; stores nothing."""
        project = self._require_project()
        tolerance = project.config.tolerance
        return compare_one(
            test,
            project.results.get(test.model_id),
            project.store,
            float(tolerance) if tolerance is not None else DEFAULT_TOLERANCE,
            project.config.default_points,
            dataset_kind=project.dataset_kind,
        )

    def _score_all(self, models: Iterable[str]) -> None:
        project = self._require_project()
        wanted = set(models)
        for test in project.tests:
            if test.model_id in wanted:
                self._score(test.model_id)
        self._emit("results", models=wanted)

    def _reload_results(self) -> None:
        """Re-read the last run and rescore — every model except the ones a
        running job has locked (their directories are being rewritten, so
        their in-memory result and verdict stand until that job's own
        completion reload). Each result is scored as it is read and
        announced as ``loading(phase="results")``; tests with no result are
        scored and announced after the read, so the last announcement is
        ``done == total``; ONE ``results`` event follows for the batch. A
        model whose scoring raises does not abort the read: the results stay
        complete, its stale verdict is dropped, and the first failure is
        raised after the batch event — ``_finish_job`` records it as the
        job's error (no rows shown for a failed job); ``open`` propagates
        it after its ``done`` event. Each announcement carries ``error``:
        True for a model whose scoring raised (its verdict is absent),
        False otherwise."""
        project = self._require_project()
        locked = self.running.models if self.running is not None else frozenset()
        to_read = [t for t in project.tests if t.model_id not in locked]
        project.results = {m: r for m, r in project.results.items() if m in locked}
        for model_id in list(self._reads):
            if model_id not in locked:
                del self._reads[model_id]
        announced: set[str] = set()
        failures: dict[str, Exception] = {}

        def score_and_announce(model_id: str) -> None:
            failed = False
            try:
                self._score(model_id)
            except Exception as e:
                self.comparisons.pop(model_id, None)
                failures.setdefault(model_id, e)
                failed = True
            if model_id in announced:
                return  # one model under two manifest keys is read twice
            announced.add(model_id)
            self._emit(
                "loading",
                phase="results",
                model=model_id,
                done=len(announced),
                total=len(to_read),
                error=failed,
            )

        def on_result(model_id: str, result: TestResult) -> None:
            if model_id in locked:
                # `read_results` reports every manifest entry, and a locked
                # model — excluded from `to_read` — comes back as "not found
                # in discovery": storing it would rescore the model to
                # SIM_FAIL in the middle of its own job.
                return
            project.results[model_id] = result
            if project.test(model_id) is not None:
                score_and_announce(model_id)

        project.backend.read_last_results(to_read, on_result=on_result)
        # Tests with no result at all (never run, or dropped from the
        # manifest) are scored now — NO_REF / SIM_FAIL states.
        for test in to_read:
            if test.model_id not in project.results:
                score_and_announce(test.model_id)
        self._emit("results", models={t.model_id for t in to_read})
        if failures:
            model_id, error = next(iter(failures.items()))
            raise RuntimeError(
                f"scoring {model_id} failed ({len(failures)} of {len(to_read)} models)"
            ) from error

    def _saved_scoring_tree(self, model_id: str) -> bool:
        """Whether the tree `_materialization` builds now is the one the
        SAVED entry scores — what marks it (`PatchOp.materializes`). Never
        when the saved entry has its own ``metrics``: it scores that tree,
        and one built after a pending op removed it is a change. Otherwise
        the tree is built from ``comparison.variable_overrides`` (the
        tracked variables are not stageable), so it is when those are as
        saved."""

        def overrides(entry: dict) -> dict:
            return (entry.get("comparison") or {}).get("variable_overrides") or {}

        saved = self.spec.saved_entry(model_id) or {"model": model_id}
        if "metrics" in saved:
            return False
        return overrides(saved) == overrides(self.spec.entry(model_id) or {})

    def _materialization(self, model_id: str) -> dict | None:
        """The ``add /metrics`` op for a test the flat path scored, built
        from the variables it scored — the leaves the report shows, in the
        report's order, so leaf ``i`` is ``/metrics/children/i`` on both
        tiers. For a test tracked by its ``.mo`` annotation those names are
        in no spec entry (and the entry may not exist yet: the op creates
        it). ``None`` — and ``rescore`` stages nothing — when the entry
        already has a tree, when nothing was scored (NO_REF, SIM_FAIL,
        simulate-only), or when a tree would not score what the flat path
        scored: a scored variable whose name is not one the result carries
        (a comparison from a run the results no longer match), a string
        ``quantity`` in
        an override (the flat path refuses it, a tree would score it), an
        override naming an
        unregistered mode (the flat path fails it; a tree would score
        nrmse), or a reference-JSON override the spec does not shadow (the
        flat path merges it; a tree ignores it). Each case has its
        negative test in tests/test_session.py."""
        project = self._require_project()
        entry = self.spec.entry(model_id) or {"model": model_id}
        comp = self.comparisons.get(model_id)
        if "metrics" in entry or comp is None or not comp.variables:
            return None
        # A pattern-tracked spec materializes from the names the pattern
        # resolved today (owner ruling, D126): the pattern keeps tracking,
        # the tree scores what it names, and a variable that later matches
        # without a leaf raises the "tracked but not scored" warning.
        names = [vc.name for vc in comp.variables]
        result = project.results.get(model_id)
        resolved = {v.name for v in result.variables} if result is not None else set()
        if not all(n and n in resolved and is_literal_variable(n) for n in names):
            # A leaf must name a variable the loaded result carries, or the
            # tree path resolves it to nothing.
            return None
        if unknown_override_modes(entry):
            return None
        spec_overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
        if any(
            isinstance(ov, dict) and isinstance(ov.get("quantity"), str)
            for ov in spec_overrides.values()
        ):
            # The flat path refuses a stats-path quantity ("needs a metrics
            # tree"); the tree path would score it — a FAIL that becomes a
            # PASS on a look. Authoring the tree is the user's act, not ours.
            return None
        ref = project.store.get_reference(model_id) or {}
        ref_overrides = (ref.get("comparison") or {}).get("variable_overrides") or {}
        if any(n in ref_overrides and n not in spec_overrides for n in names):
            return None
        return implicit_tree_op(entry, variables=names)

    def _refuse_if_busy(self) -> None:
        self._refuse_if_running()
        if self.spec.pending:
            raise PendingEditsError(len(self.spec.pending))

    def _refuse_if_running(self) -> None:
        if self.running is not None:
            raise JobRunningError(f"{self.running.label} is still running")

    def _refuse_if_locked(self, models: Iterable[str]) -> None:
        if self.running is None:
            return
        locked = set(models) & self.running.models
        if locked:
            raise LockedError(locked, self.running.label)

    def _emit(self, event: Event, **payload: Any) -> None:
        """Every listener hears every event: one that raises (a deleted UI
        element, say) is logged and skipped, never allowed to stop the
        others or to unwind a job's completion."""
        for cb in list(self._listeners.get(event, [])):
            try:
                cb({"event": event, **payload})
            except Exception:
                logger.exception("listener for %r failed", event)


@dataclass(frozen=True)
class ProjectView:
    """The project as a capability sees it: no store, no backend."""

    config: Config
    tests: tuple[TestModel, ...]
    results: Mapping[str, TestResult]
    dataset_kind: DatasetType
    _store: ReferenceStore = field(repr=False, compare=False)

    def reference(self, model_id: str) -> dict | None:
        """The primary baseline's JSON, or None."""
        return self._store.get_reference(model_id)

    def baseline_names(self, model_id: str) -> tuple[str, ...]:
        """What a leaf's ``against`` may name: ``primary`` when a baseline
        is stored, then every soft_check, in store order."""
        names: list[str] = []
        if self._store.get_reference(model_id) is not None:
            names.append(PRIMARY_BASELINE)
        names.extend(self._store.get_soft_checks(model_id))
        return tuple(names)


class SessionView:
    """What a capability receives: the Session's state and its readers, and
    the selection of the page that built the view — nothing that writes.
    The enforcement is structural — a capability never holds the Session."""

    __slots__ = ("_session", "_selection")

    def __init__(
        self,
        session: Session,
        # B008 is about a fresh mutable per call; Selection is frozen.
        selection: Selection = Selection(),  # noqa: B008
    ):
        self._session = session
        self._selection = selection

    @property
    def project(self) -> ProjectView | None:
        p = self._session.project
        if p is None:
            return None
        return ProjectView(
            config=p.config,
            tests=tuple(p.tests),
            results=MappingProxyType(p.results),
            dataset_kind=p.dataset_kind,
            _store=p.store,
        )

    @property
    def spec_path(self) -> Path:
        return self._session.spec.path

    @property
    def pending(self) -> tuple[PatchOp, ...]:
        return tuple(self._session.spec.pending)

    @property
    def selection(self) -> Selection:
        return self._selection

    @property
    def running(self) -> RunningJob | None:
        return self._session.running

    @property
    def last_job(self) -> RunningJob | None:
        """The job that ran last in THIS project, whatever its outcome."""
        return self._session.last_job

    @property
    def comparisons(self) -> Mapping[str, TestComparison]:
        return MappingProxyType(self._session.comparisons)

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(self._session.artifacts)

    @property
    def notices(self) -> tuple[str, ...]:
        """The lines the job panel shows, oldest first: what each store write
        applied so far reported, plus the open-time warning if there was one."""
        return tuple(self._session.notices)

    def is_locked(self, model_id: str) -> bool:
        running = self._session.running
        return running is not None and model_id in running.models

    def rescore(self, model_id: str) -> TestComparison:
        return self._session.rescore(model_id)

    def view(self, model_id: str, variable: str) -> VariableView:
        return self._session.view(model_id, variable)

    def figure(
        self,
        model_id: str,
        variable: str,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
    ) -> dict:
        return self._session.figure(
            model_id, variable, editing=editing, visibility=visibility
        )

    def variable_names(self, model_id: str) -> list[str]:
        return self._session.variable_names(model_id)

    def labels(self, model_id: str) -> dict[str, str]:
        """``{name: what a capability's own text reads for it}`` —
        :meth:`Session.labels`, so a panel's caption and the page's rows
        say the same thing. It is the TEXT, not the plain label: a
        capability's caption carries no muted name to disambiguate it."""
        return self._session.labels(model_id)

    def plots(self, model_id: str) -> tuple[ResolvedPlot, ...]:
        return self._session.plots(model_id)

    def plot_figure(
        self,
        model_id: str,
        index: int,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
    ) -> dict:
        return self._session.plot_figure(
            model_id, index, editing=editing, visibility=visibility
        )

    def tree_view(self, model_id: str) -> dict | None:
        return self._session.tree_view(model_id)

    def tree(self, model_id: str) -> dict | None:
        return self._session.tree(model_id)


def _known_models(config: Config, document: object) -> list[str]:
    """The model ids the spec document and the work dir's batch manifest
    name — spec first, each once — read without the scan and without the
    spec parser, which validates trees and can raise: a bad one is the
    merge's to raise. Both files are data; what is not an entry is
    skipped."""
    ids: list[str] = []
    tests = document.get("tests") if isinstance(document, dict) else None
    for entry in tests if isinstance(tests, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("model"), str):
            ids.append(entry["model"])
    work_dir = config.work_dir
    manifest = work_dir / "batch_manifest.json" if work_dir is not None else None
    if manifest is not None and manifest.is_file():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = {}
        for entry in entries.values() if isinstance(entries, dict) else ():
            if isinstance(entry, dict) and isinstance(entry.get("model_id"), str):
                ids.append(entry["model_id"])
    return list(dict.fromkeys(ids))


def _in_order(ids: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in ids:
        seen.setdefault(i, None)
    return list(seen)
