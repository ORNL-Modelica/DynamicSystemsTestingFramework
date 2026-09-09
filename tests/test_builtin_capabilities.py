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
from test_session import A, _Gate, _project  # noqa: E402


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
