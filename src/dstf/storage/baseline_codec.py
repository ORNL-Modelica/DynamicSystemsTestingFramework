"""THE reader and writer of stored-baseline files — a primary reference
(``ref_NNNN.json``), a soft check, a JSON companion's data. Every other
module sees :mod:`dstf.storage.baseline` types; nothing else parses or
writes these files, so a second format, or a new kind of data, changes this
module and the types, not their readers.

The number rule. A value is written with the fewest significant digits that
identify every number of its precision — 9 for a float32, 17 for a float64
(IEEE 754 round-trip bounds) — and JSON then spells it at its shortest. A
file whose ``sampling.precision`` is ``"single"`` is read back THROUGH
float32, so each stored number is the run's own; writing re-rounds it to 9
digits, which gives the same spelling back. Measured 2026-10-15 over 955 real
baselines (TRANSFORM + examples): decode -> encode is byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .baseline import (
    PRIMARY_BASELINE,
    Baseline,
    DiagnosticSummary,
    ReferenceFile,
    Sampling,
    StoredVariable,
)
from .ref_index import _atomic_write_text

#: Significant digits a value is written with, by its precision: the fewest
#: that identify EVERY number of it (binary32: 9, binary64: 17). A double
#: written with 15 was more often than not another double than the run's.
SINGLE_DIGITS = 9
DOUBLE_DIGITS = 17

#: ``sampling.precision`` of a file whose time and value arrays were ALL
#: single precision in the run it was accepted from.
SINGLE = "single"
DOUBLE = "double"

#: Significant digits a number is SHOWN with, by the precision it was
#: recorded with: all a float32 holds (7), all a float64 does (15). Shown,
#: never stored — storage is `SINGLE_DIGITS` / `DOUBLE_DIGITS`.
SINGLE_SHOWN = 7
DOUBLE_SHOWN = 15


# --- numbers ---------------------------------------------------------------


def stored_numbers(arr: np.ndarray) -> list[float]:
    """``arr`` as the Python floats a baseline stores: rounded to the digits
    that identify a value of its dtype (:data:`SINGLE_DIGITS` for float32,
    :data:`DOUBLE_DIGITS` otherwise)."""
    sig = SINGLE_DIGITS if arr.dtype == np.float32 else DOUBLE_DIGITS
    return [float(f"%.{sig}g" % v) for v in arr]


def recorded_digits(arr: np.ndarray) -> int:
    """The digits a RUN's number is shown with: its array's precision."""
    return SINGLE_SHOWN if np.asarray(arr).dtype == np.float32 else DOUBLE_SHOWN


def shown_digits(sampling: Sampling | None) -> int:
    """The digits a stored baseline's number is shown with: the precision
    its file says it was recorded with. The one place a missing
    ``sampling`` is read for display.

    A file without ``sampling`` was written before it existed, under the
    float32 default (the owner, 2026-10-18: "the default is float32
    currently"); the next accept writes ``sampling``, so this default only
    ever applies to old data. For display only: its arrays are still read
    as they always were (:func:`_is_single`)."""
    if sampling is None:
        return SINGLE_SHOWN
    return SINGLE_SHOWN if sampling.precision == SINGLE else DOUBLE_SHOWN


def _through_single(values: list) -> list:
    """Each number as the nearest float32, widened back to a Python float."""
    return np.asarray(values, dtype=np.float32).astype(np.float64).tolist()


def _as_written(values: list[float], single: bool) -> list[float]:
    """The numbers as the file spells them: re-rounded to 9 digits in a
    single-precision file (identity on its decoded values), as they are
    otherwise."""
    return (
        [float(f"%.{SINGLE_DIGITS}g" % v) for v in values] if single else list(values)
    )


# --- decode ----------------------------------------------------------------


#: The keys of a stored variable's entry, in the order they are written:
#: all a reader of DSTF reads of one. Any other key is dropped on decode.
VARIABLE_KEYS = ("index", "name", "expression", "values")


def unread_variable_keys(data: dict) -> dict[str, list[str]]:
    """Each key of a reference-shape document's ``variables`` entries that
    is not one of `VARIABLE_KEYS` — which decoding drops — with the names
    of the variables that carry it, both in the document's order. The codec
    says nothing of what it drops; a caller that should (``soft-check
    add``) asks this. A name is given as text, whatever the document held:
    the caller joins them, after the file is written."""
    out: dict[str, list[str]] = {}
    for entry in data.get("variables") or []:
        for key in entry:
            if key not in VARIABLE_KEYS:
                out.setdefault(key, []).append(str(entry.get("name") or ""))
    return out


def _variable(entry: dict, single: bool) -> StoredVariable:
    values = entry.get("values") or []
    return StoredVariable(
        index=entry.get("index"),
        name=entry.get("name") or "",
        # Absent (old data, see StoredVariable) stays None; null is "".
        expression=(entry.get("expression") or "") if "expression" in entry else None,
        values=_through_single(values) if single else list(values),
    )


def _diagnostic(entry: dict) -> DiagnosticSummary:
    """A stored summary. Old data, not old code: an entry written as
    ``{name, values}`` (before summaries) is summarized here, once."""
    if "values" in entry and "final" not in entry:
        values = np.asarray(entry.get("values") or [], dtype=float)
        if values.size == 0:
            return DiagnosticSummary(name=entry.get("name") or "")
        return DiagnosticSummary(
            name=entry.get("name") or "",
            final=float(values[-1]),
            min=float(values.min()),
            max=float(values.max()),
        )
    return DiagnosticSummary(
        name=entry.get("name") or "",
        final=entry.get("final"),
        min=entry.get("min"),
        max=entry.get("max"),
    )


def _sampling(raw: Any) -> Sampling | None:
    if not isinstance(raw, dict):
        return None
    setting: int | str = raw.get("setting", "all")
    return Sampling(
        setting=setting,
        source_points=int(raw.get("source_points") or 0),
        stored_points=int(raw.get("stored_points") or 0),
        precision=str(raw.get("precision") or DOUBLE),
    )


def _is_single(data: dict) -> bool:
    """Whether a document says its run was single precision — read from
    the document itself, whatever role the file has: a soft check, a JSON
    companion or a ``soft-check add`` source copied from a single-precision
    reference is read through float32 too (pinned by
    tests/test_baseline_points.py)."""
    sampling = _sampling(data.get("sampling"))
    return sampling is not None and sampling.precision == SINGLE


def decode_series(data: dict, name: str) -> Baseline:
    """A reference-shape document's baseline, named ``name``: a soft check,
    a JSON companion's data, the source of ``soft-check add``, or (with
    ``name="primary"``) a reference file's own."""
    single = _is_single(data)
    time = data.get("time") or []
    diagnostics = [_diagnostic(d) for d in data.get("diagnostics") or []]
    # Old data: a top-level number named like one of the file's own
    # diagnostics is the copy of its final a baseline held before 1.2
    # (`statistics.CPUtime`). Dropped on read, never written: the next
    # accept writes the file without it. A key naming no diagnostic, and a
    # section, stay.
    named = {d.name for d in diagnostics}
    statistics = {
        key: value
        for key, value in dict(data.get("statistics") or {}).items()
        if isinstance(value, dict) or key not in named
    }
    return Baseline(
        name=name,
        time=_through_single(time) if single else list(time),
        variables=[_variable(v, single) for v in data.get("variables") or []],
        diagnostics=diagnostics,
        simulation=dict(data.get("simulation") or {}),
        statistics=statistics,
        provenance=dict(data.get("provenance") or {}),
    )


def decode_reference(data: dict) -> ReferenceFile:
    """A primary reference file's parsed JSON as a :class:`ReferenceFile`."""
    return ReferenceFile(
        model_id=data.get("model_id") or "",
        test_id=data.get("test_id"),
        status=data.get("status"),
        date_added=data.get("date_added"),
        last_updated=data.get("last_updated"),
        sampling=_sampling(data.get("sampling")),
        comparison=dict(data.get("comparison") or {}),
        baseline=decode_series(data, PRIMARY_BASELINE),
    )


def read_reference(path: Path) -> ReferenceFile:
    """``path`` decoded. ``OSError`` and ``json.JSONDecodeError`` are the
    caller's: each says its own thing about a file it could not read."""
    return decode_reference(json.loads(path.read_text(encoding="utf-8")))


def read_series(path: Path, name: str) -> Baseline:
    """A soft check's or JSON companion's file decoded (errors as above)."""
    return decode_series(json.loads(path.read_text(encoding="utf-8")), name)


def read_reference_header(path: Path) -> tuple[str | None, str]:
    """``(model_id, status)`` of a reference file — what the index needs.
    A whole-file parse (JSON has no partial read; idea #64). ``status``
    absent reads as ``"active"``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None, "active"
    return data.get("model_id") or None, data.get("status") or "active"


# --- encode ----------------------------------------------------------------


def _variable_json(v: StoredVariable, single: bool) -> dict:
    return {
        "index": v.index,
        "name": v.name,
        # Always written: a writer never leaves the field out.
        "expression": v.expression or "",
        "values": _as_written(v.values, single),
    }


def _diagnostic_json(d: DiagnosticSummary) -> dict:
    out: dict[str, Any] = {"name": d.name}
    for key in ("final", "min", "max"):
        value = getattr(d, key)
        if value is not None:
            out[key] = value
    return out


def reference_to_json(ref: ReferenceFile) -> dict:
    """The JSON document of a reference file, keys in the file's order:
    the file's own fields first, the arrays last (readable at the top)."""
    b = ref.baseline
    single = ref.sampling is not None and ref.sampling.precision == SINGLE
    out: dict[str, Any] = {"model_id": ref.model_id}
    for key in ("test_id", "status", "date_added", "last_updated"):
        value = getattr(ref, key)
        if value is not None:
            out[key] = value
    out["simulation"] = b.simulation
    if ref.sampling is not None:
        out["sampling"] = {
            "setting": ref.sampling.setting,
            "source_points": ref.sampling.source_points,
            "stored_points": ref.sampling.stored_points,
            "precision": ref.sampling.precision,
        }
    # What produced the baseline (accept writes it on every primary; a file
    # accepted before it existed has none until its next accept).
    if b.provenance:
        out["provenance"] = b.provenance
    if ref.comparison:
        out["comparison"] = ref.comparison
    if b.statistics:
        out["statistics"] = b.statistics
    if b.diagnostics:
        out["diagnostics"] = [_diagnostic_json(d) for d in b.diagnostics]
    out["n_vars"] = len(b.variables)
    out["time"] = _as_written(b.time, single)
    out["variables"] = [_variable_json(v, single) for v in b.variables]
    return out


def encode_reference(ref: ReferenceFile) -> str:
    """The text of a reference file (:func:`reference_to_json`, indented)."""
    return json.dumps(reference_to_json(ref), indent=2) + "\n"


def write_reference(path: Path, ref: ReferenceFile) -> None:
    """Write atomically: a Ctrl-C mid-accept must not corrupt a baseline (a
    corrupt file is dropped by the next scan and its ID re-allocated)."""
    _atomic_write_text(path, encode_reference(ref))


def soft_check_to_json(b: Baseline) -> dict:
    """The JSON document of a soft check, keys in its file's order."""
    out: dict[str, Any] = {
        "time": list(b.time),
        "variables": [_variable_json(v, False) for v in b.variables],
    }
    if b.diagnostics:
        out["diagnostics"] = [_diagnostic_json(d) for d in b.diagnostics]
    if b.simulation:
        out["simulation"] = b.simulation
    if b.statistics:
        out["statistics"] = b.statistics
    if b.provenance:
        out["provenance"] = b.provenance
    return out


def encode_soft_check(b: Baseline) -> str:
    """The text of a soft check's file (:func:`soft_check_to_json`, indented)."""
    return json.dumps(soft_check_to_json(b), indent=2) + "\n"


def write_soft_check(path: Path, b: Baseline) -> None:
    """Write a soft check's file atomically."""
    _atomic_write_text(path, encode_soft_check(b))


def claim_stub(model_id: str, test_id: str) -> str:
    """The text ``RefIndex.register`` creates a new ID's file with
    (exclusively), before ``store_reference`` replaces it whole."""
    return json.dumps({"model_id": model_id, "test_id": test_id, "status": "active"})
