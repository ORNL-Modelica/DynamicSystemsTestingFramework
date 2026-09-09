"""The Session — the authoring tool with no UI (two-tier design, §4.5).

Opens a project, scores its tests from the last run on disk, holds the
spec with the ops staged against it, and applies the outcomes capabilities
return. It writes only through ``save`` (the spec) and through the outcomes
it applies (the store, a job's work dir). Nothing here imports a UI.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from ..backends import dataset_kind_for, get_backend
from ..backends.base import Backend
from ..backends.progress import recorded_backend
from ..backends.types import DatasetType, TestResult
from ..comparison.comparator import DEFAULT_TOLERANCE, compare_one
from ..comparison.types import TestComparison
from ..config import Config, ConfigError
from ..discovery.spec_parser import spec_models_from_document
from ..discovery.spec_update import (
    apply_validated_patches,
    implicit_tree_op,
    is_literal_variable,
    materialize_implicit_metrics,
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
from .spec_document import PatchOp, SpecDocument

Event = Literal["spec", "results", "job", "selection"]
Listener = Callable[[dict[str, Any]], None]

logger = logging.getLogger(__name__)


@dataclass
class Project:
    config: Config
    store: ReferenceStore
    backend: Backend
    scanned: dict[str, TestModel]  # the source scan, merged against every spec edit
    tests: list[TestModel]
    results: dict[str, TestResult]
    dataset_kind: DatasetType

    def test(self, model_id: str) -> TestModel | None:
        return next((t for t in self.tests if t.model_id == model_id), None)


@dataclass(frozen=True)
class Selection:
    tests: frozenset[str] = frozenset()
    variable: str | None = None
    leaf_path: str | None = None


@dataclass
class RunningJob:
    label: str
    models: frozenset[str]
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


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


class Session:
    def __init__(self, post: Callable[[Callable[[], None]], None] | None = None):
        """``post`` hands a job's completion to the UI loop; without one the
        completion runs on the worker thread (tests wait on ``RunningJob.done``)."""
        self.project: Project | None = None
        self.spec: SpecDocument = SpecDocument(
            Path("test_spec.json"), {"tests": []}, None
        )
        self.selection = Selection()
        self.running: RunningJob | None = None
        self.comparisons: dict[str, TestComparison] = {}
        self.artifacts: list[Artifact] = []
        self.warnings: list[str] = []
        self._post = post or (lambda fn: fn())
        self._listeners: dict[str, list[Listener]] = {}

    # -- project -----------------------------------------------------------

    def open(
        self,
        config_path: Path,
        *,
        backend: str | None = None,
        work_dir: Path | None = None,
    ) -> None:
        """Open the project ``config_path`` describes. Without an explicit
        ``backend`` the one the last run recorded wins, as ``compare`` does."""
        if self.running is not None:
            raise JobRunningError(f"{self.running.label} is still running")
        config_path = Path(config_path)
        config = Config(
            config_file=config_path, backend_name=backend, work_dir=work_dir
        )
        if backend is None:
            assert config.work_dir is not None  # resolved in __post_init__
            recorded, warning = recorded_backend(config.work_dir)
            if warning:
                self.warnings.append(warning)
            if recorded and recorded != config.backend_name:
                config = Config(
                    config_file=config_path, backend_name=recorded, work_dir=work_dir
                )
        try:
            backend_obj = get_backend(config)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        store = ReferenceStore(config)
        spec = SpecDocument.load(config.spec_path)
        scanned = scan_source_tests(config)
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
        self.selection = Selection()
        self.comparisons = {}
        self._emit("spec", models={t.model_id for t in tests})
        self._reload_results()

    def close(self) -> None:
        if self.running is not None:
            raise JobRunningError(f"{self.running.label} is still running")
        self.project = None
        self.spec = SpecDocument(Path("test_spec.json"), {"tests": []}, None)
        self.comparisons = {}
        self.selection = Selection()
        self._emit("spec", models=set())

    def reload(self) -> None:
        """Re-read the spec from disk, dropping pending ops (what a refused
        save offers)."""
        project = self._require_project()
        self.spec = SpecDocument.load(project.config.spec_path)
        self._rebuild_tests(set(self.comparisons))

    def select(
        self,
        *,
        tests: Iterable[str] | None = None,
        variable: str | None = None,
        leaf_path: str | None = None,
    ) -> None:
        self.selection = Selection(
            tests=frozenset(tests) if tests is not None else self.selection.tests,
            variable=variable,
            leaf_path=leaf_path,
        )
        self._emit("selection")

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
        entry = self.spec.entry(model_id)
        if (
            entry is not None
            and "metrics" not in entry
            and self._materializable(model_id, entry)
        ):
            op = implicit_tree_op(entry)
            self.stage([PatchOp(model_id, op["op"], op["path"], op["value"])])
            return self.comparisons[model_id]
        comp = self._score(model_id)
        self._emit("results", models={model_id})
        return comp

    def view(self, model_id: str, variable: str) -> VariableView:
        """The variable's view: its trajectories, overlays and every leaf
        scoring it. A test with no result, a failed simulation or no baseline
        gets the empty state — trajectories present or not, no leaves."""
        from ..reporting.context_builder import build_trajectories
        from ..reporting.overlay_loader import (
            attach_overlays_to_trajectories,
            load_overlays,
        )

        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)
        comp = self.comparisons.get(model_id)
        result = project.results.get(model_id)
        ref = project.store.get_reference(model_id)
        scored = list(comp.variables) if comp is not None and comp.evaluated else []
        trajectories, _diag, nobaseline = build_trajectories(scored, result, ref)
        overlays = load_overlays(project.store, model_id, config=project.config)
        attach_overlays_to_trajectories(trajectories, overlays)
        attach_overlays_to_trajectories(nobaseline, overlays)
        traj = next((t for t in trajectories if t["name"] == variable), None)
        if traj is None:
            nb = next((t for t in nobaseline if t["name"] == variable), None)
            traj = (
                {
                    "act_time": nb["time"],
                    "act_values": nb["values"],
                    "overlays": nb["overlays"],
                }
                if nb is not None
                else {}
            )
        leaves = leaves_by_variable(comp.metric_tree if comp is not None else None).get(
            variable, []
        )
        return view_from_trajectory(variable, traj, leaves)

    def figure(self, model_id: str, variable: str) -> dict:
        from ..figures.plotly import variable_figure

        project = self._require_project()
        return variable_figure(
            self.view(model_id, variable), project.config.max_embedded_samples
        )

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
            full = materialize_implicit_metrics(self.spec.entry(model_id), raw)
            staged += [
                PatchOp(model_id, r["op"], r["path"], r.get("value")) for r in full
            ]
        candidate = SpecDocument(
            self.spec.path,
            self.spec.loaded,
            self.spec.content_hash,
            [*self.spec.pending, *staged],
        )
        effective = candidate.effective()  # PatchError on a bad path
        for model_id in models:
            validate_entry_metric_tree(
                effective, model_id, project.config, project.store
            )  # PatchError
        tests = self._merge(
            project.config, project.scanned, effective
        )  # ConfigError on a bad tree
        self.spec = candidate
        project.tests = tests
        self._emit("spec", models=models)
        self._score_all(models)

    def discard(self) -> None:
        """Drop every pending op and restore the loaded verdicts."""
        models = {op.model_id for op in self.spec.pending}
        self.spec = SpecDocument(
            self.spec.path, self.spec.loaded, self.spec.content_hash
        )
        self._rebuild_tests(models)

    def save(self) -> None:
        """Apply exactly the pending ops through ``spec-update``'s
        read-modify-write, then reload. Refuses when the file changed on
        disk since it was loaded."""
        project = self._require_project()
        if self.spec.on_disk_changed():
            raise StaleSpecError(self.spec.path)
        groups = self.spec.grouped()
        if groups:
            apply_validated_patches(self.spec.path, groups, project.config)
        models = {model_id for model_id, _ in groups}
        self.spec = SpecDocument.load(self.spec.path)
        self._rebuild_tests(models)

    def export(self) -> dict:
        from ..discovery.patch_envelope import ModelPatch, build_envelope

        return build_envelope([ModelPatch(m, ops) for m, ops in self.spec.grouped()])

    # -- outcomes ----------------------------------------------------------

    def apply(
        self, outcome: Outcome | Sequence[Outcome], *, work_dir: Path | None = None
    ) -> None:
        """Apply what a capability returned. ``None`` is nothing; a sequence
        is applied in order. ``work_dir`` reaches only ``OpenProject``."""
        if outcome is None:
            return
        if isinstance(outcome, (list, tuple)):
            for one in outcome:
                self.apply(one, work_dir=work_dir)
            return
        if isinstance(outcome, Patch):
            self.stage(outcome.ops)
        elif isinstance(outcome, StoreWrite):
            self._refuse_if_locked(outcome.models)
            outcome.run(self._require_project().store)
            self._reload_results()
        elif isinstance(outcome, Job):
            self._start_job(outcome)
        elif isinstance(outcome, Artifact):
            self.artifacts.append(outcome)
            self._emit("job", state="artifact", artifact=outcome)
        elif isinstance(outcome, OpenProject):
            self.open(
                outcome.config_path, work_dir=work_dir
            )  # refuses while a job runs
        else:
            raise TypeError(f"not an Outcome: {outcome!r}")

    def _start_job(self, outcome: Job) -> None:
        project = self._require_project()
        if self.running is not None:
            raise JobRunningError(f"{self.running.label} is still running")
        job = RunningJob(label=outcome.label, models=outcome.models)
        self.running = job
        self._emit("job", state="started", job=job)
        ctx = JobContext(project=project, models=outcome.models)

        def work() -> None:
            try:
                outcome.run(ctx)
            except (
                BaseException
            ) as e:  # a job's failure is reported, not raised on its thread
                job.error = e
            finally:
                finish = lambda: self._finish_job(job, outcome.reloads_results)  # noqa: E731
                try:
                    self._post(finish)
                except BaseException as e:  # the loop is gone: finish here, never wedge
                    job.error = job.error or e
                    finish()

        threading.Thread(
            target=work, name=f"dstf-job-{outcome.label}", daemon=True
        ).start()

    def _finish_job(self, job: RunningJob, reloads_results: bool) -> None:
        """Release the lock, reload if the job asked and succeeded, tell the
        listeners, mark done — the last two whatever the reload or a
        listener does, so a waiting shell never sees a job that neither
        finished nor failed."""
        self.running = None
        try:
            if reloads_results and job.error is None and self.project is not None:
                self._reload_results()
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

    def _rebuild_tests(self, models: set[str]) -> None:
        """Re-merge the scan against the effective spec; models the spec no
        longer names drop out of ``comparisons``, models it newly names are
        scored (with whatever result the last reload holds for them)."""
        project = self._require_project()
        project.tests = self._merge(
            project.config, project.scanned, self.spec.effective()
        )
        current = {t.model_id for t in project.tests}
        gone = set(self.comparisons) - current
        new = current - set(self.comparisons)
        for model_id in gone:
            del self.comparisons[model_id]
        self._emit("spec", models=models | gone | new)
        self._score_all((models | new) & current)

    def _score(self, model_id: str) -> TestComparison:
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        tolerance = project.config.tolerance
        comp = compare_one(
            test,
            project.results.get(model_id),
            project.store,
            float(tolerance) if tolerance is not None else DEFAULT_TOLERANCE,
            project.config.default_points,
            dataset_kind=project.dataset_kind,
        )
        self.comparisons[model_id] = comp
        return comp

    def _score_all(self, models: Iterable[str]) -> None:
        project = self._require_project()
        wanted = set(models)
        for test in project.tests:
            if test.model_id in wanted:
                self._score(test.model_id)
        self._emit("results", models=wanted)

    def _reload_results(self) -> None:
        """Re-read the last run and rescore — every model except the ones a
        running job has locked: their directories are being rewritten, so
        their in-memory result and verdict stand until that job's own
        completion reload (which runs after the lock is released)."""
        project = self._require_project()
        locked = self.running.models if self.running is not None else frozenset()
        fresh = project.backend.read_last_results(project.tests)
        for model_id in locked:
            if model_id in project.results:
                fresh[model_id] = project.results[model_id]
            else:
                fresh.pop(model_id, None)
        project.results = fresh
        self._score_all(t.model_id for t in project.tests if t.model_id not in locked)

    def _materializable(self, model_id: str, entry: dict) -> bool:
        """Would a leaf per tracked variable score exactly what the flat
        path scored? False — and ``rescore`` stages nothing — when any of
        the four ways the two paths differ applies: a glob pattern (a
        sentinel leaf), a name the result did not resolve (same), an
        override whose ``mode`` no metric is registered under (the flat
        path fails it; a tree would score nrmse), or a reference-JSON
        override for a variable the spec does not override (the flat path
        merges it; a tree ignores it). Also False with no successful result.
        Each case has its negative test in tests/test_session.py."""
        project = self._require_project()
        variables = entry.get("variables") or []
        result = project.results.get(model_id)
        if not variables or result is None or not result.success:
            return False
        resolved = {v.name for v in result.variables}
        if not all(is_literal_variable(v) and v in resolved for v in variables):
            return False
        if unknown_override_modes(entry):
            return False
        spec_overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
        ref = project.store.get_reference(model_id) or {}
        ref_overrides = (ref.get("comparison") or {}).get("variable_overrides") or {}
        return not any(
            v in ref_overrides and v not in spec_overrides for v in variables
        )

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
    """What a capability receives: the Session's state and its readers,
    nothing that writes. The enforcement is structural — a capability never
    holds the Session."""

    __slots__ = ("_session",)

    def __init__(self, session: Session):
        self._session = session

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
        return self._session.selection

    @property
    def running(self) -> RunningJob | None:
        return self._session.running

    @property
    def comparisons(self) -> Mapping[str, TestComparison]:
        return MappingProxyType(self._session.comparisons)

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(self._session.artifacts)

    def is_locked(self, model_id: str) -> bool:
        running = self._session.running
        return running is not None and model_id in running.models

    def rescore(self, model_id: str) -> TestComparison:
        return self._session.rescore(model_id)

    def view(self, model_id: str, variable: str) -> VariableView:
        return self._session.view(model_id, variable)

    def figure(self, model_id: str, variable: str) -> dict:
        return self._session.figure(model_id, variable)


def _in_order(ids: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in ids:
        seen.setdefault(i, None)
    return list(seen)
