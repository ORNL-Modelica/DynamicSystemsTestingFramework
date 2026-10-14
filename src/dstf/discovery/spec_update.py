"""Applying a validated patch set to ``test_spec.json``.

The one read-modify-write both ``dstf spec update`` and the authoring
Session's ``save`` go through: materialize the implicit tree where a
per-leaf op needs one, apply each model's ops to its entry, refuse a base
tolerance the patch made no number, refuse a key nothing reads, run the
D66 validator on every touched tree, refuse a criterion the patch left
unscorable (:func:`refuse_newly_unscorable` — the delta rule), then write
once. Nothing is written unless every group passes.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern
from ..config import Config
from ..storage.ref_index import _atomic_write_text
from .patch_apply import PatchError, apply_patch_to_document, load_spec_document

if TYPE_CHECKING:
    from ..storage.reference_store import ReferenceStore


def spec_entry(data: dict, model_id: str) -> dict | None:
    """The first entry for ``model_id`` — first wins, as ``apply_patch`` and
    discovery agree — or ``None``."""
    for e in data.get("tests", []):
        if isinstance(e, dict) and e.get("model") == model_id:
            return e
    return None


def is_literal_variable(pattern: str) -> bool:
    """A tracked-variable entry that names one variable. The resolver's rule:
    ``*`` and ``?`` are wildcards, a Modelica array index ``[1]`` is not."""
    return not is_glob_pattern(pattern)


def unknown_override_modes(entry: dict) -> dict[str, str]:
    """``{variable: mode}`` for every flat override naming a ``mode`` no
    metric is registered under. The flat path fails such a variable loudly
    (a typo must not change the algorithm); ``synthesize_implicit_tree``
    would fall back to nrmse, so a tree must not be written for it."""
    from ..comparison.registry import metric

    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    return {
        var: str(ov["mode"])
        for var, ov in overrides.items()
        if isinstance(ov, dict) and ov.get("mode") and metric(str(ov["mode"])) is None
    }


def implicit_tree_op(entry: dict, *, variables: Sequence[str] | None = None) -> dict:
    """The ``add /metrics`` op that materializes the implicit tree a
    flat-override entry scores with: one leaf per tracked variable, params
    from ``comparison.variable_overrides``, wrapped in ``and`` (see
    ``synthesize_implicit_tree``). ``variables`` replaces the entry's own
    list: the authoring Session passes the names the flat path actually
    scored, which for a test tracked by its ``.mo`` annotation are not in
    the spec at all (``spec update``, with no result in hand, can only use
    the entry's). Raises :class:`PatchError` when there are no variables to
    build leaves from."""
    from ..comparison.tree_spec import spec_to_raw, synthesize_implicit_tree

    variables = (
        list(variables) if variables is not None else entry.get("variables") or []
    )
    if not variables:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: the entry declares no tracked variables "
            "to build criteria from"
        )
    globs = [v for v in variables if not is_literal_variable(v)]
    if globs:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: {globs!r} are patterns, and a criterion "
            "per pattern would score a sentinel where the flat path scored the "
            "variables the result resolved"
        )
    bad_modes = unknown_override_modes(entry)
    if bad_modes:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for "
            f"{entry.get('model')!r}: {bad_modes!r} name a mode no metric is "
            "registered under; the flat path fails them, a tree would score nrmse"
        )
    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    tree = synthesize_implicit_tree(variables, variable_overrides=overrides)
    # A copy: a leaf's params are its override's own values (a `points`
    # table, a tolerance pair), and an op that shared them with the entry it
    # was read from would change when a later op edits that override.
    return {"op": "add", "path": "/metrics", "value": copy.deepcopy(spec_to_raw(tree))}


def materialize_implicit_metrics(
    entry: dict | None, patch: Sequence[dict]
) -> list[dict]:
    """``patch`` with :func:`implicit_tree_op` prepended when some op edits
    under ``/metrics/`` and neither the entry nor the patch itself supplies
    the tree.

    review 2026-07-06 finding 41: per-leaf ops target
    ``/metrics/children/N/...``, but a flat-override entry has no ``metrics``
    block and ``patch_apply`` refuses to auto-create an array parent. A
    missing entry is left to ``apply_patch`` (which creates it and then
    reports the real failure)."""
    ops = list(patch)
    if not needs_materialization(entry, ops):
        return ops
    assert entry is not None
    return [implicit_tree_op(entry), *ops]


def has_metrics(entry: dict | None) -> bool:
    """Whether ``entry`` has a ``metrics`` tree of its own. ``"metrics":
    null`` is none — and is no entry a file may hold: the parser refuses it,
    and so does every write (:func:`validate_entry_metric_tree`). Every "has
    it a tree" question — here and in the authoring Session — asks this,
    and none tests the key: it is asked of a patched entry before that
    entry is checked."""
    return entry is not None and entry.get("metrics") is not None


def needs_materialization(entry: dict | None, patch: Sequence[dict]) -> bool:
    """Some op edits under ``/metrics/`` and neither the entry nor the patch
    itself supplies the tree (a missing entry is left to ``apply_patch``)."""
    if entry is None or has_metrics(entry):
        return False
    edits_under = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in patch
    )
    supplies_tree = any(
        isinstance(op, dict) and op.get("path") == "/metrics" for op in patch
    )
    return edits_under and not supplies_tree


def validate_entry_metric_tree(
    data: dict, model_id: str, config: Config, store: ReferenceStore | None = None
) -> None:
    """When the patched entry has a ``metrics`` key, parse it as the spec
    parser does — a ``null`` there is refused, like any other value that is
    no tree — and run the D66 validator against the reference store's
    baseline roles. Skipped when the entry has no such key (implicit AND).
    ``store`` saves the caller that already holds one (the Session) a
    rescan of the whole partition per call."""
    entry = spec_entry(data, model_id)
    if entry is None or "metrics" not in entry:
        return

    from ..comparison.tree_spec import MetricSpecError, parse_metric_tree
    from ..comparison.validator import role_lookup_from_store, validate_tree
    from ..storage.reference_store import ReferenceStore

    try:
        tree = parse_metric_tree(entry["metrics"])
    except MetricSpecError as e:
        # The reason drops the path ("metrics.window: The window must end
        # after ..."): the tool shows the reason beside the row it names.
        raise PatchError(
            f"patched metric tree is invalid: {e}", reason=e.message
        ) from e
    except Exception as e:
        raise PatchError(f"patched metric tree is invalid: {e}", reason=str(e)) from e

    if store is None:
        store = ReferenceStore(config)
    lookup = role_lookup_from_store(store, model_id)
    errors = validate_tree(tree, lookup)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise PatchError(
            f"validator rejected the patched tree: {msg}",
            # Each message is a sentence that ends in its own full stop.
            reason=" ".join(e.message for e in errors),
        )


class UnscorableCriterion(PatchError):
    """A write refused by :func:`refuse_newly_unscorable`. ``reason`` is the
    criterion and what it lacks — ``range · pipe.T[1]: needs at least one of
    min_value / max_value`` — and ``str()`` leads with where it is."""


@dataclass(frozen=True)
class Unscorable:
    """One criterion of an entry that can never be scored."""

    #: What it IS — its metric, variable, baseline, window and params as
    #: sorted JSON — and nothing of where: the same text for a flat
    #: override and for the leaf its entry's implicit tree gives it.
    content: str
    #: Where it is, as a parse error says it: ``metrics.children[1]``.
    place: str
    #: ``range · pipe.T[1]: needs at least one of min_value / max_value``.
    reason: str


def _dotted(pointer: str) -> str:
    """A leaf's JSON-pointer path as the spec parser names a node:
    ``/metrics/children/3/children/0`` → ``metrics.children[3].children[0]``."""
    parts = pointer.strip("/").split("/")
    out = parts[0]
    for part in parts[1:]:
        out += f"[{part}]" if part.isdigit() else f".{part}"
    return out


def unscorable_criteria(entry: dict | None) -> list[Unscorable]:
    """Every criterion of ``entry`` whose mode cannot be built, as the
    comparison builds it: a ``range`` with neither bound, a ``scalar`` with
    no quantity, a tolerance that is no number or pair. Each parses, and
    then fails at every compare.

    What SCORES is what is looked at. With a ``metrics`` tree that is its
    leaves, and the flat ``comparison.variable_overrides`` are not checked
    at all: under a tree they score nothing (``comparator``: the tree
    "fully replaces" them), so one that could not be built there fails
    nothing — the tool itself leaves the overrides beside the tree it
    writes out. Without a tree it is the overrides. A tree that does not
    parse and an override naming a mode nothing is registered under are
    said by their own checks, not here."""
    from ..comparison.modes import resolve_mode
    from ..comparison.registry import DEFAULT_METRIC
    from ..comparison.tree_eval import build_leaf_mode
    from ..comparison.tree_spec import (
        LeafSpec,
        MetricSpecError,
        _walk_leaves_with_paths,
        parse_metric_tree,
        spec_to_raw,
    )
    from ..comparison.types import DEFAULT_TOLERANCE

    def found(leaf: LeafSpec, place: str, why: Exception) -> Unscorable:
        return Unscorable(
            content=json.dumps(spec_to_raw(leaf), sort_keys=True, default=str),
            place=place,
            reason=f"{leaf.metric} · {leaf.variable}: {why}",
        )

    if entry is None:
        return []
    out: list[Unscorable] = []
    if has_metrics(entry):
        try:
            tree = parse_metric_tree(entry["metrics"])
        except (MetricSpecError, ValueError, TypeError, AttributeError):
            return []
        for leaf, path in _walk_leaves_with_paths(tree, "/metrics"):
            try:
                build_leaf_mode(leaf, DEFAULT_TOLERANCE)
            except ValueError as e:
                out.append(found(leaf, _dotted(path), e))
        return out
    comparison = entry.get("comparison")
    overrides = (
        comparison.get("variable_overrides") if isinstance(comparison, dict) else None
    )
    if not isinstance(overrides, dict):
        return []
    unknown = unknown_override_modes(entry)
    for variable, override in overrides.items():
        if not isinstance(override, dict) or variable in unknown:
            continue  # a mode nothing is registered under fails at compare
        try:
            resolve_mode(override, override.get("tolerance", DEFAULT_TOLERANCE))
        except ValueError as e:
            # As the leaf `synthesize_implicit_tree` makes of it, so that a
            # patch which writes the entry's tree out changes nothing here.
            params = {k: v for k, v in override.items() if k != "mode"}
            leaf = LeafSpec(
                str(override.get("mode") or DEFAULT_METRIC), variable, params
            )
            place = f"comparison.variable_overrides[{variable}]"
            out.append(found(leaf, place, e))
    return out


#: The key a leaf of the entry AS IT WAS is marked with, on a copy the
#: patch is replayed over (:func:`_places_after`): its value is the place it
#: had. No spec key (a NUL), and never in a document that is kept.
_WAS = "\0was"


def _leaf_dicts(entry: dict | None) -> dict[str, dict]:
    """``{place: the raw dict}`` of the leaves of the entry's ``metrics``
    tree, places spelled as :func:`unscorable_criteria` spells them
    (``metrics.children[1]``); none for an entry with no tree. Whatever is
    no object is passed over: it is no criterion."""
    out: dict[str, dict] = {}
    if not isinstance(entry, dict):
        return out

    def walk(node: object, pointer: str) -> None:
        if not isinstance(node, dict):
            return
        children = node.get("children")
        if "combinator" in node and isinstance(children, list):
            for i, child in enumerate(children):
                walk(child, f"{pointer}/children/{i}")
        elif "metric" in node:
            out[_dotted(pointer)] = node

    walk(entry.get("metrics"), "/metrics")
    return out


def _places_after(
    before: dict | None, ops: Sequence[dict], model_id: str
) -> dict[str, str]:
    """``{place after the patch: the place it had before}`` for every leaf
    of ``before``'s tree that is still a leaf of the entry's tree once
    ``ops`` are applied: the same object, wherever it is now and whatever
    was added to it or taken from it. A leaf an op removed, or wrote over
    whole, is in no place after; one the patch added had no place before.

    Found by replaying the patch over a marked copy: an op names a path,
    so a mark inside a leaf rides with it through every op that does not
    replace that leaf.

    A flat override is not followed. It cannot move: its place is its
    variable, and its content names that variable, so an equal one can
    only stand where it stood — kept or written over, the count in
    :func:`refuse_newly_unscorable` says the same of it."""
    if before is None:
        return {}
    marked = copy.deepcopy(before)
    for place, leaf in _leaf_dicts(marked).items():
        leaf[_WAS] = place
    document = {"tests": [marked]}
    apply_patch_to_document(document, model_id, list(ops))
    after = spec_entry(document, model_id)
    return {
        place: leaf[_WAS] for place, leaf in _leaf_dicts(after).items() if _WAS in leaf
    }


def refuse_newly_unscorable(
    before: dict | None, ops: Sequence[dict], after: dict | None, model_id: str
) -> None:
    """THE rule for a criterion that can never be scored, applied where a
    FILE is written (``spec update`` and the tool's Save, both through
    :func:`apply_validated_patches`): the write is refused only for a
    criterion that is unscorable in ``after`` — ``before`` with ``ops``
    applied — and was not already there, with the same content, in
    ``before``.

    So an entry that already holds one — written by hand, or by an earlier
    version of the tool — takes every other edit, the edit that fills it
    and the edit that removes it; two of them do not block each other. A
    patch that adds one, or edits one and leaves it unscorable, is refused:
    that is the write that could know better.

    "Already there" is said of the criterion, not of a count. One that is
    still the object it was (:func:`_places_after`) with the content it had
    is the one that was there, wherever a removed sibling has shifted it.
    Any other unscorable criterion of ``after`` — one the patch added, or
    wrote over — is the one that was there only if an equal one of
    ``before`` is GONE from the criteria that score: a move (a remove and
    an add), a leaf wrapped in a group, the entry's implicit tree written
    out over its flat override. Counted alone — which this replaces — a
    patch that filled the bare ``range`` an entry held and added an equal
    bare one beside it was written: one before, one after.

    Refusing every unscorable criterion of the patched entry — the rule
    before that — refused the patch that fixed one of two, and every edit
    of an entry that held one it did not touch."""
    found = unscorable_criteria(after)
    if not found:
        return
    was = {item.place: item.content for item in unscorable_criteria(before)}
    came_from = _places_after(before, ops, model_id)
    # The leaves of `before` that are still leaves of `after`. Every other
    # unscorable criterion of `before` stands for one equal one of `after`:
    # a leaf that is gone, and each flat override — its content names its
    # variable, so the equal one can only be the one in its place.
    still_there = set(came_from.values())
    gone = Counter(
        content for place, content in was.items() if place not in still_there
    )
    for item in found:
        if was.get(came_from.get(item.place, "")) == item.content:
            continue  # untouched, or only shifted
        if gone[item.content] > 0:
            gone[item.content] -= 1
            continue  # taken out and put back: moved, wrapped, written out
        raise UnscorableCriterion(
            f"patched entry is invalid: tests[{model_id}].{item.place}: {item.reason}",
            reason=item.reason,
        )


def validate_entry_keys(data: dict, model_id: str) -> None:
    """The patched entry holds no key nothing reads (``check_entry_keys``,
    the spec parser's own check)."""
    from .spec_parser import EntryKeyError, check_entry_keys

    entry = spec_entry(data, model_id)
    if entry is None:
        return
    try:
        check_entry_keys(entry, model_id)
    except EntryKeyError as e:
        raise PatchError(f"patched entry is invalid: {e}", reason=e.message) from e


def _base_tolerance(entry: dict | None) -> object:
    """The entry's ``comparison.tolerance`` as written, or ``None``."""
    comparison = (entry or {}).get("comparison")
    return comparison.get("tolerance") if isinstance(comparison, dict) else None


def _same_base(before: object, after: object) -> bool:
    """Whether the patch left the base as it was: the same JSON text, keys
    sorted. Not ``==``: Python's ``True == 1`` at any depth (``[1] → [true]``
    is a change), and a NaN is not equal to itself (writing the NaN already
    there is none) — as text, a bool is ``true`` and a NaN is ``NaN``."""

    def text(value: object) -> str:
        return json.dumps(value, sort_keys=True, allow_nan=True)

    return text(before) == text(after)


def validate_entry_base_tolerance(data: dict, model_id: str, before: object) -> None:
    """The patched entry's base tolerance is ONE finite number, or unset —
    when the patch changed it from ``before``. The comparison refuses a test
    whose base is anything else; a patch must not write one. One already
    there is the comparison's to refuse, not this patch's: refusing it here
    would leave no patch able to edit the entry, or remove it."""
    from ..comparison.sides import base_tolerance_refused, is_base_tolerance

    after = _base_tolerance(spec_entry(data, model_id))
    if after is None or is_base_tolerance(after) or _same_base(before, after):
        return
    text = base_tolerance_refused("test_spec.json comparison.tolerance", after)
    raise PatchError(text, reason=text)


def validate_entry_plots(data: dict, model_id: str) -> None:
    """The patched entry's ``plots`` extras, when it has the key, parse."""
    from .plots import PlotsSpecError, parse_plots

    entry = spec_entry(data, model_id)
    if entry is None or "plots" not in entry:
        return
    try:
        parse_plots(entry["plots"], path=f"tests[{model_id}].plots")
    except PlotsSpecError as e:
        raise PatchError(f"patched plots are invalid: {e}", reason=str(e)) from e


def apply_validated_patches(
    spec_path: Path,
    groups: Sequence[tuple[str, Sequence[dict]]],
    config: Config,
    store: ReferenceStore | None = None,
) -> list[tuple[str, list[dict]]]:
    """Apply every group's ops to its model's entry in one read-modify-write.

    Per group: materialize, apply, validate. The file is written once,
    atomically and LF (D122), only after every group passed. Returns the ops
    actually applied per group — materialization included — so a caller can
    say what it did."""
    if not groups:
        return []  # nothing to apply; the file is not rewritten
    data = load_spec_document(spec_path)
    applied: list[tuple[str, list[dict]]] = []
    for model_id, ops in groups:
        full = materialize_implicit_metrics(spec_entry(data, model_id), ops)
        was = copy.deepcopy(spec_entry(data, model_id))
        apply_patch_to_document(data, model_id, full)
        validate_entry_base_tolerance(data, model_id, _base_tolerance(was))
        validate_entry_keys(data, model_id)
        validate_entry_metric_tree(data, model_id, config, store)
        refuse_newly_unscorable(was, full, spec_entry(data, model_id), model_id)
        validate_entry_plots(data, model_id)
        applied.append((model_id, full))
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(spec_path, json.dumps(data, indent=2) + "\n")
    return applied
