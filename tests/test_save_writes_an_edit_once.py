"""The tool's Save writes each pending edit ONCE.

A flat test whose spec holds one criterion that can never be scored — a
``range`` override with neither bound, written by hand — makes the write's
check replay the pending ops over a marked copy of the entry
(``spec_update.refuse_newly_unscorable``). While an op's value went into
the document by reference, that replay ran every later op a second time
inside the document that was then written: one removed criterion took its
neighbour with it, one added criterion was written twice.

Each test stages through the capabilities the pages call and reads the
written ``test_spec.json``.
"""

from __future__ import annotations

import json

import pytest

from dstf.authoring.builtin.edit_tree import EditTree
from dstf.authoring.session import Selection, Session, SessionView
from dstf.authoring.spec_document import PatchOp, edit_count
from test_session import A, _two_var_project

NRMSE_X = {"metric": "nrmse", "variable": "x"}
#: The hand-written override as the leaf the entry's tree gives it.
BARE_Y = {"metric": "range", "variable": "y"}


@pytest.fixture
def session(tmp_path) -> Session:
    """A tracks ``x`` and ``y`` with no ``metrics`` tree; ``y`` has a
    ``range`` override with no bound, ``x`` is scored by default."""
    cfg, work = _two_var_project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    data = json.loads(spec.read_text(encoding="utf-8"))
    (entry,) = [e for e in data["tests"] if e["model"] == A]
    entry["comparison"] = {"variable_overrides": {"y": {"mode": "range"}}}
    spec.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def _act(session: Session, **inputs) -> None:
    view = SessionView(session, Selection(tests=frozenset({A})))
    session.apply(EditTree().act(view, model_id=A, **inputs))


def _saved_tree(session: Session) -> dict:
    data = json.loads(session.spec.path.read_text(encoding="utf-8"))
    (entry,) = [e for e in data["tests"] if e["model"] == A]
    return entry["metrics"]


def test_removing_the_other_criterion_leaves_the_held_one(session):
    _act(session, action="remove", path="/metrics/children/0")
    assert edit_count(session.spec.pending) == 1
    session.save()
    assert _saved_tree(session) == {"combinator": "and", "children": [BARE_Y]}


def test_one_added_criterion_is_written_once(session):
    _act(session, action="add", path="/metrics", variable="x", metric="tube")
    staged = session.tree(A)["children"]
    assert [leaf["metric"] for leaf in staged] == ["nrmse", "range", "tube"]
    session.save()
    assert _saved_tree(session)["children"] == staged


def test_a_criterion_added_in_a_new_group_is_written_once(session):
    """Two acts: a criterion wrapped in a group, then one added in it."""
    _act(session, action="wrap", path="/metrics/children/0", kind="or")
    _act(session, action="add", path="/metrics/children/0", variable="x", metric="tube")
    group = session.tree(A)["children"][0]
    assert [leaf["metric"] for leaf in group["children"]] == ["nrmse", "tube"]
    session.save()
    assert _saved_tree(session)["children"] == [group, BARE_Y]


def test_nor_twice_in_a_group_an_op_added_at_the_end(session):
    """The same as staged ops: a group appended, a criterion put in it."""
    group = {"combinator": "or", "children": [{**NRMSE_X, "tolerance": 0.5}]}
    added = {"metric": "range", "variable": "x", "min_value": 0, "max_value": 100}
    session.stage([PatchOp(A, "add", "/metrics/children/-", group)])
    session.stage([PatchOp(A, "add", "/metrics/children/2/children/-", added)])
    session.save()
    assert _saved_tree(session)["children"] == [
        NRMSE_X,
        BARE_Y,
        {"combinator": "or", "children": [{**NRMSE_X, "tolerance": 0.5}, added]},
    ]
