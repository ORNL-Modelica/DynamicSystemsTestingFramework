"""Any variable of a result file, by name — the three on-disk formats the
bundled backends write, each as a pair: the names in the file (``None`` when
there is no readable file) and the named series as ``{name: (time, values)}``
(a name the file lacks is left out). A backend class points its
``result_names`` / ``result_series`` at one pair. The tracked-variable read
(``read_result``) stays where it is: it resolves patterns, indexes and
diagnostics; this reads what a plot asks for."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

import numpy as np

from .mat_reader import list_result_mat_variables, read_result_mat

Series = dict[str, tuple[np.ndarray, np.ndarray]]


def mat_names(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    names = list_result_mat_variables(path)
    return None if names is None else [n for n in names if n.lower() != "time"]


def mat_series(path: Path, names: Collection[str]) -> Series:
    if not path.exists() or not names:
        return {}
    try:
        data = read_result_mat(path, variable_names=set(names))
    except Exception:  # a partial or foreign .mat: nothing to draw
        return {}
    return {n: data[n] for n in names if data and n in data}


def _json_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("variables"), dict):
        return None
    return payload


def json_names(path: Path) -> list[str] | None:
    payload = _json_payload(path)
    return None if payload is None else list(payload["variables"])


def json_series(path: Path, names: Collection[str]) -> Series:
    payload = _json_payload(path)
    if payload is None:
        return {}
    time = np.asarray(payload.get("time") or [], dtype=float)
    out: Series = {}
    for n in names:
        values = payload["variables"].get(n)
        if isinstance(values, list) and len(values) == len(time):
            out[n] = (time, np.asarray(values, dtype=float))
    return out


def _npz_array(path: Path) -> np.ndarray | None:
    try:
        with np.load(path, allow_pickle=False) as npz:
            return npz["result"]
    except (OSError, KeyError, ValueError):
        return None


def npz_names(path: Path) -> list[str] | None:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None:
        return None
    return [n for n in arr.dtype.names if n != "time"]


def npz_series(path: Path, names: Collection[str]) -> Series:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None or "time" not in arr.dtype.names:
        return {}
    time = np.asarray(arr["time"], dtype=float)
    return {
        n: (time, np.asarray(arr[n], dtype=float))
        for n in names
        if n in arr.dtype.names and n != "time"
    }
