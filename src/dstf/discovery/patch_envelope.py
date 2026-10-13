"""The JSON-Patch envelope ``dstf spec update`` reads and the authoring
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
    if isinstance(data, dict) and "patches" in data and "model" in data:
        raise _bad(label, "both a 'model' and a 'patches' key — say which")
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
    return ConfigError(
        f"{label}: not a JSON-Patch envelope — expected {_SHAPES}, got {got}"
    )
