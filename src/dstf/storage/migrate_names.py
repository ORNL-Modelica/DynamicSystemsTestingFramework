"""``dstf migrate-variable-names`` — stored baselines and spec keys move
from a UnitTests variable's expression text to its result-file name
(``<instance>.x[i]``).

The variable-identity change moved the reference JSON's variable names and
the ``test_spec.json`` keys that named an expression. Those two shapes are
what another tree depends on, so this command IS the coordination: it
rewrites both, dry-run by default, and reports everything it could not do
rather than guessing.

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
from ..discovery.mo_parser import _normalize_expression
from ..discovery.patch_apply import load_spec_document
from ..discovery.test_registry import TestModel, discover_tests
from ..discovery.unit_tests import unit_test_variables
from .ref_index import _atomic_write_text

#: ``status`` of a :class:`FileOutcome`. ``renamed`` and ``expression-added``
#: are separate because they are different events for a reviewer: the first
#: moved a key every other file keys off, the second only wrote the field the
#: writer now always writes.
RENAMED = "renamed"
EXPRESSION_ADDED = "expression-added"
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
    #: ``(model_id, old key, new key)`` per rewritten ``test_spec.json`` key.
    spec_rewritten: list[tuple[str, str, str]] = field(default_factory=list)
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

    @property
    def clean(self) -> bool:
        """Nothing was left undone — the command's exit code."""
        return not self.spec_skipped and all(
            f.status in (RENAMED, EXPRESSION_ADDED, MIGRATED) for f in self.files
        )


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
    problems: list[str] = []
    notes: list[str] = []
    #: stored name (and its normal form) -> result-file name, for this
    #: file's own override tier.
    rename: dict[str, str] = {}
    variables = []
    for entry in data.get("variables", []):
        stored = entry.get("name", "")
        index = entry.get("index")
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
        elif target.expression and _normalize_expression(stored) == target.expression:
            rename[stored] = target.name
            rename[target.expression] = target.name
            variables.append(_entry(entry, target.name, target.expression))
        else:
            problems.append(
                f"index {index}: stored {stored!r}, expected {target.expression!r} "
                f"(or {target.name!r}, already migrated)"
            )
    if problems:
        return None, problems, notes
    new = dict(data)
    if "variables" in data:
        new["variables"] = variables
    # The baseline's OWN override tier is keyed like the spec's: left
    # un-renamed it silently stops applying.
    comparison = data.get("comparison") or {}
    overrides = comparison.get("variable_overrides")
    if overrides:
        new["comparison"] = {
            **comparison,
            "variable_overrides": {rename.get(k, k): v for k, v in overrides.items()},
        }
    if json.dumps(new) == json.dumps(data):
        return None, [], notes
    return new, [], notes


def _reference_files(root: Path) -> list[tuple[Path, Path]]:
    """``(file, the primary ref that names its model)`` for every primary and
    soft-check file of every ``<Backend>/<os>/`` partition, whatever backend
    the run is configured for — a tree is migrated once, not once per
    backend someone happens to have installed.

    Companions are not listed: a companion is another source's data, keyed
    by the names that source gives its own quantities.
    """
    out = []
    for ref in sorted(root.glob("*/*/ref_*.json")):
        out.append((ref, ref))
        soft = ref.parent / "soft_checks" / ref.stem
        # A soft-check file carries no ``model_id`` of its own; the primary
        # beside it is what says which test it belongs to.
        out.extend((p, ref) for p in sorted(soft.glob("*.json")))
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
    return [
        p for p in test.variable_patterns if _normalize_expression(p) in expressions
    ]


def migrate(config: Config, *, apply: bool) -> MigrationReport:
    """Rewrite every partition's baselines and the spec. Writes only when
    ``apply``; the report is identical either way."""
    report = MigrationReport()
    tests = {t.model_id: t for t in discover_tests(config)}
    root = config.reference_root
    assert root is not None  # resolved in Config.__post_init__

    touched_models: set[str] = set()
    for path, primary in _reference_files(root):
        data = json.loads(path.read_text(encoding="utf-8"))
        model_id = json.loads(primary.read_text(encoding="utf-8")).get("model_id")
        test = tests.get(model_id)
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
        renamed = [v.get("name") for v in new.get("variables", [])] != [
            v.get("name") for v in data.get("variables", [])
        ]
        report.files.append(FileOutcome(path, RENAMED if renamed else EXPRESSION_ADDED))
        if apply:
            _atomic_write_text(path, json.dumps(new, indent=2) + "\n")

    for model_id in sorted(touched_models):
        report.reaccept += [(model_id, v) for v in _needs_reaccept(tests[model_id])]

    _migrate_spec(config, tests, report, apply=apply)
    return report


def _migrate_spec(
    config: Config,
    tests: dict[str, TestModel],
    report: MigrationReport,
    *,
    apply: bool,
) -> None:
    """Rewrite the spec keys that name an expression.

    Not a JSON-Patch: ``patch_apply``'s ops are add / remove / replace
    against the store, which cannot rename a key. The document is loaded,
    edited and written back with the same writer and the same formatting a
    Save from the authoring tool produces — which re-serializes the WHOLE
    file, so a spec written by hand comes back in this shape.
    """
    spec_path = config.spec_path
    if not spec_path.exists():
        return
    doc = load_spec_document(spec_path)
    before = json.dumps(doc)
    for entry in doc.get("tests", []):
        model_id = entry.get("model")
        test = tests.get(model_id)
        if test is None:
            continue
        by_expression: dict[str, list[str]] = {}
        for v in unit_test_variables(test):
            if v.expression:
                by_expression.setdefault(v.expression, []).append(v.name)
        patterns = set(entry.get("variables") or [])

        def target(
            key: str, model_id=model_id, by_expression=by_expression, patterns=patterns
        ):
            """The result-file name ``key`` should become, or None."""
            if key in patterns:
                return None  # a pattern names a variable of its own
            names = by_expression.get(_normalize_expression(key))
            if not names:
                return None
            if len(names) > 1:
                report.spec_skipped.append(
                    f"{model_id}: {key!r} is the expression of "
                    f"{', '.join(repr(n) for n in names)} — which one it meant "
                    "is yours to say"
                )
                return None
            report.spec_rewritten.append((model_id, key, names[0]))
            return names[0]

        comparison = entry.get("comparison") or {}
        if comparison.get("variable_overrides"):
            comparison["variable_overrides"] = {
                (target(k) or k): v for k, v in comparison["variable_overrides"].items()
            }

        def leaf(node, target=target):
            node["variable"] = target(node["variable"]) or node["variable"]

        _walk_leaves(entry.get("metrics"), leaf)
        for plot in entry.get("plots") or []:
            for series in plot.get("series") or []:
                if "var" in series:
                    series["var"] = target(series["var"]) or series["var"]
    if apply and json.dumps(doc) != before:
        _atomic_write_text(spec_path, json.dumps(doc, indent=2) + "\n")


__all__ = ["FileOutcome", "MigrationReport", "migrate", "migrate_document"]
