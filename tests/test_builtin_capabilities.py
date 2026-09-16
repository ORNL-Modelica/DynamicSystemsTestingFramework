"""The capabilities the tool ships (§4.6 table, §4.7): `act` and `available`
over the fixture project, and each panel alone in NiceGUI's in-process user
simulation through `capability_host` — no shell, no browser."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

from capability_host import host  # noqa: E402
from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.builtin.accept import Accept  # noqa: E402
from dstf.authoring.builtin.edit_criteria import EditCriteria  # noqa: E402
from dstf.authoring.builtin.edit_tree import EditTree  # noqa: E402
from dstf.authoring.builtin.rerun import Rerun  # noqa: E402
from dstf.authoring.session import Selection, Session, SessionView  # noqa: E402
from test_session import LOOSEN, A, B, C, D, _Gate, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    """The builtins register on `import dstf.authoring`; each test starts
    from that state and leaves it."""
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def session(tmp_path):
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


#: The page's selection the leaf panels are built for (D129).
LEAF0 = Selection(tests=frozenset({A}), variable="x", leaf_path="/metrics/children/0")


async def test_edit_criteria_stages_on_change(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        # derive_schema's label for NrmseConfig.tolerance.
        await user.should_see("Tolerance")
        form_tol = next(
            c
            for c in user.find(ui.number).elements
            if "olerance" in str(c.props.get("label", ""))
        )
        form_tol.set_value(0.5)
        assert len(session.spec.pending) == 2  # materialization + the replace
        assert session.spec.pending[-1].op == "replace"
        assert session.spec.pending[-1].path == "/metrics/children/0"
        assert session.spec.pending[-1].value["tolerance"] == 0.5
        from dstf.reporting.status import PASS, classify

        assert classify(session.comparisons[A]) == PASS  # rescored live


async def test_edit_criteria_switches_mode_and_offers_against_and_window(session):
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("mode")
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        assert set(mode.options) >= {"nrmse", "tube", "range"}
        mode.set_value("range")
        # RangeConfig.max_value's label is "Upper bound (optional)".
        await user.should_see("Upper bound")
        against = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "against"
        )
        assert against.options == ["primary"]
        start = next(
            c
            for c in user.find(ui.number).elements
            if c.props.get("label") == "window start"
        )
        start.set_value(0.25)
        leaf = session.spec.pending[-1].value
        assert leaf["metric"] == "range" and leaf["window"]["start"] == 0.25


def test_edit_criteria_is_greyed_while_the_test_is_locked(session):
    from dstf.authoring.capabilities import Job

    view = SessionView(session, LEAF0)
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert EditCriteria().available(view) == f"rerun is running on {A}"
    gate.release.set()
    assert job.done.wait(5)
    assert EditCriteria().available(view) is True


async def test_a_mode_switch_starts_from_the_new_modes_defaults(session):
    """Set a tolerance, switch to range, set a bound: the leaf carries no
    stale nrmse tolerance."""
    async with host(session, EditCriteria(), LEAF0) as (user, errors, _):
        await user.should_see("Tolerance")
        tol = next(
            c
            for c in user.find(ui.number).elements
            if str(c.props.get("label")) == "Tolerance"
        )
        tol.set_value(0.5)
        assert len(session.spec.pending) == 2
        mode = next(
            c for c in user.find(ui.select).elements if c.props.get("label") == "mode"
        )
        mode.set_value("range")
        await user.should_see("Upper bound")
        upper = next(
            c
            for c in user.find(ui.number).elements
            if "Upper" in str(c.props.get("label"))
        )
        upper.set_value(2.0)
        leaf = session.spec.pending[-1].value
        assert leaf == {"metric": "range", "variable": "x", "max_value": 2.0}


def _session_with_soft_check(tmp_path, *ops):
    """A fresh Session per simulation (a page cannot outlive its client):
    A has a soft_check `om_cross`, plus whatever ops are staged."""
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    t = [i / 4 for i in range(5)]
    s.project.store.add_soft_check(
        A, "om_cross", t, [{"index": 1, "name": "x", "values": t}]
    )
    s._reload_results()
    if ops:
        s.stage(list(ops))
    return s


WARN_LEAF = {
    "combinator": "warn",
    "children": [{"metric": "nrmse", "variable": "x", "against": "om_cross"}],
}

#: The leaf beneath the `warn` the two soft-check tests open the panel on.
UNDER_WARN = Selection(
    tests=frozenset({A}), variable="x", leaf_path="/metrics/children/1/children/0"
)


async def _against_select(user):
    await user.should_see("against")
    return next(
        c for c in user.find(ui.select).elements if c.props.get("label") == "against"
    )


async def test_against_offers_no_soft_check_outside_warn(tmp_path):
    """D66: a soft_check is a valid target only beneath a `warn`."""
    s = _session_with_soft_check(tmp_path)
    async with host(s, EditCriteria(), LEAF0) as (user, errors, _):
        assert (await _against_select(user)).options == ["primary"]


async def test_against_offers_the_soft_check_under_warn(tmp_path):
    from dstf.authoring.spec_document import PatchOp

    s = _session_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", WARN_LEAF)
    )
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert (
            against.options == ["primary", "om_cross"] and against.value == "om_cross"
        )


async def test_against_keeps_a_value_the_store_no_longer_knows(tmp_path):
    """The leaf one opens this panel to repair — its baseline was removed
    after the spec was written; the validator refuses to STAGE such a leaf,
    so it can only arrive from disk — must not crash the panel."""
    import json

    cfg, work = _project(tmp_path)
    doc = json.loads((cfg.parent / "test_spec.json").read_text(encoding="utf-8"))
    entry = next(e for e in doc["tests"] if e["model"] == A)
    entry["metrics"] = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {
                "combinator": "warn",
                "children": [{"metric": "nrmse", "variable": "x", "against": "ghost"}],
            },
        ],
    }
    (cfg.parent / "test_spec.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )
    s = Session()
    s.open(cfg, work_dir=work)
    async with host(s, EditCriteria(), UNDER_WARN) as (user, errors, _):
        against = await _against_select(user)
        assert "ghost" in against.options and against.value == "ghost"


def test_edit_tree_needs_exactly_one_test(session):
    view = SessionView(session, Selection(tests=frozenset({A, B})))
    assert EditTree().available(view) == "select exactly one test"


def test_edit_tree_reaches_nested_nodes(session):
    """The pointer walker consumed `children` and then indexed the LIST with
    the word — every non-root wrap/edit raised TypeError. `act` is pure, so
    this pins it without a page."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [PatchOp(A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "x"})]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    cap = EditTree()
    # wrap a child, not the root: a `replace` at that path
    outcome = cap.act(
        view, action="wrap", model_id=A, path="/metrics/children/1", kind="warn"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.path == "/metrics/children/1"
    assert (
        op.value["combinator"] == "warn" and op.value["children"][0]["variable"] == "x"
    )
    session.apply(outcome)
    tree = session.tree(A)
    assert tree["children"][1]["combinator"] == "warn"
    # edit the nested combinator: k-of-n on the warn's child list is refused
    # by the parser (warn needs one child), so change the ROOT via its path
    # and a nested leaf's removal through a nested pointer.
    outcome = cap.act(
        view, action="remove", model_id=A, path="/metrics/children/1/children/0"
    )
    assert outcome.ops[0].path == "/metrics/children/1/children/0"


def test_edit_tree_unwraps_a_single_child_combinator(session):
    """The browser editor's unwrap: warn(leaf) becomes leaf, params kept;
    remove would have lost the leaf."""
    from dstf.authoring.spec_document import PatchOp

    session.stage(
        [
            PatchOp(
                A,
                "add",
                "/metrics/children/-",
                {
                    "combinator": "warn",
                    "children": [
                        {"metric": "nrmse", "variable": "x", "tolerance": 0.3}
                    ],
                },
            )
        ]
    )
    view = SessionView(session, Selection(tests=frozenset({A})))
    outcome = EditTree().act(
        view, action="unwrap", model_id=A, path="/metrics/children/1"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.value == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    session.apply(outcome)
    assert session.tree(A)["children"][1] == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    with pytest.raises(ValueError, match="unwrap needs one"):
        EditTree().act(view, action="unwrap", model_id=A, path="/metrics")


# -- Accept (§4.6 table, §4.7) --------------------------------------------


def test_accept_is_greyed_without_a_result(session):
    """C has no result and is `pending` in the batch manifest, so the shared
    accept function refuses it — and the capability says so."""
    view = SessionView(session, Selection(tests=frozenset({C})))
    assert Accept().available(view) == "re-run before accepting"


async def test_accept_stores_the_rest(session):
    """A and B are stored, C is refused with its row's reason, and the
    reload flips A's and B's verdicts."""
    import json

    from dstf.reporting.status import PASS, classify

    selection = Selection(tests=frozenset({A, B, C}))
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see("Accept 2")  # names what it will store: A and B
        await user.should_see("re-run before accepting")  # C's row, from the manifest
        user.find(kind=ui.button, content="Accept 2 results as baselines").click()
        notice = session.notices[-1]
        assert notice.startswith("Accept: stored 2")
        assert C in notice and "re-run before accepting" in notice
        assert classify(session.comparisons[B]) == PASS  # NO_REF -> PASS
        assert classify(session.comparisons[A]) == PASS  # re-baselined
        rid = session.project.store.index.get_id(B)
        assert rid == "0004"  # A, D and G hold 0001-0003
        raw = (session.project.config.reference_dir / f"ref_{rid}.json").read_bytes()
        assert b"\r" not in raw and json.loads(raw)["model_id"] == B


def test_accept_act_returns_a_store_write_over_the_selection(session):
    """`act` is pure: the StoreWrite names the selection and its `run`
    reports one line — what it stored and what it refused."""
    from dstf.authoring.capabilities import StoreWrite

    view = SessionView(session, Selection(tests=frozenset({A, B, C})))
    out = Accept().act(view)
    assert isinstance(out, StoreWrite) and out.models == {A, B, C}
    line = out.run(session.project.store)
    assert line.startswith("Accept: stored 2") and C in line
    # The line is a report of an effect that happened: B has a baseline now.
    assert session.project.store.index.get_id(B) == "0004"


def test_accept_is_greyed_while_a_selected_test_is_locked(session):
    from dstf.authoring.capabilities import Job

    view = SessionView(session, Selection(tests=frozenset({A})))
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert Accept().available(view) == f"rerun is running on {A}"
    gate.release.set()
    assert job.done.wait(5)
    # The lock is the Session's, not the page's: it is gone with the job.
    assert session.running is None
    assert Accept().available(view) is True


async def test_accept_is_greyed_when_the_selection_is_a_pending_test(tmp_path):
    """A's result file parses, but the run never finished, so the shared
    accept function would refuse it and the capability says so instead of
    offering `Accept 0`."""
    import json

    from dstf.authoring.builtin.accept import REFUSED

    cfg, work = _project(tmp_path)
    manifest_path = work / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_0001"]["last_run_status"] = "pending"  # A, in flight
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    session = Session()
    session.open(cfg, work_dir=work)

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == REFUSED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(REFUSED)  # the host's reason, in the panel's place
        await user.should_not_see("Accept 0")


async def test_accept_excludes_a_simulate_only_test_from_the_count(session):
    """A simulate-only test is neither stored nor skipped by the shared
    accept function — the forecast says so and the count leaves it out."""
    from dstf.authoring.builtin.accept import SIMULATE_ONLY
    from dstf.reporting.status import NO_REF

    session.project.test(B).simulate_only = True

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {SIMULATE_ONLY}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == SIMULATE_ONLY


async def test_accept_excludes_a_result_with_no_tracked_variables(session):
    """`store_reference` returns False for a variable-less result and
    `accept_results` skips it with this reason; the panel has the result in
    hand, so it never promises the baseline."""
    from dstf.authoring.builtin.accept import NO_VARIABLES
    from dstf.backends.types import TestResult
    from dstf.reporting.status import NO_REF

    session.project.results[B] = TestResult(model_id=B, success=True, variables=[])

    async with host(session, Accept(), Selection(tests=frozenset({A, B}))) as (
        user,
        errors,
        _,
    ):
        await user.should_see(f"{B} · {NO_REF} · {NO_VARIABLES}")
        await user.should_see("Accept 1")  # A only
    view = SessionView(session, Selection(tests=frozenset({B})))
    assert Accept().available(view) == NO_VARIABLES


async def test_accept_reports_the_row_reason_the_empty_selection_and_a_selection_naming_no_test(
    session,
):
    """With rows to draw, the greyed capability says exactly what the first
    row says; the generic line is left for a selection with no rows at all."""
    from dstf.authoring.builtin.accept import SIM_FAILED
    from dstf.backends.types import TestResult

    session.project.results[A] = TestResult(
        model_id=A, success=False, variables=[], error_message="boom"
    )

    selection = Selection(tests=frozenset({A}))
    assert Accept().available(SessionView(session, selection)) == SIM_FAILED
    async with host(session, Accept(), selection) as (user, errors, _):
        await user.should_see(SIM_FAILED)  # the host's reason
        await user.should_not_see("Accept 0")
    empty = SessionView(session, Selection(tests=frozenset()))
    assert Accept().available(empty) == "Accept needs a selection"
    assert (
        Accept().available(SessionView(session, Selection(tests=frozenset({"Nope.X"}))))
        == "no successful result in the selection"
    )


# -- Rerun (§4.6 table, §4.7) ---------------------------------------------


def test_the_builtins_register_in_order():
    assert [c.label for c in caps.registered_capabilities()] == [
        "Edit criteria",
        "Edit tree",
        "Accept",
        "Rerun",
        "Edit on plot",
    ]


def test_rerun_act_returns_a_job_over_the_selection(session, monkeypatch):
    """`act` is pure: the Job names the selection, and its `run` builds a
    fresh quiet persistent-preferring backend and hands the selected tests,
    in project order, to the run callable."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.capabilities import Job, JobContext

    view = SessionView(session, Selection(tests=frozenset({A, D})))
    job = Rerun().act(view)
    assert isinstance(job, Job)
    assert job.label == "rerun" and job.models == {A, D} and job.reloads_results

    class _Backend:
        quiet = False

    built = _Backend()
    calls: dict = {}

    def _select_backend(config, *, persistent):
        calls["select"] = (config, persistent)
        return built

    def _run_pipeline(config, tests, *, backend, store, **kwargs):
        calls["pipeline"] = (config, list(tests), backend, store, kwargs)

    monkeypatch.setattr(rerun_mod, "select_backend", _select_backend)
    monkeypatch.setattr(rerun_mod, "run_pipeline", _run_pipeline)

    project = session.project
    job.run(JobContext(project=project, models=frozenset({A, D})))

    assert calls["select"] == (project.config, True)
    assert built.quiet is True  # the tool's process never narrates
    config, tests, backend, store, kwargs = calls["pipeline"]
    assert config is project.config
    assert backend is built and store is project.store
    assert [t.model_id for t in tests] == [A, D]  # project order
    assert kwargs == {}  # no merge, no candidates: the scope is the run


def test_rerun_job_run_propagates_an_unbuildable_backend(session, monkeypatch):
    """`run` has no `try`: a backend that cannot be built on this machine
    raises out of the job. That is what the Session's thread body catches
    into `job.error` (tests/test_session.py) rather than the tool reporting
    a rerun that did nothing."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.capabilities import JobContext
    from dstf.backends import BackendUnavailableError

    def _unavailable(config, *, persistent):
        raise BackendUnavailableError("no omc")

    monkeypatch.setattr(rerun_mod, "select_backend", _unavailable)

    view = SessionView(session, Selection(tests=frozenset({A})))
    job = Rerun().act(view)
    ctx = JobContext(project=session.project, models=frozenset({A}))
    with pytest.raises(BackendUnavailableError, match="no omc"):
        job.run(ctx)


async def test_rerun_names_the_backend_and_is_greyed_while_a_job_runs(session):
    from dstf.authoring.capabilities import Job

    selection = Selection(tests=frozenset({A}))
    view = SessionView(session, selection)
    async with host(session, Rerun(), selection) as (user, errors, _):
        await user.should_see("Rerun 1")
        # Via the MARKER: a header elsewhere reads `backend: Python`, so a
        # `should_see("Python")` would pass without the panel's label.
        (label,) = user.find(marker="rerun-backend").elements
        assert label.text == "runs through Python"
    gate = _Gate()
    session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
    job = session.running
    assert job is not None
    assert Rerun().available(view) == "rerun is running"
    gate.release.set()
    assert job.done.wait(5)
    assert Rerun().available(view) is True


def test_the_builtins_declare_their_placements():
    """Brief §3.2: Accept and Rerun at suite and test; Edit tree on the Test
    page's tree; Edit on plot at a variable; Edit criteria at a criterion.
    The placements the Test page (sub-project 3) and the editing merge
    (sub-project 4) build on."""
    from dstf.authoring.builtin.edit_on_plot import EditOnPlot
    from dstf.authoring.capabilities import Placement

    S, T, V, C_ = (
        Placement.SUITE,
        Placement.TEST,
        Placement.VARIABLE,
        Placement.CRITERION,
    )
    assert Accept.placements == {S, T} and Rerun.placements == {S, T}
    assert EditTree.placements == {T}
    assert EditOnPlot.placements == {V}
    assert EditCriteria.placements == {C_}


def test_accept_caption_counts_what_it_will_store_and_needs_a_selection(session):
    """DESIGN.md "Buttons": object, effect, count — and at zero the label
    still says what it would do while `available` carries the sentence."""
    three = SessionView(session, Selection(tests=frozenset({A, B, C})))
    assert Accept().caption(three) == "Accept 2 results as baselines"  # C is refused
    one = SessionView(session, Selection(tests=frozenset({D})))
    assert Accept().caption(one) == "Accept 1 result as baselines"
    none = SessionView(session, Selection())
    assert Accept().caption(none) == "Accept 0 results as baselines"
    assert Accept().available(none) == "Accept needs a selection"


def test_rerun_caption_names_count_backend_and_the_selections_unsaved_edits(session):
    one = SessionView(session, Selection(tests=frozenset({A})))
    assert Rerun().caption(one) == "Rerun 1 selected test (Python)"
    assert (
        Rerun().available(SessionView(session, Selection()))
        == "Rerun needs a selection"
    )
    session.stage([LOOSEN])  # two ops on A: the materialization and the edit
    assert (
        Rerun().caption(one) == "Rerun 1 selected test (Python) (with 2 unsaved edits)"
    )
    # Another model's edits are not this selection's.
    other = SessionView(session, Selection(tests=frozenset({D})))
    assert Rerun().caption(other) == "Rerun 1 selected test (Python)"
