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
