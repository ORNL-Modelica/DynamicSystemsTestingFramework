"""The Session — the authoring tool with no UI (two-tier design, §4.5).

Opens a project, scores its tests from the last run on disk, holds the
spec with the ops staged against it, and applies the outcomes capabilities
return. It writes only through ``save`` (the spec) and through the outcomes
it applies (the store, a job's work dir). Nothing here imports a UI.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
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
from ..storage.reference_store import ReferenceStore
from .capabilities import Artifact
from .spec_document import PatchOp, SpecDocument

# Task 10 adds: Job, JobContext, OpenProject, Outcome, Patch, StoreWrite to the
# capabilities import, and Mapping / MappingProxyType for SessionView. Not
# before — ruff F401 would make this commit red.

Event = Literal["spec", "results", "job", "selection"]
Listener = Callable[[dict[str, Any]], None]


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
        self._reload_results()
        self._emit("spec", models=set(self.comparisons))

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
                effective, model_id, project.config
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
        project = self._require_project()
        project.tests = self._merge(
            project.config, project.scanned, self.spec.effective()
        )
        self._emit("spec", models=models)
        self._score_all(models)

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
        project = self._require_project()
        project.results = project.backend.read_last_results(project.tests)
        self._score_all(t.model_id for t in project.tests)

    def _materializable(self, model_id: str, entry: dict) -> bool:
        """Every tracked variable is a literal name the test's result
        resolved, so a leaf per variable scores the same set the flat path
        scored. A glob, a missing name, or no successful result: False."""
        project = self._require_project()
        variables = entry.get("variables") or []
        result = project.results.get(model_id)
        if not variables or result is None or not result.success:
            return False
        resolved = {v.name for v in result.variables}
        return all(is_literal_variable(v) and v in resolved for v in variables)

    def _refuse_if_locked(self, models: Iterable[str]) -> None:
        if self.running is None:
            return
        locked = set(models) & self.running.models
        if locked:
            raise LockedError(locked, self.running.label)

    def _emit(self, event: Event, **payload: Any) -> None:
        for cb in list(self._listeners.get(event, [])):
            cb({"event": event, **payload})


def _in_order(ids: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in ids:
        seen.setdefault(i, None)
    return list(seen)
