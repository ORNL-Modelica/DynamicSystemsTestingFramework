"""``dstf migrate-variable-names`` — stored baselines and spec keys move
from a UnitTests variable's expression text to its result-file name
(``<instance>.x[i]``).

The variable-identity change moved the reference JSON's variable names and
the ``test_spec.json`` keys that named an expression. Those two shapes are
what another tree depends on, so this command IS the coordination: it
rewrites both, dry-run by default, and reports everything it could not do
rather than guessing.

TWO PHASES, and that is the point. Every partition and the spec are READ
and every file's outcome DECIDED before any file is written, so a truncated
file late in the walk cannot leave the earlier half of a tree migrated and
the rest not. A baseline this command cannot read, or cannot decide, is a
skip carrying the file and the reason — never a traceback — and the rest of
the run is decided and written around it: no cross-file decision reads a
baseline's content (an unreadable primary already makes the soft checks
beside it a skip of their own, and the spec tier reads only the discovered
tests), so one stray junk file must not stall a tree of 900. The exit code
is what says it happened.

The SPEC is the exception: while IT cannot be read nothing at all is
written, baselines included. Its keys and the names the baselines are
stored under are one coordinated change, and half of it is worse than
neither half.

DELETED AT THE v1.0 TAG. Nothing reads the old shape — an un-migrated
baseline simply reads as a missing one — so once every tree that matters
has been migrated this module has no caller, and a one-shot migration kept
past its use is a code path nobody exercises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..discovery.mo_parser import normalize_expression
from ..discovery.patch_apply import PatchError, load_spec_document
from ..discovery.test_registry import TestModel, discover_tests
from ..discovery.unit_tests import unit_test_variables
from .ref_index import _REF_FILE_PATTERN, _atomic_write_text

#: ``status`` of a :class:`FileOutcome`. ``renamed`` and ``expression-added``
#: are separate because they are different events for a reviewer: the first
#: moved a key every other file keys off, the second only wrote the field the
#: writer now always writes. ``reordered`` is the third: every entry already
#: HAS its expression and the rewrite only moves it up beside the name, so a
#: reviewer reading "gained an expression field" would look for a field that
#: was there all along.
RENAMED = "renamed"
EXPRESSION_ADDED = "expression-added"
REORDERED = "reordered"
MIGRATED = "migrated"
SKIPPED = "skipped"
UNKNOWN_MODEL = "unknown-model"


@dataclass
class FileOutcome:
    """What happened to one reference file."""

    path: Path
    status: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class MigrationReport:
    files: list[FileOutcome] = field(default_factory=list)
    #: ``(model_id, old key, new key, occurrences)`` per rewritten
    #: ``test_spec.json`` key. One entry per (model, key): the same key
    #: reached through an override, a leaf and a plot series is ONE decision
    #: a reader checks, not three lines to reconcile.
    spec_rewritten: list[tuple[str, str, str, int]] = field(default_factory=list)
    spec_skipped: list[str] = field(default_factory=list)
    #: A baseline entry already under its result-file name whose recorded
    #: expression is not the one the model writes today. Left exactly as
    #: stored — this command renames, and which quantity a baseline holds is
    #: the compare-time expression warning's business, not a guess made
    #: while rewriting the file. Reported, but not a failure.
    expression_differs: list[str] = field(default_factory=list)
    #: ``(model_id, variable)``: a variable that has no baseline entry of its
    #: own AFTER the migration, because before it the UnitTests variable and
    #: the spec pattern were one entry under one name. No data can be
    #: invented for it; it needs a re-accept. Not a failure either.
    reaccept: list[tuple[str, str]] = field(default_factory=list)
    #: Rewritten files whose stored bytes held CRLF. The store writes every
    #: baseline LF, so each of these shows as a whole-file diff — worth
    #: saying once, before a reviewer reads a one-name change as a rewrite
    #: of everything.
    crlf: list[Path] = field(default_factory=list)
    #: ``soft_checks/ref_NNNN/`` directories whose primary is gone. Nothing
    #: here can say which model they hold, so they are named, not migrated.
    #: Informational, not a skip.
    orphan_soft_checks: list[Path] = field(default_factory=list)
    #: Spec entries whose model has no UnitTests variables HERE, so none of
    #: their keys were considered: the model has no such component, or it
    #: left the library and only the spec still names it. Informational,
    #: not a skip — but it is the one place a spec entry silently does
    #: nothing, so it is said out loud.
    spec_not_considered: list[str] = field(default_factory=list)
    #: Files no decision could be made about at all — each of them a skip of
    #: its OWN file. Reported apart from the content skips because they are a
    #: different job for the reader: a file to fix, not a decision to make.
    unreadable: list[Path] = field(default_factory=list)
    #: The spec, when IT could not be read. The one file whose reading gates
    #: every write: its keys and the names the baselines are stored under are
    #: one coordinated change.
    spec_unreadable: Path | None = None

    @property
    def clean(self) -> bool:
        """Nothing was left undone — the command's exit code.

        Three independent terms, and each moves it on its own: a file this
        command skipped, a file whose model discovery does not know, and a
        spec key it may not rename.
        """
        skipped_file = any(f.status == SKIPPED for f in self.files)
        unknown_model = any(f.status == UNKNOWN_MODEL for f in self.files)
        return not skipped_file and not unknown_model and not self.spec_skipped


def _read_document(path: Path) -> tuple[dict | None, str, bool]:
    """``(document, the reason it could not be read, whether it held CRLF)``.

    Decoded ``utf-8-sig``: a baseline a Windows editor saved keeps a BOM,
    and a BOM is not a reason to refuse a file that otherwise parses. A file
    that is truncated, empty, not UTF-8 or not a JSON object comes back with
    a reason naming it — the caller turns that into a skip, because a raw
    ``JSONDecodeError`` over a tree of 900 baselines names no file.

    The SHAPE the rest of this module walks is checked here too, and for
    the same reason: ``"variables": [1, 2]``, a ``null`` entry in it, a
    ``null`` variable name, a ``comparison`` that is a string and a
    ``variable_overrides`` that is a list each raised an ``AttributeError``
    naming no file at all. A document this module cannot walk has not been
    read, so it is a skip of its own file like any other unreadable one.
    """
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}", False
    if not isinstance(document, dict):
        return None, f"not a JSON object but a {type(document).__name__}", False
    shape = _shape_problem(document)
    if shape:
        return None, shape, False
    return document, "", b"\r\n" in raw


def _shape_problem(document: dict) -> str:
    """Why this module cannot walk ``document``, or ``""``. Only the keys it
    reads: everything else is carried through untouched."""
    variables = document.get("variables", [])
    if not isinstance(variables, list):
        return f"'variables' is not a list but a {type(variables).__name__}"
    for position, entry in enumerate(variables, start=1):
        if not isinstance(entry, dict):
            return (
                f"variable entry {position} is not an object but a "
                f"{type(entry).__name__}"
            )
        # Both are READ as text — the name is normalized to compare it with
        # the model's expression, and the expression is carried into the
        # rewritten entry — so their type is part of the shape. A ``null``
        # expression is the one non-string this module can answer: nothing
        # was recorded, which is what ``""`` says.
        name = entry.get("name")
        if "name" in entry and not isinstance(name, str):
            return (
                f"variable entry {position}: 'name' is not a string but a "
                f"{type(name).__name__}"
            )
        expression = entry.get("expression")
        if expression is not None and not isinstance(expression, str):
            return (
                f"variable entry {position}: 'expression' is not a string but a "
                f"{type(expression).__name__}"
            )
    comparison = document.get("comparison")
    if comparison is not None and not isinstance(comparison, dict):
        return f"'comparison' is not an object but a {type(comparison).__name__}"
    overrides = (comparison or {}).get("variable_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return (
            "'comparison.variable_overrides' is not an object but a "
            f"{type(overrides).__name__}"
        )
    return ""


def _entry(entry: dict, name: str, expression: str) -> dict:
    """``entry`` with ``name`` and ``expression`` set, the expression
    directly after the name, every other key and its order kept."""
    out: dict = {}
    for key, value in entry.items():
        if key == "expression":
            continue
        out[key] = name if key == "name" else value
        if key == "name":
            out["expression"] = expression
    if "name" not in entry:
        out["name"], out["expression"] = name, expression
    return out


def _override_targets(
    key: str, by_expression: dict[str, list[str]], patterns: set[str]
) -> list[str]:
    """The result-file names an override key could become.

    Empty for a key that names a variable of its own — a spec pattern tracks
    it under that very name in the result file — and for a key that is no
    expression of this model's. SEVERAL when one expression covers two
    indices (``x={a, a}``): the caller reports it and renames nothing,
    because picking the last one silently moves the override to a different
    variable.

    Also empty for anything that is not a string: a metrics leaf written
    ``{"variable": null}`` is nothing this command can rename, and it used
    to reach the normalizer as a ``None`` and raise an ``AttributeError``
    naming no file.
    """
    if not isinstance(key, str):
        return []
    if key in patterns:
        return []
    return by_expression.get(normalize_expression(key), [])


def migrate_document(
    data: dict, test: TestModel
) -> tuple[dict | None, list[str], list[str]]:
    """``(new_document, problems, notes)`` for one reference file.

    ``new_document`` is None when there is nothing to write (already
    migrated) or when there are ``problems`` — a file is rewritten whole or
    not at all, because half a file's variables renamed is a baseline whose
    entries no longer agree with each other.
    """
    wanted = {i: v for i, v in enumerate(unit_test_variables(test), start=1)}
    #: expression -> the result-file names carrying it, from the MODEL's own
    #: rule. Built whether or not any entry renames: a file already under
    #: result-file names can still hold an expression-keyed override.
    by_expression: dict[str, list[str]] = {}
    for variable in wanted.values():
        if variable.expression:
            by_expression.setdefault(variable.expression, []).append(variable.name)
    problems: list[str] = []
    notes: list[str] = []
    variables = []
    claimed: set[int] = set()
    for position, entry in enumerate(data.get("variables", []), start=1):
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            # Every writer writes an integer index; without one nothing says
            # which variable the entry holds, and the pattern branch below
            # would report the file migrated while its names stayed as they
            # were.
            problems.append(
                f"entry {position}: {index!r} is not an integer 'index' — which "
                "variable it holds is yours to say"
            )
            continue
        if index in wanted:
            if index in claimed:
                problems.append(
                    f"index {index}: two entries claim it — which one holds "
                    f"{wanted[index].name} is yours to say"
                )
                continue
            claimed.add(index)
        stored = entry.get("name", "")
        target = wanted.get(index)
        if target is None:
            # A pattern-tracked variable, or an index past the component's
            # own: it keeps its name and gains the field the writer now
            # always writes.
            variables.append(_entry(entry, stored, entry.get("expression", "")))
        elif stored == target.name:
            # Already migrated — and exact, not a coincidence: the model's
            # own array ``x`` (``x=x``) reads ``x[i]``, never
            # ``<instance>.x[i]``.
            recorded = entry.get("expression", "")
            if recorded and target.expression and recorded != target.expression:
                notes.append(
                    f"index {index}: recorded {recorded!r}, the model now writes "
                    f"{target.expression!r} — left as stored"
                )
                variables.append(_entry(entry, stored, recorded))
            else:
                variables.append(_entry(entry, stored, recorded or target.expression))
        elif target.expression and normalize_expression(stored) == target.expression:
            variables.append(_entry(entry, target.name, target.expression))
        else:
            problems.append(
                f"index {index}: stored {stored!r}, expected {target.expression!r} "
                f"(or {target.name!r}, already migrated)"
            )
    # The baseline's OWN override tier is keyed like the spec's: left
    # un-renamed it silently stops applying.
    comparison = data.get("comparison") or {}
    overrides = comparison.get("variable_overrides") or {}
    patterns = set(test.variable_patterns)
    moved: dict[str, dict] = {}
    #: new key -> the override key that claimed it. Two SPELLINGS of one
    #: expression (``b[1, 1]`` and ``b[1,1]``, which normalize alike) both
    #: name one variable, and writing them both into one dict dropped the
    #: first silently. Same class as the old/new collision below.
    claimed_key: dict[str, str] = {}
    for key, value in overrides.items():
        names = _override_targets(key, by_expression, patterns)
        if len(names) > 1:
            problems.append(
                f"override key {key!r} is the expression of "
                f"{', '.join(repr(n) for n in names)} — which one it meant is "
                "yours to say"
            )
            continue
        new_key = names[0] if names else key
        if new_key != key and new_key in overrides:
            problems.append(
                f"override keys {key!r} and {new_key!r} are both present — which "
                "one wins is yours to say"
            )
            continue
        if new_key in claimed_key:
            problems.append(
                f"override keys {claimed_key[new_key]!r} and {key!r} are two "
                f"spellings of one expression and both name {new_key!r} — which "
                "one wins is yours to say"
            )
            continue
        claimed_key[new_key] = key
        moved[new_key] = value
    if problems:
        return None, problems, notes
    new = dict(data)
    if "variables" in data:
        new["variables"] = variables
    if overrides:
        new["comparison"] = {**comparison, "variable_overrides": moved}
    if json.dumps(new) == json.dumps(data):
        return None, [], notes
    return new, [], notes


def _reference_files(root: Path) -> list[tuple[Path, Path]]:
    """``(file, the primary ref that names its model)`` for every primary and
    soft-check file of every ``<Backend>/<os>/`` partition, whatever backend
    the run is configured for — a tree is migrated once, not once per
    backend someone happens to have installed.

    A primary is what the STORE calls one (``_REF_FILE_PATTERN``), so a
    ``ref_0001_backup.json`` somebody kept beside it is not rewritten — the
    store does not read it either.

    Companions are not listed: a companion is another source's data, keyed
    by the names that source gives its own quantities.
    """
    out = []
    for ref in sorted(root.glob("*/*/ref_*.json")):
        if not _REF_FILE_PATTERN.match(ref.name):
            continue
        out.append((ref, ref))
        soft = ref.parent / "soft_checks" / ref.stem
        # A soft-check file carries no ``model_id`` of its own; the primary
        # BESIDE IT — in its own partition, ids being per partition — is
        # what says which test it belongs to.
        out.extend((p, ref) for p in sorted(soft.glob("*.json")))
    return out


def _orphan_soft_checks(root: Path) -> list[Path]:
    """``soft_checks/ref_NNNN/`` directories holding files whose primary is
    gone. Nothing here can resolve their model, so they are named rather
    than migrated — informational, not a skip."""
    out = []
    for soft in sorted(root.glob("*/*/soft_checks/ref_*")):
        if not soft.is_dir() or not any(soft.glob("*.json")):
            continue
        if not (soft.parent.parent / f"{soft.name}.json").exists():
            out.append(soft)
    return out


def _walk_leaves(node: object, fn) -> None:
    if isinstance(node, dict):
        if "variable" in node and "children" not in node:
            fn(node)
        for child in node.get("children") or []:
            _walk_leaves(child, fn)


def _needs_reaccept(test: TestModel) -> list[str]:
    """Spec patterns of ``test`` that lose their baseline entry.

    Before the identity change a UnitTests variable was stored under its
    expression text, so a test that ALSO tracked that same name by a spec
    pattern had ONE entry for the two of them. After it they are two
    variables with two names, and the second has no stored data. Nothing
    here can invent it: the test is listed, and a re-accept fills it.
    """
    expressions = {v.expression for v in unit_test_variables(test) if v.expression}
    return [p for p in test.variable_patterns if normalize_expression(p) in expressions]


def _renamed(old: dict, new: dict) -> bool:
    """Whether the rewrite moved a key something else reads off — a
    variable's name or an override key — as opposed to only adding the
    field the writer now always writes."""

    def keys(data: dict) -> tuple[list, list]:
        overrides = (data.get("comparison") or {}).get("variable_overrides") or {}
        return [v.get("name") for v in data.get("variables", [])], list(overrides)

    return keys(old) != keys(new)


def _change_status(old: dict, new: dict) -> str:
    """What a rewritten file's outcome is CALLED. Three different events:
    a key moved, a field was added, or a field that was there all along
    moved up beside the name (``expression`` after ``values`` — the shape a
    hand-edited file has; ``_entry`` writes it directly after ``name``)."""
    if _renamed(old, new):
        return RENAMED
    if any("expression" not in entry for entry in old.get("variables", [])):
        return EXPRESSION_ADDED
    return REORDERED


def migrate(config: Config, *, apply: bool) -> MigrationReport:
    """Rewrite every partition's baselines and the spec. Writes only when
    ``apply``; the report is identical either way.

    Every file is read and every outcome decided first; the writes happen
    together at the end. A baseline that could not be read is a skip of its
    own file and the rest is still written; the SPEC that could not be read
    stops every write.
    """
    report = MigrationReport()
    tests = {t.model_id: t for t in discover_tests(config)}
    root = config.reference_root
    assert root is not None  # resolved in Config.__post_init__

    documents: dict[Path, dict] = {}
    had_crlf: dict[Path, bool] = {}
    unreadable: dict[Path, str] = {}

    def read(path: Path) -> dict | None:
        if path not in documents and path not in unreadable:
            document, reason, crlf = _read_document(path)
            if document is None:
                unreadable[path] = reason
            else:
                documents[path], had_crlf[path] = document, crlf
        return documents.get(path)

    planned: list[tuple[Path, str]] = []
    touched_models: set[str] = set()
    for path, primary in _reference_files(root):
        data = read(path)
        if data is None:
            report.files.append(
                FileOutcome(path, SKIPPED, [f"cannot be read: {unreadable[path]}"])
            )
            report.unreadable.append(path)
            continue
        primary_data = read(primary)
        if primary_data is None:
            report.files.append(
                FileOutcome(
                    path,
                    SKIPPED,
                    [
                        f"its primary {primary.name} cannot be read "
                        f"({unreadable[primary]}), so which model it holds is unknown"
                    ],
                )
            )
            continue
        model_id = primary_data.get("model_id")
        test = tests.get(model_id) if isinstance(model_id, str) else None
        if test is None:
            report.files.append(
                FileOutcome(path, UNKNOWN_MODEL, [f"no discovered test {model_id!r}"])
            )
            continue
        touched_models.add(test.model_id)
        new, problems, notes = migrate_document(data, test)
        report.expression_differs += [
            f"{path.name} ({test.model_id}) {note}" for note in notes
        ]
        if problems:
            report.files.append(FileOutcome(path, SKIPPED, problems))
            continue
        if new is None:
            report.files.append(FileOutcome(path, MIGRATED))
            continue
        report.files.append(FileOutcome(path, _change_status(data, new)))
        planned.append((path, json.dumps(new, indent=2) + "\n"))
        if had_crlf[path]:
            report.crlf.append(path)

    report.orphan_soft_checks = _orphan_soft_checks(root)
    for model_id in sorted(touched_models):
        report.reaccept += [(model_id, v) for v in _needs_reaccept(tests[model_id])]

    spec_write = _migrate_spec(config, tests, report)
    if apply and report.spec_unreadable is None:
        for path, text in planned:
            _atomic_write_text(path, text)
        if spec_write is not None:
            _atomic_write_text(*spec_write)
    return report


def _migrate_spec(
    config: Config,
    tests: dict[str, TestModel],
    report: MigrationReport,
) -> tuple[Path, str] | None:
    """Decide the spec keys that name an expression; write nothing.

    Not a JSON-Patch: ``patch_apply``'s ops are add / remove / replace
    against the store, which cannot rename a key. The document is loaded,
    edited and handed back to the caller to write with the same writer and
    the same formatting a Save from the authoring tool produces — which
    re-serializes the WHOLE file, so a spec written by hand comes back in
    this shape.
    """
    spec_path = config.spec_path
    if not spec_path.exists():
        return None
    try:
        doc = load_spec_document(spec_path)
    except PatchError as exc:
        report.spec_unreadable = spec_path
        report.spec_skipped.append(f"{spec_path.name} cannot be read: {exc}")
        return None
    before = json.dumps(doc)
    #: (model, old key) -> [new key, occurrences]. A key reached through an
    #: override AND a leaf AND a plot series is one decision, counted.
    rewritten: dict[tuple[str, str], list] = {}
    for entry in doc.get("tests", []):
        model_id = entry.get("model")
        test = tests.get(model_id)
        variables = unit_test_variables(test) if test is not None else []
        if not variables:
            if model_id and model_id not in report.spec_not_considered:
                report.spec_not_considered.append(model_id)
            continue
        by_expression: dict[str, list[str]] = {}
        for v in variables:
            if v.expression:
                by_expression.setdefault(v.expression, []).append(v.name)
        patterns = set(entry.get("variables") or [])

        def skip(line: str) -> None:
            if line not in report.spec_skipped:
                report.spec_skipped.append(line)

        def target(
            key: str,
            model_id=model_id,
            by_expression=by_expression,
            patterns=patterns,
            skip=skip,
        ):
            """The result-file name ``key`` should become, or None."""
            names = _override_targets(key, by_expression, patterns)
            if not names:
                return None
            if len(names) > 1:
                skip(
                    f"{model_id}: {key!r} is the expression of "
                    f"{', '.join(repr(n) for n in names)} — which one it meant "
                    "is yours to say"
                )
                return None
            counted = rewritten.setdefault((model_id, key), [names[0], 0])
            counted[1] += 1
            return names[0]

        comparison = entry.get("comparison") or {}
        overrides = comparison.get("variable_overrides")
        if overrides:
            collisions = set()
            #: new key -> the override keys that would become it. Two
            #: SPELLINGS of one expression (``b[1, 1]``, ``b[1,1]``) name one
            #: variable, and renaming both into one dict dropped the first
            #: silently — the same decision as the collision above.
            wanted: dict[str, list[str]] = {}
            for key in overrides:
                names = _override_targets(key, by_expression, patterns)
                if len(names) != 1:
                    continue
                if names[0] != key and names[0] in overrides:
                    collisions.add(key)
                    skip(
                        f"{model_id}: {key!r} and {names[0]!r} are both override "
                        "keys — which one wins is yours to say"
                    )
                    continue
                wanted.setdefault(names[0], []).append(key)
            for new_key, keys in wanted.items():
                if len(keys) > 1:
                    collisions.update(keys)
                    skip(
                        f"{model_id}: {', '.join(repr(k) for k in keys)} are "
                        f"spellings of one expression and all name {new_key!r} — "
                        "which one wins is yours to say"
                    )
            comparison["variable_overrides"] = {
                (key if key in collisions else (target(key) or key)): value
                for key, value in overrides.items()
            }

        def leaf(node, target=target):
            node["variable"] = target(node["variable"]) or node["variable"]

        _walk_leaves(entry.get("metrics"), leaf)
        for extra in entry.get("plots") or []:
            if "var" in extra:
                extra["var"] = target(extra["var"]) or extra["var"]
            if "context" in extra:
                extra["context"] = [target(n) or n for n in extra["context"]]
    report.spec_rewritten = [
        (model_id, old, new, count)
        for (model_id, old), (new, count) in rewritten.items()
    ]
    if json.dumps(doc) == before:
        return None
    return spec_path, json.dumps(doc, indent=2) + "\n"


__all__ = ["FileOutcome", "MigrationReport", "migrate", "migrate_document"]
