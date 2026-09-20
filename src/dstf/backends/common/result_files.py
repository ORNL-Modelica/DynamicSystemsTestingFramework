"""Any variable of a result file, by name — the three on-disk formats the
bundled backends write, each as a pair: the names in the file (``None`` when
there is no readable file) and the named series as ``{name: (time, values)}``
(a name the file lacks is left out). A backend class points its
``result_names`` / ``result_series`` at one pair. The tracked-variable read
(``read_result``) stays where it is: it resolves patterns, indexes and
diagnostics; this reads what a plot asks for."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Collection
from pathlib import Path

import numpy as np

from .mat_reader import list_result_mat_variables, read_result_mat

SeriesByName = dict[str, tuple[np.ndarray, np.ndarray]]


def mat_names(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    names = list_result_mat_variables(path)
    return None if names is None else [n for n in names if n.lower() != "time"]


def mat_series(path: Path, names: Collection[str]) -> SeriesByName:
    if not path.exists() or not names:
        return {}
    # A partial or foreign .mat reads as None: the reader swallows its own
    # errors.
    data = read_result_mat(path, variable_names=set(names))
    return {n: data[n] for n in names if data and n in data}


def _json_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # unreadable, not UTF-8, not JSON
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("variables"), dict):
        return None
    return payload


def json_names(path: Path) -> list[str] | None:
    payload = _json_payload(path)
    if payload is None:
        return None
    return [n for n in payload["variables"] if n != "time"]


def json_series(path: Path, names: Collection[str]) -> SeriesByName:
    payload = _json_payload(path)
    if payload is None:
        return {}
    time = _vector(payload.get("time"))
    if time is None:
        return {}
    out: SeriesByName = {}
    for n in names:
        values = _vector(payload["variables"].get(n)) if n != "time" else None
        if values is not None and values.shape == time.shape:
            out[n] = (time, values)
    return out


def _vector(raw: object) -> np.ndarray | None:
    """``raw`` as a 1-D float array, or ``None`` for anything that is not a
    list of numbers — a run killed mid-write, a driver that dumped strings."""
    if not isinstance(raw, list):
        return None
    try:
        arr = np.asarray(raw, dtype=float)
    except (ValueError, TypeError):
        return None
    return arr if arr.ndim == 1 else None


def _npz_array(path: Path) -> np.ndarray | None:
    try:
        with np.load(path, allow_pickle=False) as npz:
            return npz["result"]
    except (OSError, KeyError, ValueError, EOFError, zipfile.BadZipFile):
        return None  # absent, or half-written: savez_compressed is not atomic


def npz_names(path: Path) -> list[str] | None:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None:
        return None
    return [n for n in arr.dtype.names if n != "time"]


def npz_series(path: Path, names: Collection[str]) -> SeriesByName:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None or "time" not in arr.dtype.names:
        return {}
    time = np.asarray(arr["time"], dtype=float)
    out: SeriesByName = {}
    for n in names:
        if n in arr.dtype.names and n != "time":
            values = np.asarray(arr[n], dtype=float)
            if values.shape == time.shape:  # a subarray column is not a series
                out[n] = (time, values)
    return out
