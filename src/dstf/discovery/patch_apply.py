"""RFC 6902 JSON-Patch applier with whitelist + unknown-key preservation.

Used by ``dstf spec update`` (through :mod:`spec_update`) and the authoring
Session to apply small, scoped edits to ``test_spec.json`` without
clobbering hand-authored fields. The ops arrive in the envelope
:mod:`patch_envelope` reads; one model's ops look like::

    [
      {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
      {"op": "add",     "path": "/comparison/variable_overrides/v/tube_rel",
       "value": 0.02}
    ]

Paths are RFC 6901 JSON-Pointers **relative to the matched test entry**
(so `/comparison/...`, `/metrics/...` and `/plots/...`, not `/tests/3/...`). A path
whitelist constrains which subtrees the applier will touch — an editor
is not allowed to mutate arbitrary structure.

This module is self-contained: no external JSON-Patch / JSON-Pointer
libraries. The surface is small (replace/add/remove on a single path
each), so the RFC 6901 unescape rules fit in ~10 lines.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PatchError(Exception):
    """Raised when a patch cannot be applied cleanly."""

    def __init__(
        self, message: str, path: str | None = None, *, reason: str | None = None
    ):
        self.path = path
        #: The plain sentence a person reads: ``str()`` keeps the op and the
        #: pointer for the CLI, the tool shows this beside the edit it came
        #: from (`authoring.errors.user_text`).
        self.reason = reason if reason is not None else message
        super().__init__(f"{path}: {message}" if path else message)


_SUPPORTED_OPS = {"replace", "add", "remove"}

#: The subtrees of a test entry an editor may touch. One constant, shared by
#: both entry points below, so the two cannot drift.
DEFAULT_WHITELIST: tuple[str, ...] = ("/comparison", "/metrics", "/plots")


def apply_patch_to_document(
    data: dict,
    model_id: str,
    patch_ops: list[dict],
    *,
    whitelist: tuple[str, ...] = DEFAULT_WHITELIST,
) -> dict:
    """Apply ``patch_ops`` to the entry in the parsed document ``data`` that
    matches ``model_id`` — to ``{"model": model_id}`` where there is none —
    and put the patched entry in ``data``'s tests. Returns ``data``.
    Raises :class:`PatchError` naming the failing op. The authoring
    Session's in-memory document goes through here; so does the disk path,
    ``spec_update.apply_validated_patches``.

    All or nothing: the ops are applied to a copy of the entry, which
    replaces it (or, for a new model, is appended) only once every op has
    applied. A refused patch leaves ``data`` as it was — not the earlier
    ops of the patch, not a ``"metrics": {}`` an op's descent made before
    the op was refused, not a ``{"model": ...}`` for a model it named. The
    entry is a new object afterwards: read it back from ``data``."""
    index = _entry_index(data, model_id)
    entry = (
        {"model": model_id} if index is None else copy.deepcopy(data["tests"][index])
    )
    for i, op in enumerate(patch_ops):
        try:
            _apply_one_op(entry, op, whitelist)
        except PatchError as e:
            raise PatchError(
                f"patch_ops[{i}] ({op!r}): {e}",
                path=e.path,
                reason=e.reason,
            ) from None
    if index is None:
        data["tests"].append(entry)
    else:
        data["tests"][index] = entry
    return data


def load_spec_document(spec_path: Path) -> dict:
    """The spec document a patch is applied to: the file's
    (``spec_parser.read_spec_document`` — the one reader, a
    :class:`~dstf.config.ConfigError` for a file that is no spec), or the
    empty document when there is no file yet."""
    from .spec_parser import read_spec_document

    data = read_spec_document(spec_path)
    return {"tests": []} if data is None else data


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _entry_index(data: dict, model_id: str) -> int | None:
    """Where ``model_id``'s entry is in ``data["tests"]``; None for none."""
    matches = [
        i
        for i, entry in enumerate(data["tests"])
        if isinstance(entry, dict) and entry.get("model") == model_id
    ]
    if len(matches) > 1:
        # review 2026-07-06 finding 51: duplicates — first entry wins
        # everywhere (discovery + edit helpers use the same rule); warn so
        # the user notices the dead entries.
        logger.warning(
            "test_spec has %d duplicate entries for model '%s'; "
            "patching the first — remove the duplicates",
            len(matches),
            model_id,
        )
    return matches[0] if matches else None


def _apply_one_op(entry: dict, op: dict, whitelist: tuple[str, ...]) -> None:
    op_name = op.get("op")
    path = op.get("path")
    if op_name not in _SUPPORTED_OPS:
        raise PatchError(
            f"unsupported op {op_name!r} (supported: {sorted(_SUPPORTED_OPS)})",
            path=path,
        )
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError(
            "'path' must be an RFC 6901 JSON-Pointer starting with /", path=path
        )
    if not any(path == w or path.startswith(w + "/") for w in whitelist):
        raise PatchError(
            f"path not in whitelist {whitelist}",
            path=path,
        )
    tokens = _split_pointer(path)
    if op_name == "remove":
        _remove_at(entry, tokens, path)
        return
    if "value" not in op:
        raise PatchError(f"{op_name!r} requires 'value'", path=path)
    # A copy: the document never shares an object with the op that put it
    # there. Written in by reference, a later op of the same patch edits the
    # earlier op's own value — and the list a caller still holds is then no
    # longer the patch it gave: applied again (a replay, a second document)
    # it does those later ops twice.
    value = copy.deepcopy(op["value"])
    if op_name == "replace":
        _set_at(entry, tokens, value, path, must_exist=True)
    elif op_name == "add":
        _set_at(entry, tokens, value, path, must_exist=False)


def _split_pointer(pointer: str) -> list[str]:
    """RFC 6901: split on '/' after a leading slash; unescape ~1 → / and ~0 → ~."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError("JSON-Pointer must start with /", path=pointer)
    raw = pointer[1:].split("/")
    # Order matters: ~1 before ~0 per RFC 6901.
    return [seg.replace("~1", "/").replace("~0", "~") for seg in raw]


def _set_at(
    obj: Any, tokens: list[str], value: Any, path: str, *, must_exist: bool
) -> None:
    if not tokens:
        raise PatchError("empty path points at the entry root; refuse", path=path)
    *parents, last = tokens
    cur = obj
    for i, tok in enumerate(parents):
        next_token = parents[i + 1] if i + 1 < len(parents) else last
        cur = _descend(
            cur, tok, path, create_missing=not must_exist, next_token=next_token
        )
    if isinstance(cur, list):
        idx = _list_index(cur, last, path, allow_append=not must_exist)
        if idx > len(cur) or (must_exist and idx >= len(cur)):
            raise PatchError(
                f"list index {idx} out of range (len {len(cur)})", path=path
            )
        if must_exist:
            cur[idx] = value
        else:
            # review 2026-07-06 finding 49: RFC 6902 `add` on an existing
            # array index INSERTS before it (it used to replace, silently
            # destroying a sibling leaf); idx == len(cur) appends, matching
            # the '-' token.
            cur.insert(idx, value)
    elif isinstance(cur, dict):
        if must_exist and last not in cur:
            raise PatchError(
                f"key {last!r} not present (use 'add' for new keys)", path=path
            )
        cur[last] = value
    else:
        raise PatchError(
            f"cannot set on non-container {type(cur).__name__}",
            path=path,
        )


def _remove_at(obj: Any, tokens: list[str], path: str) -> None:
    if not tokens:
        raise PatchError("empty path points at the entry root; refuse", path=path)
    *parents, last = tokens
    cur = obj
    for tok in parents:
        cur = _descend(cur, tok, path, create_missing=False)
    if isinstance(cur, list):
        idx = _list_index(cur, last, path, allow_append=False)
        if idx >= len(cur):
            raise PatchError(f"list index {idx} out of range", path=path)
        del cur[idx]
    elif isinstance(cur, dict):
        if last not in cur:
            raise PatchError(f"key {last!r} not present", path=path)
        del cur[last]
    else:
        raise PatchError(
            f"cannot remove from non-container {type(cur).__name__}",
            path=path,
        )


def _descend(
    cur: Any,
    tok: str,
    path: str,
    *,
    create_missing: bool,
    next_token: str | None = None,
) -> Any:
    if isinstance(cur, list):
        idx = _list_index(cur, tok, path, allow_append=False)
        if idx >= len(cur):
            raise PatchError(f"list index {idx} out of range", path=path)
        return cur[idx]
    if isinstance(cur, dict):
        if tok not in cur:
            if not create_missing:
                raise PatchError(
                    f"key {tok!r} not present (use 'add' for new keys)",
                    path=path,
                )
            # review 2026-07-06 finding 49: never auto-create a dict where
            # the path expects an ARRAY — a per-leaf op like
            # /metrics/children/0/... against a spec without a metrics tree
            # used to silently create {"children": {"0": ...}}, which the
            # tree validator then rejects. Fail loudly instead.
            if next_token is not None and (next_token == "-" or next_token.isdigit()):
                raise PatchError(
                    f"key {tok!r} not present and the next token "
                    f"{next_token!r} is an array index — refusing to "
                    f"auto-create a dict in place of an array (the test "
                    f"entry likely has no explicit 'metrics' tree yet)",
                    path=path,
                )
            cur[tok] = {}
        return cur[tok]
    raise PatchError(
        f"cannot descend into {type(cur).__name__}",
        path=path,
    )


def _list_index(cur: list, tok: str, path: str, *, allow_append: bool) -> int:
    if tok == "-":
        if not allow_append:
            raise PatchError("'-' (append) not allowed on non-add op", path=path)
        return len(cur)
    try:
        idx = int(tok)
    except ValueError:
        raise PatchError(f"list index {tok!r} must be an integer", path=path) from None
    if idx < 0:
        raise PatchError(f"list index {idx} must be non-negative", path=path)
    return idx
