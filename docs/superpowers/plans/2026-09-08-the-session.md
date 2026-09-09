# The Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The authoring tool's core with no UI: a `Session` that opens a project, rescores one test in memory against a spec with pending edits applied, builds a variable's figure, stages / saves / discards / exports patch ops, applies capability outcomes, and locks the models a job is running on — plus the capability protocol and registry that sub-project 4's shell will list. Three seams land first as their own commits: in-memory spec parsing and discovery, the multi-model patch envelope, and the shared accept function with the aborted-run guard.

**Architecture:** Sub-project 3 of `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.5 The Session, §4.6 Capabilities, §4.7 the envelope and accept paragraphs, §5 step 3, §6 "The Session"). Sub-projects 1 and 2 shipped at 75e91f7: the report paints Python figure dicts and computes nothing. This plan adds `src/dstf/authoring/` (pure Python, no framework) and moves four pieces of CLI-only logic into library functions the CLI and the Session both call: spec parsing from a document, the spec-update read-modify-write, the recorded-backend rule, and the accept guard. `cmd_run`'s run pipeline is NOT extracted here (sub-project 5).

**Tech Stack:** Python 3.10+, dataclasses, `typing.Protocol`, `threading`, pytest. No NiceGUI, no plotly.py.

**Revision:** this is the second text. A fresh-context review of the first ran every fixture against the tree and found: the per-leaf op path used `params/` where the raw tree is flat (every Session test would have failed); Task 1 committed red under mypy and under a `ConfigError` the test fixture raised; `rescore`'s auto-materialization flipped a glob-pattern test PASS→FAIL; two job tests raced; `SessionView` handed out the mutable project. All folded in below (calls 7–10 in Conventions).

---

## Conventions for every task

- Run commands from the repo root with `uv run ...`. Gates before every commit: `uv run ruff check`, `uv run ruff format --check` (fix with `uv run ruff format <files>`), `uv run mypy` **bare, no path**, and `uv run pytest -q -m "not playwright"` plus the tests each task names. The browser suite (`uv run pytest -q -m playwright`) runs in Task 12 only; nothing here touches the templates.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`.** Other agents may hold staged changes in the same index. New files need `git add <file>` first, then the path-scoped commit. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf compare` or `dstf run` against `testing_output/` or any real work directory** from a task or a review; `cmd_compare` wipes the report pages there at start. Tests use `tmp_path`. Task 12's real-library gate is run by the orchestrator only.
- Done-checks are predicates over the end state, never counts.
- Every moved function keeps its body unless a step shows a change; a move is `git mv`-like in spirit — the old name is deleted, every caller (src AND tests — grep both) is updated, no alias is left behind.
- `docs/ideas.md` and `.soul/` are NOT touched by this plan. `docs/decisions.md` gains no entry: D125 already records the split; this plan builds what it announced.
- Calls made here, not in the spec (each is recorded in the spec's §4.6 by Task 11):
  1. **`Job` and `StoreWrite` carry `models: frozenset[str]`** — the models they touch. The spec's dataclasses omit it, but "a locked model refuses `StoreWrite`" and "a job locks its models" both need the outcome to say which models, and a selection snapshot taken at `apply` time would lock the wrong set for a capability that acts on a subset.
  2. **Job completion is posted through `Session(post=...)`.** The spec says the job "posts its completion back to" the UI loop; plan 4's shell passes NiceGUI's loop. With no `post`, the completion runs on the worker thread, and tests wait on `RunningJob.done`.
  3. **`rescore` is the only reader that stages.** The spec makes `rescore` stage the implicit-tree materialization op for a flat-override test. Internal rescoring after a results reload uses the pure `_score`, so opening a 331-test project does not stage 331 ops; a test's tree is materialized in memory the first time the tool looks at it.
  4. **`spec-update` becomes one read and one write for every model in the envelope** (`apply_validated_patches`), replacing three reads per model. That single read-modify-write is the path the Session's `save` calls.
  5. **The Session does not filter.** `--filter` is plan 4's CLI flag.
  6. **The review loop's `[a]` keeps calling `store_reference` directly.** It accepts a result the same process produced seconds earlier; routing it through the shared accept function is a plan-5 decision when the tool's Accept lands.
  7. **Materialization refuses glob patterns, and `rescore` stages it only when it cannot change the verdict.** The flat path scores the variables the *result* resolved; a synthesized tree has one leaf per *pattern*, so for `"variables": ["*"]` (the documented form) the tree's `*` leaf is a sentinel and a passing test fails. `spec-update` does this silently today; after this plan `implicit_tree_op` raises `PatchError` for a non-literal pattern (a `*` leaf is never right), and `rescore` stages the op only when every tracked variable is a literal name present in the test's result — otherwise it scores without staging, and that test's leaves are not editable in the tool until the owner decides how a glob spec materializes (resolve the patterns against the result's variables at materialization time, or keep them uneditable). OPEN OWNER QUESTION — recorded in the handoff, not decided here.
  8. **Discovery reads `config.spec_path`, the same file the Session and `dstf add` use.** Today `discover_tests` reads only `config.test_spec_file` and ignores the `<reference_root>/test_spec.json` that `dstf add` writes when `test_spec` is not configured; the Session would have scored tests `compare` never discovered. One rule now; the CLI change (a stray fallback file is discovered) is listed in the spec's acceptance criterion 4 by Task 11.
  9. **`SessionView.project` is a frozen `ProjectView`**, not the `Project`: config, the tests as a tuple, the results as a read-only mapping, the dataset kind, and two store readers (`reference`, `baseline_names`). The store's writers are reachable only through `StoreWrite.run` and `JobContext`, which the Session hands out, never the view. `SessionView.rescore` does stage the materialization op (the spec lists `rescore` on the view; it is the one write, and it is the Session's own).
  10. **A model-less envelope is a config error** (exit 2) like every other malformed shape, where it used to print and exit 1. Listed in the spec's criterion 4 by Task 11.

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/config.py` | `Config.spec_path` — the one place the `<reference_root>/test_spec.json` fallback is spelled. |
| `src/dstf/discovery/spec_parser.py` | `spec_models_from_document(data, *, base_dir, label)` — TestModels from a parsed document; `parse_test_spec(path)` is its disk wrapper. |
| `src/dstf/discovery/test_registry.py` | `scan_source_tests(config)`, `merge_tests(scanned, spec_tests)` (copies its inputs), `discover_tests(config, *, spec_document=None)`. |
| `src/dstf/discovery/patch_apply.py` | `apply_patch_to_document(data, model_id, ops, *, whitelist)` — the in-memory half; `apply_patch(path, ...)` wraps it. |
| `src/dstf/discovery/spec_update.py` (new) | `spec_entry`, `implicit_tree_op`, `materialize_implicit_metrics`, `validate_entry_metric_tree`, `apply_validated_patches` — the read-modify-write both `spec-update` and `Session.save` use. |
| `src/dstf/discovery/patch_envelope.py` (new) | `ModelPatch`, `parse_envelope` (both forms), `build_envelope` (multi-model form only). |
| `src/dstf/cli/spec_cmds.py` | `cmd_spec_update` parses the envelope and calls `apply_validated_patches`; the three private helpers are gone. |
| `src/dstf/cli/review.py` | `_get_spec_path` deleted; uses `config.spec_path`. |
| `src/dstf/storage/batch_manifest.py` | `unfinished_run_models(work_dir)` lives beside the manifest it reads. |
| `src/dstf/storage/accept.py` (new) | `accept_run_results(store, tests, results, work_dir)` — `accept_results` plus the aborted-run refusal. |
| `src/dstf/backends/progress.py` | `recorded_backend(work_dir) -> (label, warning)` beside the writer of `status.json`. |
| `src/dstf/backends/__init__.py` | `dataset_kind_for(backend)`. |
| `src/dstf/cli/run_cmds.py` | Both `--accept` branches call `accept_run_results` and one `_report_accept`; `cmd_compare` uses `recorded_backend`; `_dataset_kind_for` gone. |
| `src/dstf/comparison/comparator.py` | `compare_one(test, result, store, ...)` — the per-test body of `compare_all`, including the no-result / sim-failed / no-baseline short-circuits. |
| `src/dstf/reporting/context_builder.py` | `build_trajectories` (public). |
| `src/dstf/figures/assemble.py` (new) | `leaves_by_variable(metric_tree)`, `view_from_trajectory(name, trajectory, leaves)` — shared by `report_suite._build_figures` and `Session.view`. |
| `src/dstf/authoring/__init__.py` (new) | Package docstring. |
| `src/dstf/authoring/spec_document.py` (new) | `PatchOp`, `SpecDocument` (loaded document, content hash, pending ops, `effective()`, `grouped()`, `on_disk_changed()`). |
| `src/dstf/authoring/capabilities.py` (new) | `Scope`, `Patch` / `StoreWrite` / `Job` / `Artifact` / `OpenProject`, `Outcome`, `JobContext`, `Panel`, `Capability`, `register_capability`, `registered_capabilities`, `_reset_for_tests`. |
| `src/dstf/authoring/session.py` (new) | `Project`, `Selection`, `RunningJob`, `LockedError`, `StaleSpecError`, `JobRunningError`, `Session`, `SessionView`. |
| `pyproject.toml` | `[tool.mypy] files` gains `src/dstf/authoring` and `src/dstf/figures`. |
| `tests/test_discovery.py`, `tests/test_patch_apply.py`, `tests/test_spec_update_cli.py`, `tests/test_compare_accept.py`, `tests/test_cli.py`, `tests/test_comparator.py`, `tests/test_capability_gating.py`, `tests/test_interactive_report.py` | Adapted to the moves; new seam tests. |
| `tests/test_patch_envelope.py`, `tests/test_spec_update.py`, `tests/test_accept.py`, `tests/test_spec_document.py`, `tests/test_capabilities.py`, `tests/test_session.py` (new) | The seam and Session suites (§6 "The Session"). |
| `docs/architecture.md`, `docs/extensibility.md`, `docs/usage.md`, `CLAUDE.md`, the spec's §4.6 | Updated. |

---

### Task 1: Spec parsing from a document, and one spec path

**Files:**
- Modify: `src/dstf/discovery/spec_parser.py:102-272`
- Modify: `src/dstf/config.py` (after the `reference_dir` property, ~line 692)
- Modify: `src/dstf/cli/spec_cmds.py:23-25, 65-67`, `src/dstf/cli/review.py:9-13, 58`
- Test: `tests/test_discovery.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_discovery.py`:

```python
class TestSpecFromDocument:
    """`spec_models_from_document` is `parse_test_spec` without the disk."""

    def _spec(self):
        return {
            "tests": [
                {
                    "model": "Lib.A",
                    "variables": ["x", "y"],
                    "simulation": {"stop_time": 3.0},
                    "comparison": {"variable_overrides": {"x": {"tolerance": 0.1}}},
                },
                {"model": "Lib.B", "variables": ["z"], "source": "Examples/B.py"},
            ]
        }

    def test_document_and_file_produce_the_same_models(self, tmp_path):
        from dstf.discovery.spec_parser import parse_test_spec, spec_models_from_document

        (tmp_path / "Examples").mkdir()
        (tmp_path / "Examples" / "B.py").write_text("")
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")

        from_file = parse_test_spec(spec_path)
        from_doc = spec_models_from_document(
            self._spec(), base_dir=tmp_path, label="in memory"
        )
        assert [t.model_id for t in from_file] == [t.model_id for t in from_doc]
        for a, b in zip(from_file, from_doc):
            assert (a.variable_patterns, a.stop_time, a.variable_overrides, a.source_file) == (
                b.variable_patterns, b.stop_time, b.variable_overrides, b.source_file
            )
        assert from_doc[1].source_file == (tmp_path / "Examples" / "B.py").resolve()

    def test_non_object_root_yields_nothing(self):
        from dstf.discovery.spec_parser import spec_models_from_document

        assert spec_models_from_document([], base_dir=Path("."), label="x") == []
        assert spec_models_from_document({"tests": "no"}, base_dir=Path("."), label="x") == []

    def test_bad_metric_tree_names_the_label(self):
        from dstf.config import ConfigError
        from dstf.discovery.spec_parser import spec_models_from_document

        doc = {"tests": [{"model": "Lib.A", "variables": ["x"], "metrics": 42}]}
        with pytest.raises(ConfigError, match="in memory"):
            spec_models_from_document(doc, base_dir=Path("."), label="in memory")
```

And to `tests/test_config.py`:

```python
def test_spec_path_is_the_configured_file_else_the_reference_root_fallback(tmp_path):
    from dstf.config import Config

    (tmp_path / "package.mo").write_text("package X\nend X;\n", encoding="utf-8")
    explicit = Config(source_path=tmp_path, reference_root=tmp_path / "refs",
                      test_spec_file=tmp_path / "s.json")
    assert explicit.spec_path == (tmp_path / "s.json").resolve()
    fallback = Config(source_path=tmp_path, reference_root=tmp_path / "refs")
    assert fallback.spec_path == fallback.reference_root / "test_spec.json"
```

- [ ] **Step 2: Run them** — `uv run pytest tests/test_discovery.py::TestSpecFromDocument tests/test_config.py -q -k "spec_path or TestSpecFromDocument"`. Expected: FAIL, `ImportError: cannot import name 'spec_models_from_document'` and `AttributeError: 'Config' object has no attribute 'spec_path'`.

- [ ] **Step 3: Split the parser.** In `spec_parser.py`, replace the head of `parse_test_spec` (from `def parse_test_spec` through the `tests_data` check) with:

```python
def parse_test_spec(spec_path: Path) -> list[TestModel]:
    """Parse a test_spec.json file into TestModel entries.

    (keep the existing docstring's Format / Minimal entry / Variable patterns
    sections here, verbatim)

    Reads the file, then hands the document to
    :func:`spec_models_from_document`; the authoring Session calls that
    directly with its in-memory document.
    """
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read test spec %s: %s", spec_path, e)
        return []
    return spec_models_from_document(
        data, base_dir=spec_path.parent, label=str(spec_path)
    )


def spec_models_from_document(
    data: object, *, base_dir: Path, label: str
) -> list[TestModel]:
    """TestModels from an already-parsed spec document.

    ``base_dir`` anchors each entry's relative ``source``; ``label`` names the
    document in log messages and in the ``ConfigError`` a bad ``metrics``
    tree raises (a path on disk, or "in memory" for the Session's copy).
    A non-object root or a non-list ``tests`` yields ``[]`` with an error
    logged, as a malformed file always has.
    """
    if not isinstance(data, dict):
        logger.error("Spec root must be an object in %s", label)
        return []
    tests_data = data.get("tests", [])
    if not isinstance(tests_data, list):
        logger.error("'tests' must be a list in %s", label)
        return []
```

Then in the loop body that follows (unchanged otherwise): every `spec_path` in a log message becomes `label` (the non-dict-entry warning at the top of the loop); `(spec_path.parent / source_rel).resolve()` becomes `(base_dir / source_rel).resolve()`; the final `raise ConfigError(f"{spec_path}: {e}")` becomes `raise ConfigError(f"{label}: {e}")`. When done, `grep -n spec_path src/dstf/discovery/spec_parser.py` shows hits only in `parse_test_spec`, `_load_spec_for_edit`, `_write_spec`, `add_to_test_spec` and `update_test_variables`.

- [ ] **Step 4: `Config.spec_path`.** Add after the `reference_dir` property in `config.py`:

```python
    @property
    def spec_path(self) -> Path:
        """Where ``test_spec.json`` lives: the configured file, else
        ``<reference_root>/test_spec.json``. The one spelling of a fallback
        that ``add``, ``spec-update`` and the review loop each used to
        carry; the authoring Session reads the same property."""
        if self.test_spec_file is not None:
            return self.test_spec_file
        assert self.reference_root is not None  # resolved in __post_init__, as reference_dir relies on
        return self.reference_root / "test_spec.json"
```

In `cli/spec_cmds.py` replace both

```python
    spec_path = config.test_spec_file
    if spec_path is None:
        spec_path = config.reference_root / "test_spec.json"
```

with `spec_path = config.spec_path`. In `cli/review.py` delete `_get_spec_path` and change line 58 to `spec_path = config.spec_path if config else None`; `from pathlib import Path` at its line 3 is then unused — delete it (ruff F401). `grep -rn 'reference_root / "test_spec.json"' src/` then hits only `config.py`.

- [ ] **Step 5: Run** — `uv run pytest tests/test_discovery.py tests/test_config.py tests/test_spec_update_cli.py tests/test_cli.py -q`. Expected: PASS. Then the gates.

- [ ] **Step 6: Commit** (paths: `src/dstf/discovery/spec_parser.py src/dstf/config.py src/dstf/cli/spec_cmds.py src/dstf/cli/review.py tests/test_discovery.py tests/test_config.py`). Message: `feat: test models from a parsed spec document; Config.spec_path`

---

### Task 2: Discovery accepts a pre-parsed spec

**Files:**
- Modify: `src/dstf/discovery/test_registry.py:173-295`
- Test: `tests/test_discovery.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_discovery.py` (uses the module's existing `_write_lib` helper at ~line 460, which writes `package.mo` + `Foo.mo` with an `experiment` annotation + `testing.json` and returns a `Config`; read it first):

```python
class TestDiscoveryFromDocument:
    def test_document_replaces_the_file(self, tmp_path):
        from dstf.discovery.test_registry import discover_tests

        config = _write_lib(tmp_path, spec={"tests": [{"model": "Foo.OnDisk", "variables": ["x"]}]})
        doc = {"tests": [{"model": "Foo.InMemory", "variables": ["y"], "simulation": {"stop_time": 7.0}}]}

        ids = {t.model_id for t in discover_tests(config, spec_document=doc)}
        assert "Foo.InMemory" in ids and "Foo.OnDisk" not in ids
        by_id = {t.model_id: t for t in discover_tests(config, spec_document=doc)}
        assert by_id["Foo.InMemory"].stop_time == 7.0
        # The .mo scan still contributes: the annotated model _write_lib wrote is there.
        assert any(t.source != "spec" for t in by_id.values())

    def test_merge_does_not_mutate_the_scan(self, tmp_path):
        from dstf.discovery.test_registry import merge_tests, scan_source_tests

        config = _write_lib(tmp_path)
        scanned = scan_source_tests(config)
        model_id = next(iter(scanned))
        before = scanned[model_id].stop_time
        spec_test = _mk_model(model_id=model_id, stop_time=99.0, variable_patterns=["q"])

        merged = merge_tests(scanned, {model_id: spec_test})
        assert next(t for t in merged if t.model_id == model_id).stop_time == 99.0
        assert scanned[model_id].stop_time == before
        assert scanned[model_id].variable_patterns != ["q"]
        # A second merge against a different spec sees the untouched scan.
        again = merge_tests(scanned, {})
        assert next(t for t in again if t.model_id == model_id).variable_patterns != ["q"]

    def test_the_fallback_spec_file_is_discovered(self, tmp_path):
        """`dstf add` writes <reference_root>/test_spec.json when no test_spec
        is configured; discovery reads that same file (Conventions #8)."""
        from dstf.discovery.test_registry import discover_tests

        config = _write_lib(tmp_path)  # no "test_spec" key in testing.json
        assert config.test_spec_file is None
        config.spec_path.parent.mkdir(parents=True, exist_ok=True)
        config.spec_path.write_text(
            json.dumps({"tests": [{"model": "Foo.Fallback", "variables": ["x"]}]}), encoding="utf-8"
        )
        assert "Foo.Fallback" in {t.model_id for t in discover_tests(config)}
```

(If `_write_lib` always writes a `test_spec` key, add a keyword to skip it — read the helper.)

(`_mk_model` is the module's helper at ~line 491; it builds a `TestModel` with keyword overrides.)

- [ ] **Step 2: Run** — `uv run pytest tests/test_discovery.py::TestDiscoveryFromDocument -q`. Expected: FAIL (`TypeError: discover_tests() got an unexpected keyword argument`, `ImportError` for `merge_tests`).

- [ ] **Step 3: Split `discover_tests`** into three functions plus the wrapper. `import copy` at the top of the module. The bodies are the existing Step 1 / Step 2 / Step 3 blocks; only the seams below are new:

```python
def scan_source_tests(config: Config) -> dict[str, TestModel]:
    """Step 1 of discovery: recognizer results over the source tree, keyed
    by model_id. Empty unless ``source_type == "modelica"``."""
    # Trigger bundled-recognizer registration on first call.
    from . import mo_parser  # noqa: F401

    ut_tests: dict[str, TestModel] = {}
    # ... the existing Step 1 body, verbatim (the `if config.source_type == "modelica":` block) ...
    return ut_tests


def spec_tests_by_model(models: list[TestModel]) -> dict[str, TestModel]:
    """Step 2: the spec's models keyed by model_id, first entry wins on a
    duplicate (matching patch_apply / the spec edit helpers), with a warning."""
    spec_tests: dict[str, TestModel] = {}
    for test in models:
        if test.model_id in spec_tests:
            logger.warning(
                "test_spec has duplicate entries for model '%s'; "
                "using the first — remove the duplicates",
                test.model_id,
            )
            continue
        spec_tests[test.model_id] = test
    return spec_tests


def merge_tests(
    scanned: dict[str, TestModel], spec_tests: dict[str, TestModel]
) -> list[TestModel]:
    """Step 3: union by model_id; the spec wins per field. Copies both
    inputs, so a caller (the authoring Session) can keep one scan and
    re-merge it against every edit of the spec."""
    merged: dict[str, TestModel] = {
        model_id: copy.deepcopy(test) for model_id, test in scanned.items()
    }
    for model_id, spec_test in spec_tests.items():
        spec_test = copy.deepcopy(spec_test)
        # ... the existing Step 3 per-model body, verbatim ...
    tests = sorted(merged.values(), key=lambda t: t.model_id)
    for test in tests:
        test.finalize_defaults()
    return tests


def discover_tests(
    config: Config, *, spec_document: dict | None = None
) -> list[TestModel]:
    """Discover all test models from registered recognizers and/or the spec.

    (keep the existing docstring's Sources paragraph)

    ``spec_document`` replaces the on-disk spec when given — the authoring
    Session passes its in-memory document with pending edits applied; the
    ``.mo`` scan and the merge are the same either way.
    """
    scanned = scan_source_tests(config)
    if spec_document is not None:
        from .spec_parser import spec_models_from_document

        models = spec_models_from_document(
            spec_document,
            base_dir=config.spec_path.parent,
            label=f"{config.spec_path} (in memory)",
        )
    elif config.spec_path.exists():
        from .spec_parser import parse_test_spec

        models = parse_test_spec(config.spec_path)
    else:
        models = []
    return merge_tests(scanned, spec_tests_by_model(models))
```

(`config.spec_path` is `test_spec_file` when configured, else the `<reference_root>/test_spec.json` fallback `dstf add` writes — Conventions #8. The docstring's Sources paragraph says "External test_spec.json (the configured file, else `<reference_root>/test_spec.json` when it exists)".)

- [ ] **Step 4: Run** — `uv run pytest tests/test_discovery.py tests/test_pta_demo.py tests/test_recognizer_wiring.py tests/test_cli.py -q`. Expected: PASS. Gates.

- [ ] **Step 5: Commit** (paths: `src/dstf/discovery/test_registry.py tests/test_discovery.py`). Message: `feat: discovery takes a pre-parsed spec; the scan and the merge are separable`

---

### Task 3: One read-modify-write for a validated patch

**Files:**
- Modify: `src/dstf/discovery/patch_apply.py:47-80`
- Create: `src/dstf/discovery/spec_update.py`
- Modify: `src/dstf/cli/spec_cmds.py:52-204`
- Test: `tests/test_spec_update.py` (new), `tests/test_patch_apply.py`, `tests/test_spec_update_cli.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_spec_update.py`:

```python
"""The spec-update read-modify-write as a library: materialization, validation,
one read and one write for every model in a patch set."""

import json
from pathlib import Path

import pytest

from dstf.config import Config
from dstf.discovery.patch_apply import PatchError, apply_patch_to_document
from dstf.discovery.spec_update import (
    apply_validated_patches,
    implicit_tree_op,
    materialize_implicit_metrics,
    spec_entry,
)

FLAT = {"model": "M", "variables": ["x", "y"],
        "comparison": {"variable_overrides": {"x": {"tolerance": 0.1}}}}
# The raw tree is flat: a leaf is {"metric": ..., "variable": ..., <param>: ...}, no "params" key.
LEAF_OP = {"op": "replace", "path": "/metrics/children/0/tolerance", "value": 0.5}


def _config(tmp_path) -> Config:
    (tmp_path / "testing.json").write_text(
        json.dumps({"source_type": "fmu", "backend": "FMPy", "reference_root": "."}),
        encoding="utf-8",
    )
    return Config(config_file=tmp_path / "testing.json")


def _spec(tmp_path, *entries) -> Path:
    p = tmp_path / "test_spec.json"
    p.write_text(json.dumps({"tests": list(entries)}, indent=2) + "\n", encoding="utf-8")
    return p


class TestMaterialization:
    def test_flat_entry_gets_the_implicit_tree_prepended(self):
        ops = materialize_implicit_metrics(dict(FLAT), [LEAF_OP])
        assert ops[0]["op"] == "add" and ops[0]["path"] == "/metrics"
        assert ops[0] == implicit_tree_op(dict(FLAT))
        assert ops[1:] == [LEAF_OP]
        leaves = ops[0]["value"]["children"]
        assert [leaf["variable"] for leaf in leaves] == ["x", "y"]
        assert leaves[0]["tolerance"] == 0.1 and "tolerance" not in leaves[1]

    def test_a_glob_pattern_is_refused(self):
        """A `*` leaf is a sentinel that fails; the flat path scored the
        variables the result resolved. Never write that tree (Conventions #7)."""
        for patterns in (["*"], ["pipe.T*"], ["x", "tank.?"]):
            with pytest.raises(PatchError, match="pattern"):
                implicit_tree_op({"model": "M", "variables": patterns})

    def test_a_patch_that_supplies_the_tree_is_not_materialized_again(self):
        tree_op = implicit_tree_op(dict(FLAT))
        assert materialize_implicit_metrics(dict(FLAT), [tree_op, LEAF_OP]) == [tree_op, LEAF_OP]

    def test_an_entry_with_a_tree_is_left_alone(self):
        entry = {**FLAT, "metrics": {"metric": "nrmse", "variable": "x"}}
        assert materialize_implicit_metrics(entry, [LEAF_OP]) == [LEAF_OP]

    def test_ops_outside_metrics_need_nothing(self):
        op = {"op": "replace", "path": "/comparison/tolerance", "value": 1.0}
        assert materialize_implicit_metrics(dict(FLAT), [op]) == [op]

    def test_no_variables_is_a_patch_error(self):
        with pytest.raises(PatchError, match="no tracked variables"):
            implicit_tree_op({"model": "M", "variables": []})

    def test_missing_entry_is_left_to_apply_patch(self):
        assert materialize_implicit_metrics(None, [LEAF_OP]) == [LEAF_OP]


class TestApplyValidatedPatches:
    def test_two_models_one_write(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT), {"model": "N", "variables": ["z"]})
        applied = apply_validated_patches(
            spec,
            [("M", [LEAF_OP]), ("N", [{"op": "add", "path": "/comparison", "value": {"tolerance": 0.2}}])],
            _config(tmp_path),
        )
        data = json.loads(spec.read_text(encoding="utf-8"))
        m, n = spec_entry(data, "M"), spec_entry(data, "N")
        assert m["metrics"]["children"][0]["tolerance"] == 0.5
        assert n["comparison"] == {"tolerance": 0.2}
        assert [model for model, _ in applied] == ["M", "N"]
        assert applied[0][1][0]["path"] == "/metrics"  # materialization is reported
        assert spec.read_bytes().endswith(b"}\n") and b"\r\n" not in spec.read_bytes()

    def test_a_bad_group_writes_nothing(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT), {"model": "N", "variables": ["z"]})
        before = spec.read_bytes()
        with pytest.raises(PatchError):
            apply_validated_patches(
                spec,
                [("M", [LEAF_OP]), ("N", [{"op": "replace", "path": "/simulation/stop_time", "value": 1}])],
                _config(tmp_path),
            )
        assert spec.read_bytes() == before

    def test_the_result_equals_applying_to_the_document_in_memory(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT))
        expected = json.loads(spec.read_text(encoding="utf-8"))
        apply_patch_to_document(expected, "M", materialize_implicit_metrics(dict(FLAT), [LEAF_OP]))
        apply_validated_patches(spec, [("M", [LEAF_OP])], _config(tmp_path))
        assert json.loads(spec.read_text(encoding="utf-8")) == expected
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_spec_update.py -q`. Expected: FAIL with `ModuleNotFoundError: dstf.discovery.spec_update`.

- [ ] **Step 3: Split `apply_patch`.** In `patch_apply.py` replace `apply_patch` with:

```python
def apply_patch_to_document(
    data: dict,
    model_id: str,
    patch_ops: list[dict],
    *,
    whitelist: tuple[str, ...] = ("/comparison", "/metrics"),
) -> dict:
    """Apply ``patch_ops`` to the entry in the parsed document ``data`` that
    matches ``model_id`` (created if absent), in place. Returns ``data``.
    Raises :class:`PatchError` naming the failing op. The authoring
    Session's in-memory document goes through here; the disk path is
    :func:`apply_patch`."""
    entry = _find_or_create_entry(data, model_id)
    for i, op in enumerate(patch_ops):
        try:
            _apply_one_op(entry, op, whitelist)
        except PatchError as e:
            raise PatchError(
                f"patch_ops[{i}] ({op!r}): {e}",
                path=e.path,
            ) from None
    return data


def apply_patch(
    spec_path: Path,
    model_id: str,
    patch_ops: list[dict],
    *,
    whitelist: tuple[str, ...] = ("/comparison", "/metrics"),
    write: bool = True,
) -> dict:
    """Apply ``patch_ops`` to the entry in ``spec_path`` matching ``model_id``.

    (keep the existing docstring body)
    """
    data = apply_patch_to_document(
        _load_spec(spec_path), model_id, patch_ops, whitelist=whitelist
    )
    if write:
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        # review 2026-07-06 (finding 31): atomic — an interrupted patch must
        # not leave the user's spec truncated.
        _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")
    return data
```

Also make the document reader shared: rename `_load_spec` to `load_spec_document` (public; same body). `_load_spec` has no callers outside this module — confirm with `grep -rn "_load_spec\b" src tests`; `spec_parser._load_spec_for_edit` is a different function and stays.

- [ ] **Step 4: Create `src/dstf/discovery/spec_update.py`:**

```python
"""Applying a validated patch set to ``test_spec.json``.

The one read-modify-write both ``dstf spec-update`` and the authoring
Session's ``save`` go through: materialize the implicit tree where a
per-leaf op needs one, apply each model's ops to its entry, run the D66
validator on every touched tree, then write once. Nothing is written
unless every group passes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from ..config import Config
from ..storage.ref_index import _atomic_write_text
from .patch_apply import PatchError, apply_patch_to_document, load_spec_document


def spec_entry(data: dict, model_id: str) -> dict | None:
    """The first entry for ``model_id`` — first wins, as ``apply_patch`` and
    discovery agree — or ``None``."""
    for e in data.get("tests", []):
        if isinstance(e, dict) and e.get("model") == model_id:
            return e
    return None


def is_literal_variable(pattern: str) -> bool:
    """A tracked-variable entry that names one variable. The resolver's rule
    (``backends.types.is_glob_pattern``): ``*`` and ``?`` are wildcards, a
    Modelica array index ``[1]`` is not."""
    return not is_glob_pattern(pattern)


def implicit_tree_op(entry: dict) -> dict:
    """The ``add /metrics`` op that materializes the implicit tree a
    flat-override entry scores with: one leaf per tracked variable, params
    from ``comparison.variable_overrides``, wrapped in ``and`` (see
    ``synthesize_implicit_tree``). Raises :class:`PatchError` when the entry
    declares no variables to build leaves from."""
    from ..comparison.tree_spec import spec_to_raw, synthesize_implicit_tree

    variables = entry.get("variables") or []
    if not variables:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: the entry declares no tracked variables "
            "to build leaves from"
        )
    globs = [v for v in variables if not is_literal_variable(v)]
    if globs:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: {globs!r} are patterns, and a leaf per "
            "pattern would score a sentinel where the flat path scored the "
            "variables the result resolved"
        )
    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    tree = synthesize_implicit_tree(variables, variable_overrides=overrides)
    return {"op": "add", "path": "/metrics", "value": spec_to_raw(tree)}


def materialize_implicit_metrics(entry: dict | None, patch: Sequence[dict]) -> list[dict]:
    """``patch`` with :func:`implicit_tree_op` prepended when some op edits
    under ``/metrics/`` and neither the entry nor the patch itself supplies
    the tree.

    review 2026-07-06 finding 41: per-leaf ops target
    ``/metrics/children/N/...``, but a flat-override entry has no ``metrics``
    block and ``patch_apply`` refuses to auto-create an array parent. A
    missing entry is left to ``apply_patch`` (which creates it and then
    reports the real failure)."""
    ops = list(patch)
    if entry is None or "metrics" in entry:
        return ops
    edits_under = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in ops
    )
    supplies_tree = any(isinstance(op, dict) and op.get("path") == "/metrics" for op in ops)
    if not edits_under or supplies_tree:
        return ops
    return [implicit_tree_op(entry), *ops]


def validate_entry_metric_tree(data: dict, model_id: str, config: Config) -> None:
    """When the patched entry has a ``metrics`` tree, run the D66 validator
    against the reference store's baseline roles. Skipped when the entry has
    no tree (implicit AND)."""
    entry = spec_entry(data, model_id)
    if entry is None or "metrics" not in entry:
        return

    from ..comparison.tree_spec import parse_metric_tree
    from ..comparison.validator import role_lookup_from_store, validate_tree
    from ..storage.reference_store import ReferenceStore

    try:
        tree = parse_metric_tree(entry["metrics"])
    except Exception as e:
        raise PatchError(f"patched metric tree is invalid: {e}") from e

    store = ReferenceStore(config)
    lookup = role_lookup_from_store(store, model_id)
    errors = validate_tree(tree, lookup)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise PatchError(f"validator rejected the patched tree: {msg}")


def apply_validated_patches(
    spec_path: Path,
    groups: Sequence[tuple[str, Sequence[dict]]],
    config: Config,
) -> list[tuple[str, list[dict]]]:
    """Apply every group's ops to its model's entry in one read-modify-write.

    Per group: materialize, apply, validate. The file is written once,
    atomically and LF (D122), only after every group passed. Returns the ops
    actually applied per group — materialization included — so a caller can
    say what it did."""
    data = load_spec_document(spec_path)
    applied: list[tuple[str, list[dict]]] = []
    for model_id, ops in groups:
        full = materialize_implicit_metrics(spec_entry(data, model_id), ops)
        apply_patch_to_document(data, model_id, full)
        validate_entry_metric_tree(data, model_id, config)
        applied.append((model_id, full))
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")
    return applied
```

- [ ] **Step 5: Slim `cli/spec_cmds.py`.** Delete `_apply_validated_patch`, `_materialize_implicit_metrics` and `_validate_entry_metric_tree`. Rewrite `cmd_spec_update` so that after reading `update_data` it does:

```python
    from ..discovery.patch_apply import PatchError
    from ..discovery.spec_update import apply_validated_patches

    model_id = update_data.get("model")
    if not model_id:
        print("JSON must contain a 'model' field")
        return 1
    patch = update_data.get("patch")
    if not isinstance(patch, list):
        raise ConfigError(
            f"{json_file}: not a JSON-Patch envelope — expected "
            '{"model": "<model id>", "patch": [<RFC 6902 op>, ...]}, '
            f"got a 'patch' of type {type(patch).__name__}"
        )

    try:
        applied = apply_validated_patches(spec_path, [(model_id, patch)], config)
    except PatchError as e:
        print(f"Patch failed: {e}")
        return 1
    _print_applied(applied, [(model_id, patch)], spec_path)
    return 0


def _print_applied(applied, requested, spec_path) -> None:
    """One block per model: the materialization note when the applied ops
    outgrew the requested ones, then the requested ops."""
    for (model_id, full), (_, ops) in zip(applied, requested):
        if len(full) > len(ops):
            n_leaves = len(full[0]["value"]["children"])
            print(
                f"Note: materialized implicit metrics tree for {model_id} "
                f"({n_leaves} leaves) — entry had no 'metrics' block"
            )
        print(f"Applied {len(ops)} op(s) to {model_id} → {spec_path}")
        for op in ops:
            print(f"  {op.get('op'):<8} {op.get('path'):<60} {op.get('value', '')}")
```

(The envelope parsing above is replaced again in Task 4; this step keeps the CLI's behaviour byte-for-byte while the library moves.)

- [ ] **Step 6: Run** — `uv run pytest tests/test_spec_update.py tests/test_patch_apply.py tests/test_spec_update_cli.py -q`. Expected: PASS. `grep -rn "_materialize_implicit_metrics\|_apply_validated_patch\|_validate_entry_metric_tree" src tests` is empty. Gates.

- [ ] **Step 7: Commit** (paths: `src/dstf/discovery/patch_apply.py src/dstf/discovery/spec_update.py src/dstf/cli/spec_cmds.py tests/test_spec_update.py`). Message: `refactor: the validated spec patch is a library function with one read and one write`

---

### Task 4: The multi-model envelope

**Files:**
- Create: `src/dstf/discovery/patch_envelope.py`
- Modify: `src/dstf/cli/spec_cmds.py` (`cmd_spec_update`)
- Test: `tests/test_patch_envelope.py` (new), `tests/test_spec_update_cli.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_patch_envelope.py`:

```python
"""Both envelope forms are read; only the multi-model form is written."""

import pytest

from dstf.config import ConfigError
from dstf.discovery.patch_envelope import ModelPatch, build_envelope, parse_envelope

OP = {"op": "replace", "path": "/comparison/tolerance", "value": 0.5}


def test_single_model_form_is_read():
    assert parse_envelope({"model": "M", "patch": [OP]}, label="f") == [ModelPatch("M", [OP])]


def test_multi_model_form_is_read_in_order():
    data = {"patches": [{"model": "B", "patch": [OP]}, {"model": "A", "patch": []}]}
    assert [p.model_id for p in parse_envelope(data, label="f")] == ["B", "A"]


def test_build_writes_only_the_multi_model_form():
    env = build_envelope([ModelPatch("M", [OP])])
    assert env == {"patches": [{"model": "M", "patch": [OP]}]}
    assert "model" not in env
    assert parse_envelope(env, label="round trip") == [ModelPatch("M", [OP])]


@pytest.mark.parametrize(
    "bad",
    [
        {"model": "M", "comparison": {"tolerance": 1e-3}},   # no patch
        {"model": "M", "patch": {"op": "replace"}},          # patch not a list
        {"patches": {"model": "M"}},                         # patches not a list
        {"patches": [{"patch": [OP]}]},                      # a group with no model
        {"patch": [OP]},                                     # no model at all
        [],                                                  # not an object
    ],
)
def test_other_shapes_are_config_errors_naming_both_forms(bad):
    with pytest.raises(ConfigError, match=r'not a JSON-Patch envelope.*"patch".*"patches"'):
        parse_envelope(bad, label="f")
```

And to `tests/test_spec_update_cli.py`, inside `TestSpecUpdateMaterialization` or as a new class (the module's `_write_spec(tmp_path, entry)` and `_run_spec_update(spec, patch_file)` helpers exist; read them):

```python
class TestMultiModelEnvelope:
    def test_two_models_in_one_file(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps({"tests": [
                {"model": "A", "variables": ["x"]},
                {"model": "B", "variables": ["y"]},
            ]}),
            encoding="utf-8",
        )
        patch = tmp_path / "p.json"
        patch.write_text(json.dumps({"patches": [
            {"model": "A", "patch": [{"op": "add", "path": "/comparison", "value": {"tolerance": 0.1}}]},
            {"model": "B", "patch": [{"op": "add", "path": "/comparison", "value": {"tolerance": 0.2}}]},
        ]}), encoding="utf-8")

        assert _run_spec_update(spec, patch) == 0
        data = json.loads(spec.read_text(encoding="utf-8"))
        assert data["tests"][0]["comparison"] == {"tolerance": 0.1}
        assert data["tests"][1]["comparison"] == {"tolerance": 0.2}

    def test_a_bad_second_model_leaves_the_first_unwritten(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(json.dumps({"tests": [{"model": "A", "variables": ["x"]}]}), encoding="utf-8")
        before = spec.read_bytes()
        patch = tmp_path / "p.json"
        patch.write_text(json.dumps({"patches": [
            {"model": "A", "patch": [{"op": "add", "path": "/comparison", "value": {"tolerance": 0.1}}]},
            {"model": "A", "patch": [{"op": "replace", "path": "/simulation/stop_time", "value": 3}]},
        ]}), encoding="utf-8")
        assert _run_spec_update(spec, patch) == 1
        assert spec.read_bytes() == before
```

The existing tests `test_flat_tolerance_dict_is_rejected` (`match=r'"patch"'`) and `test_non_list_patch_is_rejected` (`match="not a JSON-Patch envelope"`) must still pass against the new message; a missing `model` is now a `ConfigError` (exit 2) rather than `rc == 1` — Conventions #10; `grep -n "must contain" tests/` confirms no test asserts the old code.

- [ ] **Step 2: Run** — `uv run pytest tests/test_patch_envelope.py tests/test_spec_update_cli.py -q`. Expected: FAIL (`ModuleNotFoundError`, then the two new CLI tests).

- [ ] **Step 3: Create `src/dstf/discovery/patch_envelope.py`:**

```python
"""The JSON-Patch envelope ``dstf spec-update`` reads and the authoring
Session's ``export`` writes.

Two shapes are read — ``{"model": ..., "patch": [...]}`` because files in
that shape exist (the reporter's export panel wrote them until D125), and
``{"patches": [{"model": ..., "patch": [...]}, ...]}`` because a tool that
spans tests needs one. Only the second is written. Old data, not old code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..config import ConfigError

_SHAPES = (
    '{"model": "<model id>", "patch": [<RFC 6902 op>, ...]} or '
    '{"patches": [{"model": ..., "patch": [...]}, ...]}'
)


@dataclass(frozen=True)
class ModelPatch:
    """One model's ops, in order. Paths are relative to the model's entry."""

    model_id: str
    ops: list[dict]


def parse_envelope(data: object, *, label: str) -> list[ModelPatch]:
    """Read either envelope form into ``ModelPatch`` groups, in file order.
    Anything else raises :class:`ConfigError` naming both forms."""
    if isinstance(data, dict) and "patches" in data:
        groups = data["patches"]
        if not isinstance(groups, list):
            raise _bad(label, f"'patches' of type {type(groups).__name__}")
        return [_group(g, label) for g in groups]
    if isinstance(data, dict) and "model" in data:
        return [_group(data, label)]
    raise _bad(label, "neither a 'model' nor a 'patches' key")


def build_envelope(groups: Sequence[ModelPatch]) -> dict:
    """The multi-model form, and only that form."""
    return {"patches": [{"model": g.model_id, "patch": list(g.ops)} for g in groups]}


def _group(raw: object, label: str) -> ModelPatch:
    if not isinstance(raw, dict) or not raw.get("model"):
        raise _bad(label, "a group without a 'model'")
    ops = raw.get("patch")
    if not isinstance(ops, list):
        raise _bad(label, f"a 'patch' of type {type(ops).__name__}")
    return ModelPatch(str(raw["model"]), ops)


def _bad(label: str, got: str) -> ConfigError:
    return ConfigError(f"{label}: not a JSON-Patch envelope — expected {_SHAPES}, got {got}")
```

- [ ] **Step 4: `cmd_spec_update` reads the envelope through it.** Replace the `model_id` / `patch` block from Task 3 Step 5 with:

```python
    from ..discovery.patch_envelope import parse_envelope

    groups = parse_envelope(update_data, label=str(json_file))
    requested = [(g.model_id, g.ops) for g in groups]
    try:
        applied = apply_validated_patches(spec_path, requested, config)
    except PatchError as e:
        print(f"Patch failed: {e}")
        return 1
    _print_applied(applied, requested, spec_path)
    return 0
```

Update the docstring: the envelope is what the authoring tool exports (multi-model) and what older single-model files carry.

- [ ] **Step 5: Run** — `uv run pytest tests/test_patch_envelope.py tests/test_spec_update_cli.py tests/test_spec_update.py -q`. Expected: PASS. Gates.

- [ ] **Step 6: Commit** (paths: `src/dstf/discovery/patch_envelope.py src/dstf/cli/spec_cmds.py tests/test_patch_envelope.py tests/test_spec_update_cli.py`). Message: `feat: spec-update reads the multi-model patch envelope; the single-model form is read, not written`

---

### Task 5: The shared accept function with the aborted-run guard

**Files:**
- Modify: `src/dstf/backends/base.py:216-237` (delete `unfinished_run_models`), `src/dstf/storage/batch_manifest.py` (add it)
- Create: `src/dstf/storage/accept.py`
- Modify: `src/dstf/cli/run_cmds.py:293-309, 493-533`
- Test: `tests/test_accept.py` (new), `tests/test_compare_accept.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_accept.py`:

```python
"""`accept_run_results`: `ReferenceStore.accept_results` plus the refusal of a
test whose last run never reached a verdict — the guard that used to live
only in `cmd_compare`, now the one function every accept path calls."""

import json

import numpy as np
from conftest import make_test_model

from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.accept import accept_run_results
from dstf.storage.reference_store import ReferenceStore


def _result(model_id):
    t = np.linspace(0.0, 1.0, 5)
    return TestResult(model_id=model_id, success=True,
                      variables=[VariableResult(index=1, time=t, values=t * 2, name="x")])


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
    config, store = _env(tmp_path, {
        "test_0001": {"model_id": "FakeLib.Done", "ref_id": None, "last_run_status": "passed"},
        "test_0002": {"model_id": "FakeLib.InFlight", "ref_id": None, "last_run_status": "pending"},
    })
    tests = [make_test_model("FakeLib.Done", variable_patterns=["x"]),
             make_test_model("FakeLib.InFlight", variable_patterns=["x"])]
    results = {t.model_id: _result(t.model_id) for t in tests}

    stored, skipped = accept_run_results(store, tests, results, config.work_dir)

    assert stored == 1
    assert [m for m, _ in skipped] == ["FakeLib.InFlight"]
    assert "re-run before accepting" in skipped[0][1] and str(config.work_dir) in skipped[0][1]
    assert store.get_reference("FakeLib.Done") is not None
    assert store.get_reference("FakeLib.InFlight") is None


def test_no_manifest_means_no_refusal(tmp_path):
    config, store = _env(tmp_path, {})
    (config.work_dir / "batch_manifest.json").unlink()
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    stored, skipped = accept_run_results(store, tests, {"FakeLib.A": _result("FakeLib.A")}, config.work_dir)
    assert (stored, skipped) == (1, [])


def test_store_skips_still_come_through(tmp_path):
    config, store = _env(tmp_path, {})
    tests = [make_test_model("FakeLib.A", variable_patterns=["x"])]
    stored, skipped = accept_run_results(store, tests, {}, config.work_dir)
    assert stored == 0 and skipped == [("FakeLib.A", "no simulation result")]
```

In `tests/test_compare_accept.py`: change the four `from dstf.backends.base import unfinished_run_models` imports to `from dstf.storage.batch_manifest import unfinished_run_models`, and replace the body of `TestAcceptRefusesPending._accept` (lines ~478-489, the verbatim copy of the CLI branch) with `return accept_run_results(store, tests, results, config.work_dir)`. `_accept` returned a 3-tuple `(stored, skipped, unfinished)` and `test_pending_plus_running_is_refused` asserted `skipped == []` because the caller added the reason; the shared function returns the pair with the refusal INSIDE `skipped`. Update every `_accept` caller to unpack two values, and that test to assert `[m for m, _ in skipped] == ["FakeLib.InFlight"]` and `"re-run before accepting" in skipped[0][1]`; drop the `unfinished` assertions. In `tests/test_cli.py`, add beside the `run --accept` tests (~line 480-545; they use the `run_env` fixture, which monkeypatches `discover_tests` and `_get_backend` — read it and `_FakeBackend` first):

```python
    def test_run_accept_refuses_a_test_the_manifest_records_as_pending(self, run_env, capsys):
        """The fake backend's run_tests returns an in-memory manifest and
        writes nothing, so a pending entry written here is what the guard
        reads after the run — the run path refuses with the message the
        compare path prints."""
        t1 = _mk_test("Lib.T1")
        run_env.make([t1], results={"Lib.T1": _mk_result("Lib.T1")})
        (run_env.work_dir / "batch_manifest.json").write_text(
            json.dumps({"test_0001": {"model_id": "Lib.T1", "ref_id": None,
                                      "last_run_status": "pending"}}),
            encoding="utf-8",
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Lib.T1" in out and "re-run before accepting" in out
        assert _stored_model_ids(run_env) == set()
```

(Goes inside `TestAcceptExitCodeAndScope`, beside `test_full_accept_exits_zero`; `_mk_test`, `_mk_result`, `_run_args`, `_stored_model_ids` and `cmd_run` are the module's.)

- [ ] **Step 2: Run** — `uv run pytest tests/test_accept.py tests/test_compare_accept.py -q`. Expected: FAIL (`ModuleNotFoundError: dstf.storage.accept`, `ImportError` on the moved name).

- [ ] **Step 3: Move `unfinished_run_models`** from `backends/base.py` into `storage/batch_manifest.py` (append it after the `BatchManifest` class; it uses `BatchManifest.load` and `PENDING_RUN_STATUS`, both already there; keep the docstring). Delete it from `base.py`, and drop `PENDING_RUN_STATUS` from `base.py`'s `storage.batch_manifest` import if nothing else there uses it (ruff F401). `grep -rn unfinished_run_models src` then hits `storage/batch_manifest.py` and, after Step 4, `storage/accept.py` only.

- [ ] **Step 4: Create `src/dstf/storage/accept.py`:**

```python
"""Accepting a run's results as baselines — the one function the CLI's two
``--accept`` paths and the authoring tool's Accept call."""

from __future__ import annotations

from pathlib import Path

from ..backends.types import TestResult
from ..discovery.test_registry import TestModel
from .batch_manifest import unfinished_run_models
from .reference_store import ReferenceStore


def accept_run_results(
    store: ReferenceStore,
    tests: list[TestModel],
    results: dict[str, TestResult],
    work_dir: Path,
) -> tuple[int, list[tuple[str, str]]]:
    """:meth:`ReferenceStore.accept_results` plus the aborted-run refusal.

    A test the batch manifest in ``work_dir`` still records as pending was
    in flight when a run was aborted: it has no recorded verdict, and its
    truncated result file may still parse. Acceptance requires an
    authoritative successful run, so it is skipped with the reason, never
    stored. Deliberately NOT keyed on the comparison verdict — a test that
    ran fine and now compares FAILED is exactly the drift case accepting
    exists to re-baseline.

    Returns ``(stored, skipped)`` exactly as ``accept_results`` does, with
    the refusals appended in model order.
    """
    unfinished = unfinished_run_models(work_dir) & {t.model_id for t in tests}
    stored, skipped = store.accept_results(
        [t for t in tests if t.model_id not in unfinished], results
    )
    return stored, list(skipped) + [
        (
            model_id,
            f"last run did not complete for this test (aborted while it was "
            f"in flight; {work_dir} still records it as pending) — "
            f"re-run before accepting",
        )
        for model_id in sorted(unfinished)
    ]
```

- [ ] **Step 5: Both CLI callers.** In `run_cmds.py` add:

```python
def _report_accept(config, stored: int, skipped: list[tuple[str, str]]) -> int:
    """Print the accept outcome; nonzero when anything was skipped (review
    2026-07-06 finding 30: a partial accept is a failure CI must see)."""
    print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} test(s):")
        for model_id, reason in skipped:
            print(f"  {model_id}: {reason}")
        return 1
    return 0
```

`cmd_run`'s accept branch becomes (keep its finding-30 comment about `tests` vs `scope_tests`):

```python
    if args.accept:
        from ..storage.accept import accept_run_results

        stored, skipped = accept_run_results(store, tests, results, config.work_dir)
        return _report_accept(config, stored, skipped)
```

`cmd_compare`'s accept branch keeps its first comment paragraph (why read_last_results already forced failed runs to `success=False`) and becomes:

```python
    if getattr(args, "accept", False):
        from ..storage.accept import accept_run_results

        stored, skipped = accept_run_results(store, tests, results, config.work_dir)
        return _report_accept(config, stored, skipped)
```

- [ ] **Step 6: Run** — `uv run pytest tests/test_accept.py tests/test_compare_accept.py tests/test_cli.py tests/test_storage.py -q`. Expected: PASS. Gates.

- [ ] **Step 7: Commit** (paths: `src/dstf/backends/base.py src/dstf/storage/batch_manifest.py src/dstf/storage/accept.py src/dstf/cli/run_cmds.py tests/test_accept.py tests/test_compare_accept.py tests/test_cli.py`). Message: `feat: one accept function carries the aborted-run guard for run, compare and the tool`

---

### Task 6: The per-test comparator, the recorded-backend rule, and the figure assembly

Three extractions the Session calls; each keeps its caller byte-for-byte in behaviour.

**Files:**
- Modify: `src/dstf/comparison/comparator.py:510-605`
- Modify: `src/dstf/cli/run_cmds.py:140-160, 395-462` and `src/dstf/backends/progress.py`, `src/dstf/backends/__init__.py`
- Modify: `src/dstf/reporting/context_builder.py:276` (+ its caller in `_build_template_context`), `src/dstf/reporting/report_suite.py:141-193`
- Create: `src/dstf/figures/assemble.py`
- Test: `tests/test_comparator.py`, `tests/test_cli.py:1132-1230`, `tests/test_capability_gating.py`, `tests/test_interactive_report.py`, `tests/test_figures.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_comparator.py` (the module has `_NoRefStore` / `_StoreWithRef` stand-ins around line 454; reuse the one that returns a reference for the last case):

```python
class TestCompareOne:
    """compare_all's per-test body, callable for one test. `_mk_test`,
    `_mk_result`, `_make_test`, `_make_result_and_ref` and `_NoRefStore` are
    this module's helpers (model ids "MyLib.Test" and "Test.Model")."""

    def test_no_result_is_sim_fail(self):
        from dstf.comparison.comparator import compare_one

        comp = compare_one(_mk_test(), None, _NoRefStore())
        assert comp.sim_success is False
        assert comp.error_message == "No simulation results found"

    def test_failed_result_is_sim_fail_before_the_baseline_check(self):
        from dstf.comparison.comparator import compare_one

        result = TestResult(model_id="MyLib.Test", success=False, error_message="boom")
        comp = compare_one(_mk_test(), result, _NoRefStore())
        assert comp.sim_success is False and comp.error_message == "boom"

    def test_no_baseline_is_no_ref(self):
        from dstf.comparison.comparator import compare_one

        comp = compare_one(_mk_test(), _mk_result(), _NoRefStore())
        assert comp.has_reference is False and comp.passed is True
        assert not comp.evaluated

    def test_compare_all_is_compare_one_per_test(self):
        from dstf.comparison.comparator import compare_all, compare_one

        scored_result, ref = _make_result_and_ref(offset=0.5)  # a real FAIL

        class _Store:
            def get_reference(self, model_id):
                return ref if model_id == "Test.Model" else None

            def get_soft_checks(self, model_id):
                return {}

        tests = [_make_test(), _mk_test(model_id="MyLib.Test"), _mk_test(model_id="MyLib.Gone")]
        results = {"Test.Model": scored_result, "MyLib.Test": _mk_result()}
        store = _Store()
        fields = ("model_id", "passed", "sim_success", "has_reference", "error_message")

        def key(c):
            return tuple(getattr(c, f) for f in fields)

        assert [key(c) for c in compare_all(tests, results, store)] == [
            key(compare_one(t, results.get(t.model_id), store)) for t in tests
        ]
        assert [key(c)[1:3] for c in compare_all(tests, results, store)] == [
            (False, True), (True, True), (False, False)
        ]
``` In `tests/test_cli.py` change the eight `_recorded_backend(...)` calls: `from dstf.backends.progress import recorded_backend` and `recorded_backend(work)[0]` where a label was asserted; the two tests that check the stderr warning through `capsys` after calling the function directly now assert on the returned warning string instead (`label, warning = recorded_backend(work); assert label is None and "predates the Backend rename" in warning`); the CLI-driven ones (`test_stale_work_dir_without_backend_name_warns` if it drives `main`) keep their `capsys` assertion, since `cmd_compare` still prints it. In `tests/test_capability_gating.py` the two uses (lines ~122, 128) call `cli_mod._dataset_kind_for(...)` through the module attribute — change them to `dataset_kind_for(...)` imported from `dstf.backends`. In `tests/test_interactive_report.py` change `_build_trajectories` to `build_trajectories`. Add to `tests/test_figures.py`:

```python
def test_view_from_trajectory_reads_the_report_shape():
    from dstf.figures.assemble import view_from_trajectory
    traj = {"act_time": [0, 1], "act_values": [1, 2], "ref_time": [0, 1], "ref_values": [1, 2],
            "overlays": [{"name": "s", "role": "soft_check", "kind": "json", "time": [0, 1], "values": [1, 1]}]}
    view = view_from_trajectory("x", traj, [])
    assert view.name == "x" and list(view.act_values) == [1, 2] and len(view.overlays) == 1
    assert view_from_trajectory("x", {}, []).act_time is None


def test_leaves_by_variable_groups_stamped_leaves():
    from dstf.comparison.metric_tree import implicit_and_tree
    from dstf.figures.assemble import leaves_by_variable

    t = np.linspace(0.0, 1.0, 11)
    vcs = [_compare_trajectories(t, np.sin(t), t, np.sin(t) + 0.01, 0.1) for _ in range(3)]
    vcs[0].name, vcs[1].name, vcs[2].name = "x", "y", "x"
    tree = implicit_and_tree(vcs)  # stamps /metrics/children/{i}

    grouped = leaves_by_variable(tree)
    assert set(grouped) == {"x", "y"}
    assert [path for path, _ in grouped["x"]] == ["/metrics/children/0", "/metrics/children/2"]
    assert all(vc.name == name for name, pairs in grouped.items() for _, vc in pairs)
    assert leaves_by_variable(None) == {}
```

(Add `_compare_trajectories` — the nrmse comparison, `algorithms.py:164` — to the `dstf.comparison.algorithms` import at the top of `test_figures.py`.)

- [ ] **Step 2: Run** — `uv run pytest tests/test_comparator.py::TestCompareOne tests/test_figures.py tests/test_cli.py tests/test_capability_gating.py -q`. Expected: FAIL on the new names.

- [ ] **Step 3: `compare_one`.** In `comparator.py`, above `compare_all`:

```python
def compare_one(
    test: TestModel,
    result: TestResult | None,
    store: ReferenceStore,
    default_tolerance: float = DEFAULT_TOLERANCE,
    default_points: bool = False,
    dataset_kind: DatasetType = DatasetType.TIME_SERIES,
) -> TestComparison:
    """One test's comparison, short-circuits included: no result and a
    failed simulation are SIM_FAIL; no baseline is NO_REF unless the test is
    simulate-only or every leaf scores without a reference. ``compare_all``
    is this per test; the authoring Session's rescore is this for one."""
    if result is None:
        return TestComparison(
            model_id=test.model_id,
            passed=False,
            sim_success=False,
            error_message="No simulation results found",
        )
    # ... the existing loop body from `if not result.success:` to the final
    #     `comparisons.append(comp)`, with every `comparisons.append(X); continue`
    #     becoming `return X` and the final append becoming `return comp`;
    #     the two comments (OM SIM_FAIL ordering; simulate_only / baseline-free)
    #     move with it ...
```

`compare_all` keeps its stderr line and becomes:

```python
    print(f"Comparing {len(tests)} tests against references...", file=_sys.stderr)
    return [
        compare_one(
            test,
            results.get(test.model_id),
            store,
            default_tolerance=default_tolerance,
            default_points=default_points,
            dataset_kind=dataset_kind,
        )
        for test in tests
    ]
```

- [ ] **Step 4: `recorded_backend` and `dataset_kind_for`.** Move `_recorded_backend` from `run_cmds.py` to `backends/progress.py` as:

```python
def recorded_backend(work_dir: Path) -> tuple[str | None, str | None]:
    """``(label, warning)``: the backend the last run in *work_dir* used, per
    ``status.json``'s metadata, and a warning for the caller to show when the
    snapshot predates run provenance.

    ``label`` is ``None`` when the work dir has no snapshot, an unreadable
    one, or one written before provenance was stamped (``metadata: null``).
    ``warning`` is set only when metadata exists but names no backend —
    written before the Backend rename (D123), when the key was "simulator".
    The guard this function exists for (D120) cannot fire then, so
    ``compare`` would fall back to environment auto-detect and could score,
    say, an OpenModelica work dir against the Dymola partition. Reading the
    old key would be a compatibility path; saying so is not.
    """
    try:
        snap = json.loads((work_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    meta = snap.get("metadata") or {}
    label = meta.get("backend_name")
    if isinstance(label, str) and label:
        return label, None
    if meta:
        return None, (
            f"Warning: {work_dir / 'status.json'} predates the Backend rename "
            "and does not record which backend produced it. Pass --backend "
            "explicitly, or re-run instead of comparing."
        )
    return None, None
```

In `cmd_compare` (lines ~438-462) replace `recorded = _recorded_backend(config.work_dir)` with:

```python
        recorded, warning = recorded_backend(config.work_dir)
        if warning:
            print(warning, file=sys.stderr)
```

and keep the rest of that block as is. Move `_dataset_kind_for` from `run_cmds.py` to `backends/__init__.py` as `dataset_kind_for(backend)` (same body and docstring; it needs `DatasetType` from `.types`), and update the three call sites in `run_cmds.py` (`grep -n _dataset_kind_for src/dstf/cli/run_cmds.py`) plus the docstring mention in `src/dstf/comparison/comparator.py:521`. `grep -rn "_recorded_backend\|_dataset_kind_for" src tests` is then empty.

- [ ] **Step 5: `build_trajectories` and `figures/assemble.py`.** Rename `_build_trajectories` to `build_trajectories` in `context_builder.py` (definition + its one caller in `_build_template_context`) and in the docstring at `src/dstf/comparison/tree_eval.py:458`. Create `src/dstf/figures/assemble.py`:

```python
"""From a comparison and a trajectory to a VariableView — the assembly the
report's figure build and the authoring Session's ``view`` share."""

from __future__ import annotations

from collections.abc import Sequence

from ..comparison.types import VariableComparison
from .view import VariableView, build_view

LeafPair = tuple[str, VariableComparison]


def leaves_by_variable(metric_tree) -> dict[str, list[LeafPair]]:
    """Every scored leaf, keyed by the variable it scores, as the
    ``(leaf_path, comparison)`` pairs ``build_view`` takes. Empty for
    ``None``."""
    from ..comparison.tree_eval import leaf_comparisons_by_path

    out: dict[str, list[LeafPair]] = {}
    if metric_tree is None:
        return out
    for path, vc in leaf_comparisons_by_path(metric_tree).items():
        out.setdefault(vc.name or f"x[{vc.index}]", []).append((path, vc))
    return out


def view_from_trajectory(
    name: str, trajectory: dict, leaves: Sequence[LeafPair]
) -> VariableView:
    """The view for one variable from a report-shaped trajectory entry
    (``act_time`` / ``act_values`` / ``ref_time`` / ``ref_values`` /
    ``overlays``, any of them absent)."""
    return build_view(
        name,
        act_time=trajectory.get("act_time"),
        act_values=trajectory.get("act_values"),
        ref_time=trajectory.get("ref_time"),
        ref_values=trajectory.get("ref_values"),
        overlays=trajectory.get("overlays") or [],
        leaves=leaves,
    )
```

In `report_suite._build_figures`, replace the `by_path` / `leaves_by_var` block and the per-variable `build_view(...)` call with `leaves_by_var = leaves_by_variable(metric_tree)` and `view = view_from_trajectory(name, var.get("trajectory") or {}, leaves_by_var.get(name, []))`; the diagnostic and no-baseline branches keep their direct `build_view` calls (their keys differ).

- [ ] **Step 6: Run** — `uv run pytest tests/test_comparator.py tests/test_cli.py tests/test_capability_gating.py tests/test_figures.py tests/test_figure_goldens.py tests/test_report_suite.py tests/test_report_size_budget.py -q -m "not playwright"`. Expected: PASS; the figure goldens are unchanged (`git status tests/golden` clean). Gates.

- [ ] **Step 7: Commit** (paths: `src/dstf/comparison/comparator.py src/dstf/cli/run_cmds.py src/dstf/backends/progress.py src/dstf/backends/__init__.py src/dstf/reporting/context_builder.py src/dstf/reporting/report_suite.py src/dstf/figures/assemble.py tests/test_comparator.py tests/test_cli.py tests/test_capability_gating.py tests/test_interactive_report.py tests/test_figures.py`). Message: `refactor: compare_one, recorded_backend, dataset_kind_for and the figure assembly are library calls`

---

### Task 7: `PatchOp` and `SpecDocument`

**Files:**
- Create: `src/dstf/authoring/__init__.py`, `src/dstf/authoring/spec_document.py`
- Test: `tests/test_spec_document.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/test_spec_document.py`:

```python
"""The spec as the Session holds it: loaded document, content hash, pending ops."""

import json

import pytest

from dstf.authoring.spec_document import PatchOp, SpecDocument, content_hash
from dstf.config import ConfigError
from dstf.discovery.patch_apply import PatchError

ENTRY = {"model": "M", "variables": ["x"], "comparison": {"tolerance": 0.1}}


def _spec(tmp_path, *entries):
    p = tmp_path / "test_spec.json"
    p.write_text(json.dumps({"tests": list(entries)}, indent=2) + "\n", encoding="utf-8")
    return p


def test_load_keeps_the_document_and_its_hash(tmp_path):
    p = _spec(tmp_path, ENTRY)
    doc = SpecDocument.load(p)
    assert doc.loaded == {"tests": [ENTRY]}
    assert doc.content_hash == content_hash(p) and doc.pending == []
    assert not doc.on_disk_changed()


def test_a_missing_file_is_the_empty_document(tmp_path):
    doc = SpecDocument.load(tmp_path / "absent.json")
    assert doc.loaded == {"tests": []} and doc.content_hash is None
    assert not doc.on_disk_changed()
    (tmp_path / "absent.json").write_text("{}", encoding="utf-8")
    assert doc.on_disk_changed()


def test_malformed_json_is_a_config_error(tmp_path):
    p = tmp_path / "test_spec.json"
    p.write_text('{"tests": [', encoding="utf-8")
    with pytest.raises(ConfigError, match="test_spec.json"):
        SpecDocument.load(p)


def test_effective_applies_pending_without_touching_loaded(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "replace", "/comparison/tolerance", 0.5))
    eff = doc.effective()
    assert eff["tests"][0]["comparison"]["tolerance"] == 0.5
    assert doc.loaded["tests"][0]["comparison"]["tolerance"] == 0.1
    assert doc.entry("M")["comparison"]["tolerance"] == 0.5


def test_grouped_keeps_first_seen_model_order_and_op_order(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY, {"model": "N", "variables": ["y"]}))
    doc.pending += [
        PatchOp("N", "add", "/comparison", {"tolerance": 1.0}),
        PatchOp("M", "replace", "/comparison/tolerance", 0.5),
        PatchOp("N", "replace", "/comparison/tolerance", 2.0),
    ]
    assert doc.grouped() == [
        ("N", [{"op": "add", "path": "/comparison", "value": {"tolerance": 1.0}},
               {"op": "replace", "path": "/comparison/tolerance", "value": 2.0}]),
        ("M", [{"op": "replace", "path": "/comparison/tolerance", "value": 0.5}]),
    ]


def test_remove_op_carries_no_value():
    assert PatchOp("M", "remove", "/comparison/tolerance").raw() == {"op": "remove", "path": "/comparison/tolerance"}


def test_a_bad_pending_op_surfaces_from_effective(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "replace", "/simulation/stop_time", 3))
    with pytest.raises(PatchError):
        doc.effective()


def test_on_disk_changed_after_a_write(tmp_path):
    p = _spec(tmp_path, ENTRY)
    doc = SpecDocument.load(p)
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert doc.on_disk_changed()
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_spec_document.py -q`. Expected: FAIL, `ModuleNotFoundError: dstf.authoring`.

- [ ] **Step 3: Create the package.** `src/dstf/authoring/__init__.py`:

```python
"""The authoring tier's core — the Session, its spec document and the
capability contract (two-tier design, §4.5–4.6). Pure Python: no UI
framework is imported here; sub-project 4's shell sits on top."""
```

`src/dstf/authoring/spec_document.py`:

```python
"""The spec as the Session holds it: the document as loaded, its content
hash, and the ops staged against it."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..discovery.patch_apply import apply_patch_to_document


@dataclass(frozen=True)
class PatchOp:
    """One RFC 6902 op against one model's spec entry. ``path`` is relative
    to the entry, as ``apply_patch`` reads it."""

    model_id: str
    op: str  # "add" | "replace" | "remove"
    path: str
    value: Any = None

    def raw(self) -> dict:
        """The op as ``apply_patch`` and the envelope carry it."""
        raw: dict = {"op": self.op, "path": self.path}
        if self.op != "remove":
            raw["value"] = copy.deepcopy(self.value)
        return raw


def content_hash(path: Path) -> str | None:
    """SHA-256 of the file's bytes; ``None`` when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


@dataclass
class SpecDocument:
    path: Path
    loaded: dict  # as read; never mutated
    content_hash: str | None  # of the bytes ``loaded`` came from
    pending: list[PatchOp] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> SpecDocument:
        """Read ``path``. A missing file is the empty document; a malformed
        one is a :class:`ConfigError` — the Session must not save over a
        file it could not read."""
        digest = content_hash(path)
        if digest is None:
            return cls(path=path, loaded={"tests": []}, content_hash=None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ConfigError(f"{path}: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: spec root must be an object")
        data.setdefault("tests", [])
        return cls(path=path, loaded=data, content_hash=digest)

    def on_disk_changed(self) -> bool:
        return content_hash(self.path) != self.content_hash

    def grouped(self) -> list[tuple[str, list[dict]]]:
        """Pending ops as ``(model_id, raw ops)`` groups: models in
        first-seen order, ops in staging order."""
        groups: dict[str, list[dict]] = {}
        for op in self.pending:
            groups.setdefault(op.model_id, []).append(op.raw())
        return list(groups.items())

    def effective(self) -> dict:
        """A copy of ``loaded`` with every pending op applied."""
        data = copy.deepcopy(self.loaded)
        for model_id, ops in self.grouped():
            apply_patch_to_document(data, model_id, ops)
        return data

    def entry(self, model_id: str) -> dict | None:
        """The model's entry in the effective document."""
        from ..discovery.spec_update import spec_entry

        return spec_entry(self.effective(), model_id)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_spec_document.py -q`. Expected: PASS. Gates (mypy does not see `authoring/` yet; Task 11 adds it — run `uv run mypy src/dstf/authoring` once here anyway and fix what it reports in these files).

- [ ] **Step 5: Commit** (paths: `src/dstf/authoring/__init__.py src/dstf/authoring/spec_document.py tests/test_spec_document.py`). Message: `feat: the authoring package — PatchOp and the SpecDocument with its content hash`

---

### Task 8: The capability contract and registry

**Files:**
- Create: `src/dstf/authoring/capabilities.py`
- Test: `tests/test_capabilities.py` (new)

- [ ] **Step 1: Write the failing tests** — create `tests/test_capabilities.py` (the plug-in half mirrors `tests/test_plugins.py`'s `_FakeEntryPoint` and `_serve`; read that module first and copy the two helpers rather than importing test-private names):

```python
"""The capability contract: the registry is loud on a duplicate name, keeps
registration order, and a plug-in module registers through `dstf.plugins`
exactly as it registers a metric."""

import dataclasses
import importlib.metadata

import pytest

from dstf import plugins
from dstf.authoring import capabilities as caps
from dstf.authoring.capabilities import (
    Artifact, Capability, Job, JobContext, OpenProject, Patch, Scope, StoreWrite,
    register_capability, registered_capabilities,
)


@pytest.fixture(autouse=True)
def _clean():
    # Before AND after: cli/main.py calls load_plugins() in earlier test
    # modules, and its idempotence guard would otherwise skip our entry point.
    caps._reset_for_tests()
    plugins._reset_for_tests()
    yield
    caps._reset_for_tests()
    plugins._reset_for_tests()


class _Dummy:
    def __init__(self, name="dummy", scope=Scope.TESTS):
        self.name, self.label, self.scope = name, name.title(), scope

    def available(self, s):
        return True

    def panel(self, s, ui):
        pass

    def act(self, s, **inputs):
        return None


def test_registration_order_is_kept_and_a_duplicate_name_is_loud():
    a, b = _Dummy("a"), _Dummy("b")
    assert register_capability(a) is a
    register_capability(b)
    assert registered_capabilities() == (a, b)
    with pytest.raises(ValueError, match="capability 'a' is already registered"):
        register_capability(_Dummy("a"))


def test_the_protocol_is_checkable():
    assert isinstance(_Dummy(), Capability)
    assert not isinstance(object(), Capability)


def test_a_plugin_registers_through_the_entry_point_group(monkeypatch):
    """The entry point's load() returns a callable that registers — the
    registration happens INSIDE load_plugins(), so a load_plugins that never
    reaches the entry point fails this test."""

    def register_mine():
        register_capability(_Dummy("from-plugin", Scope.LEAF))

    class _EP:
        name = "fake"

        def load(self):
            return register_mine

    assert not any(c.name == "from-plugin" for c in registered_capabilities())

    def fake_entry_points(*, group=None):
        return [_EP()] if group == plugins.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    plugins.load_plugins()
    assert any(c.name == "from-plugin" for c in registered_capabilities())


def test_outcomes_are_frozen_and_carry_their_models():
    sw = StoreWrite(run=lambda store: None, models=frozenset({"M"}))
    job = Job(run=lambda ctx: None, label="rerun", models=frozenset({"M"}))
    assert job.reloads_results is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.label = "x"
    assert Patch(ops=[]).ops == [] and Artifact(paths=[], label="png").label == "png"
    assert OpenProject(config_path=__import__("pathlib").Path("x")).config_path.name == "x"
    assert JobContext.__dataclass_fields__.keys() >= {"project", "models"}
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_capabilities.py -q`. Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/dstf/authoring/capabilities.py`:**

```python
"""The capability contract (two-tier design, §4.6).

A capability is a plug-in the authoring shell lists by scope: it reads a
:class:`SessionView`, builds its controls in a :class:`Panel` the shell
provides, and returns an :class:`Outcome`. **The Session applies outcomes;
a capability applies nothing.** It holds a view only, so the enforcement
is structural. Registration goes through the single ``dstf.plugins``
entry-point group: a plug-in module calls :func:`register_capability` on
import exactly as it calls ``register_metric``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..storage.reference_store import ReferenceStore
    from .session import Project, SessionView
    from .spec_document import PatchOp


class Scope(Enum):
    PROJECT = "project"
    TESTS = "tests"
    VARIABLE = "variable"
    LEAF = "leaf"


@dataclass(frozen=True)
class JobContext:
    """What a job's callable receives: the open project and the models the
    job locked. Sub-project 5's run callable reads its config and tests
    from here."""

    project: Project
    models: frozenset[str]


@dataclass(frozen=True)
class Patch:
    """Ops to stage; the Session rescores their models."""

    ops: list[PatchOp]


@dataclass(frozen=True)
class StoreWrite:
    """A write against the reference store; results reload afterwards.
    ``models`` names what it touches, so a locked model can refuse it."""

    run: Callable[[ReferenceStore], None]
    models: frozenset[str]


@dataclass(frozen=True)
class Job:
    """Work on a worker thread. The Session locks ``models`` while it runs
    and, unless ``reloads_results`` is false, reloads results and rescores
    them when it completes."""

    run: Callable[[JobContext], None]
    label: str
    models: frozenset[str]
    reloads_results: bool = True


@dataclass(frozen=True)
class Artifact:
    """Something written outside the spec and the store; shown as a link."""

    paths: list[Path]
    label: str


@dataclass(frozen=True)
class OpenProject:
    config_path: Path


Outcome = Patch | StoreWrite | Job | Artifact | OpenProject | None


class FigureHook(Protocol):
    """The shell's centre figure, as a capability may subscribe to it."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None: ...


class Panel(Protocol):
    """A container the capability owns entirely (sub-project 4 provides the
    NiceGUI one). ``figure`` is the hook on-plot editing will use first."""

    figure: FigureHook


@runtime_checkable
class Capability(Protocol):
    name: str
    label: str
    scope: Scope

    def available(self, s: SessionView) -> bool | str:
        """``True``, or ``False`` / the reason it is greyed out."""
        ...

    def panel(self, s: SessionView, ui: Panel) -> None: ...

    def act(self, s: SessionView, **inputs: Any) -> Outcome | Sequence[Outcome]: ...


_CAPABILITIES: dict[str, Capability] = {}


def register_capability(cap: Capability) -> Capability:
    """Register ``cap`` under its name; returns it. Loud on a duplicate —
    two capabilities sharing a name would make the shell's tab list depend
    on import order."""
    if cap.name in _CAPABILITIES:
        raise ValueError(f"capability {cap.name!r} is already registered")
    _CAPABILITIES[cap.name] = cap
    return cap


def registered_capabilities() -> tuple[Capability, ...]:
    """Every registered capability, in registration order."""
    return tuple(_CAPABILITIES.values())


def _reset_for_tests() -> None:
    _CAPABILITIES.clear()
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_capabilities.py tests/test_plugins.py -q`. Expected: PASS. `uv run mypy src/dstf/authoring` clean (the `Outcome` union alias and the `Protocol` with attributes are fine on 3.10; `X | Y` in the alias needs the `from __future__ import annotations` — it is a runtime expression, so if mypy or the interpreter objects, write it as `Union[...]` from `typing`). Gates.

- [ ] **Step 5: Commit** (paths: `src/dstf/authoring/capabilities.py tests/test_capabilities.py`). Message: `feat: the capability contract — scopes, outcomes, the protocol and its registry`

---

### Task 9: The Session — open, score, view, stage, discard, export, save

**Files:**
- Create: `src/dstf/authoring/session.py`
- Test: `tests/test_session.py` (new)

- [ ] **Step 1: The fixture.** Create `tests/test_session.py` with this header. It builds a Python-backend project on disk with two flat-override tests, a work dir whose manifest and `result.json` files stand in for a finished run, and a baseline for the first test. `read_last_results` reads exactly these files (the Python backend's `read_result` opens `<work_dir>/<test_key>/result.json`), so nothing runs.

```python
"""The Session (two-tier design §4.5, tests per §6 "The Session"): pure
Python against a Python-backend project whose run is on disk."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import make_test_model

from dstf.authoring.session import Session, StaleSpecError
from dstf.authoring.spec_document import PatchOp
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.discovery.spec_update import implicit_tree_op, materialize_implicit_metrics
from dstf.reporting.status import FAIL, NO_REF, PASS, SIM_FAIL, classify
from dstf.storage.reference_store import ReferenceStore

A, B, C, D, G = "FakeLib.A", "FakeLib.B", "FakeLib.C", "FakeLib.D", "FakeLib.G"


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """testing.json + work dir. A: baseline x=t, result x=1.25t — nrmse 0.153,
    FAIL at the config tolerance 0.01. B: no baseline (NO_REF). C: no result
    (SIM_FAIL). D: baseline x=t, result x=1.001t — nrmse 6e-4, PASS at 0.01
    and FAIL at the 1e-4 default, so a scorer that drops config.tolerance is
    caught. G: like A but tracked as ["*"] — a glob spec (Conventions #7)."""
    lib = tmp_path / "FakeLib"
    (lib / "Examples").mkdir(parents=True)
    for short in ("A", "B", "C", "D", "G"):
        (lib / "Examples" / f"{short}.py").write_text(
            "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
        )
    refs = lib / "refs"
    refs.mkdir()
    (refs / "testing.json").write_text(json.dumps({
        "source_type": "python", "source_path": "..", "library_name": "FakeLib",
        "test_spec": "./test_spec.json", "reference_root": ".",
        "backend": "Python", "backends": {"Python": ["python3", "python"]},
        "tolerance": 0.01,
    }), encoding="utf-8")
    entries = [
        {"model": m, "source": f"../Examples/{m.rsplit('.', 1)[-1]}.py",
         "variables": ["*"] if m == G else ["x"],
         "simulation": {"stop_time": 1.0, "tolerance": 1e-6}}
        for m in (A, B, C, D, G)
    ]
    (refs / "test_spec.json").write_text(json.dumps({"tests": entries}, indent=2) + "\n", encoding="utf-8")

    work = tmp_path / "work"
    work.mkdir()
    t = [i / 4 for i in range(5)]
    manifest = {
        "test_0001": {"model_id": A, "ref_id": None, "last_run_status": "passed"},
        "test_0002": {"model_id": B, "ref_id": None, "last_run_status": "passed"},
        "test_0003": {"model_id": C, "ref_id": None, "last_run_status": "pending"},
        "test_0004": {"model_id": D, "ref_id": None, "last_run_status": "passed"},
        "test_0005": {"model_id": G, "ref_id": None, "last_run_status": "passed"},
    }
    (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for key, values in (
        ("test_0001", [v * 1.25 for v in t]),
        ("test_0002", t),
        ("test_0004", [v * 1.001 for v in t]),
        ("test_0005", [v * 1.25 for v in t]),
    ):
        (work / key).mkdir()
        (work / key / "result.json").write_text(
            json.dumps({"success": True, "time": t, "variables": {"x": values}}), encoding="utf-8"
        )
    (work / "status.json").write_text(json.dumps({"metadata": {"backend_name": "Python"}, "tests": []}), encoding="utf-8")

    config = Config(config_file=refs / "testing.json", work_dir=work)
    tt = np.asarray(t)
    store = ReferenceStore(config)
    for m in (A, D, G):
        store.store_reference(
            make_test_model(m, variable_patterns=["x"]),
            TestResult(model_id=m, success=True,
                       variables=[VariableResult(index=1, time=tt, values=tt, name="x")]),
        )
    return refs / "testing.json", work


def _snapshot(work: Path) -> list[tuple[str, bytes]]:
    return sorted((str(p.relative_to(work)), p.read_bytes()) for p in work.rglob("*") if p.is_file())


@pytest.fixture
def session(tmp_path) -> Session:
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


# The materialized leaf for A is {"metric": "nrmse", "variable": "x"} — no
# tolerance key (the entry has no variable_overrides), so the op is an add.
LOOSEN = PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)
```

- [ ] **Step 2: Write the failing tests for this task** — append:

```python
class TestOpen:
    def test_loaded_verdicts_match_the_cli_classifier(self, session):
        assert classify(session.comparisons[A]) == FAIL
        assert classify(session.comparisons[B]) == NO_REF
        assert classify(session.comparisons[C]) == SIM_FAIL
        assert classify(session.comparisons[D]) == PASS  # config tolerance 0.01, not the 1e-4 default
        assert classify(session.comparisons[G]) == PASS
        assert session.project.config.backend_name == "Python"
        assert [t.model_id for t in session.project.tests] == [A, B, C, D, G]
        assert session.spec.pending == []

    def test_open_writes_nothing_in_the_work_dir(self, tmp_path):
        cfg, work = _project(tmp_path)
        before = _snapshot(work)
        Session().open(cfg, work_dir=work)
        assert _snapshot(work) == before

    def test_the_recorded_backend_wins_over_the_config(self, tmp_path):
        cfg, work = _project(tmp_path)
        (work / "status.json").write_text(json.dumps({"metadata": {"backend_name": "Nonesuch"}, "tests": []}))
        from dstf.config import ConfigError
        with pytest.raises(ConfigError, match="Nonesuch"):
            Session().open(cfg, work_dir=work)


class TestRescoreAndStage:
    def test_rescore_on_a_flat_test_stages_the_materialization_spec_update_would(self, session):
        entry = session.spec.entry(A)
        assert "metrics" not in entry
        session.rescore(A)
        assert session.spec.pending == [PatchOp(A, "add", "/metrics", implicit_tree_op(entry)["value"])]
        assert session.spec.pending[0].raw() == materialize_implicit_metrics(entry, [LOOSEN.raw()])[0]
        # And again is a no-op: the tree is there now.
        session.rescore(A)
        assert len(session.spec.pending) == 1

    def test_rescore_verdict_equals_the_loaded_one_when_nothing_changed(self, session):
        assert classify(session.rescore(A)) == FAIL
        assert classify(session.rescore(D)) == PASS  # the tree path keeps config.tolerance too

    def test_a_glob_spec_rescores_without_staging(self, session):
        """Conventions #7: a leaf per pattern would score a `*` sentinel and
        flip G to FAIL; rescore scores it as loaded and stages nothing."""
        assert classify(session.rescore(G)) == PASS
        assert session.spec.pending == []
        assert "metrics" not in session.spec.entry(G)

    def test_a_missing_literal_rescores_without_staging(self, session):
        """A tracked name the result never resolved would become a sentinel
        leaf; the Session stages materialization only when the tree scores
        exactly what the flat path scored."""
        entry = dict(session.spec.entry(D))
        entry["variables"] = ["x", "ghost"]
        assert session._materializable(D, entry) is False
        assert session._materializable(D, session.spec.entry(D)) is True

    def test_rescore_reflects_a_staged_op(self, session):
        session.stage([LOOSEN])
        assert classify(session.comparisons[A]) == PASS
        assert classify(session.rescore(A)) == PASS
        assert [op.path for op in session.spec.pending] == ["/metrics", LOOSEN.path]

    def test_a_bad_op_leaves_the_session_unchanged(self, session):
        from dstf.discovery.patch_apply import PatchError
        with pytest.raises(PatchError):
            session.stage([PatchOp(A, "replace", "/simulation/stop_time", 3)])
        assert session.spec.pending == [] and classify(session.comparisons[A]) == FAIL

    def test_stage_emits_spec_then_results(self, session):
        seen = []
        session.on("spec", lambda e: seen.append(("spec", e["models"])))
        session.on("results", lambda e: seen.append(("results", e["models"])))
        session.stage([LOOSEN])
        assert seen == [("spec", {A}), ("results", {A})]

    def test_no_result_and_no_baseline_rescore_to_the_cli_states(self, session):
        assert classify(session.rescore(B)) == NO_REF
        assert classify(session.rescore(C)) == SIM_FAIL


class TestView:
    def test_a_scored_variable_carries_its_leaves(self, session):
        view = session.view(A, "x")
        assert view.ref_time is not None and len(view.leaves) == 1
        assert view.leaves[0].path.startswith("/metrics")
        fig = session.figure(A, "x")
        assert {"data", "layout"} <= fig.keys() and len(fig["data"]) >= 2

    def test_no_baseline_is_the_empty_state_with_the_trajectory(self, session):
        view = session.view(B, "x")
        assert view.act_time is not None and view.ref_time is None and view.leaves == ()

    def test_no_result_is_the_empty_state_without_a_trajectory(self, session):
        view = session.view(C, "x")
        assert view.act_time is None and view.leaves == ()
        assert "layout" in session.figure(C, "x")


class TestSaveDiscardExport:
    def test_save_applies_exactly_the_pending_ops_and_clears_them(self, session):
        session.stage([LOOSEN])
        expected = session.spec.effective()
        session.save()
        on_disk = json.loads(session.spec.path.read_text(encoding="utf-8"))
        assert on_disk == expected
        assert session.spec.pending == [] and not session.spec.on_disk_changed()
        assert on_disk["tests"][0]["metrics"]["children"][0]["tolerance"] == 0.5
        assert classify(session.comparisons[A]) == PASS
        assert b"\r\n" not in session.spec.path.read_bytes()

    def test_save_refuses_on_a_changed_hash(self, session):
        session.stage([LOOSEN])
        p = session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        before = p.read_bytes()
        with pytest.raises(StaleSpecError, match=str(p)):
            session.save()
        assert p.read_bytes() == before and len(session.spec.pending) == 2

    def test_reload_after_a_refused_save(self, session):
        session.stage([LOOSEN])
        p = session.spec.path
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        session.reload()
        assert session.spec.pending == [] and not session.spec.on_disk_changed()
        assert classify(session.comparisons[A]) == FAIL

    def test_discard_restores_the_loaded_verdicts(self, session):
        session.stage([LOOSEN])
        assert classify(session.comparisons[A]) == PASS
        session.discard()
        assert session.spec.pending == [] and classify(session.comparisons[A]) == FAIL
        assert "metrics" not in session.spec.entry(A)

    def test_export_is_the_multi_model_envelope(self, session):
        session.stage([LOOSEN, PatchOp(B, "add", "/comparison", {"tolerance": 0.2})])
        env = session.export()
        assert [g["model"] for g in env["patches"]] == [A, B]
        assert env["patches"][0]["patch"][-1] == LOOSEN.raw()
        assert "model" not in env
```

- [ ] **Step 3: Run** — `uv run pytest tests/test_session.py -q`. Expected: FAIL, `ModuleNotFoundError: dstf.authoring.session`.

- [ ] **Step 4: Create `src/dstf/authoring/session.py`** (Task 10 adds `apply`, the job thread, the locks and `SessionView`; write the file with these parts now):

```python
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
        super().__init__(f"{path} changed on disk since it was loaded; reload before saving")


class JobRunningError(RuntimeError):
    pass


class Session:
    def __init__(self, post: Callable[[Callable[[], None]], None] | None = None):
        """``post`` hands a job's completion to the UI loop; without one the
        completion runs on the worker thread (tests wait on ``RunningJob.done``)."""
        self.project: Project | None = None
        self.spec: SpecDocument = SpecDocument(Path("test_spec.json"), {"tests": []}, None)
        self.selection = Selection()
        self.running: RunningJob | None = None
        self.comparisons: dict[str, TestComparison] = {}
        self.artifacts: list[Artifact] = []
        self.warnings: list[str] = []
        self._post = post or (lambda fn: fn())
        self._listeners: dict[str, list[Listener]] = {}

    # -- project -----------------------------------------------------------

    def open(self, config_path: Path, *, backend: str | None = None, work_dir: Path | None = None) -> None:
        """Open the project ``config_path`` describes. Without an explicit
        ``backend`` the one the last run recorded wins, as ``compare`` does."""
        if self.running is not None:
            raise JobRunningError(f"{self.running.label} is still running")
        config_path = Path(config_path)
        config = Config(config_file=config_path, backend_name=backend, work_dir=work_dir)
        if backend is None:
            assert config.work_dir is not None  # resolved in __post_init__
            recorded, warning = recorded_backend(config.work_dir)
            if warning:
                self.warnings.append(warning)
            if recorded and recorded != config.backend_name:
                config = Config(config_file=config_path, backend_name=recorded, work_dir=work_dir)
        try:
            backend_obj = get_backend(config)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        store = ReferenceStore(config)
        spec = SpecDocument.load(config.spec_path)
        scanned = scan_source_tests(config)
        tests = self._merge(config, scanned, spec.effective())
        self.project = Project(
            config=config, store=store, backend=backend_obj, scanned=scanned, tests=tests,
            results={}, dataset_kind=dataset_kind_for(backend_obj),
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

    def select(self, *, tests: Iterable[str] | None = None, variable: str | None = None,
               leaf_path: str | None = None) -> None:
        self.selection = Selection(
            tests=frozenset(tests) if tests is not None else self.selection.tests,
            variable=variable, leaf_path=leaf_path,
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
        if entry is not None and "metrics" not in entry and self._materializable(model_id, entry):
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
        from ..reporting.overlay_loader import attach_overlays_to_trajectories, load_overlays

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
                {"act_time": nb["time"], "act_values": nb["values"], "overlays": nb["overlays"]}
                if nb is not None
                else {}
            )
        leaves = leaves_by_variable(comp.metric_tree if comp is not None else None).get(variable, [])
        return view_from_trajectory(variable, traj, leaves)

    def figure(self, model_id: str, variable: str) -> dict:
        from ..figures.plotly import variable_figure

        project = self._require_project()
        return variable_figure(self.view(model_id, variable), project.config.max_embedded_samples)

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
            staged += [PatchOp(model_id, r["op"], r["path"], r.get("value")) for r in full]
        candidate = SpecDocument(self.spec.path, self.spec.loaded, self.spec.content_hash,
                                 [*self.spec.pending, *staged])
        effective = candidate.effective()  # PatchError on a bad path
        for model_id in models:
            validate_entry_metric_tree(effective, model_id, project.config)  # PatchError
        tests = self._merge(project.config, project.scanned, effective)  # ConfigError on a bad tree
        self.spec = candidate
        project.tests = tests
        self._emit("spec", models=models)
        self._score_all(models)

    def discard(self) -> None:
        """Drop every pending op and restore the loaded verdicts."""
        models = {op.model_id for op in self.spec.pending}
        self.spec = SpecDocument(self.spec.path, self.spec.loaded, self.spec.content_hash)
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

    def _merge(self, config: Config, scanned: dict[str, TestModel], document: dict) -> list[TestModel]:
        models = spec_models_from_document(
            document, base_dir=config.spec_path.parent, label=f"{config.spec_path} (in memory)"
        )
        return merge_tests(scanned, spec_tests_by_model(models))

    def _rebuild_tests(self, models: set[str]) -> None:
        project = self._require_project()
        project.tests = self._merge(project.config, project.scanned, self.spec.effective())
        self._emit("spec", models=models)
        self._score_all(models)

    def _score(self, model_id: str) -> TestComparison:
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        tolerance = project.config.tolerance
        comp = compare_one(
            test, project.results.get(model_id), project.store,
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
```

Notes for the implementer: `_score_all` iterates `project.tests` so verdict dictionaries keep discovery order; `discard` rebuilds from the loaded document so a materialized tree vanishes with the ops that needed it; `stage` refuses the whole batch when one model is locked, so a capability cannot half-apply; `open` and `close` refuse while a job runs (the job's completion would reload a project it never ran against). `select(tests=...)` replaces `variable` and `leaf_path` with what is passed (a new test selection has no leaf); the shell decides what to redraw. `DEFAULT_TOLERANCE` is whatever `comparator.py` already imports for its own default — import it from there or from its source. The test `test_a_missing_literal_rescores_without_staging` reaches `_materializable` directly because no whitelisted op can put an unresolved name into `variables`.

- [ ] **Step 5: Run** — `uv run pytest tests/test_session.py -q`. Expected: PASS. If `test_the_recorded_backend_wins_over_the_config` fails because `Config` rejects an unknown backend before `get_backend` does, keep whichever exception carries the name and adjust the `match` — the claim is that the recorded label is what gets validated. `uv run mypy src/dstf/authoring`; gates.

- [ ] **Step 6: Commit** (paths: `src/dstf/authoring/session.py tests/test_session.py`). Message: `feat: the Session — open, rescore, view, stage, discard, export, save with the hash check`

---

### Task 10: Outcomes, the job thread, the lock set, and `SessionView`

**Files:**
- Modify: `src/dstf/authoring/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_session.py`, and extend its imports with `from dstf.authoring.capabilities import Artifact, Job, OpenProject, Patch, StoreWrite`, `from dstf.authoring.session import JobRunningError, LockedError, SessionView`, and `import threading`:

```python
def _started(session):
    """Capture the RunningJob from the started event — reading
    `session.running` after apply() races a job that completes at once."""
    jobs = []
    session.on("job", lambda e: jobs.append(e["job"]) if e["state"] == "started" else None)
    return jobs


class _Gate:
    """A job that blocks until released, so the lock set can be observed."""

    def __init__(self):
        self.release = threading.Event()
        self.seen = None

    def __call__(self, ctx):
        self.seen = ctx
        assert self.release.wait(5)


class TestOutcomes:
    """Each Outcome kind reaches its effect and no other."""

    def test_patch_stages_and_touches_nothing_else(self, session, tmp_path):
        before = session.spec.path.read_bytes()
        session.apply(Patch(ops=[LOOSEN]))
        assert [op.path for op in session.spec.pending] == ["/metrics", LOOSEN.path]
        assert session.spec.path.read_bytes() == before and session.running is None
        assert session.project.store.get_reference(B) is None

    def test_store_write_runs_against_the_store_and_reloads_results(self, session):
        calls = []
        seen = []
        session.on("results", lambda e: seen.append(e["models"]))
        session.apply(StoreWrite(run=lambda store: calls.append(store), models=frozenset({B})))
        assert calls == [session.project.store]
        assert session.spec.pending == [] and session.running is None
        assert seen and seen[-1] == {A, B, C}

    def test_job_runs_on_a_thread_locks_its_models_and_reloads_on_completion(self, session):
        gate = _Gate()
        events = []
        session.on("job", lambda e: events.append(e["state"]))
        work = session.project.config.work_dir

        def fix_a_then_wait(ctx):
            # What a rerun does: A's result on disk changes. Reloading must see it.
            t = [i / 4 for i in range(5)]
            (work / "test_0001" / "result.json").write_text(
                json.dumps({"success": True, "time": t, "variables": {"x": t}}), encoding="utf-8"
            )
            gate(ctx)

        session.apply(Job(run=fix_a_then_wait, label="rerun", models=frozenset({A})))
        assert session.running is not None and session.running.models == {A}
        assert events == ["started"]
        with pytest.raises(LockedError, match="rerun is running"):
            session.stage([LOOSEN])
        with pytest.raises(LockedError):
            session.rescore(A)
        with pytest.raises(LockedError):
            session.apply(StoreWrite(run=lambda s: None, models=frozenset({A, B})))
        session.stage([PatchOp(B, "add", "/comparison", {"tolerance": 0.2})])  # B is free
        with pytest.raises(JobRunningError):
            session.apply(Job(run=gate, label="second", models=frozenset({B})))
        job = session.running
        gate.release.set()
        assert job.done.wait(5)
        assert session.running is None and job.error is None
        assert gate.seen.models == {A} and gate.seen.project is session.project
        assert events == ["started", "finished"]
        assert classify(session.comparisons[A]) == PASS  # the reload read the rewritten result

    def test_job_failure_is_recorded_and_unlocks(self, session):
        jobs = _started(session)

        def boom(ctx):
            raise RuntimeError("driver died")

        session.apply(Job(run=boom, label="rerun", models=frozenset({A})))
        (job,) = jobs
        assert job.done.wait(5)
        assert session.running is None and "driver died" in str(job.error)
        session.stage([LOOSEN])  # unlocked again

    def test_job_without_reload_leaves_results_alone(self, session):
        jobs = _started(session)
        seen = []
        session.on("results", lambda e: seen.append(e["models"]))
        session.apply(Job(run=lambda ctx: None, label="export", models=frozenset(), reloads_results=False))
        (job,) = jobs
        assert job.done.wait(5)
        assert seen == [] and session.running is None

    def test_open_and_close_refuse_while_a_job_runs(self, session, tmp_path):
        gate = _Gate()
        jobs = _started(session)
        session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        with pytest.raises(JobRunningError):
            session.close()
        with pytest.raises(JobRunningError):
            session.apply(OpenProject(config_path=session.project.config.config_file))
        gate.release.set()
        assert jobs[0].done.wait(5)

    def test_completion_goes_through_post(self, tmp_path):
        cfg, work = _project(tmp_path)
        posted = []
        s = Session(post=posted.append)
        s.open(cfg, work_dir=work)
        s.apply(Job(run=lambda ctx: None, label="rerun", models=frozenset({A})))
        deadline = threading.Event()
        for _ in range(50):
            if posted:
                break
            deadline.wait(0.1)
        assert len(posted) == 1 and s.running is not None  # not finished until the loop runs it
        posted[0]()
        assert s.running is None

    def test_artifact_is_recorded_and_nothing_reloads(self, session):
        seen = []
        session.on("results", lambda e: seen.append(e))
        art = Artifact(paths=[Path("x.png")], label="PNG")
        session.apply(art)
        assert session.artifacts == [art] and seen == []

    def test_open_project_swaps_the_project(self, session, tmp_path):
        other_cfg, other_work = _project(tmp_path / "other")
        old = session.project
        session.apply(OpenProject(config_path=other_cfg), work_dir=other_work)
        assert session.project is not old and session.project.config.spec_path == other_cfg.parent / "test_spec.json"

    def test_none_and_sequences(self, session):
        session.apply(None)
        session.apply([Patch(ops=[LOOSEN]), None])
        assert LOOSEN in session.spec.pending


class TestSessionView:
    def test_exposes_readers_and_no_writer(self, session):
        view = SessionView(session)
        public = {n for n in dir(view) if not n.startswith("_")}
        assert public == {"project", "spec_path", "pending", "selection", "running",
                          "comparisons", "artifacts", "rescore", "view", "figure", "is_locked"}
        assert not public & {"stage", "save", "discard", "apply", "open", "close", "reload", "select"}

    def test_the_project_view_reaches_no_store_writer(self, session):
        """§4.6: enforcement is structural. The view's project is a frozen
        projection; the store's writers exist only behind StoreWrite.run and
        JobContext, which the Session hands out."""
        import dataclasses
        pv = SessionView(session).project
        assert not hasattr(pv, "store") and not hasattr(pv, "backend")
        assert isinstance(pv.tests, tuple) and pv.tests[0].model_id == A
        with pytest.raises(dataclasses.FrozenInstanceError):
            pv.tests = ()
        with pytest.raises(TypeError):
            pv.results[A] = None
        assert pv.reference(A) is not None and pv.reference(B) is None
        assert pv.baseline_names(A) == ("primary",)
        assert pv.config.backend_name == "Python" and pv.dataset_kind is session.project.dataset_kind

    def test_readers_reflect_the_session(self, session):
        view = SessionView(session)
        session.stage([LOOSEN])
        assert view.pending == tuple(session.spec.pending)
        assert view.comparisons[A] is session.comparisons[A]
        assert view.is_locked(A) is False
        with pytest.raises(TypeError):
            view.comparisons[A] = None  # a read-only mapping
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_session.py -q`. Expected: the new tests FAIL (`AttributeError: 'Session' object has no attribute 'apply'`, no `SessionView`).

- [ ] **Step 3: Add to `session.py`.** Extend the imports: `from .capabilities import Artifact, Job, JobContext, OpenProject, Outcome, Patch, StoreWrite`, `from collections.abc import Mapping`, `from types import MappingProxyType`, `from ..storage.baseline import PRIMARY_BASELINE`. Then inside `Session`, after `export`:

```python
    # -- outcomes ----------------------------------------------------------

    def apply(self, outcome: Outcome | Sequence[Outcome], *, work_dir: Path | None = None) -> None:
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
            self.open(outcome.config_path, work_dir=work_dir)  # refuses while a job runs
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
            except BaseException as e:  # a job's failure is reported, not raised on its thread
                job.error = e
            finally:
                self._post(lambda: self._finish_job(job, outcome.reloads_results))

        threading.Thread(target=work, name=f"dstf-job-{outcome.label}", daemon=True).start()

    def _finish_job(self, job: RunningJob, reloads_results: bool) -> None:
        self.running = None
        if reloads_results and job.error is None and self.project is not None:
            self._reload_results()
        self._emit("job", state="failed" if job.error is not None else "finished", job=job)
        job.done.set()
```

And at module level, after `Session`:

```python
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
            config=p.config, tests=tuple(p.tests), results=MappingProxyType(p.results),
            dataset_kind=p.dataset_kind, _store=p.store,
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
```

`PRIMARY_BASELINE` is the constant `storage/baseline.py` exports (its value is `"primary"`; check). `get_soft_checks(model_id)` returns a name-keyed mapping (comparator.py:233 does `all_baselines.update(store.get_soft_checks(...))`); if it returns something else, adapt `baseline_names` to yield names. A `StoreWrite` whose `run` raises propagates to the caller and the Session's state is what it was — `_reload_results` is not reached.

- [ ] **Step 4: Run** — `uv run pytest tests/test_session.py tests/test_capabilities.py -q`. Expected: PASS. `uv run mypy src/dstf/authoring`; gates.

- [ ] **Step 5: Commit** (paths: `src/dstf/authoring/session.py tests/test_session.py`). Message: `feat: the Session applies outcomes; a job locks its models; SessionView holds no writer`

---

### Task 11: mypy scope, documents, and the spec's amendments

**Files:**
- Modify: `pyproject.toml` (`[tool.mypy] files`), `docs/architecture.md`, `docs/extensibility.md`, `docs/usage.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md` (§4.6)

- [ ] **Step 1: mypy.** Add `"src/dstf/authoring"` and `"src/dstf/figures"` to `[tool.mypy] files`. Run `uv run mypy` bare; fix what it reports in those two packages only (the carry-forward from plan 2 said `figures/` is clean; verify). Resolve `Optional` fields in the Session (the review found `config.tolerance: float | None` and `config.work_dir: Path | None`; Tasks 9's code already narrows both) — never loosen a callee's signature to make the Session type-check.

- [ ] **Step 2: Documents.**
  - `docs/architecture.md`: in the layer ↔ code map, add the authoring tier: `authoring/session.py` (the Session: open / rescore / view / stage / save / discard / export / apply), `authoring/capabilities.py` (the contract), `authoring/spec_document.py`; note that `discovery/spec_update.py` and `storage/accept.py` are the shared paths the CLI and the tool both call, and that the shell is sub-project 4.
  - `docs/extensibility.md`: the "Capability contract" subsection the cut plan added now points at `authoring/capabilities.py`: the `Capability` protocol, `Scope`, the five outcomes with `models` on `Job` and `StoreWrite`, `register_capability` through the `dstf.plugins` group, and the rule that a capability applies nothing. The shell that lists them is sub-project 4.
  - `docs/usage.md`: where `spec-update` is described, show both envelope forms and say the tool writes the multi-model one; the single-model form is read because such files exist.
  - `CLAUDE.md`: the project tree gains `authoring/` (one line: "Session, capability contract, spec document — the tool's core; the shell lands with `dstf author`"); the `spec-update` example line mentions "single- or multi-model envelope".
  - The spec, §4.6: under the outcome dataclasses add a short "Amended in sub-project 3" note: `Job` and `StoreWrite` carry `models`; completion is posted through `Session(post=...)`; `rescore` stages materialization only when the tree scores what the flat path scored, and a glob spec is not editable until the owner decides how it materializes (open question, §9); `SessionView.project` is a frozen `ProjectView`. And §7 criterion 4 gains: discovery reads `<reference_root>/test_spec.json` when no `test_spec` is configured (the file `dstf add` writes); `spec-update` refuses to materialize a tree for a glob-pattern spec instead of writing a `*` leaf; a model-less envelope exits 2 like every other malformed envelope.
  - `docs/usage.md`, where `test_spec` is described: the fallback file is discovered.
  - From the Task 4 review, the places that mention the envelope without its shape or list the discovery modules: `README.md:161-162` and `CLAUDE.md:86-87` (the `spec-update` example — say "single- or multi-model envelope"), `docs/usage.md:341` (show the multi-model shape), `docs/architecture.md:75` and `CLAUDE.md:21` (add `patch_envelope.py`, `spec_update.py`, `storage/accept.py`), `docs/patterns.md:152` (describes the pre-D125 export panel producing the file — reword to "a patch file, from the authoring tool's Export or by hand"). Also the Task 5 review's note: `discover` and `manifest cleanup --orphans` see the fallback spec file too (criterion 4 wording says "every command that discovers tests").

- [ ] **Step 3: Run** the gates. `uv run pytest -q -m "not playwright"`.

- [ ] **Step 4: Commit** (paths: `pyproject.toml docs/architecture.md docs/extensibility.md docs/usage.md CLAUDE.md docs/superpowers/specs/2026-09-08-two-tier-architecture-design.md`). Message: `docs: the authoring core — architecture, the capability contract, the envelope; authoring and figures under mypy`

---

### Task 12: Sub-project gate

- [ ] **Step 1: Full gates** — `uv run pytest -q` (browser tests included), `uv run ruff check && uv run ruff format --check && uv run mypy`, `uv run pytest --cov -q | tail -3` (report the number; the floor is 66%).

- [ ] **Step 2: End-state predicates**
  1. `src/dstf/authoring/` imports no UI framework: `grep -rn "nicegui\|plotly\b\|matplotlib" src/dstf/authoring` is empty.
  2. Every accept path calls the shared function: `grep -rn "accept_results(" src/dstf` hits `storage/accept.py` (the call) and `storage/reference_store.py` (the definition) only.
  3. The spec is parsed in one place: `grep -rn "json.loads" src/dstf/discovery src/dstf/cli/spec_cmds.py` — every hit reads a file the function names (`parse_test_spec`, `load_spec_document`, `_load_spec_for_edit`, `cmd_spec_update`'s envelope file); none re-reads the spec inside a patch path.
  4. `spec-update` reads both forms: `tests/test_patch_envelope.py` and `TestMultiModelEnvelope` pass; `grep -rn '"patches"' src/dstf` hits `patch_envelope.py` only.
  5. `unfinished_run_models`, `recorded_backend`, `dataset_kind_for`, `compare_one`, `build_trajectories`, `apply_patch_to_document` each have exactly one definition, and no `_`-prefixed former name survives (`grep -rn "_recorded_backend\|_dataset_kind_for\|_build_trajectories\|_materialize_implicit_metrics" src tests` is empty).
  6. The §6 "The Session" list, item by item, names a passing test in `tests/test_session.py` / `tests/test_spec_update_cli.py` / `tests/test_accept.py` (write the mapping into the handoff).
  7. The figure goldens are unchanged: `git diff --stat 75e91f7 -- tests/golden` is empty.

- [ ] **Step 3: Real-library gate** (orchestrator only; the ONE permitted use of the real work dir):
  1. `uv run dstf --config /mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json compare --work-dir /mnt/d/Modelica/DynamicSystemsTestingFramework/testing_output/TRANSFORM/Dymola/linux --report`. Expected: 330 PASS + 1 SIM_FAIL, unchanged; 330 pages.
  2. A scratch script (in the session scratchpad, NOT named after a stdlib module) opens a `Session` on the same config and work dir, classifies every `comparisons[...]` with `reporting.status.classify`, and compares the counts to step 1's; then calls `rescore` on ten flat-override tests and asserts each verdict is unchanged and `spec.pending` holds exactly ten `add /metrics` ops. The script never calls `save`. Before and after, `ls -la testing_output/TRANSFORM/Dymola/linux/reports | wc -l` and the spec file's hash are unchanged — the Session wrote nothing.

- [ ] **Step 4: Plan-versus-spec check** — a fresh-context agent reads spec §4.5, §4.6, §4.7 (the envelope and accept paragraphs), §5 step 3, §6 "The Session" against `git diff 75e91f7..HEAD`: what the spec asked that is missing; what got built that it did not ask (the six calls in Conventions are expected); spec sentences the code now contradicts. Tell it which directories are shared state it must not run the CLI against.

- [ ] **Step 5: Handoff** — the OPEN OWNER QUESTION from Conventions #7 (how a glob-pattern spec materializes: resolve the patterns against the result's variables when the tree is written, or leave such tests uneditable in the tool — the first also changes what `spec-update` writes); what ran and what did not (TRANSFORM compare-only and the Session script ran; no live backend; no Windows), the coverage number, the §6 mapping from Step 2.6, the plan-versus-spec findings, and the carry-forwards: `read_results` prints its progress to stdout (the Session inherits it; a `quiet` flag or a logger is plan 5's call with the run callable); the review loop's `[a]` still bypasses the shared accept; `_filter_tests` stays in the CLI until `dstf author --filter`. Nothing is pushed.
