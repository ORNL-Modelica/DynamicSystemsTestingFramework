"""The spec as the Session holds it: the document as loaded, its content
hash, and the ops staged against it."""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..discovery.patch_apply import apply_patch_to_document
from ..discovery.spec_parser import spec_document_from_bytes


@dataclass(frozen=True)
class PatchOp:
    """One RFC 6902 op against one model's spec entry. ``path`` is relative
    to the entry, as ``apply_patch`` reads it."""

    model_id: str
    op: str  # "add" | "replace" | "remove"
    path: str
    value: Any = None
    #: The ``add /metrics`` `Session.stage` prepends to a flat entry's first
    #: per-leaf edit: the implicit tree written out, scoring the same. The
    #: pending drawer diffs against the saved entry WITH it (`preview_base`)
    #: so an edit reads as itself, not as the whole tree added; and while
    #: the entry's ``metrics`` is this tree and nothing more, the ops that
    #: wrote it are no edit — they are dropped, whatever else is pending
    #: but an edit of the ``variable_overrides`` the tree is built from
    #: (`Session.stage`). Not part of the op: not in `raw()`, not in its
    #: equality.
    materializes: bool = field(default=False, compare=False)
    #: An ``add /metrics`` that is an EDIT of the tree its entry scores by,
    #: given whole — one gesture on a folded criteria row (D152,
    #: `family_rows.as_one`) — rather than a new tree: `Session.stage`
    #: writes a flat entry's implicit tree out first, marked, exactly as
    #: for a per-leaf op, so the preview diffs the edit against that tree
    #: and says Save writes it. Not part of the op, as ``materializes``.
    edits_tree: bool = field(default=False, compare=False)
    #: What the op did, in words, for the preview to say above its entry's
    #: diff — ``(key, sentence)`` pairs: where a later op replaces this one
    #: (`Session.stage`), a sentence under the same key replaces its
    #: sentence and any other is kept, so a value typed over is one line
    #: and two gestures are two. Not part of the op.
    notes: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    #: Which act of the user's staged it (`Session.stage` numbers its
    #: calls): one Ctrl+click on a flat test's plot is two ops — the tree
    #: written out, and the row — and one edit; so is an add over twenty
    #: Indices, and a change of a folded row's twenty criteria. An op that
    #: supersedes the last one keeps that one's number: a value typed over
    #: is the same edit. None for an op nothing staged (a test's own list):
    #: it counts as itself (`edit_counts`). Not part of the op.
    gesture: int | None = field(default=None, compare=False)

    def raw(self) -> dict:
        """The op as ``apply_patch`` and the envelope carry it."""
        raw: dict = {"op": self.op, "path": self.path}
        if self.op != "remove":
            raw["value"] = copy.deepcopy(self.value)
        return raw


def edit_counts(pending: Iterable[PatchOp]) -> Counter[str]:
    """By model, how many EDITS ``pending`` holds of its entry: the things
    a person did (`PatchOp.gesture`), not the ops they took. The ONE count
    — the header's ``N pending edits``, Save's caption, the Suite's Edits
    cell, the Tests drawer's mark, the strip's ``N unsaved edits`` and the
    preview's ``N edits`` all read it, so no two of them can disagree."""
    edits = {
        (op.model_id, ("op", index) if op.gesture is None else op.gesture)
        for index, op in enumerate(pending)
    }
    return Counter(model_id for model_id, _ in edits)


def edit_count(pending: Iterable[PatchOp]) -> int:
    """How many edits ``pending`` holds in all (`edit_counts`)."""
    return sum(edit_counts(pending).values())


def content_hash(path: Path) -> str | None:
    """SHA-256 of the file's bytes; ``None`` when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def json_equal(a: Any, b: Any) -> bool:
    """Whether ``a`` and ``b`` are one JSON value: objects by their members
    in any order, arrays in order, numbers by value (``1`` is ``1.0``), and
    ``true`` is no number — Python's ``==`` calls ``True`` and ``1`` equal."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return False
    return bool(a == b)


@dataclass
class SpecDocument:
    path: Path
    loaded: dict  # as read; never mutated
    content_hash: str | None  # of the bytes ``loaded`` came from
    pending: list[PatchOp] = field(default_factory=list)
    #: ``(len(pending), document)`` — the last replay, reused while pending
    #: only grows (the Session never edits an op in place; it replaces the
    #: document). A rescore over every test is then one replay, not one per
    #: test.
    _effective_cache: tuple[int, dict] | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def load(cls, path: Path) -> SpecDocument:
        """Read ``path`` once: the hash and the document come from the same
        bytes. A missing file is the empty document; one that is no spec —
        no UTF-8 text, no JSON, a non-object root, a non-list ``tests`` — is
        a :class:`ConfigError` (``spec_parser.spec_document_from_bytes``,
        the one decoder every command reads the file through), because the
        Session must not save over a file it could not read. A Save writes
        a file read with a BOM back without it."""
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return cls(path=path, loaded={"tests": []}, content_hash=None)
        except OSError as e:
            raise ConfigError(f"{path}: {e}") from e
        data = spec_document_from_bytes(raw, str(path))
        return cls(path=path, loaded=data, content_hash=hashlib.sha256(raw).hexdigest())

    def on_disk_changed(self) -> bool:
        return content_hash(self.path) != self.content_hash

    def grouped(self) -> list[tuple[str, list[dict]]]:
        """Pending ops as ``(model_id, raw ops)`` groups: models in
        first-seen order, ops in staging order."""
        groups: dict[str, list[dict]] = {}
        for op in self.pending:
            groups.setdefault(op.model_id, []).append(op.raw())
        return list(groups.items())

    def saved_entry(self, model_id: str) -> dict | None:
        """The model's entry as LOADED — whatever is pending — as a copy the
        caller may keep; ``None`` when the loaded document has no entry for
        it (a pending op may be creating one)."""
        from ..discovery.spec_update import spec_entry

        entry = spec_entry(self.loaded, model_id)
        return copy.deepcopy(entry) if entry is not None else None

    def as_saved(self, model_id: str) -> bool:
        """Whether the model's entry with the pending ops applied is the
        one the file holds (:func:`json_equal`): ops that put it back are no
        edit of it. A model the file has no entry for is as saved while its
        entry would hold its model and nothing else — an entry that says
        nothing (a tree added to an annotation-only test, then removed).
        The document's half of the rule: a ``metrics`` member that is the
        tree the saved entry already scores by is no edit either, and only
        the Session knows that tree — it takes those ops out before asking
        this (`Session._its_tree_written_out`)."""
        from ..discovery.spec_update import spec_entry

        bare = {"model": model_id}
        return json_equal(
            self.entry(model_id) or bare, spec_entry(self.loaded, model_id) or bare
        )

    def preview_base(self, model_id: str) -> dict | None:
        """What the pending drawer diffs the model's entry against: the
        saved entry with the model's materializing ops applied (a new entry
        starts as ``{"model": model_id}``), a copy; with none of them, the
        saved entry (``None`` for a new one)."""
        from ..discovery.spec_update import spec_entry

        ops = [
            op.raw()
            for op in self.pending
            if op.model_id == model_id and op.materializes
        ]
        if not ops:
            return self.saved_entry(model_id)
        data = copy.deepcopy(self.loaded)
        apply_patch_to_document(data, model_id, ops)
        return copy.deepcopy(spec_entry(data, model_id))

    def effective(self) -> dict:
        """A copy of ``loaded`` with every pending op applied."""
        return copy.deepcopy(self._effective())

    def entry(self, model_id: str) -> dict | None:
        """The model's entry in the effective document — read it, never
        edit it (it is the cached replay)."""
        from ..discovery.spec_update import spec_entry

        return spec_entry(self._effective(), model_id)

    def _effective(self) -> dict:
        n = len(self.pending)
        if self._effective_cache is not None and self._effective_cache[0] == n:
            return self._effective_cache[1]
        data = copy.deepcopy(self.loaded)
        for model_id, ops in self.grouped():
            apply_patch_to_document(data, model_id, ops)
        self._effective_cache = (n, data)
        return data
