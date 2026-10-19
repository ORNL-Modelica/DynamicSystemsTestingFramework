"""The Session — the authoring tool with no UI (two-tier design, §4.5).

Opens a project, scores its tests from the last run on disk, holds the
spec with the ops staged against it, and applies the outcomes capabilities
return. It writes only through ``save`` (the spec) and through the outcomes
it applies (the store, the queue's work dir). It owns the simulation queue
(D150): one per process, over a worker pool that lives until the tool
stops. Nothing here imports a UI.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, cast

from ..backends import BackendUnavailableError, dataset_kind_for, select_backend
from ..backends.base import Backend
from ..backends.common.result_variables import tracked_results
from ..backends.pool import ItemState, QueueItem, Refused, StartingWorker
from ..backends.progress import recorded_backend, refuse_unregistered
from ..backends.types import (
    DatasetType,
    TestResult,
    VariableText,
)
from ..comparison.comparator import DEFAULT_TOLERANCE, compare_one
from ..comparison.types import TestComparison
from ..config import Config, ConfigError
from ..discovery.library_state import (
    LibraryState,
    Stamp,
    following,
    source_stamps,
    stamp,
)
from ..discovery.mo_parser import LocatedClass, find_class
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
    UnscorableCriterion,
    apply_validated_patches,
    has_metrics,
    implicit_tree_op,
    is_literal_variable,
    needs_materialization,
    not_stored_notes,
    unknown_override_modes,
    validate_entry_keys,
    validate_entry_metric_tree,
)
from ..discovery.test_registry import (
    SpecModelLocator,
    TestModel,
    diagnostic_names,
    merge_tests,
    scan_files,
    scan_source_tests,
    spec_tests_by_model,
)
from ..figures.assemble import leaves_by_variable, view_from_trajectory
from ..figures.view import VariableView, error_series
from ..storage.baseline import PRIMARY_BASELINE, DiagnosticSummary, ReferenceFile
from ..storage.reference_store import ReferenceStore
from .capabilities import (
    Artifact,
    Enqueue,
    OpenProject,
    Outcome,
    Patch,
    StoreWrite,
    plural,
)
from .live_rows import Partial, unscored_view
from .live_runs import LiveRuns
from .situation import unrecorded_variables
from .spec_document import PatchOp, SpecDocument, edit_count, json_equal

if TYPE_CHECKING:
    import numpy as np

    from ..backends.pool import SimulationPool
    from ..comparison.tree_spec import LeafSpec, SpecNode
    from ..comparison.validator import BaselineRoleLookup
    from ..figures.plotly import Visibility
    from ..pipeline import ItemRun
    from ..reporting.stats_rows import StatRow

Event = Literal["spec", "results", "queue", "notice", "loading", "known", "overlays"]
Listener = Callable[[dict[str, Any]], None]

logger = logging.getLogger(__name__)

#: The states of a queue item that is still to end.
ACTIVE = (ItemState.WAITING, ItemState.RUNNING)

# Why a test has no tree to edit — `Session.tree_or_reason`, from
# `_materialization`'s refusal. Each is one short line: a Statistics row's
# `+` says it as its tooltip, a criterion's editor in its row.
#: No comparison scored a variable (no baseline, a failed run, no result).
NOTHING_SCORED = "nothing is scored"
#: A simulate-only test: the comparator scores nothing on it. Also why an
#: add form's Add is grey there, tree or not.
SIMULATE_ONLY = "simulate-only: nothing is scored"
#: A scored name the loaded result does not carry: the comparison is from
#: a run the results no longer match, and a leaf on it would resolve to
#: nothing.
STALE = "its last run no longer holds what it scored — queue it again"
#: Every other refusal: a flat setting of the spec or the baseline that a
#: tree would score differently.
NO_TREE = (
    "its flat criteria can't become a tree without changing its scores — "
    "write a metrics tree in test_spec.json"
)


@dataclass
class Project:
    config: Config
    store: ReferenceStore
    backend: Backend
    scanned: dict[str, TestModel]  # the source scan, merged against every spec edit
    #: Where a spec-only test's model is, with its experiment annotation —
    #: asked on every merge and read once per model; replaced with the scan.
    locate: SpecModelLocator
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
    leaf or a combinator; ``leaf_path`` only ever names a criterion;
    ``leaf_paths`` every member of a folded criteria row (FAMILY_CRITERION,
    D152), in tree order, ``leaf_path`` the first of them — empty wherever
    one criterion is selected;
    ``quantity`` the statistic a Statistics row shows (STATISTIC), by the
    path a leaf would read it through (``translation.nonlinear_count``).

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
    leaf_paths: tuple[str, ...] = ()
    node_path: str | None = None
    quantity: str | None = None
    texts: Mapping[str, VariableText] = MappingProxyType({})


@dataclass
class _ModelReads:
    """What one click re-read from disk before this cache (brief §2.2): the
    primary reference and the overlays; plus the decimated base view per
    variable — trajectories, overlays and error series, no leaves — held
    here and shared by `figure` and `plot_figure` through `_editable_view`,
    so an edit re-decimates only the criterion's geometry and a plot's
    several series draw over the bases held here, one at a time."""

    ref: ReferenceFile | None
    overlays: list
    bases: dict[str, VariableView] = field(default_factory=dict)
    #: Per variable, the decimated view with no Actual — the baseline's
    #: trace and the overlays — that a run in progress, or one that left no
    #: result, is drawn over (D151); dropped with the bases.
    references: dict[str, VariableView] = field(default_factory=dict)
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


def no_tree_error(model_id: str, why: str) -> PatchError:
    """The refusal of an edit that needs ``model_id``'s tree where there is
    none (`Session.tree_or_reason`'s ``why``): a patch the Session will not
    take, whose ``reason`` is ``why`` — what the tool shows alone beside the
    edit it came from — and whose message names the model too."""
    return PatchError(f"{model_id}: {why}", reason=why)


class QueueError(RuntimeError):
    """The simulation queue did not do what was asked, or stands in the way
    of what was; ``str()`` is the sentence why."""


@dataclass(frozen=True)
class _Ask:
    """One test a Queue click names, as the click's read needs it."""

    model_id: str
    #: The file the test was read from; None for a test that has none (a
    #: spec-only test whose model the library does not hold, any test of a
    #: source that is not Modelica).
    path: Path | None
    #: That file's stamp when the Session last read the test from it; None
    #: for a test only the scan at open read, at a moment nobody stamped.
    read_at: Stamp | None
    #: Whether the scan found it; else only ``test_spec.json`` declares it.
    scanned: bool


@dataclass
class _ClickRead:
    """What one Queue click read of the library, off the UI loop."""

    #: The source files' stamps (`library_state.source_stamps`); None for a
    #: source no worker keeps loaded.
    stamps: Mapping[Path, Stamp] | None
    #: The stamp each test's file was read at, for the tests read again.
    read_at: dict[str, Stamp] = field(default_factory=dict)
    #: The scanned tests read again from their files.
    scanned: dict[str, TestModel] = field(default_factory=dict)
    #: The spec-only tests' models, found again.
    located: dict[str, LocatedClass] = field(default_factory=dict)
    #: By model, the sentence for a test that cannot be read any more.
    refused: dict[str, str] = field(default_factory=dict)


def _read_click(config: Config, asks: Sequence[_Ask]) -> _ClickRead:
    """Sweep the source files (stat only) and read again each asked test
    whose own file's stamp is not the one it was last read at. Touches
    nothing of a Session: safe on any thread.

    A stamp is taken BEFORE its file is read: a file edited between the two
    is read newer than its stamp says, and the next click reads it once
    more — never the other way round."""
    stamps = source_stamps(config)
    read = _ClickRead(stamps)
    if stamps is None or config.source_type != "modelica":
        return read
    stale: dict[Path, list[tuple[str, Stamp]]] = {}
    for ask in asks:
        if ask.path is None:
            continue
        # Not in the sweep: spelled another way than the sweep spells it (a
        # scan cached under another path), or gone.
        now = stamps.get(ask.path) or stamp(ask.path)
        if now is not None and now == ask.read_at:
            continue
        if ask.scanned:
            if now is None:
                read.refused[ask.model_id] = _unreadable(ask, "is gone")
            else:
                stale.setdefault(ask.path, []).append((ask.model_id, now))
            continue
        # Declared in the spec only: looked for again — its class may be in
        # another file of the library by now.
        assert config.library_name is not None  # resolved in __post_init__
        located = find_class(config.library_dir, config.library_name, ask.model_id)
        if located is None:
            read.refused[ask.model_id] = _unreadable(
                ask, "is gone" if now is None else _no_longer(ask.path)
            )
            continue
        read.located[ask.model_id] = located
        at = stamps.get(located.mo_file) or stamp(located.mo_file)
        if at is not None:
            read.read_at[ask.model_id] = at
    if stale:
        fresh = scan_files(config, list(stale))
        for path, models in stale.items():
            for model_id, now in models:
                test = fresh.get(model_id)
                if test is None:
                    read.refused[model_id] = (
                        f"{model_id}: its file {path} {_no_longer(path)}"
                    )
                else:
                    read.scanned[model_id] = test
                    read.read_at[model_id] = now
    return read


def _unreadable(ask: _Ask, why: str) -> str:
    return f"{ask.model_id}: its file {ask.path} {why}"


def _no_longer(path: Path) -> str:
    """Why a file that is there gave no test: ``no longer holds it`` — or,
    for one that cannot be opened, ``cannot be read: Permission denied``
    in the system's own words. A file made unreadable read as one edited
    until the test was gone from it."""
    try:
        with open(path, "rb"):
            pass
    except OSError as e:
        return f"cannot be read: {e.strerror or type(e).__name__}"
    return "no longer holds it"


def resolve_project(
    config_path: Path, *, backend: str | None = None, work_dir: Path | None = None
) -> tuple[Config, str | None, Backend]:
    """What an open of ``config_path`` works with: its Config, the warning
    about the backend the last run recorded (or None), and the backend that
    reads its results. Without an explicit ``backend`` the one the last run
    recorded wins, as ``compare`` does. ``ConfigError`` for a testing.json
    that cannot be read as one and for a backend this machine cannot
    build."""
    config_path = Path(config_path)
    config = Config(config_file=config_path, backend_name=backend, work_dir=work_dir)
    warning: str | None = None
    if backend is None:
        assert config.work_dir is not None  # resolved in __post_init__
        recorded, warning = recorded_backend(config.work_dir)
        if recorded and recorded != config.backend_name:
            refuse_unregistered(recorded, config.work_dir)
            config = Config(
                config_file=config_path, backend_name=recorded, work_dir=work_dir
            )
    try:
        # The Session's backend reads results; it is not the queue's
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
    return config, warning, backend_obj


def check_project(
    config_path: Path, *, backend: str | None = None, work_dir: Path | None = None
) -> None:
    """Everything of an open that a config or spec error stops, and nothing
    slow: the Config, the backend, and the spec file parsed as the open
    parses it — no scan of the library, no result read. ``ConfigError``
    with the sentence every other command prints.

    ``dstf author`` runs it BEFORE it serves: the project used to be opened
    only once the server was up, on a thread, so `"theme": "neon"`, a
    `baseline_points` of 1 or an unknown spec key started the server,
    logged a traceback and showed the error in the page — where `run` and
    `compare` print one `Config error: …` line and exit 2."""
    config, _warning, _backend = resolve_project(
        config_path, backend=backend, work_dir=work_dir
    )
    spec = SpecDocument.load(config.spec_path)
    spec_models_from_document(
        spec.loaded, base_dir=config.spec_path.parent, label=str(config.spec_path)
    )


class StaleSpecError(RuntimeError):
    """The spec file is not the one the Session loaded: nothing that reads
    the loaded spec to WRITE — Save, its edits; Accept, a baseline with the
    entry's `baseline_points` and comparison settings — goes on. ``doing``
    is what was refused, for the sentence."""

    #: What each refused act did NOT do, as the dialog says it.
    NOT_DONE = {"saving": "Nothing was saved.", "accepting": "Nothing was accepted."}

    def __init__(self, path: Path, doing: str = "saving"):
        self.path = path
        self.not_done = self.NOT_DONE[doing]
        super().__init__(
            f"{path} changed on disk since it was loaded; reload before {doing}"
        )


class UnscorableSaveError(RuntimeError):
    """Save refused: a pending edit leaves a criterion that can never be
    scored. One sentence naming it; nothing was written and every edit is
    still pending."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{reason} — fill it or remove it before saving")


class PendingEditsError(RuntimeError):
    def __init__(self, count: int):
        self.count = count
        super().__init__(
            f"{count} staged edit(s) would be lost; save or discard them first"
        )


class _Background:
    """One daemon thread that runs what the Session hands it off the UI
    loop — the drained queue's ``status.json`` and dashboard — one at a
    time and in the order handed. Started when there is work, ended when there is none. What it
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
        """``post`` hands what another thread has for the Session — the
        pool's callbacks, the rescan's merge — to the UI loop, where every
        mutation happens; without one it runs on the thread that has it."""
        self.project: Project | None = None
        self.spec: SpecDocument = SpecDocument(
            Path("test_spec.json"), {"tests": []}, None
        )
        #: The simulation queue (D150): every item queued and not cleared
        #: since, in run order — first in, first out. Read it; `enqueue`,
        #: `remove`, `cancel` and `clear_finished` change it.
        self.queue: list[QueueItem] = []
        #: The pool the queue runs on: opened at the first `enqueue`, alive
        #: — its workers with it — until `stop_queue`.
        self._pool: SimulationPool | None = None
        #: The queue's items still to end, by model: what the pool's
        #: callbacks are matched against, so one for an item the Session
        #: has already dealt with (a close's, a replaced pool's) is nothing.
        self._live: dict[str, QueueItem] = {}
        #: The items that ran since ``status.json`` was last written.
        self._unwritten: list[QueueItem] = []
        #: The workers the user asked for; None is ``Config.parallel``.
        self._workers: int | None = None
        #: The pool's ``write_status`` (`pipeline.ItemRun`), kept past the
        #: pool: the last items' snapshot is written after it closed.
        self._write_status: Callable[[Iterable[QueueItem]], None] | None = None
        #: Why this project's backend cannot be queued on, once an
        #: `enqueue` found out; None while nothing says so.
        self.queue_refusal: str | None = None
        #: The message the pool stopped with (no worker could start), until
        #: `clear_finished` or the next item that starts.
        self.queue_stopped: str | None = None
        #: The source files as the last Queue click swept them: the next
        #: click's items carry this same state when nothing changed since,
        #: and no worker then reads anything again.
        self._library: LibraryState | None = None
        #: By model, the stamp of the test's file when a Queue click last
        #: read the test from it. A test with none here was read by the
        #: scan at open, at a moment nobody stamped.
        self._read_at: dict[str, Stamp] = {}
        #: The models of the Queue clicks whose read of the library is
        #: still running off the UI loop: a second click on one is refused.
        self._reading: set[str] = set()
        #: The number of the last `stage` call (`PatchOp.gesture`).
        self._gesture = 0
        #: By model, the sentence a Queue click's read refused it with,
        #: while it stands among the notices (`_forget_refusals`).
        self._refusals: dict[str, str] = {}
        #: Counts `stop_queue`: a click read before one is not queued after
        #: it (the tool stopping must leave no worker behind).
        self._queue_epoch = 0
        self.comparisons: dict[str, TestComparison] = {}
        self.artifacts: list[Artifact] = []
        self.notices: list[str] = []
        self._post = post or (lambda fn: fn())
        #: Whether there is a UI loop to keep free: a Queue click's read of
        #: the library then runs on the background thread (`_enqueue_later`).
        self._has_loop = post is not None
        self._listeners: dict[str, list[Listener]] = {}
        #: The number of the last `spec` event, which carries it as
        #: ``revision`` (`_emit_spec`): a page tells the event an act of
        #: its own produced from one queued before or after it.
        self.spec_revision = 0
        self.rescan_done = threading.Event()
        self._reads: dict[str, _ModelReads] = {}
        self._background = _Background()
        #: The runs whose result the Session does not have (D151): the
        #: tests the queue is running, and the partial results of runs
        #: that did not finish.
        self.live_runs = LiveRuns(
            self, lambda state, models: self._emit("queue", state=state, models=models)
        )

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
        Refused while the queue holds a test or while edits are pending —
        save or discard first; a swap must never drop them silently."""
        self._refuse_if_busy()
        config, warning, backend_obj = resolve_project(
            config_path, backend=backend, work_dir=work_dir
        )
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
            locate = SpecModelLocator(config)
            tests = self._merge(config, scanned, spec.effective(), locate, edited=False)
            # The queue is the open project's, and its workers the open
            # backend's: both end here, at the commit point (nothing is
            # queued or running — refused above).
            self._reset_queue()
            self.project = Project(
                config=config,
                store=store,
                backend=backend_obj,
                scanned=scanned,
                locate=locate,
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
            # notice, so the queue panel shows it.
            self.notices = [warning] if warning else []
            self.artifacts = []
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
            self._emit_spec({t.model_id for t in tests})
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
        which a running model cannot read again until its run ends.
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
                    self._take_rescan(project, fresh, changed_models)
                self._emit("loading", phase="rescan", changed=bool(changed_models))
            finally:
                done.set()

        self._post_or_run(apply)

    def _take_rescan(
        self, project: Project, fresh: dict[str, TestModel], changed: set[str]
    ) -> None:
        """On the UI loop: the background rescan's scan in place of the
        project's. What a Queue click read since the open stays: the click
        read its tests' files at a stamp it kept, the rescan read them at a
        moment nobody stamped — before the click's, for all anyone knows —
        and a waiting item must not be handed the older test."""
        kept = {m: project.scanned[m] for m in self._read_at if m in project.scanned}
        located = {
            m: found
            for m in self._read_at
            if m not in project.scanned and (found := project.locate(m)) is not None
        }
        scan = {**fresh, **kept}
        if kept:
            self._store_scan_later(project.config, scan)
        changed = changed - set(kept)
        if changed:
            # Only what changed is rescored: a one-file edit over
            # TRANSFORM must not run 331 comparisons on the UI loop.
            # A running model is not re-read: its directory is being
            # rewritten, and its result arrives when it ends.
            # The changed models' results are re-read with their new
            # TestModels before they are rescored: a model the cache
            # did not name was read as "not found in discovery"
            # (SIM_FAIL where a cold open says NO_REF), and a model
            # whose tracked variables changed was read with the old
            # list.
            self._rebuild_tests(
                changed - self._running_models(),
                scanned=scan,
                # A new scan is the library read again: so are the
                # spec-only models.
                locate=SpecModelLocator(project.config).having(located),
            )

    def _post_or_run(self, fn: Callable[[], None]) -> None:
        """Hand ``fn`` to the UI loop; when the loop is gone (``post``
        raises) run it here, so a waiter on the Event is never left
        hanging."""
        try:
            self._post(fn)
        except BaseException:
            fn()

    def close(self) -> None:
        self._refuse_if_busy()
        self._reset_queue()
        self.project = None
        self.artifacts = []
        self.notices = []
        self.spec = SpecDocument(Path("test_spec.json"), {"tests": []}, None)
        self.comparisons = {}
        self._reads = {}
        self._emit_spec(set())

    def reload(self) -> None:
        """Re-read the spec from disk, dropping pending ops (what a refused
        save offers). A reload that cannot be done leaves the Session as it
        was — its spec, its pending ops, its tests: a file that is no spec
        (`ConfigError`, the decoder's and the merge's), and a file that was
        there when it was loaded and is gone now. `SpecDocument.load` reads
        a missing file as the empty document, which is right for a project
        that never had one; over a deleted file it emptied the Suite."""
        project = self._require_project()
        held = self.spec
        spec = SpecDocument.load(project.config.spec_path)
        if spec.content_hash is None and held.content_hash is not None:
            raise ConfigError(
                f"{spec.path} is gone: nothing was reloaded — put the file "
                "back, then Reload"
            )
        self.spec = spec
        try:
            self._rebuild_tests(set(self.comparisons))
        except BaseException:
            # The merge reads before it replaces anything (`_rebuild_tests`).
            self.spec = held
            raise

    def wait_background(self, timeout: float | None = None) -> bool:
        """Wait until what the Session runs off the UI loop — a drained
        queue's ``status.json`` and dashboard — has finished; False on a
        timeout."""
        return self._background.wait(timeout)

    def on(self, event: Event, cb: Listener) -> None:
        self._listeners.setdefault(event, []).append(cb)

    # -- scoring and figures ----------------------------------------------

    def rescore(self, model_id: str) -> TestComparison:
        """Score ``model_id`` against the spec with pending ops applied. A
        flat-override test first gets the same ``add /metrics`` op
        ``spec update`` prepends, so the document the tool edits and the one
        it saves never differ in shape — but only when that tree scores
        exactly what the flat path scored (Conventions #7); otherwise the
        test is scored as loaded and nothing is staged. That op does not
        stay while it is the tree the saved entry scores by, whatever else
        is pending: an entry's own tree written out is no edit of it
        (`stage`). It stays where a pending op edited the overrides it is
        built from."""
        op, _why = self._materialization(model_id)
        if op is not None:
            # Marked as `stage` marks its own: the drawer previews it as
            # nothing changed, with the note that Save writes it. And no
            # act of its own: where it stays, a pending op of this test
            # edited what it is built from, and it is part of THAT edit —
            # one override staged, then a rescore, read "2 pending edits".
            mine = [o.gesture for o in self.spec.pending if o.model_id == model_id]
            self.stage(
                [
                    PatchOp(
                        model_id,
                        op["op"],
                        op["path"],
                        op["value"],
                        materializes=self._saved_scoring_tree(self.spec, model_id),
                    )
                ],
                part_of=mine[-1] if mine else None,
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
        shown = self.live_runs.series(model_id, variable)
        if shown is not None:
            return self._unfinished_view(model_id, variable, *shown)
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
        leaves = leaves_by_variable(
            comp.metric_tree if comp is not None else None, self.card_paths(model_id)
        ).get(variable, [])
        return view_from_trajectory(variable, traj, leaves)

    def _unfinished_view(
        self,
        model_id: str,
        variable: str,
        series: tuple[np.ndarray, np.ndarray] | None,
        samples: int,
    ) -> VariableView:
        """The view of a variable whose test has no result because of a
        run (D151): the baseline's trace and the overlays as always, and as
        the Actual ``series`` — the samples read so far of a run in
        progress, a partial file's after one that did not finish — or none.
        No leaf: nothing of it is scored. ``samples`` is how many the series
        was decimated from, 0 for one handed in whole."""
        from ..figures.view import decimate_view
        from ..reporting.overlay_loader import attach_overlays_to_trajectories

        reads = self._reads_for(model_id)
        reference = reads.references.get(variable)
        if reference is None:
            texts = self.variable_texts(model_id)
            ref = reads.ref
            _comp, kept = self._layout_source(model_id)
            ref_var = ref.baseline.variable(variable) if ref is not None else None
            traj = {
                "name": variable,
                # What an unmapped companion is matched by (D132).
                "expression": next(
                    (
                        v.expression
                        for v in (kept.variables if kept is not None else ())
                        if v.name == variable
                    ),
                    "",
                ),
                "text": texts[variable].text if variable in texts else variable,
                "ref_time": list(ref.baseline.time)
                if ref is not None and ref_var
                else [],
                "ref_values": list(ref_var.values) if ref_var else [],
            }
            attach_overlays_to_trajectories([traj], reads.overlays)
            reference = reads.references[variable] = decimate_view(
                view_from_trajectory(variable, traj, []),
                self._require_project().config.max_embedded_samples,
            )
        if series is None:
            return reference
        time, values = series
        return replace(
            reference,
            act_time=time,
            act_values=values,
            # Nothing to compare before the run's first sample.
            errors=error_series(reference.ref_time, reference.ref_values, time, values)
            if len(time)
            else (),
            full_samples={**reference.full_samples, "act": samples or len(time)},
        )

    def _layout_source(
        self, model_id: str
    ) -> tuple[TestComparison | None, TestResult | None]:
        """The comparison and the result ``model_id``'s LAYOUT is read from
        — the scored names its plots derive from, its tracked names, its
        labels: the Session's own, or, while a run has taken them (in
        progress, or ended with no result), the ones that run replaced
        (D151). Never a verdict's or a trajectory's source."""
        kept = self.live_runs.kept(model_id)
        if kept is not None:
            return kept
        return (
            self.comparisons.get(model_id),
            self._require_project().results.get(model_id),
        )

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
            reads.references.clear()
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
        from ..comparison.tree_spec import collect_variables, tree_variables

        project = self._require_project()
        comp, result = self._layout_source(model_id)
        # A card leaf scores a stored number: a statistic's parsed
        # `variable` (its quantity path) is no variable — listed here, the
        # add form's picker offered `translation.*` paths as "Other
        # variables" — and a listed name only numbers score is no tracked
        # one: the drawer lists it under Diagnostics. A name a curve
        # criterion scores too stays (`tree_variables`).
        spec = self._entry_tree(model_id)
        numbers = (
            set(collect_variables(spec))
            - set(tree_variables(spec, self.diagnostic_names(model_id)))
            if spec
            else set()
        )
        scored = (
            [vc.name for vc in comp.variables if vc.name not in numbers]
            if comp is not None
            else []
        )
        test = project.test(model_id)
        if result is not None and test is not None:
            result = replace(result, variables=tracked_results(test, result))
        return tracked_names(scored, result)

    def not_stored_notes(self, model_id: str) -> list[str]:
        """A sentence per baseline the test's entry, as staged, names that
        this folder stores none of (:func:`~dstf.discovery.spec_update.
        not_stored_notes`): `stage` took it — a soft check of another
        folder — and the pending preview says it is not scored here, as
        ``spec update`` prints it."""
        project = self._require_project()
        return not_stored_notes(self.spec.entry(model_id), model_id, project.store)

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
        comp, result = self._layout_source(model_id)
        return test_texts(
            list(comp.variables) if comp is not None else [],
            result,
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
        the model is running and it was not listed before."""
        project = self._require_project()
        reads = self._reads_for(model_id)
        if reads.names_read:
            return reads.names or ()
        if self._is_running(model_id):
            # The file its run is writing, once that can be listed (D151):
            # kept with reads that go when the run ends, so the finished
            # file is listed again.
            live = self.live_runs.names(model_id)
            if live is not None:
                reads.names, reads.names_read = live, True
            return reads.names or ()
        if self.live_runs.partial(model_id) is not None:
            # What its last run left, through the one parse its plots are
            # read through (`LiveRuns.names`): the same file, not listed a
            # second time.
            reads.names = self.live_runs.names(model_id)
            reads.names_read = True
            return reads.names or ()
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
            test = by_id.get(model_id)
            missing = unrecorded_variables(
                test,
                project.results.get(model_id),
                diagnostic_names(test, project.config.diagnostic_variables)
                if test is not None
                else (),
            )
            if missing and not set(missing).isdisjoint(self._file_names(model_id)):
                out.add(model_id)
        return out

    def _is_running(self, model_id: str) -> bool:
        """The queue is running this model: its work dir is being rewritten,
        so the file reads serve what they already hold and open nothing —
        as `view` and `figure` always have, from memory. Read off the item
        itself, which the pool marks RUNNING before it clears the
        directory: the Session hears of the start a tick later."""
        item = self._live.get(model_id)
        return item is not None and item.state is ItemState.RUNNING

    def _running_models(self) -> frozenset[str]:
        return frozenset(m for m in self._live if self._is_running(m))

    def _file_series(
        self, model_id: str, variable: str
    ) -> tuple[np.ndarray, np.ndarray] | None:
        reads = self._reads_for(model_id)
        if variable not in reads.series:
            if self._is_running(model_id):
                return None  # not cached: the run's end answers it
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

    def diagnostic_names(self, model_id: str) -> list[str]:
        """The names ``model_id`` lists (`diagnostic_names`: the project's
        `diagnostic_variables`, then the test's own). An unknown model is a
        KeyError."""
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        return diagnostic_names(test, project.config.diagnostic_variables)

    def card_paths(self, model_id: str) -> set[str]:
        """The tree paths of the test's leaves on the Statistics card
        (`statistic_leaves`, read from the entry's tree, never `tree_view`):
        drawn on no plot (`leaves_by_variable`)."""
        return {path for _, path in self.statistic_leaves(model_id)}

    def baseline_summaries(self, model_id: str) -> dict[str, DiagnosticSummary]:
        """The summaries the test's baseline stores of the names it listed,
        by name — read from the CACHED reference; ``{}`` without one. What
        a number's seed reads where no run recorded the name. An unknown
        model is a KeyError."""
        if self._require_project().test(model_id) is None:
            raise KeyError(model_id)
        ref = self._reads_for(model_id).ref
        return {d.name: d for d in ref.baseline.diagnostics} if ref is not None else {}

    def _plot_leaves(self, model_id: str) -> list[LeafSpec] | None:
        """:func:`~dstf.discovery.plots.plot_leaves` for this test: the
        leaves its plots derive from, ``None`` for one plot per tracked
        name. From the tree SPEC and what was compared — no baseline read."""
        test = self._require_project().test(model_id)
        if test is None:
            raise KeyError(model_id)
        comp, _result = self._layout_source(model_id)
        compared = [vc.name for vc in comp.variables] if comp is not None else []
        return plot_leaves(test, compared, self.diagnostic_names(model_id))

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
        never folds (O3); a failed run has none. While a model runs whose
        names were never read, nothing is missing."""
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        self.variable_names(model_id)  # reads the file's names, once
        reads = self._reads_for(model_id)
        comp, result = self._layout_source(model_id)
        plots = plots_of_test(
            test,
            [vc.name for vc in comp.variables] if comp is not None else [],
            result,
            listed=reads.names,
            diagnostics=self.diagnostic_names(model_id),
        )
        if reads.names_read:
            return plots
        # The model is running and its names were never read: a listing
        # not taken is not a file without the context and watch series, so
        # nothing is missing until its run has ended and the file is listed.
        return tuple(replace(plot, missing=()) for plot in plots)

    def plot_figure(
        self,
        model_id: str,
        index: int,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
        theme: str = "default",
    ) -> dict:
        """The editable figure of plot ``index`` (IndexError past the end).
        A subject of one variable with no context is :meth:`figure`; a
        family, or context, is drawn over the same cached bases. A context
        view draws as a trace only — a context variable scored on another
        plot signs no leaf onto this one. ``editing``, ``visibility`` and
        ``theme`` as in :meth:`figure`."""
        from ..figures.plotly import plot_figure

        if index < 0:
            raise IndexError(index)
        plot = self.plots(model_id)[index]
        if len(plot.subject) == 1 and not plot.context:
            return self.figure(
                model_id,
                plot.subject[0],
                editing=editing,
                visibility=visibility,
                theme=theme,
            )
        return self._dressed(
            model_id,
            plot_figure(
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
                theme=theme,
            ),
            theme,
        )

    def _dressed(self, model_id: str, figure: dict, theme: str) -> dict:
        """``figure`` with what a run without a result adds to it
        (`LiveRuns.dress`), in the theme's muted text; as it is for every
        other model."""
        from ..figures.palette import palette_of

        return self.live_runs.dress(model_id, figure, palette_of(theme).context)

    def figure(
        self,
        model_id: str,
        variable: str,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
        theme: str = "default",
    ) -> dict:
        """The tool's figure is the editable one — handle rings a user can
        drag (``variable_figure(..., editable=True)``); the report's is not.
        ``editing``: the leaf paths whose handles get a ring, ``None`` meaning
        every leaf; the Plotly ``config`` never depends on it. ``visibility``:
        what the page chose to show (legend b); the cached base does not
        depend on it. ``theme``: the bundled theme the figure is drawn in —
        the TAB's (D148), which is why it is an argument and nothing the
        Session holds; the cached base does not depend on it either."""
        from ..figures.plotly import variable_figure

        if self._require_project().test(model_id) is None:
            raise KeyError(model_id)  # before the cache: never cache a read for it
        return self._dressed(
            model_id,
            variable_figure(
                self._editable_view(model_id, variable),
                None,
                editable=True,
                rings_for=editing,
                visibility=visibility,
                theme=theme,
            ),
            theme,
        )

    def _editable_view(self, model_id: str, variable: str) -> VariableView:
        """The decimated base (cached) joined to the leaves decimated on
        their own — what both figure reads draw, WITHOUT a budget.
        The base traces are decimated once per model and variable and
        cached until the model's scored list moves (`_ModelReads.scored`);
        only the leaves' geometry is decimated per call. The exception is a
        running model: nothing is cached for it, so every call there
        re-decimates the base."""
        from ..figures.view import build_view, decimate_view

        project = self._require_project()
        budget = project.config.max_embedded_samples
        reads = self._reads_for(model_id)
        scored = _scored_key(self.comparisons.get(model_id))
        if reads.scored != scored:
            reads.bases.clear()
            reads.references.clear()
            reads.scored = scored
        base = reads.bases.get(variable)
        if base is None:
            base = decimate_view(
                replace(self.view(model_id, variable), leaves=()), budget
            )
            # Not cached while the model runs, for `_file_series`' reason: it
            # answers None there and caches nothing, so an untracked series'
            # base is EMPTY.
            if not self._is_running(model_id):
                reads.bases[variable] = base
        comp = self.comparisons.get(model_id)
        leaves = leaves_by_variable(
            comp.metric_tree if comp is not None else None, self.card_paths(model_id)
        ).get(variable, [])
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
            return self._unscored_view(test)
        base = (
            (comp.base_tolerance, comp.tolerance_source)
            if comp.base_tolerance is not None and comp.tolerance_source is not None
            else None
        )
        return build_tree_view(
            list(comp.variables),
            test,
            comp.metric_tree,
            self.variable_texts(model_id),
            # the editor's "in effect" hints (K3); the report shows none
            base,
        )

    def _unscored_view(self, test: TestModel) -> dict | None:
        """The criteria of a test with nothing scored BECAUSE OF A RUN —
        in progress, or ended without finishing (D151) — as a tree view
        with a word in place of every verdict (`live_rows.unscored_view`):
        the tree as authored, pending edits included, else the implicit
        tree of what its last result scored. None for any other test, and
        for one with neither."""
        from ..comparison.tree_spec import synthesize_implicit_tree

        model_id = test.model_id
        word = self.live_runs.word(model_id)
        if word is None:
            return None
        spec = test.metric_tree_spec
        if spec is None:
            comp, _result = self._layout_source(model_id)
            compared = [vc.name for vc in comp.variables] if comp is not None else []
            if not compared:
                return None
            spec = synthesize_implicit_tree(
                compared, variable_overrides=test.variable_overrides
            )
        return unscored_view(spec, self.variable_texts(model_id), word)

    def partial(self, model_id: str) -> Partial | None:
        """What ``model_id``'s last run left when it did not finish — how
        it ended and the time its file reached — or None. Shown on its
        page; never scored, never a baseline."""
        return self.live_runs.partial(model_id)

    def statistics(self, model_id: str) -> list[StatRow]:
        """The test's Statistics rows (:func:`~dstf.reporting.stats_rows.
        statistics_rows`): the last run's statistics beside its baseline's,
        plus a row for every quantity a statistics leaf of the entry's tree
        reads that no recorded statistic holds. The baseline's rows alone
        with no result; with neither, only those leaves' "other scored"
        rows (``[]`` when the tree reads no statistic). Reads the CACHED
        reference, which `statistics_rows` never writes. An unknown model is a
        KeyError, as for :meth:`tree`."""
        from ..comparison.tree_spec import statistic_paths
        from ..reporting.stats_rows import statistics_rows
        from ..storage.baseline_codec import DOUBLE_SHOWN, shown_digits

        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)
        result = project.results.get(model_id)
        ref = self._reads_for(model_id).ref
        return statistics_rows(
            result.statistics if result is not None else None,
            ref.baseline.statistics if ref is not None else None,
            run_diagnostics=(result.diagnostics or []) if result is not None else (),
            baseline_diagnostics=ref.baseline.diagnostics if ref is not None else (),
            extra_paths=statistic_paths(
                self._entry_tree(model_id), self.diagnostic_names(model_id)
            ),
            baseline_digits=shown_digits(ref.sampling)
            if ref is not None
            else DOUBLE_SHOWN,
        )

    def statistic_leaves(self, model_id: str) -> list[tuple[LeafSpec, str]]:
        """The card leaves of the entry's OWN tree (`on_the_card`: a
        statistic, or a listed name's number), staged edits included, with
        their paths (:func:`~dstf.comparison.tree_spec.statistic_leaves`):
        which Statistics rows are scored. Read from the
        tree as authored, not from :meth:`tree_view` — a test with nothing
        compared (no baseline) has no view, and its criteria are criteria
        all the same. An unknown model is a KeyError."""
        from ..comparison.tree_spec import statistic_leaves

        return statistic_leaves(
            self._entry_tree(model_id), self.diagnostic_names(model_id)
        )

    def _entry_tree(self, model_id: str) -> SpecNode | None:
        """The entry's OWN tree, parsed; None when it has none. Never the
        tree :meth:`tree` materializes for a flat entry: that reads the
        baseline, and an implicit tree holds no statistics leaf."""
        from ..comparison.tree_spec import parse_metric_tree

        raw = (self.spec.entry(model_id) or {}).get("metrics")
        return parse_metric_tree(raw) if raw is not None else None

    def tree(self, model_id: str) -> dict | None:
        """The raw tree a Patch edits (:meth:`tree_or_reason`'s tree)."""
        return self.tree_or_reason(model_id)[0]

    def tree_or_reason(self, model_id: str) -> tuple[dict | None, str | None]:
        """``(tree, None)`` — the raw tree a Patch edits: the entry's
        ``metrics`` block, else the tree ``rescore`` would materialize (the
        report's leaves) — or ``(None, why)``: why there is none, the ONE
        text every surface says it with (`stage`'s refusal, a criterion's
        editor, Edit tree, Edit on plot, a Statistics row's `+`), from
        `_materialization`'s own refusal, so the baseline is read once for
        both. The tree is a copy: a capability may build its next op from it
        without touching the document. An unknown model is a KeyError, as
        for ``tree_view``."""
        if self._require_project().test(model_id) is None:
            raise KeyError(model_id)
        entry = self.spec.entry(model_id)
        if has_metrics(entry):
            assert entry is not None  # `has_metrics` said so
            return copy.deepcopy(entry["metrics"]), None
        op, why = self._materialization(model_id)
        return (op["value"], None) if op is not None else (None, why)

    # -- the spec ----------------------------------------------------------

    def stage(self, ops: Sequence[PatchOp], *, part_of: int | None = None) -> None:
        """Stage ``ops`` and rescore their models. Materializes the implicit
        tree where a per-leaf op needs one — or an op that edits the tree
        whole (`PatchOp.edits_tree`); validates each touched entry
        (paths, the parsed tree, the D66 roles). On any error the Session
        is unchanged. A queued test takes every edit, and runs with the spec
        as it stands when it starts; a running one takes its criteria's —
        scored when its result arrives — and none of its simulation
        settings', which its run has already taken (D150).

        One call is one edit (`PatchOp.gesture`), unless ``part_of`` names
        the edit these ops belong to: `rescore`'s, which no one did."""
        project = self._require_project()
        models = {op.model_id for op in ops}
        for op in ops:
            if self._is_running(op.model_id) and _in_simulation(op.path):
                raise QueueError(
                    f"{op.model_id} is running: its simulation settings are "
                    "locked until its run ends"
                )
        staged: list[PatchOp] = []
        # One call is one act of the user's, however many ops it takes
        # (`PatchOp.gesture`): what every count of pending edits counts.
        self._gesture += 1
        gesture = self._gesture if part_of is None else part_of
        for model_id in _in_order(op.model_id for op in ops):
            raw = [op.raw() for op in ops if op.model_id == model_id]
            entry = self.spec.entry(model_id) or {"model": model_id}
            # A gesture on a folded row gives the tree WHOLE (D152): on a
            # flat entry it is an edit of the implicit tree all the same,
            # and that tree is written out first, as for a per-leaf op — or
            # the preview showed every line of the tree as added, with no
            # note that Save writes it.
            edits_whole = not has_metrics(entry) and any(
                op.edits_tree for op in ops if op.model_id == model_id
            )
            if needs_materialization(entry, raw) or edits_whole:
                # The same tree rescore stages — from what the flat path
                # scored — never one from the entry's list: on both tiers
                # leaf i is /metrics/children/i of the report's tree.
                tree_op, why = self._materialization(model_id)
                if tree_op is None:
                    assert why is not None  # the entry has no tree
                    raise no_tree_error(model_id, why)
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
                        materializes=self._saved_scoring_tree(self.spec, model_id),
                        gesture=gesture,
                    )
                )
            # The ops as given: `rescore`'s materialization carries its mark.
            # Each value a copy: the caller's dict stays the caller's, and
            # editing it after staging changes neither export nor Save.
            staged += [
                replace(op, value=copy.deepcopy(op.value), gesture=gesture)
                for op in ops
                if op.model_id == model_id
            ]
        # Convention 8: an edit that lands on the path the last pending op
        # wrote supersedes it, so the pending count and the envelope carry
        # one op, never a keystroke log (the drawer shows the entry's diff
        # either way): a replace after an add or a replace of the same path
        # is that op with the new value, a remove after a replace is the
        # remove, and an add after an add on an OBJECT member is the second
        # add — it replaces what the first put there (two moves in a row:
        # two whole trees). An add after an add at a list INDEX inserts
        # again, and both stay. A remove after an ADD is
        # kept as two: when the member existed, the add replaced it, and
        # dropping both would leave it. A batch is never collapsed: its ops
        # belong together. A replace at an append path (``/-``) names no
        # member and is left for `effective` to refuse, never folded in. A
        # fold onto the op that materialized the tree is no longer that
        # tree: the folded op is not marked (`PatchOp.materializes`). What
        # the replaced op said it did (`PatchOp.notes`) is kept with what
        # the new one says: under one key the new sentence, else both.
        pending = list(self.spec.pending)
        if len(staged) == 1 and pending:
            new, last = staged[0], pending[-1]
            same = (new.model_id, new.path) == (last.model_id, last.path)
            if same and not new.path.endswith("/-"):
                # The op that supersedes is the SAME edit as the one it
                # replaces (its `gesture`): a value typed over is one.
                if new.op == "replace" and last.op in ("add", "replace"):
                    pending.pop()
                    staged = [
                        PatchOp(
                            new.model_id,
                            last.op,
                            new.path,
                            new.value,
                            gesture=last.gesture,
                        )
                    ]
                elif (new.op, last.op) == ("remove", "replace"):
                    pending.pop()
                    staged = [replace(new, gesture=last.gesture)]
                elif (new.op, last.op) == ("add", "add") and not new.path.rsplit(
                    "/", 1
                )[-1].isdigit():
                    pending.pop()
                    said = {**dict(last.notes), **dict(new.notes)}
                    staged = [
                        replace(new, notes=tuple(said.items()), gesture=last.gesture)
                    ]
        candidate = SpecDocument(
            self.spec.path,
            self.spec.loaded,
            self.spec.content_hash,
            [*pending, *staged],
        )
        effective = candidate.effective()  # PatchError on a bad path
        for model_id in models:
            # A key nothing reads is refused at once, as Save's own check
            # refuses it, with the sentence alone. A criterion that can
            # never be scored is NOT refused here: it is staged, its row
            # says why it is not scored, and Save refuses the write that
            # makes it (`spec_update.refuse_newly_unscorable`). Refused
            # here, a bare `range` could not be added for its editor to
            # fill, and an entry that already held one took no edit at all
            # — not the one that filled it.
            validate_entry_keys(effective, model_id)  # PatchError
            validate_entry_metric_tree(
                effective, model_id, project.config, project.store
            )  # PatchError
        # An edit that leaves an entry exactly as saved is no pending edit
        # of it — a value typed and cleared again, a refused one put back:
        # its ops counted ("1 pending edit", Save enabled), and Save rewrote
        # the file for nothing. Decided HERE, on the folded list, the one
        # thing the count, the strip, the Tests drawer's mark, Save and the
        # preview read: every op of such a model goes. After the checks
        # above, so a stage that is refused is refused as it was; the model
        # is still rescored below, back to its saved verdict. A flat entry's
        # own tree written out is no edit of it either
        # (`_its_tree_written_out`): the first per-leaf edit writes that
        # tree, and the edit put back left it — "2 unsaved", and a Save that
        # added a `metrics` block scoring as the entry already did. Those
        # ops go FIRST and the rest is then asked whether it is as saved:
        # beside a plots edit the tree goes and the plots edit stays, and
        # with nothing else changed nothing is left.
        gone = [op for m in models for op in self._its_tree_written_out(candidate, m)]
        if gone:
            candidate = SpecDocument(
                self.spec.path,
                self.spec.loaded,
                self.spec.content_hash,
                # By identity: an op equal to a dropped one (a second
                # `add /metrics` of the same tree) is dropped as itself.
                [op for op in candidate.pending if not any(op is g for g in gone)],
            )
        put_back = {m for m in models if candidate.as_saved(m)}
        if put_back:
            candidate = SpecDocument(
                self.spec.path,
                self.spec.loaded,
                self.spec.content_hash,
                [op for op in candidate.pending if op.model_id not in put_back],
            )
        if gone or put_back:
            effective = candidate.effective()
        tests = self._merge(
            project.config,
            project.scanned,
            effective,
            project.locate,
            edited=bool(candidate.pending),
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
        self._update_waiting()
        if reread:
            self._keep_read(reread, fresh)
        self._emit_spec(models)
        self._score_all(models)

    def discard(self, models: set[str] | None = None) -> None:
        """Drop the pending ops of ``models`` — every pending op when
        ``None`` — and restore their loaded verdicts; the other models' ops
        stay, in order. ``models`` none of which has a pending op change
        nothing, and emit nothing."""
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
                for t in self._merge(
                    project.config,
                    project.scanned,
                    self.spec.loaded,
                    project.locate,
                    edited=False,
                )
            }
            for m in rest:
                test = tests.get(m)
                out[m] = None if test is None else self._compare(test)
        return out

    def save(self) -> None:
        """Apply exactly the pending ops through ``spec update``'s
        read-modify-write, then reload. Refuses when the file changed on
        disk since it was loaded, and when the edits leave a criterion that
        can never be scored (`UnscorableSaveError`: `stage` took it, for its
        editor to fill)."""
        project = self._require_project()
        if self.spec.on_disk_changed():
            raise StaleSpecError(self.spec.path)
        groups = self.spec.grouped()
        if groups:
            try:
                apply_validated_patches(
                    self.spec.path, groups, project.config, project.store
                )
            except UnscorableCriterion as e:
                raise UnscorableSaveError(e.reason) from e
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
            # A queued or running test's result is about to be replaced:
            # a baseline written from the one in memory would be the last
            # run's, stored under the next one's settings.
            for model_id in sorted(outcome.models):
                item = self._live.get(model_id)
                if item is not None:
                    raise QueueError(
                        f"{model_id} is {_doing(item)}: accept it when its run "
                        "has ended"
                    )
            # The check Save makes. A baseline is written with the test as
            # the Session holds it — its `baseline_points`, the comparison
            # settings stored beside the data — which is the spec as
            # LOADED: after an edit of the file by hand an Accept stored
            # the old entry's baseline (50 points where the file said 77)
            # and said nothing.
            if self.spec.on_disk_changed():
                raise StaleSpecError(self.spec.path, "accepting")
            message = outcome.run(self._require_project().store)
            self._reload_results()
            # The reload first, so a listener that reads verdicts on
            # the notice sees what the write did to them.
            if message:
                self.notices.append(message)
                self._emit("notice", message=message)
        elif isinstance(outcome, Enqueue):
            if self._has_loop:
                self._enqueue_later(outcome.models)
            else:
                self.enqueue(outcome.models)
        elif isinstance(outcome, Artifact):
            self.artifacts.append(outcome)
            self._emit("notice", artifact=outcome)
        elif isinstance(outcome, OpenProject):
            self.open(outcome.config_path, work_dir=outcome.work_dir)
        else:
            raise TypeError(f"not an Outcome: {outcome!r}")

    # -- the simulation queue ----------------------------------------------

    @property
    def workers(self) -> int:
        """How many tests the queue runs at once: what `set_workers` was
        given, else ``Config.parallel``."""
        if self._workers is not None:
            return self._workers
        project = self.project
        return max(1, int(project.config.parallel)) if project is not None else 1

    def set_workers(self, count: int) -> None:
        """Run ``count`` tests at once from now on, between 1 and the
        machine's cores. Workers already started stay: more start as items
        need them, and a surplus one leaves when its item ends."""
        self._workers = max(1, min(int(count), os.cpu_count() or 1))
        if self._pool is not None:
            self._pool.workers = self._workers
        self._emit("queue", state="workers", models=set())

    @property
    def starting_workers(self) -> tuple[StartingWorker, ...]:
        """The queue's workers that are not ready right now (`pool.starting`):
        being brought up, or reading the library again — a large library is
        minutes of load, and its tests wait meanwhile."""
        pool = self._pool
        return pool.starting if pool is not None else ()

    @property
    def checking(self) -> tuple[str, ...]:
        """The tests a Queue click named whose click is still being read
        (`_enqueue_later`) and that the queue does not hold yet, in project
        order — the order they will be queued in. Each is TAKEN, and every
        surface says so until the read is back: the Suite row reads QUEUED,
        the panel lists it (`queue_rows.CHECKING`) and the header counts
        it. For the tenth of a second to several the files are being looked
        at — a large library on a slow mount — nothing on the page moved,
        and a click that went nowhere is clicked again."""
        project = self.project
        if project is None or not self._reading:
            return ()
        return tuple(
            t.model_id
            for t in project.tests
            if t.model_id in self._reading and t.model_id not in self._live
        )

    def queue_item(self, model_id: str) -> QueueItem | None:
        """The model's item in the queue — the last one listed — or None."""
        return next((i for i in reversed(self.queue) if i.model_id == model_id), None)

    def item_state(self, item: QueueItem) -> ItemState:
        """``item``'s state as the Session has taken it in. The pool ends an
        item on its own thread; until the Session has read its result
        (`_item_ended`, a tick later) it is still in the queue here."""
        if self._live.get(item.model_id) is item and item.state not in ACTIVE:
            return (
                ItemState.RUNNING if item.started_at is not None else ItemState.WAITING
            )
        return item.state

    def queue_state(self, model_id: str) -> ItemState | None:
        """Where ``model_id`` stands in the queue (`item_state` of its
        item): WAITING or RUNNING while it is in it, how its last run there
        ended until `clear_finished`, else None."""
        item = self.queue_item(model_id)
        return self.item_state(item) if item is not None else None

    @property
    def cannot_cancel(self) -> str | None:
        """Why a running test cannot be stopped on the pool's backend, or
        None where it can (and before there is a pool)."""
        pool = self._pool
        if pool is None or pool.can_cancel:
            return None
        return (
            f"{pool.backend.label} cannot stop a running simulation: it runs "
            "to its end, or to its timeout"
        )

    def enqueue(self, model_ids: Sequence[str]) -> list[QueueItem]:
        """Append ``model_ids`` to the queue, in that order; returns their
        items. A test already queued or running is skipped (the pool's
        refusal: a test is never queued twice); when that leaves nothing to
        add, the refusals are raised as one :class:`QueueError`. The first
        call opens the pool — on a backend that cannot run one test at a
        time, or cannot be built here, a :class:`QueueError` with that
        backend's sentence, kept as `queue_refusal`.

        Each call is one click, and what it queues runs the library AS IT
        IS ON DISK NOW. The source files are swept once (stat only); a
        queued test whose own file changed is read from it again — its
        annotation's values, its UnitTests expressions — into the
        project's scan; and the click's items carry the swept state, so a
        live worker that read the library before a change reads it again
        before it runs one of them. A test whose file is gone, or no longer
        holds it, is refused with one sentence — a notice when others were
        queued — and the rest are queued.

        The read happens HERE, on the calling thread; a Session with a UI
        loop queues an `Enqueue` outcome through `_enqueue_later`."""
        project = self._require_project()
        self._known(project, model_ids)
        self._open_pool()
        read = _read_click(project.config, self._asks(project, model_ids))
        return self._queue_read(project, model_ids, read)

    def _enqueue_later(self, model_ids: Sequence[str]) -> None:
        """`enqueue` for a Session with a UI loop: this call returns at
        once, the click's read of the library — seconds over a large one on
        a slow mount — runs on the background thread, and the tests are
        queued on the loop when it is done. What `enqueue` raises after its
        read is a notice here; what it raises before (an unknown test, a
        backend that cannot be queued on) is raised here too. A test whose
        click is still being read is not read twice."""
        project = self._require_project()
        self._known(project, model_ids)
        models = [m for m in model_ids if m not in self._reading]
        if not model_ids:
            return
        if not models:
            raise QueueError(
                f"{model_ids[0]} is already being queued"
                if len(model_ids) == 1
                else f"all {len(model_ids)} tests are already being queued"
            )
        self._open_pool()
        asks = self._asks(project, models)
        epoch = self._queue_epoch
        self._reading.update(models)
        # Said at once: these tests were taken (`checking`).
        self._emit("queue", state="checking", models=set(models))

        def read() -> None:
            got: _ClickRead | Exception
            try:
                got = _read_click(project.config, asks)
            except Exception as e:  # said on the loop, never lost on a thread
                got = e
            self._post(lambda: self._click_read(project, models, epoch, got))

        self._background.submit("a Queue click's read of the library", read)

    def _click_read(
        self,
        project: Project,
        models: Sequence[str],
        epoch: int,
        got: _ClickRead | Exception,
    ) -> None:
        """On the UI loop: a click's read is back (`_enqueue_later`)."""
        if self.project is not project or epoch != self._queue_epoch:
            return  # the project was swapped, or the queue stopped, meanwhile
        self._reading.difference_update(models)
        added: list[QueueItem] = []
        try:
            if isinstance(got, Exception):
                raise got
            added = self._queue_read(project, models, got)
        except QueueError as e:
            self._say(str(e))
        except Exception as e:
            logger.exception("queueing %s failed", ", ".join(models))
            self._say(f"Queue failed: {type(e).__name__}: {e}")
        finally:
            # A test the read refused was shown as taken (`checking`): its
            # row goes, and the sentence above says why.
            dropped = set(models) - {item.model_id for item in added}
            if dropped:
                self._emit("queue", state="refused", models=dropped)

    def _say(self, message: str) -> None:
        self.notices.append(message)
        self._emit("notice", message=message)

    def _known(self, project: Project, model_ids: Sequence[str]) -> None:
        for model_id in model_ids:
            if project.test(model_id) is None:
                raise KeyError(model_id)

    def _asks(self, project: Project, model_ids: Sequence[str]) -> list[_Ask]:
        """What a click's read needs of each test it names: the file the
        test came from and the stamp it was last read at."""
        asks = []
        for model_id in dict.fromkeys(model_ids):
            scanned = project.scanned.get(model_id)
            if scanned is not None:
                path: Path | None = scanned.source_file
            else:
                # Asked at every merge since the open: answered from memory.
                located = project.locate(model_id)
                path = located.mo_file if located is not None else None
            asks.append(
                _Ask(model_id, path, self._read_at.get(model_id), scanned is not None)
            )
        return asks

    def _queue_read(
        self, project: Project, model_ids: Sequence[str], read: _ClickRead
    ) -> list[QueueItem]:
        """Queue ``model_ids`` against what their click ``read``."""
        library: LibraryState | None = None
        if read.stamps is not None:
            # The last click's own object when no file differs from it: a
            # worker then compares two serials and reads nothing.
            library = self._library = following(self._library, read.stamps)
        self._take_read(project, read)
        pool = self._open_pool()
        added: list[QueueItem] = []
        said = list(read.refused.values())
        self._refusals.update(read.refused)
        for model_id in model_ids:
            test = project.test(model_id)
            if model_id in read.refused or test is None:
                continue
            held = self._live.get(model_id)
            if held is not None:
                # Already queued: while it waits, it runs what THIS click
                # read, on a worker that has read the library since.
                pool.update(held, test, library)
            try:
                item = pool.submit(test, library)
            except Refused as e:
                said.append(str(e))
                continue
            self._live[model_id] = item
            self.queue.append(item)
            added.append(item)
        # A test that is queued again was read: what an earlier click said
        # of its file — gone, unreadable, no longer holding it — is over,
        # and stood in the panel after the file was back and the test had
        # passed.
        self._forget_refusals([item.model_id for item in added])
        if not added:
            if said:
                raise QueueError("; ".join(said))
            return []
        for sentence in read.refused.values():
            self._say(sentence)
        self._emit("queue", state="queued", models={i.model_id for i in added})
        return added

    def _forget_refusals(self, model_ids: Sequence[str]) -> None:
        """Take the notes the read's refusals of ``model_ids`` left out of
        the notices — each its own notice, or one part of a notice that
        says several (a click all of whose tests were refused)."""
        gone = {s for m in model_ids if (s := self._refusals.pop(m, None)) is not None}
        if not gone:
            return
        kept = []
        for notice in self.notices:
            parts = [part for part in notice.split("; ") if part not in gone]
            if parts:
                kept.append("; ".join(parts))
        if kept != self.notices:
            self.notices = kept
            self._emit("notice")

    def _store_scan_later(self, config: Config, scan: dict[str, TestModel]) -> None:
        """The scan cache follows the project's scan, off the UI loop."""

        def store() -> None:
            store_scan(config, scan)

        self._background.submit("the scan cache", store)

    def _take_read(self, project: Project, read: _ClickRead) -> None:
        """What a click read of its tests' files, into the project's scan
        (and the scan cache) — as the rescan's is, and NOT into the queue's
        items alone: every later re-merge (a spec edit, `_update_waiting`)
        then hands a waiting item the test as the click read it, where it
        would otherwise hand back the one read at open."""
        self._read_at.update(read.read_at)
        scanned = {
            m: test
            for m, test in read.scanned.items()
            if project.scanned.get(m) != test
        }
        located = {
            m: found for m, found in read.located.items() if project.locate(m) != found
        }
        if not scanned and not located:
            return
        scan = {**project.scanned, **scanned}
        if scanned:
            self._store_scan_later(project.config, scan)
        # The `spec` event of the re-merge is what tells a page a test's
        # definition changed: its Suite row and its Test page are redrawn.
        self._rebuild_tests(
            (set(scanned) | set(located)) - self._running_models(),
            scanned=scan,
            locate=project.locate.having(located),
        )

    def remove(self, item: QueueItem) -> bool:
        """Take a waiting ``item`` out of the queue, its row with it.
        ``False`` once it has started: nothing is changed."""
        pool = self._pool
        if pool is None or not pool.remove(item):
            return False
        self._forget(item)
        self._emit("queue", state="removed", models={item.model_id})
        self._drained()
        return True

    def cancel(self, item: QueueItem) -> bool:
        """Stop a running ``item``'s simulation; it ends CANCELLED once its
        thread says so — at once on most backends, after its worker's grace
        where the simulation does not look at the request. Said meanwhile:
        a ``queue`` event, state ``cancelling``, so its row and its strip
        read that until the end. ``False`` when it is not running; the
        pool's ``Refused``, a sentence, on a backend that cannot stop a
        test."""
        pool = self._pool
        if pool is None or not pool.cancel(item):
            return False
        self._emit("queue", state="cancelling", models={item.model_id})
        return True

    def clear_finished(self) -> None:
        """Drop the rows of the items that ended — finished, failed or
        cancelled — and the stopped pool's message. Their results stay."""
        gone = {i.model_id for i in self.queue if self._live.get(i.model_id) is not i}
        self.queue = [i for i in self.queue if self._live.get(i.model_id) is i]
        self.queue_stopped = None
        self._emit("queue", state="cleared", models=gone)

    def stop_workers(self) -> None:
        """Close the pool: its workers end and their licence seats are
        free; the next `enqueue` starts new ones. Tests still waiting leave
        the queue. Refused while a test runs."""
        count = len(self._running_models())
        if count:
            raise QueueError(
                f"{plural(count, 'test')} {'is' if count == 1 else 'are'} running — "
                f"cancel {'it' if count == 1 else 'them'} or wait, then stop the "
                "workers"
            )
        self.stop_queue()

    def stop_queue(self) -> None:
        """End the queue's pool, whatever it holds — what the tool does
        when it stops: waiting tests are taken out, running ones stopped
        (CANCELLED, where the backend can; else it finishes them first),
        every worker closed and every pool thread joined. The rows of what
        ended stay until `clear_finished`."""
        # A click whose read is still running is not queued after this.
        self._queue_epoch += 1
        gone = set(self.checking)
        self._reading.clear()
        pool, self._pool = self._pool, None
        if pool is None:
            return
        pool.close()
        gone |= set(self._live)
        for item in list(self._live.values()):
            if item.state is ItemState.REMOVED:
                self._forget(item)
            else:
                # Stopped by the close, or ended while it closed: taken in
                # here. Its callback is still on its way, and finds nothing.
                self._item_ended(item)
        self._emit("queue", state="stopped", models=gone)
        self._drained()

    def _reset_queue(self) -> None:
        """The queue of a project that is closing: its pool ended, its rows
        and everything said about it gone."""
        self.stop_queue()
        self.live_runs.clear()
        self._library, self._read_at = None, {}
        self._refusals = {}
        self.queue, self._unwritten = [], []
        self._workers = self.queue_refusal = self.queue_stopped = None
        self._write_status = None

    def _open_pool(self) -> SimulationPool:
        from ..pipeline import open_pool

        project = self._require_project()
        if self.queue_refusal is not None:
            raise QueueError(self.queue_refusal)
        if self._pool is not None and self._pool.stopped is not None:
            # A stopped pool takes nothing more: a new one is the retry.
            self.stop_queue()
        if self._pool is None:
            # The tool reads a run's result WHILE it is in progress (D151): a
            # backend that would otherwise write it late writes it promptly
            # for this pool. Set here and nowhere else — no CLI run is.
            project.config.live_results = True
            try:
                # The pool's OWN backend, the persistent variant a CLI run
                # would choose — the instance `open` holds is the batch
                # class it reads results through — and silenced: the tool's
                # process never narrates a run to the console it was
                # started from.
                backend = select_backend(project.config, persistent=True, to_run=True)
                backend.quiet = True
                self._pool = open_pool(
                    project.config,
                    backend=backend,
                    store=project.store,
                    # On the POOL's threads: nothing of the Session is
                    # touched there. Each is handed to the UI loop.
                    on_start=lambda item: self._post(lambda: self._item_started(item)),
                    on_end=lambda item: self._post(lambda: self._item_ended(item)),
                    workers=self.workers,
                )
            except RuntimeError as e:
                # A backend that runs whole batches only, or cannot be
                # built here (`BackendUnavailableError` is a RuntimeError):
                # the Queue action carries the sentence from now on.
                self.queue_refusal = str(e)
                raise QueueError(self.queue_refusal) from e
            self._write_status = cast("ItemRun", self._pool.path).write_status
        return self._pool

    def _forget(self, item: QueueItem) -> None:
        if self._live.get(item.model_id) is item:
            del self._live[item.model_id]
        self.queue.remove(item)

    def _update_waiting(self) -> None:
        """The waiting items follow the spec: a queued test runs with its
        test as it stands when it STARTS (D150), so every re-merge hands
        the pool the new one. A running item's is its run's."""
        pool, project = self._pool, self.project
        if pool is None or project is None:
            return
        for item in self._live.values():
            test = project.test(item.model_id)
            if test is not None and test != item.test:
                pool.update(item, test)

    def _item_started(self, item: QueueItem) -> None:
        """On the UI loop, once the pool's thread said ``item`` started. Its
        run cleared what the last one left on disk, so the result in memory
        is no file's any more: it goes, and the test reads NOT RUN until
        this run's arrives."""
        model_id, project = item.model_id, self.project
        if self._live.get(model_id) is not item or project is None:
            return
        # A worker took an item: whatever stopped the pool before is over.
        self.queue_stopped = None
        # Before the result goes: the page stays laid out by it (D151).
        self.live_runs.started(item)
        project.results.pop(model_id, None)
        self._reads.pop(model_id, None)
        self._score_one(model_id)
        self._emit("queue", state="started", models={model_id})

    def _item_ended(self, item: QueueItem) -> None:
        """On the UI loop, once the pool's thread said ``item`` ended: THAT
        test's result is taken from the item and it alone is rescored — with
        the criteria as they are now, pending edits included. Nothing else
        is read: a reload per item read TRANSFORM's 331 results for each."""
        model_id = item.model_id
        if self._live.get(model_id) is not item:
            return
        del self._live[model_id]
        if item.run_result is not None:
            self._unwritten.append(item)
        pool = self._pool
        if pool is not None and pool.stopped is not None:
            self.queue_stopped = pool.stopped
        # An item that never started is one the pool failed (no worker
        # could start): nothing ran, and the test's last result stands.
        # The queue says it ended, whatever its file reads as: the live
        # view goes, and what an unfinished run wrote is kept to be shown.
        self.live_runs.ended(item)
        if item.started_at is not None and self.project is not None:
            self._take_result(item)
        self._emit("queue", state="ended", models={model_id})
        self._drained()

    def _take_result(self, item: QueueItem) -> None:
        project = self._require_project()
        model_id = item.model_id
        self._reads.pop(model_id, None)
        if item.result is None:
            # Cancelled, or its run raised: what it left on disk is no result.
            project.results.pop(model_id, None)
        else:
            project.results[model_id] = item.result
            # The result was read for the test as it started. A criterion
            # added while it ran may name a variable the file holds and
            # that read did not extract: read again, this test only. A
            # read that fails costs that variable, never the run: the
            # result that WAS read is scored, and the end is said.
            try:
                reread = self._unread_in_file({model_id}, project.tests)
                if reread:
                    self._keep_read(reread, self._read_again(reread, project.tests))
            except Exception as e:
                logger.error(
                    "reading %s again failed (%s: %s): its result stands as its run read it",
                    model_id,
                    type(e).__name__,
                    e,
                )
        self._score_one(model_id)

    def _score_one(self, model_id: str) -> None:
        """Rescore ``model_id`` and say so; a scoring that raises is logged
        and leaves the model without a verdict, as a reload does."""
        project = self._require_project()
        try:
            if project.test(model_id) is not None:
                self._score(model_id)
        except Exception:
            logger.exception("scoring %s failed", model_id)
            self.comparisons.pop(model_id, None)
        self._emit("results", models={model_id})

    def _drained(self) -> None:
        """``status.json`` and the dashboard, once nothing is queued or
        running, for the items that ran since they were last written — not
        per item: both are one run's snapshot, and the queue's live view is
        the tool's own pages. Off the UI loop (the dashboard's render is
        1.6 s over TRANSFORM's work dir with --report sidecars), on a
        snapshot of the verdicts as they stand."""
        if self._live:
            return
        items, self._unwritten = self._unwritten, []
        write, project = self._write_status, self.project
        if not items or write is None or project is None:
            return
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        comparisons = list(self.comparisons.values())
        results = dict(project.results)
        theme = project.config.theme

        def run() -> None:
            from ..backends.progress import stamp_verdicts
            from ..reporting.dashboard_render import render_final
            from ..reporting.verdicts import status_word

            write(items)
            # Each row's verdict beside its simulation status, as a `run`
            # leaves it: "passed" there says only that the simulation ran.
            stamp_verdicts(work_dir, {c.model_id: status_word(c) for c in comparisons})
            # The page lists every test the Session scored, with its
            # verdict — not the queue's items alone.
            render_final(
                work_dir, comparisons=comparisons, results=results, theme=theme
            )

        self._background.submit("the queue's status.json and dashboard", run)

    # -- internals ---------------------------------------------------------

    def _require_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("no project is open")
        return self.project

    def _merge(
        self,
        config: Config,
        scanned: dict[str, TestModel],
        document: dict,
        locate: SpecModelLocator,
        *,
        edited: bool,
    ) -> list[TestModel]:
        """The project's tests from the scan and the spec ``document``.
        ``edited`` says whether that document holds pending edits: an error
        in one that does not is the FILE's, and names the file alone — it
        said "(in memory)" of a spec just read from disk."""
        label = str(config.spec_path)
        models = spec_models_from_document(
            document,
            base_dir=config.spec_path.parent,
            label=f"{label} (with unsaved edits)" if edited else label,
        )
        return merge_tests(scanned, spec_tests_by_model(models), locate=locate)

    def _rebuild_tests(
        self,
        models: set[str],
        *,
        scanned: dict[str, TestModel] | None = None,
        locate: SpecModelLocator | None = None,
    ) -> None:
        """Re-merge the scan against the effective spec; models the spec no
        longer names drop out of ``comparisons``, models it newly names are
        scored (with whatever result the last reload holds for them).

        ``scanned`` and ``locate`` are the library READ AGAIN — the
        rescan's scan and a new locator, or a Queue click's read of the
        tests it queues: they are merged in place of the project's, and
        the ``models`` are re-read from the last run with their merged
        TestModels. The read comes BEFORE anything is replaced — the scan,
        the tests, the comparisons — so a read that raises (the disk went
        away) leaves the Session as it was."""
        project = self._require_project()
        again = scanned is not None or locate is not None
        scan = project.scanned if scanned is None else scanned
        locate = project.locate if locate is None else locate
        tests = self._merge(
            project.config,
            scan,
            self.spec.effective(),
            locate,
            edited=bool(self.spec.pending),
        )
        current = {t.model_id for t in tests}
        reread = models & current if again else set()
        fresh = self._read_again(reread, tests) if reread else {}
        project.scanned, project.locate, project.tests = scan, locate, tests
        self._update_waiting()
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
        self._emit_spec(models | gone | new)
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
        for ``models``. A model it has no result for (no manifest entry, or
        one its last run never finished) ends with none and is scored NOT
        RUN by the caller (D145), as after a full reload. The result FILE did not change, so its listing
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
        """Re-read the last run and rescore — every model except the ones
        the queue is running (their directories are being rewritten, so
        their in-memory result and verdict stand until their run ends).
        Each result is scored as it is read and
        announced as ``loading(phase="results")``; tests with no result are
        scored and announced after the read, so the last announcement is
        ``done == total``; ONE ``results`` event follows for the batch. A
        model whose scoring raises does not abort the read: the results stay
        complete, its stale verdict is dropped, and the first failure is
        raised after the batch event; ``open`` propagates it after its
        ``done`` event. Each announcement carries ``error``:
        True for a model whose scoring raised (its verdict is absent),
        False otherwise."""
        project = self._require_project()
        locked = self._running_models()
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
                # `read_results` reports every manifest entry, and a running
                # model — excluded from `to_read` — comes back as "not found
                # in discovery": storing it would rescore the model to
                # SIM_FAIL in the middle of its own run.
                return
            project.results[model_id] = result
            if project.test(model_id) is not None:
                score_and_announce(model_id)

        project.backend.read_last_results(to_read, on_result=on_result)
        # Tests with no result at all (never run, never finished, or
        # dropped from the manifest) are scored now — NOT RUN (D145).
        for test in to_read:
            if test.model_id not in project.results:
                score_and_announce(test.model_id)
        self._emit("results", models={t.model_id for t in to_read})
        if failures:
            model_id, error = next(iter(failures.items()))
            raise RuntimeError(
                f"scoring {model_id} failed ({len(failures)} of {len(to_read)} models)"
            ) from error

    def _its_tree_written_out(
        self, candidate: SpecDocument, model_id: str
    ) -> list[PatchOp]:
        """The model's ops in ``candidate`` that do nothing but write out,
        as ``metrics``, the tree its SAVED entry already scores by — the ops
        `stage` drops; ``[]`` when there are none to drop. They are every op
        of the model on ``/metrics``, when all of this holds:

        - the saved entry has no tree of its own and the entry as edited
          has one, equal to the tree the saved entry scores by;
        - the entry would score by that same tree WITHOUT its ``metrics``:
          a flat entry's tree is built from its ``variable_overrides``
          (`_saved_scoring_tree`), so those are as saved. With an override
          edited, the entry the other ops leave scores by another tree, and
          taking this one away would change its verdict.

        What is kept is then the entry as edited but for its ``metrics``,
        and nothing checks it: an op the applier admits is an add, a replace
        or a remove whose path is at or under exactly one of
        ``/comparison``, ``/metrics`` and ``/plots``
        (``patch_apply.DEFAULT_WHITELIST``), so the ops under ``/metrics``
        neither read nor write another member, nor the others ``metrics``.

        The other members need not be as saved: a watch plot added beside a
        tolerance typed and cleared is one pending edit, the plot's. It was
        the whole entry or nothing, and the tree stayed pending beside any
        other edit — counted, previewed as "Save also writes the tree", and
        saved as a ``metrics`` block where only ``plots`` had changed.

        "The tree the saved entry scores by" is `_materialization`'s and no
        other's, so what is dropped here and what `stage` writes out cannot
        drift: the value of an op it built and marked (`PatchOp.materializes`
        — marked only while it IS the saved entry's tree,
        `_saved_scoring_tree`) where the pending list still holds one, and
        otherwise `_materialization` asked for the saved entry. The marked
        op is read first because it is free: every keystroke in a flat
        test's editor comes through here with one, and the other way scores
        the test as saved (`saved_comparison`: a merge and a compare). The
        other way is for a tree no op of the Session's wrote — a move or a
        wrap stages the whole tree (`edit_tree`), and moved back it is the
        implicit one again, unmarked."""
        entry = candidate.entry(model_id)
        if entry is None or not has_metrics(entry):
            return []
        if not self._saved_scoring_tree(candidate, model_id):
            return []
        mine = [op for op in candidate.pending if op.model_id == model_id]
        marked = [op.value for op in mine if op.materializes]
        if marked:
            tree = marked[0]
        else:
            op, _why = self._materialization(model_id, saved=True)
            if op is None:
                return []
            tree = op["value"]
        if not json_equal(entry["metrics"], tree):
            return []
        return [
            op
            for op in mine
            if op.path == "/metrics" or op.path.startswith("/metrics/")
        ]

    def _saved_scoring_tree(self, spec: SpecDocument, model_id: str) -> bool:
        """Whether the tree `_materialization` builds for the model's entry
        in ``spec`` — the Session's document, or the candidate `stage` is
        deciding on — is the one the SAVED entry scores: what marks it
        (`PatchOp.materializes`), and what lets it go again
        (`_its_tree_written_out`). Never when the saved entry has its own
        ``metrics``: it scores that tree, and one built after a pending op
        removed it is a change. Otherwise the tree is built from
        ``comparison.variable_overrides`` (the tracked variables are not
        stageable), so it is when those are as saved."""

        def overrides(entry: dict) -> dict:
            return (entry.get("comparison") or {}).get("variable_overrides") or {}

        saved = spec.saved_entry(model_id) or {"model": model_id}
        if has_metrics(saved):
            return False
        return overrides(saved) == overrides(spec.entry(model_id) or {})

    def _materialization(
        self, model_id: str, *, saved: bool = False
    ) -> tuple[dict | None, str | None]:
        """``(op, None)``: the ``add /metrics`` op for a test the flat path
        scored, built from the variables it scored — the leaves the report
        shows, in the report's order, so leaf ``i`` is
        ``/metrics/children/i`` on both tiers. ``saved``: for the entry as
        the FILE holds it and the comparison of that (`saved_comparison`),
        whatever is pending — the tree `_its_tree_written_out` drops.
        For a test tracked by its
        ``.mo`` annotation those names are in no spec entry (and the entry
        may not exist yet: the op creates it). ``(None, None)`` when the
        entry already has a tree (`has_metrics`: a null ``metrics`` is
        none). Otherwise ``(None, why)`` — and ``rescore`` stages nothing:
        :data:`SIMULATE_ONLY` or :data:`NOTHING_SCORED` when nothing was
        scored (NO_REF, SIM_FAIL, NOT RUN, simulate-only; and a model not in the
        project, which `stage` refuses with it as a patch — `tree_or_reason`
        raises a KeyError for one before asking); :data:`STALE` when a scored variable's name is not one the
        loaded result carries (a comparison from a run the results no
        longer match); :data:`NO_TREE` when a tree would not score what the
        flat path scored: an unnamed or non-literal scored name, a string
        ``quantity`` in an override (the flat path refuses it, a tree would
        score it), an override naming an unregistered mode (the flat path
        fails it; a tree would score nrmse), or a reference-JSON override
        the spec does not shadow (the flat path merges it; a tree ignores
        it). Each case has its negative test in tests/test_session.py."""
        project = self._require_project()
        bare: dict = {"model": model_id}
        if saved:
            entry = self.spec.saved_entry(model_id) or bare
        else:
            entry = self.spec.entry(model_id) or bare
        if has_metrics(entry):
            return None, None
        comp = (
            self.saved_comparison(model_id) if saved else self.comparisons.get(model_id)
        )
        if comp is None or not comp.variables:
            test = project.test(model_id)
            simulate_only = test is not None and test.simulate_only
            return None, SIMULATE_ONLY if simulate_only else NOTHING_SCORED
        # A pattern-tracked spec materializes from the names the pattern
        # resolved today (owner ruling, D126): the pattern keeps tracking,
        # the tree scores what it names, and a variable that later matches
        # without a leaf raises the "tracked but not scored" warning.
        names = [vc.name for vc in comp.variables]
        if not all(n and is_literal_variable(n) for n in names):
            return None, NO_TREE
        result = project.results.get(model_id)
        resolved = {v.name for v in result.variables} if result is not None else set()
        if not all(n in resolved for n in names):
            # A leaf must name a variable the loaded result carries, or the
            # tree path resolves it to nothing.
            return None, STALE
        if unknown_override_modes(entry):
            return None, NO_TREE
        spec_overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
        if any(
            isinstance(ov, dict) and isinstance(ov.get("quantity"), str)
            for ov in spec_overrides.values()
        ):
            # The flat path refuses a stats-path quantity ("needs a metrics
            # tree"); the tree path would score it — a FAIL that becomes a
            # PASS on a look. Authoring the tree is the user's act, not ours.
            return None, NO_TREE
        ref = project.store.get_reference(model_id)
        ref_overrides = (
            ref.comparison.get("variable_overrides") or {} if ref is not None else {}
        )
        if any(n in ref_overrides and n not in spec_overrides for n in names):
            return None, NO_TREE
        return implicit_tree_op(entry, variables=names), None

    def _refuse_if_busy(self) -> None:
        if self._live:
            raise QueueError(
                f"{_queued_or_running(len(self._live))}; remove or cancel them, or wait"
            )
        if self.spec.pending:
            raise PendingEditsError(edit_count(self.spec.pending))

    def _emit_spec(self, models: set[str]) -> None:
        """The `spec` event for ``models``, numbered one up from the last
        (`spec_revision`)."""
        self.spec_revision += 1
        self._emit("spec", models=models, revision=self.spec_revision)

    def _emit(self, event: Event, **payload: Any) -> None:
        """Every listener hears every event: one that raises (a deleted UI
        element, say) is logged and skipped, never allowed to stop the
        others or to unwind what emitted it."""
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

    def baseline_names(self, model_id: str) -> tuple[str, ...]:
        """What a leaf's ``against`` may name: ``primary`` when a baseline
        is stored, then every soft_check, in store order."""
        names: list[str] = []
        ref_file = self._store.reference_file(model_id)
        if ref_file is not None and ref_file.exists():
            names.append(PRIMARY_BASELINE)
        names.extend(self._store.get_soft_checks(model_id))
        return tuple(names)

    def role_lookup(self, model_id: str) -> BaselineRoleLookup:
        """Each baseline name's role for ``model_id`` — what the D66
        validator checks a tree against, as `Session.stage` does: a move
        checks its new tree with it before it is staged."""
        from ..comparison.validator import role_lookup_from_store

        return role_lookup_from_store(self._store, model_id)


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
    def comparisons(self) -> Mapping[str, TestComparison]:
        return MappingProxyType(self._session.comparisons)

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(self._session.artifacts)

    @property
    def notices(self) -> tuple[str, ...]:
        """The lines the queue panel shows, oldest first: what each store write
        applied so far reported, plus the open-time warning if there was one."""
        return tuple(self._session.notices)

    def queue_state(self, model_id: str) -> ItemState | None:
        """Where the test stands in the simulation queue
        (:meth:`Session.queue_state`): WAITING or RUNNING while it is in
        it, how its last run there ended until that row is cleared, else
        ``None``."""
        return self._session.queue_state(model_id)

    @property
    def queue_refusal(self) -> str | None:
        """Why nothing can be queued on this project's backend, or ``None``."""
        return self._session.queue_refusal

    def partial(self, model_id: str) -> Partial | None:
        """What the test's last run left when it did not finish
        (:meth:`Session.partial`), or ``None``."""
        return self._session.partial(model_id)

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
        theme: str = "default",
    ) -> dict:
        return self._session.figure(
            model_id, variable, editing=editing, visibility=visibility, theme=theme
        )

    def variable_names(self, model_id: str) -> list[str]:
        return self._session.variable_names(model_id)

    def variable_texts(self, model_id: str) -> dict[str, VariableText]:
        """Both readings of every variable of the test's page
        (:meth:`Session.variable_texts`): a control that shows the muted
        name beside a variable reads the ``label``, as the card title does."""
        return self._session.variable_texts(model_id)

    def labels(self, model_id: str) -> dict[str, str]:
        """``{name: what a capability's own text reads for it}`` —
        :meth:`Session.labels`, so a panel's caption and the page's rows
        say the same thing. It is the TEXT, not the plain label: a
        capability's caption carries no muted name to disambiguate it."""
        return self._session.labels(model_id)

    def plots(self, model_id: str) -> tuple[ResolvedPlot, ...]:
        return self._session.plots(model_id)

    def diagnostic_names(self, model_id: str) -> list[str]:
        return self._session.diagnostic_names(model_id)

    def baseline_summaries(self, model_id: str) -> dict[str, DiagnosticSummary]:
        return self._session.baseline_summaries(model_id)

    def card_paths(self, model_id: str) -> set[str]:
        return self._session.card_paths(model_id)

    def plot_figure(
        self,
        model_id: str,
        index: int,
        *,
        editing: Collection[str] | None = None,
        visibility: Visibility | None = None,
        theme: str = "default",
    ) -> dict:
        return self._session.plot_figure(
            model_id, index, editing=editing, visibility=visibility, theme=theme
        )

    def tree_view(self, model_id: str) -> dict | None:
        return self._session.tree_view(model_id)

    def tree(self, model_id: str) -> dict | None:
        return self._session.tree(model_id)

    def tree_or_reason(self, model_id: str) -> tuple[dict | None, str | None]:
        return self._session.tree_or_reason(model_id)


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


def _in_simulation(path: str) -> bool:
    """Whether a patch path names a test's simulation settings."""
    return path == "/simulation" or path.startswith("/simulation/")


def _doing(item: QueueItem) -> str:
    return "running" if item.state is ItemState.RUNNING else "queued"


def _queued_or_running(count: int) -> str:
    return f"{plural(count, 'test')} {'is' if count == 1 else 'are'} queued or running"


def _in_order(ids: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in ids:
        seen.setdefault(i, None)
    return list(seen)
