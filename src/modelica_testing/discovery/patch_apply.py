"""RFC 6902 JSON-Patch applier with whitelist + unknown-key preservation.

Used by :func:`cli.cmd_spec_update` (and 6.4 spec-update round-trip) to
apply small, scoped edits to ``test_spec.json`` without clobbering
hand-authored fields. The reporter emits patches of the form::

    {
      "model": "BouncingBall",
      "patch": [
        {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
        {"op": "add",     "path": "/comparison/variable_overrides/v/tube_rel",
         "value": 0.02}
      ]
    }

Paths are RFC 6901 JSON-Pointers **relative to the matched test entry**
(so `/comparison/...` and `/metrics/...`, not `/tests/3/...`). A path
whitelist constrains which subtrees the applier will touch — the
reporter is explicitly not allowed to mutate arbitrary structure.

This module is self-contained: no external JSON-Patch / JSON-Pointer
libraries. The surface is small (replace/add/remove on a single path
each), so the RFC 6901 unescape rules fit in ~10 lines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional


class PatchError(Exception):
    """Raised when a patch cannot be applied cleanly."""

    def __init__(self, message: str, path: Optional[str] = None):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


_SUPPORTED_OPS = {"replace", "add", "remove"}


def apply_patch(
    spec_path: Path,
    model_id: str,
    patch_ops: list[dict],
    *,
    whitelist: tuple[str, ...] = ("/comparison", "/metrics"),
    write: bool = True,
) -> dict:
    """Apply ``patch_ops`` to the entry in ``spec_path`` matching ``model_id``.

    Returns the (mutated) spec dict. When ``write=True`` (default), also
    persists it back to disk preserving key order. Raise :class:`PatchError`
    on any issue; the caller (CLI) surfaces the message and aborts without
    writing if the dry-run path signals invalidity.
    """
    data = _load_spec(spec_path)
    entry = _find_or_create_entry(data, model_id)

    for i, op in enumerate(patch_ops):
        try:
            _apply_one_op(entry, op, whitelist)
        except PatchError as e:
            raise PatchError(
                f"patch_ops[{i}] ({op!r}): {e}", path=e.path,
            ) from None

    if write:
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )
    return data


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_spec(spec_path: Path) -> dict:
    if not spec_path.exists():
        return {"tests": []}
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PatchError(f"cannot read {spec_path}: {e}")
    if not isinstance(data, dict):
        raise PatchError("spec root must be an object")
    data.setdefault("tests", [])
    if not isinstance(data["tests"], list):
        raise PatchError("spec 'tests' must be a list")
    return data


def _find_or_create_entry(data: dict, model_id: str) -> dict:
    for entry in data["tests"]:
        if isinstance(entry, dict) and entry.get("model") == model_id:
            return entry
    entry = {"model": model_id}
    data["tests"].append(entry)
    return entry


def _apply_one_op(entry: dict, op: dict, whitelist: tuple[str, ...]) -> None:
    op_name = op.get("op")
    path = op.get("path")
    if op_name not in _SUPPORTED_OPS:
        raise PatchError(
            f"unsupported op {op_name!r} (supported: {sorted(_SUPPORTED_OPS)})",
            path=path,
        )
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError("'path' must be an RFC 6901 JSON-Pointer starting with /", path=path)
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
    value = op["value"]
    if op_name == "replace":
        _set_at(entry, tokens, value, path, must_exist=True)
    elif op_name == "add":
        _set_at(entry, tokens, value, path, must_exist=False)


def _split_pointer(pointer: str) -> list[str]:
    """RFC 6901: split on '/' after a leading slash; unescape ~1 → / and ~0 → ~."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"JSON-Pointer must start with /", path=pointer)
    raw = pointer[1:].split("/")
    # Order matters: ~1 before ~0 per RFC 6901.
    return [seg.replace("~1", "/").replace("~0", "~") for seg in raw]


def _set_at(obj: Any, tokens: list[str], value: Any, path: str, *,
            must_exist: bool) -> None:
    if not tokens:
        raise PatchError("empty path points at the entry root; refuse", path=path)
    *parents, last = tokens
    cur = obj
    for tok in parents:
        cur = _descend(cur, tok, path, create_missing=not must_exist)
    if isinstance(cur, list):
        idx = _list_index(cur, last, path, allow_append=not must_exist)
        if must_exist and idx >= len(cur):
            raise PatchError(f"list index {idx} out of range (len {len(cur)})", path=path)
        if idx == len(cur):
            cur.append(value)
        else:
            cur[idx] = value
    elif isinstance(cur, dict):
        if must_exist and last not in cur:
            raise PatchError(f"key {last!r} not present (use 'add' for new keys)", path=path)
        cur[last] = value
    else:
        raise PatchError(
            f"cannot set on non-container {type(cur).__name__}", path=path,
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
            f"cannot remove from non-container {type(cur).__name__}", path=path,
        )


def _descend(cur: Any, tok: str, path: str, *, create_missing: bool) -> Any:
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
            cur[tok] = {}
        return cur[tok]
    raise PatchError(
        f"cannot descend into {type(cur).__name__}", path=path,
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
