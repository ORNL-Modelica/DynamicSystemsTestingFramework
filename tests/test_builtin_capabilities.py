"""The capabilities the tool ships (§4.6 table, §4.7), driven through the
shell with NiceGUI's in-process user simulation — no browser."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.app import Shell  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from test_session import A, B, C, D, _Gate, _project  # noqa: E402


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
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell()
    s.open(cfg, work_dir=work)
    return s


async def _select_leaf(user, model_id: str, path: str = "/metrics/children/0") -> None:
    user.find(marker=model_id).click()
    # The leaf ROW, by kind: a staged op carries the same path text in the
    # pending drawer, and a bare `should_see(path)` would be satisfied by
    # that label before the centre's refresh has drawn the row to click.
    await user.should_see(kind=ui.button, content=path)
    user.find(kind=ui.button, content=path).click()
    await user.should_see("Edit criteria")
    user.find("Edit criteria").click()


async def test_edit_criteria_tab_appears_for_a_leaf_and_stages_on_change(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await user.should_not_see("Edit criteria")
        await _select_leaf(user, A)
        # derive_schema's label for NrmseConfig.tolerance.
        await user.should_see("Tolerance")
        form_tol = next(
            c
            for c in user.find(ui.number).elements
            if "olerance" in str(c.props.get("label", ""))
        )
        form_tol.set_value(0.5)
        await user.should_see("2 pending")  # materialization + the replace
        assert shell.session.spec.pending[-1].op == "replace"
        assert shell.session.spec.pending[-1].path == "/metrics/children/0"
        assert shell.session.spec.pending[-1].value["tolerance"] == 0.5
        from dstf.reporting.status import PASS, classify

        assert classify(shell.session.comparisons[A]) == PASS  # rescored live


async def test_edit_criteria_switches_mode_and_offers_against_and_window(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await _select_leaf(user, A)
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
        leaf = shell.session.spec.pending[-1].value
        assert leaf["metric"] == "range" and leaf["window"]["start"] == 0.25


async def test_edit_criteria_is_greyed_while_the_test_is_locked(shell):
    from dstf.authoring.capabilities import Job

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await _select_leaf(user, A)
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see("rerun is running")  # the greyed tab's reason
        gate.release.set()
        await user.should_see("no job running")


async def test_a_leaf_config_error_shows_inline_on_the_form(shell):
    """§4.7: a range leaf with neither bound is a sentinel; its reason is
    on the form, not only in the tree row."""
    from dstf.authoring.spec_document import PatchOp

    shell.session.stage([PatchOp(A, "add", "/metrics/children/0/tolerance", 0.5)])
    shell.session.stage(
        [
            PatchOp(
                A,
                "replace",
                "/metrics/children/0",
                {"metric": "range", "variable": "x"},
            )
        ]
    )
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await _select_leaf(user, A)
        await user.should_see("requires at least one of")
        # ON THE FORM: the label sits inside the tab panel, not only in the
        # centre's tree row — and it follows a rescore. Set a bound and it goes.
        (label,) = user.find(marker="leaf-error").elements
        assert any(_inside(label, panel) for panel in user.find(ui.tab_panel).elements)
        upper = next(
            c
            for c in user.find(ui.number).elements
            if "Upper" in str(c.props.get("label"))
        )
        upper.set_value(2.0)
        await user.should_not_see("requires at least one of")


def _inside(element, container) -> bool:
    node = element
    while node is not None:
        if node is container:
            return True
        node = node.parent_slot.parent if node.parent_slot is not None else None
    return False


async def test_a_mode_switch_starts_from_the_new_modes_defaults(shell):
    """Set a tolerance, switch to range, set a bound: the leaf carries no
    stale nrmse tolerance."""
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        await _select_leaf(user, A)
        await user.should_see("Tolerance")
        tol = next(
            c
            for c in user.find(ui.number).elements
            if str(c.props.get("label")) == "Tolerance"
        )
        tol.set_value(0.5)
        await user.should_see("2 pending")
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
        leaf = shell.session.spec.pending[-1].value
        assert leaf == {"metric": "range", "variable": "x", "max_value": 2.0}


def _shell_with_soft_check(tmp_path, *ops):
    """A fresh shell per simulation (a page cannot outlive its client):
    A has a soft_check `om_cross`, plus whatever ops are staged."""
    from dstf.authoring.spec_document import PatchOp  # noqa: F401

    cfg, work = _project(tmp_path)
    s = Shell()
    s.open(cfg, work_dir=work)
    t = [i / 4 for i in range(5)]
    s.session.project.store.add_soft_check(
        A, "om_cross", t, [{"index": 1, "name": "x", "values": t}]
    )
    s.session._reload_results()
    if ops:
        s.session.stage(list(ops))
    return s


WARN_LEAF = {
    "combinator": "warn",
    "children": [{"metric": "nrmse", "variable": "x", "against": "om_cross"}],
}


async def _against_select(user):
    await user.should_see("against")
    return next(
        c for c in user.find(ui.select).elements if c.props.get("label") == "against"
    )


async def test_against_offers_no_soft_check_outside_warn(tmp_path):
    """D66: a soft_check is a valid target only beneath a `warn`."""
    s = _shell_with_soft_check(tmp_path)
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await _select_leaf(user, A)
        assert (await _against_select(user)).options == ["primary"]


async def test_against_offers_the_soft_check_under_warn(tmp_path):
    from dstf.authoring.spec_document import PatchOp

    s = _shell_with_soft_check(
        tmp_path, PatchOp(A, "add", "/metrics/children/-", WARN_LEAF)
    )
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await _select_leaf(user, A, path="/metrics/children/1/children/0")
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
    s = Shell()
    s.open(cfg, work_dir=work)
    async with user_simulation(root=s.root) as user:
        await user.open("/")
        await _select_leaf(user, A, path="/metrics/children/1/children/0")
        against = await _against_select(user)
        assert "ghost" in against.options and against.value == "ghost"


async def test_edit_tree_needs_exactly_one_test(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A, D})
        await user.should_see("select exactly one test")  # should_see retries; no sleep


def test_edit_tree_reaches_nested_nodes(shell):
    """The pointer walker consumed `children` and then indexed the LIST with
    the word — every non-root wrap/edit raised TypeError. `act` is pure, so
    this pins it without a page."""
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import SessionView
    from dstf.authoring.spec_document import PatchOp

    shell.session.stage(
        [PatchOp(A, "add", "/metrics/children/-", {"metric": "nrmse", "variable": "x"})]
    )
    view = SessionView(shell.session)
    shell.session.select(tests={A})
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
    shell.session.apply(outcome)
    tree = shell.session.tree(A)
    assert tree["children"][1]["combinator"] == "warn"
    # edit the nested combinator: k-of-n on the warn's child list is refused
    # by the parser (warn needs one child), so change the ROOT via its path
    # and a nested leaf's removal through a nested pointer.
    outcome = cap.act(
        view, action="remove", model_id=A, path="/metrics/children/1/children/0"
    )
    assert outcome.ops[0].path == "/metrics/children/1/children/0"


def test_edit_tree_unwraps_a_single_child_combinator(shell):
    """The browser editor's unwrap: warn(leaf) becomes leaf, params kept;
    remove would have lost the leaf."""
    from dstf.authoring.builtin.edit_tree import EditTree
    from dstf.authoring.session import SessionView
    from dstf.authoring.spec_document import PatchOp

    shell.session.stage(
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
    view = SessionView(shell.session)
    shell.session.select(tests={A})
    outcome = EditTree().act(
        view, action="unwrap", model_id=A, path="/metrics/children/1"
    )
    (op,) = outcome.ops
    assert op.op == "replace" and op.value == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    shell.session.apply(outcome)
    assert shell.session.tree(A)["children"][1] == {
        "metric": "nrmse",
        "variable": "x",
        "tolerance": 0.3,
    }
    with pytest.raises(ValueError, match="unwrap needs one"):
        EditTree().act(view, action="unwrap", model_id=A, path="/metrics")


async def test_edit_tree_wrap_add_remove_and_combinator_edits(shell):
    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        user.find(marker=A).click()
        await user.should_see("Edit tree")
        user.find("Edit tree").click()
        # The button TEXT is `wrap /metrics`; the click goes through the
        # marker, because content matching is a substring test and
        # "wrap /metrics" also matches "wrap /metrics/children/0", which
        # `find` would click too (markers match exactly).
        await user.should_see("wrap /metrics")
        before = shell.session.tree(A)
        # Wrapping the whole tree in warn leaves no hard leaf: the D66
        # validator refuses, the tab says why, the tree is what it was.
        user.find(marker="wrap:/metrics").click()
        await user.should_see("apply wrap")
        kind = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "combinator"
        )
        kind.set_value("warn")
        user.find("apply wrap").click()
        await user.should_see("validator rejected")
        assert shell.session.tree(A) == before
        # A legal sequence: add a second leaf, make the root k-of-n with
        # k=1, remove the second leaf.
        await user.should_see("add leaf under /metrics")
        user.find(marker="add:/metrics").click()
        await user.should_see("apply add")
        var = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "leaf variable"
        )
        var.set_value("x")
        user.find("apply add").click()
        await user.should_see("/metrics/children/1")
        assert len(shell.session.tree(A)["children"]) == 2
        await user.should_see("edit /metrics")
        user.find(marker="edit:/metrics").click()
        await user.should_see("apply edit")
        kind = next(
            c
            for c in user.find(ui.select).elements
            if c.props.get("label") == "combinator"
        )
        kind.set_value("k-of-n")
        k = next(
            c for c in user.find(ui.number).elements if c.props.get("label") == "k"
        )
        k.set_value(1)
        user.find("apply edit").click()
        await user.should_see("k=1")
        tree = shell.session.tree(A)
        assert tree["combinator"] == "k-of-n" and tree["k"] == 1
        await user.should_see("remove /metrics/children/1")
        user.find(marker="remove:/metrics/children/1").click()
        await user.should_see("k-of-n k=1 [1]")  # the centre's root row
        assert len(shell.session.tree(A)["children"]) == 1


# -- Accept (§4.6 table, §4.7) --------------------------------------------


async def test_accept_is_greyed_without_a_result_and_stores_the_rest(shell):
    """C has no result and is `pending` in the batch manifest, so the shared
    accept function refuses it; A and B are stored, and the reload flips
    their verdicts."""
    import json

    from dstf.reporting.status import PASS, classify

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={C})
        # C is `pending` in the batch manifest, so the tab carries its row's
        # own reason — the same string the panel draws for it below.
        await user.should_see("re-run before accepting")
        shell.session.select(tests={A, B, C})
        await user.should_see("Accept")
        user.find("Accept").click()  # the tab (lowest id); the button reads "Accept 2"
        await user.should_see("Accept 2")  # names what it will store: A and B
        await user.should_see("re-run before accepting")  # C's row, from the manifest
        user.find("Accept 2").click()
        # The written event refreshes the tabs: B's row carries its new verdict.
        await user.should_see(f"{B} · {PASS}", retries=20)
        notice = shell.session.notices[-1]
        assert notice.startswith("Accept: stored 2")
        assert C in notice and "re-run before accepting" in notice
        assert classify(shell.session.comparisons[B]) == PASS  # NO_REF -> PASS
        assert classify(shell.session.comparisons[A]) == PASS  # re-baselined
        rid = shell.session.project.store.index.get_id(B)
        assert rid == "0004"  # A, D and G hold 0001-0003
        raw = (
            shell.session.project.config.reference_dir / f"ref_{rid}.json"
        ).read_bytes()
        assert b"\r" not in raw and json.loads(raw)["model_id"] == B


def test_accept_act_returns_a_store_write_over_the_selection(shell):
    """`act` is pure: the StoreWrite names the selection and its `run`
    reports one line — what it stored and what it refused."""
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.capabilities import StoreWrite

    shell.session.select(tests={A, B, C})
    out = Accept().act(shell.view)
    assert isinstance(out, StoreWrite) and out.models == {A, B, C}
    line = out.run(shell.session.project.store)
    assert line.startswith("Accept: stored 2") and C in line
    # The line is a report of an effect that happened: B has a baseline now.
    assert shell.session.project.store.index.get_id(B) == "0004"


async def test_accept_is_greyed_while_a_selected_test_is_locked(shell):
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.capabilities import Job

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A})
        await user.should_see("Accept")
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        await user.should_see(f"rerun is running on {A}")
        gate.release.set()
        await user.should_see("no job running", retries=20)
        # The lock is the Session's, not the page's: it is gone with the job.
        assert shell.session.running is None
        assert Accept().available(shell.view) is True


async def test_accept_is_greyed_when_the_selection_is_a_pending_test(tmp_path):
    """The tab's answer and the button's count come from the same rows: A's
    result file parses, but the run never finished, so the shared accept
    function would refuse it and the tab says so instead of offering
    `Accept 0`."""
    import json

    from dstf.authoring.builtin.accept import REFUSED, Accept

    cfg, work = _project(tmp_path)
    manifest_path = work / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_0001"]["last_run_status"] = "pending"  # A, in flight
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    shell = Shell()
    shell.open(cfg, work_dir=work)

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A})
        assert Accept().available(shell.view) == REFUSED
        await user.should_see(REFUSED)  # the greyed tab's reason
        await user.should_not_see("Accept 0")


async def test_accept_excludes_a_simulate_only_test_from_the_count(shell):
    """A simulate-only test is neither stored nor skipped by the shared
    accept function — the forecast says so and the count leaves it out."""
    from dstf.authoring.builtin.accept import SIMULATE_ONLY, Accept
    from dstf.reporting.status import NO_REF

    shell.session.project.test(B).simulate_only = True

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A, B})
        await user.should_see("Accept")
        user.find("Accept").click()
        await user.should_see(f"{B} · {NO_REF} · {SIMULATE_ONLY}")
        await user.should_see("Accept 1")  # A only
        shell.session.select(tests={B})
        await user.should_see(SIMULATE_ONLY)  # now the greyed tab's reason
        await user.should_not_see("Accept 0")
        assert Accept().available(shell.view) == SIMULATE_ONLY


async def test_accept_excludes_a_result_with_no_tracked_variables(shell):
    """`store_reference` returns False for a variable-less result and
    `accept_results` skips it with this reason; the panel has the result in
    hand, so it never promises the baseline."""
    from dstf.authoring.builtin.accept import NO_VARIABLES, Accept
    from dstf.backends.types import TestResult
    from dstf.reporting.status import NO_REF

    shell.session.project.results[B] = TestResult(
        model_id=B, success=True, variables=[]
    )

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A, B})
        await user.should_see("Accept")
        user.find("Accept").click()
        await user.should_see(f"{B} · {NO_REF} · {NO_VARIABLES}")
        await user.should_see("Accept 1")  # A only
        shell.session.select(tests={B})
        await user.should_see(NO_VARIABLES)
        assert Accept().available(shell.view) == NO_VARIABLES


async def test_accept_reports_the_row_reason_and_the_empty_selection(shell):
    """With rows to draw, the greyed tab says exactly what the first row
    says; the generic line is left for a selection with no rows at all."""
    from dstf.authoring.builtin.accept import SIM_FAILED, Accept
    from dstf.backends.types import TestResult

    shell.session.project.results[A] = TestResult(
        model_id=A, success=False, variables=[], error_message="boom"
    )

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A})
        assert Accept().available(shell.view) == SIM_FAILED
        await user.should_see(SIM_FAILED)  # the greyed tab's reason
        await user.should_not_see("Accept 0")
        shell.session.select(tests=set())
        assert Accept().available(shell.view) == "no successful result in the selection"


# -- Rerun (§4.6 table, §4.7) ---------------------------------------------


def test_the_four_builtins_register_in_order():
    assert [c.label for c in caps.registered_capabilities()] == [
        "Edit criteria",
        "Edit tree",
        "Accept",
        "Rerun",
    ]


def test_rerun_act_returns_a_job_over_the_selection(shell, monkeypatch):
    """`act` is pure: the Job names the selection, and its `run` builds a
    fresh quiet persistent-preferring backend and hands the selected tests,
    in project order, to the run callable."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.builtin.rerun import Rerun
    from dstf.authoring.capabilities import Job, JobContext

    shell.session.select(tests={A, D})
    job = Rerun().act(shell.view)
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

    project = shell.session.project
    job.run(JobContext(project=project, models=frozenset({A, D})))

    assert calls["select"] == (project.config, True)
    assert built.quiet is True  # the tool's process never narrates
    config, tests, backend, store, kwargs = calls["pipeline"]
    assert config is project.config
    assert backend is built and store is project.store
    assert [t.model_id for t in tests] == [A, D]  # project order
    assert kwargs == {}  # no merge, no candidates: the scope is the run


def test_rerun_job_run_propagates_an_unbuildable_backend(shell, monkeypatch):
    """`run` has no `try`: a backend that cannot be built on this machine
    raises out of the job. That is what the Session's thread body catches
    into `job.error` (tests/test_session.py) rather than the tool reporting
    a rerun that did nothing."""
    from dstf.authoring.builtin import rerun as rerun_mod
    from dstf.authoring.builtin.rerun import Rerun
    from dstf.authoring.capabilities import JobContext
    from dstf.backends import BackendUnavailableError

    def _unavailable(config, *, persistent):
        raise BackendUnavailableError("no omc")

    monkeypatch.setattr(rerun_mod, "select_backend", _unavailable)

    shell.session.select(tests={A})
    job = Rerun().act(shell.view)
    ctx = JobContext(project=shell.session.project, models=frozenset({A}))
    with pytest.raises(BackendUnavailableError, match="no omc"):
        job.run(ctx)


async def test_rerun_names_the_backend_and_is_greyed_while_a_job_runs(shell):
    from dstf.authoring.builtin.rerun import Rerun
    from dstf.authoring.capabilities import Job

    async with user_simulation(root=shell.root) as user:
        await user.open("/")
        shell.session.select(tests={A})
        await user.should_see("Rerun")
        user.find("Rerun").click()  # the tab (lowest id); the button reads "Rerun 1"
        await user.should_see("Rerun 1")
        # Via the MARKER: the header already reads `backend: Python`, so a
        # `should_see("Python")` would pass without the panel's label.
        (label,) = user.find(marker="rerun-backend").elements
        assert label.text == "runs through Python"
        gate = _Gate()
        shell.session.apply(Job(run=gate, label="rerun", models=frozenset({A})))
        # The page text alone does not pin THIS tab: Accept's greyed reason
        # for the same selection reads `rerun is running on FakeLib.A` and
        # contains the string. The wait holds until the refresh has drawn a
        # greyed tab; `Rerun.available` is asserted on the Session.
        await user.should_see("rerun is running")
        assert Rerun().available(shell.view) == "rerun is running"
        gate.release.set()
        await user.should_see("no job running", retries=20)
        assert Rerun().available(shell.view) is True


async def test_edit_criteria_follows_a_wrap_of_its_leaf_through_the_shell(shell):
    """Edit tree wraps the selected leaf in `warn` through `Shell.act` — the
    path a user's click takes. The slots rebuild on the rescore, and the
    selection's path now names the combinator: Edit criteria says so rather
    than raising."""
    from nicegui import app

    from dstf.authoring.spec_document import PatchOp
    from test_handles import TREE

    shell.session.stage([PatchOp(D, "add", "/metrics", TREE)])
    edit_tree = next(c for c in caps.registered_capabilities() if c.name == "edit-tree")
    errors: list[Exception] = []
    async with user_simulation(root=shell.root) as user:
        app.on_exception(errors.append)
        try:
            await user.open("/")
            shell.session.select(
                tests={D}, variable="x", leaf_path="/metrics/children/1"
            )
            await user.should_see("Upper bound")  # Edit criteria's range form
            shell.act(
                edit_tree,
                action="wrap",
                model_id=D,
                path="/metrics/children/1",
                kind="warn",
            )
            await user.should_see("this leaf is not in the scored tree")
            await user.should_not_see("Upper bound")
        finally:
            app._exception_handlers.remove(errors.append)
    assert errors == []
    assert shell.errors == {}
    assert shell.session.tree(D)["children"][1]["combinator"] == "warn"
    assert shell.session.selection.leaf_path == "/metrics/children/1"
