"""A fixture project with an array family (D152): FakeLib.A's result holds
``pipe.T[1]`` … ``pipe.T[n]`` beside ``x``, each with a baseline. Tests
import it as ``from family_project import family_project``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from conftest import PROVENANCE, make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.reference_store import ReferenceStore
from test_session import A, _project

FAMILY = "pipe.T"


def element(i: int) -> str:
    return f"{FAMILY}[{i}]"


def family_project(
    tmp_path: Path,
    n: int = 6,
    *,
    tracked: Sequence[int] | None = None,
    fail: Sequence[int] = (3,),
    tree: dict | None = None,
    first: int = 1,
) -> tuple[Path, Path]:
    """`test_session._project` with A's result holding ``x`` (PASS against
    its baseline) and ``pipe.T[1]`` … ``pipe.T[n]``: element ``i`` is
    ``i * t`` in the baseline, and in the result ``i * t`` times 1.25 for
    the indices in ``fail`` (nrmse 0.153: FAIL at the project's 0.01) and
    times 1.001 for the rest (6e-4: PASS). The baseline holds every
    element; the spec tracks ``x`` and the ``tracked`` ones (all by
    default), so an untracked element is in the result FILE and the
    baseline and scores as soon as a criterion names it. ``tree`` is A's
    ``metrics``; without one its tree is the implicit ``and`` of one nrmse
    per tracked variable. ``first`` is the array's first index: 0 for a
    zero-based one, ``pipe.T[0]`` … ``pipe.T[n - 1]``, whose values are
    those of the one-based array's elements in the same places."""
    cfg, work = _project(tmp_path)
    t = [i / 4 for i in range(5)]
    indices = list(range(first, first + n))
    scale = {i: i - first + 1 for i in indices}  # never a flat zero signal
    variables = {"x": [v * 1.001 for v in t]}
    for i in indices:
        variables[element(i)] = [
            scale[i] * v * (1.25 if i in fail else 1.001) for v in t
        ]
    (work / "test_0001" / "result.json").write_text(
        json.dumps({"success": True, "time": t, "variables": variables}),
        encoding="utf-8",
    )
    names = ["x", *(element(i) for i in (indices if tracked is None else tracked))]
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = names
            if tree is not None:
                entry["metrics"] = tree
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    tt = np.asarray(t)
    every = ["x", *(element(i) for i in indices)]
    ReferenceStore(Config(config_file=cfg, work_dir=work)).store_reference(
        make_test_model(A, variable_patterns=every),
        TestResult(
            model_id=A,
            success=True,
            variables=[
                VariableResult(index=k, time=tt, values=tt, name="x")
                if name == "x"
                else VariableResult(index=k, time=tt, values=(k - 1) * tt, name=name)
                for k, name in enumerate(every, start=1)
            ],
        ),
        provenance=PROVENANCE,
    )
    return cfg, work
