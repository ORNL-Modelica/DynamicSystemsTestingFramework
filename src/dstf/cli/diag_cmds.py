"""Query and diagnostic commands: `discover`, `baseline export`, the
`manifest` group, `check`."""

import argparse
import json
import logging
import re
import shutil
from pathlib import Path

from ..backends import get_backend_class
from ..config import Config, ConfigError, NoProject
from ..discovery.test_registry import TestModel, discover_tests
from ..discovery.unit_tests import unit_test_variables
from ..pipeline import write_id_mapping
from ..storage.names import baseline_file, baseline_stem
from .common import _build_config, _filter_tests

logger = logging.getLogger(__name__)


def _vars_text(test: TestModel) -> str:
    """What a test tracks, as far as discovery knows it: the count of its
    UnitTests variables, and its ``variables`` entries as PATTERNS — `2`,
    `1 pattern`, `2 + 3 patterns`. A pattern names no variable until it is
    matched against a result; counted as variables they read `0`."""
    declared = len(unit_test_variables(test))
    patterns = len(test.variable_patterns)
    if not patterns:
        return str(declared)
    text = f"{patterns} pattern{'' if patterns == 1 else 's'}"
    return f"{declared} + {text}" if declared else text


def _method_text(test: TestModel, config: Config) -> str:
    """The solver the test names, or `—` where it names none and the
    framework's default would not be true: ``Dassl`` is what a Modelica tool
    is handed, while FMPy maps it to its own solver and a Julia or Python
    source has no solver setting at all. A name the backend does not know
    reads with the solver that runs instead (`Backend.solver`):
    ``Esdirk45a → dassl``."""
    defaulted = test.field_sources.get("method") == "default"
    if defaulted and config.source_type != "modelica":
        return "—"
    solver = get_backend_class(config).solver(test)
    if solver.asked is not None:
        return f"{solver.asked} → {solver.name}"
    return str(test.method)


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and list all test models."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests found.")
        return 1

    # Print summary table. Every cell is something true before any run:
    # see `_vars_text` and `_method_text`. The Method column is as wide as
    # its longest cell: `Esdirk45a → dassl` pushed Source out of line.
    methods = [_method_text(t, config) for t in tests]
    width = max(10, *(len(m) for m in methods))
    header = (
        f"{'Model ID':<90} {'Vars':>16}  {'StopTime':>10}  "
        f"{'Method':<{width}}  {'Source':<10}"
    )
    print(header)
    print("-" * len(header))
    for t, method in zip(tests, methods, strict=True):
        stop = f"{t.stop_time:g}"
        print(
            f"{t.model_id:<90} {_vars_text(t):>16}  {stop:>10}  "
            f"{method:<{width}}  {t.source:<10}"
        )

    print(f"\nTotal: {len(tests)} tests")

    print(f"  UnitTests variables: {sum(len(unit_test_variables(t)) for t in tests)}")
    print(
        f"  Variable patterns: {sum(len(t.variable_patterns) for t in tests)} "
        f"(each is matched against its test's result after the run)"
    )

    by_source = {}
    for t in tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  Source '{source}': {count}")

    # A test whose tree is wrong in its FILE is listed, and refused where it
    # is scored (`broken_roles`): say so here, in the refusal's own words,
    # before a run finds out. The exit code says only that tests were found.
    from ..comparison.comparator import broken_roles

    refused = [(t.model_id, b) for t in tests if (b := broken_roles(t)) is not None]
    if refused:
        print()
    for model_id, sentence in refused:
        print(f"{model_id} will not be scored: {sentence}")

    return 0


#: `baseline export --format`: each format and the file it is written to
#: in the work dir when no --output names one. The first is the default.
EXPORT_FILES = {"json": "references.json", "csv": "references.csv"}


def cmd_baseline_export(args: argparse.Namespace) -> int:
    """Write every stored baseline of the partition to one file."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    assert config.work_dir is not None  # resolved in Config.__post_init__
    # review 2026-07-06 finding 60: --output arrives as a string — coerce to
    # Path (the ReferenceStore export APIs call .parent on it).
    out = (
        Path(args.output)
        if args.output is not None
        else config.work_dir / EXPORT_FILES[args.format]
    )
    if args.format == "csv":
        store.export_csv(out)
    else:
        store.export_json(out)
    print(f"Exported to {out}")
    return 0


def cmd_manifest_show(args: argparse.Namespace) -> int:
    """List the partition's baselines — each one's file, status and model,
    in model-id order — with the count of active ones."""
    config = _build_config(args)
    from ..storage.ref_index import STATUSES
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    entries = sorted(
        (e["model_id"], baseline_file(e["model_id"]), e["status"])
        for e in store.index.all_tests().values()
    )
    n_active = sum(status in STATUSES for _, _, status in entries)

    print(f"References: {config.reference_dir}")
    print(f"Active: {n_active}\n")

    if entries:
        print(f"{'File':<21}  {'Status':<10}  {'Model ID'}")
        print("-" * 90)
        for model, file, status in entries:
            print(f"{file:<21}  {status:<10}  {model}")

    return 0


def cmd_manifest_dump(args: argparse.Namespace) -> int:
    """Write the ref ID → model ID mapping to the work directory."""
    config = _build_config(args)
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    assert config.work_dir is not None  # resolved in Config.__post_init__
    written = config.work_dir / "reference_manifest.json"
    try:
        write_id_mapping(store, config)
    except OSError as e:
        # A work dir this user may not write: the sentence, not a traceback.
        raise ConfigError(f"{written} cannot be written: {e.strerror}") from e
    print(f"Written to {written}")
    return 0


#: The names a work dir's report dirs are written under, and only those:
#: ``ref_<code>`` of a test with a baseline, ``test_NNNN`` (its batch
#: manifest key) of one without, and ``ref_NNNN`` of a baseline before the
#: names were codes. `manifest cleanup` removes a directory of these shapes
#: that no test uses, and never one of another shape: a user's notes, a
#: page named after a model.
_REPORT_DIR = re.compile(r"ref_\d{4,}|ref_[0-9a-f]{12}|test_\d{4,}")

#: A batch manifest key, the name of a test's simulation dir.
_TEST_KEY = re.compile(r"test_\d{4,}")


def cmd_manifest_cleanup(args: argparse.Namespace) -> int:
    """List what the work dir holds that no test uses, or — ``--apply`` —
    remove it: the batch manifest's entries of models the project no
    longer declares, with their simulation and report dirs, and every
    report dir under a test's name (``ref_<code>``, ``test_NNNN``, an old
    ``ref_NNNN``) that no declared test is reported under.

    Refused (a ConfigError, exit 2, nothing removed) when what it decides
    by is not there or cannot be read: the reference folder, which says
    which tests are reported under ``ref_<code>``, and the batch manifest,
    which says which ``test_NNNN`` is whose. Without them every report dir
    would read as unused. A reference folder of old numbered names is
    refused by the store (`OldBaselineNames`).
    """
    config = _build_config(args)
    # What it reads, named on the line: one that is not there would read as
    # "nothing to clean up" of a spec or work dir never looked at.
    spec, work = config.test_spec_file, config.work_dir
    assert work is not None  # resolved in Config.__post_init__
    if args.test_spec is not None and spec is not None and not spec.is_file():
        raise ConfigError(f"--test-spec {spec}: no such file")
    if args.work_dir is not None and not work.is_dir():
        raise ConfigError(f"--work-dir {work}: no such directory")

    ref_dir = config.reference_dir
    if not ref_dir.is_dir():
        raise ConfigError(
            f"the reference folder {ref_dir} is not there — nothing was removed"
        )
    try:
        next(ref_dir.iterdir(), None)
    except OSError as e:
        raise ConfigError(
            f"the reference folder {ref_dir} cannot be read: {e.strerror} — "
            "nothing was removed"
        ) from e
    manifest_path = work / "batch_manifest.json"
    if not manifest_path.is_file():
        raise ConfigError(f"{manifest_path} is not there — nothing was removed")
    # Read here, not by `BatchManifest.load`: that moves a file it cannot
    # read aside to `.corrupt`, and a refusal removes nothing.
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(entries, dict):
            raise ValueError(f"expected a JSON object, got {type(entries).__name__}")
    except (ValueError, OSError) as e:
        raise ConfigError(
            f"{manifest_path} cannot be read: {e} — nothing was removed"
        ) from e

    from ..backends import BatchManifest
    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)  # refuses a folder of old names
    manifest = BatchManifest.load(manifest_path)
    declared = {t.model_id for t in discover_tests(config)}

    # Each entry's model. One that names none (no model_id, or not an
    # object) cannot be told to be anyone's: warned of and left, with its
    # directories, rather than removed or raised over.
    model_of: dict[str, str] = {}
    for key, e in manifest.manifest.items():
        model_id = e.get("model_id") if isinstance(e, dict) else None
        if isinstance(model_id, str) and model_id:
            model_of[key] = model_id
        else:
            logger.warning(
                "batch_manifest.json: %s names no model — left as it is", key
            )
    # The names a declared test is reported under: its baseline's, when the
    # store reads one for it, and its manifest key.
    stems = {store.index.stem(model_id) for model_id in declared}
    keys = {key for key, model_id in model_of.items() if model_id in declared}
    keep = (stems - {None}) | keys | (manifest.manifest.keys() - model_of.keys())
    orphans = {
        key: model_id for key, model_id in model_of.items() if model_id not in declared
    }
    reports = work / "reports"
    stale: set[Path] = set()
    for key, model_id in orphans.items():
        if _TEST_KEY.fullmatch(key):
            stale |= {work / key, reports / key}
        stale.add(reports / baseline_stem(model_id))
    if reports.is_dir():
        stale |= {
            p
            for p in reports.iterdir()
            if _REPORT_DIR.fullmatch(p.name) and p.name not in keep
        }
    dirs = sorted(
        p for p in stale if p.is_dir() and not p.is_symlink() and p.name not in keep
    )

    if not dirs and not orphans:
        print(
            f"Nothing to clean up in {work}: every entry and directory "
            "belongs to a test."
        )
        return 0
    lines = [
        f"batch_manifest.json: {key} ({model_id})" for key, model_id in orphans.items()
    ] + [f"{p.relative_to(work).as_posix()}/" for p in dirs]
    print(f"In {work}, no test uses:")
    for line in sorted(lines):
        print(f"  {line}")
    if not args.apply:
        print("Dry run: nothing was removed. Re-run with --apply to remove them.")
        return 0

    for p in dirs:
        shutil.rmtree(p)
    if orphans:
        for key in orphans:
            del manifest.manifest[key]
        manifest.save()
    n_dirs, n_entries = len(dirs), len(orphans)
    print(
        f"Removed {n_dirs} director{'y' if n_dirs == 1 else 'ies'} and "
        f"{n_entries} manifest entr{'y' if n_entries == 1 else 'ies'}."
    )
    return 0


def _check_entries(config: Config | None) -> list[tuple[str, dict]]:
    """What `dstf check` lists, each with its settings block: the entries
    of the project's `backends` map, in the order written, then every
    registered backend the project has no entry of (the search alone)."""
    from ..backends import backend_family, backend_names

    entries = (
        [(name, dict(block)) for name, block in config.backends.items()]
        if config
        else []
    )
    selected = config.backend_name if config else None
    if selected and selected not in dict(entries) and config is not None:
        entries.append((selected, dict(config.backend_settings)))
    listed = {backend_family(name) for name, _ in entries}
    entries.extend((name, {}) for name in backend_names() if name not in listed)
    return entries


def _check_target(named: str, entries: list[tuple[str, dict]], selected: str) -> str:
    """The one of ``entries`` that `dstf check <named>` diagnoses: the
    entry of that name; else, for a backend's name, its entry the project
    runs with, or its first written. A name that is neither — it starts
    with a backend's, or the parser had refused it — is a config error
    that lists both."""
    from ..backends import backend_family, backend_names

    names = [name for name, _ in entries]
    if named in names:
        return named
    of_backend = [name for name in names if backend_family(name) == named]
    if named in backend_names() and of_backend:
        return selected if selected in of_backend else of_backend[0]
    written = [name for name in names if name not in backend_names()]
    listed = f" ({', '.join(written)})" if written else ""
    raise ConfigError(
        f'check: "{named}" is no entry of backends{listed} and no backend\'s '
        f"name ({', '.join(backend_names())})"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Say where each backend is on this machine and by which rule it was
    found, then diagnose one: the entry or backend named, else the one the
    project selects. Exit 0 iff that one is usable."""
    from ..backends import backend_class, backend_family
    from ..config import _pick_backend
    from ..config_shape import default

    # The reference root is read here only to look for testing.json in it
    # (and not at all beside --config: `cli.parser.UNREAD`). One that is
    # not there holds none, and the search would go on as if it had not
    # been given.
    root = args.reference_root
    if root is not None and not Path(root).is_dir():
        raise ConfigError(
            f"--reference-root {root}: no such directory (check reads it "
            "only to look for testing.json in it)"
        )
    try:
        config: Config | None = _build_config(args)
    except NoProject:
        # No testing.json and no library here: nothing to read. The
        # backends are still where they are.
        config = None

    if config is None:
        project = "no testing.json here: the search alone"
        # As a project with an empty testing.json would be read.
        selected, _ = _pick_backend({}, default("source_type"))
    else:
        project = (
            f"testing.json: {config.config_found}"
            if config.config_found is not None
            else f"no testing.json: {config.library_name} on defaults"
        )
        assert config.backend_name is not None  # resolved in __post_init__
        selected = config.backend_name
    entries = _check_entries(config)
    # An entry of the project's map says its settings; any other is searched for.
    in_config = set(config.backends) if config is not None else set()
    # Every name listed is a backend's or starts with one (`Config`).
    classes = {
        name: cls
        for name, _ in entries
        if (cls := backend_class(backend_family(name))) is not None
    }
    located = {name: classes[name].locate(block) for name, block in entries}

    if args.check_backend is None:
        print(f"Backends ({project})")
        # An entry by its name, with the backend it is of where that is
        # not its name: `Dymola 2025 [Dymola]`.
        labels = {
            name: name
            if backend_family(name) == name
            else f"{name} [{backend_family(name)}]"
            for name, _ in entries
        }
        width = max(len(label) for label in labels.values())
        for name, _ in entries:
            found = located[name]
            if not found.found:
                state = f"not found  {found.parts[0].detail}"
            else:
                first = found.parts[0]
                assert first.rule is not None  # a found part names its rule
                rule = first.rule.said(name if name in in_config else "")
                state = f"found      {first.path}  ({rule})"
            mark = "*" if name == selected else " "
            print(f"  {mark} {labels[name]:<{width}}  {state}")
        print(
            "  * the backend this project runs with"
            if config is not None
            else "  * the backend a Modelica project with no testing.json runs with"
        )
        if config is not None and config.passed_over:
            print(f"  {config.passed_over}")
        print()
        target = selected
    else:
        target = _check_target(args.check_backend, entries, selected)

    diagnosis = classes[target].diagnose(
        located[target], entry=target if target in in_config else "", config=config
    )
    print(target)
    for line in diagnosis.lines:
        print(line)
    if diagnosis.usable:
        print(f"\nOK — {target} is ready.")
        return 0
    print(f"\nFAIL — {target} is not ready. Fix one of:")
    fixes = diagnosis.fixes or _default_fixes(target, classes[target])
    for i, fix in enumerate(fixes, start=1):
        print(f"  {i}. {fix}")
    return 1


def _default_fixes(name: str, cls) -> tuple[str, ...]:
    """What to do about a backend that says no more than that a part of it
    was not found: install it — or, where its block takes ``executables``,
    say where it is."""
    if "executables" not in cls.settings:
        return (f"Install {name}",)
    return (
        f'Install {name}, or set "executables" in its block of testing.json\'s '
        '"backends" to where it is',
    )
