"""Edit criteria (§4.6 table, §4.7): the CRITERION placement; a mode switch,
the mode's form, the window, the ``against`` selector. Each change stages one
``replace`` of the whole leaf and the test rescores. The Session collapses
consecutive replaces of one leaf into one pending op, so the pending count
and the exported envelope carry one edit, never a keystroke log (the
preview drawer shows the entry's diff either way).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...comparison.mode_schema import derive_schema
from ...comparison.registry import metric, metrics
from ...comparison.seeds import seeded_leaf
from ...comparison.tree_spec import LeafSpec, spec_to_raw
from ..capabilities import Panel, Patch, Placement

if TYPE_CHECKING:
    from ..session import SessionView
from ..spec_document import PatchOp
from .edit_tree import seed_data

# NO module-level `nicegui` or `..forms` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.


def _leaf_node(view: dict | None, path: str) -> dict | None:
    """The node at ``path`` in the report's tree view, or None."""
    if view is None:
        return None
    if view.get("path") == path:
        return view
    for child in view.get("children") or []:
        found = _leaf_node(child, path)
        if found is not None:
            return found
    return None


class EditCriteria:
    """One leaf's metric, its mode's parameters, its window and the baseline
    it scores against."""

    name = "edit-criteria"
    label = "Edit criteria"
    placements = frozenset({Placement.CRITERION})

    def caption(self, s: SessionView) -> str:
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        model_id = next(iter(sel.tests)) if len(sel.tests) == 1 else None
        if model_id is None or sel.leaf_path is None:
            return "select one leaf"
        running = s.running
        if running is not None and s.is_locked(model_id):
            return f"{running.label} is running on {model_id}"
        return True

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        from ..forms import BOTTOM, render_form

        sel = s.selection
        model_id = next(iter(sel.tests))
        path = sel.leaf_path or ""
        leaf = _leaf_node(s.tree_view(model_id), path)
        # The panel is rebuilt after edits made elsewhere: Edit tree wrapping
        # the selected leaf leaves the selection's path naming a combinator.
        if leaf is None or leaf.get("kind") != "leaf":
            ui.label("this leaf is not in the scored tree")
            return
        state: dict[str, Any] = {
            "metric": leaf["metric"],
            "variable": leaf["variable"],
            "params": dict(leaf.get("params") or {}),
            "against": leaf.get("against") or "primary",
            "window": dict(leaf.get("window") or {}),
        }
        # No heading: the criterion's own row sits directly above this panel
        # and names it, and a second name there made the editor read as two
        # panels rather than one (brief §3.2).
        # Nor an error line: a leaf the scorer could not score says why in
        # the criteria ROW directly above this panel, which reads out the
        # criterion ("scalar leaf not scored — …"). Drawing it on the form
        # itself (§4.7 "inline on its form") is sub-project 4's.

        def emit() -> None:
            ui_panel.submit(model_id=model_id, path=path, **state)

        def build_form() -> None:
            form_slot.clear()
            desc = metric(state["metric"])
            with form_slot:
                if desc is None:
                    ui.label(f"unknown metric {state['metric']!r}")
                    return
                render_form(
                    derive_schema(desc.config_cls, mode=desc.name),
                    state["params"],
                    on_change=set_params,
                    classes="dstf-field",
                    # The mode the leaf was scored in: a switched mode's
                    # fields were never applied, so nothing is in effect.
                    in_effect=(
                        leaf.get("in_effect")
                        if state["metric"] == leaf["metric"]
                        else None
                    ),
                )

        def set_params(values: dict) -> None:
            state["params"] = values
            emit()

        def set_metric(name: str) -> None:
            state["metric"] = name
            # A new mode starts from its seed, else its defaults: a range
            # switched to with no bound does not resolve. The window stays,
            # so the seed reads the samples it scores.
            data = seed_data(s, model_id, state["variable"], window=state["window"])
            seeded = seeded_leaf(name, state["variable"], data)
            state["params"] = {
                k: v for k, v in seeded.items() if k not in ("metric", "variable")
            }
            build_form()
            emit()

        def set_window(key: str, value: float | None) -> None:
            if value is None:
                state["window"].pop(key, None)
            else:
                state["window"][key] = value
            emit()

        def set_against(name: str) -> None:
            state["against"] = name
            emit()

        project = s.project
        names = list(project.baseline_names(model_id)) if project is not None else []
        if not leaf.get("advisory"):
            # D66: a soft_check may be targeted only under a `warn`; offering
            # it here would stage an op the validator refuses every time.
            names = [n for n in names if n == "primary"]
        if state["against"] not in names:
            # A leaf naming a baseline the store no longer has is exactly the
            # leaf one opens this panel to repair: keep its value selectable.
            names.append(state["against"])
        # ONE wrapping row (the board: the plot and its editor fit a screen
        # together), the mode FIRST — it decides what the fields after it
        # are, and drawn below them it read as a footnote to the form it
        # controls. `dstf-form` hands the mode's own fields straight to this
        # row rather than stacking them in a column of its own.
        with ui.row().classes("dstf-fields items-end gap-3 wrap w-full"):
            ui.select(
                [d.name for d in metrics()],
                label="mode",
                value=state["metric"],
                on_change=lambda e: set_metric(e.value),
            ).props(f"dense {BOTTOM}").classes("dstf-field")
            form_slot = ui.column().classes("dstf-form")
            ui.number(
                label="window start",
                value=state["window"].get("start"),
                on_change=lambda e: set_window("start", e.value),
            ).props(f"dense {BOTTOM}").classes("dstf-field")
            ui.number(
                label="window end",
                value=state["window"].get("end"),
                on_change=lambda e: set_window("end", e.value),
            ).props(f"dense {BOTTOM}").classes("dstf-field")
            ui.select(
                names or ["primary"],
                label="against",
                value=state["against"],
                on_change=lambda e: set_against(e.value),
            ).props(f"dense {BOTTOM}").classes("dstf-field")
        build_form()

    def act(self, s: SessionView, **inputs: Any) -> Patch:
        leaf = LeafSpec(
            metric=inputs["metric"],
            variable=inputs["variable"],
            params=dict(inputs["params"]),
            against=inputs["against"],
            window_start=inputs["window"].get("start"),
            window_end=inputs["window"].get("end"),
        )
        return Patch(
            ops=[
                PatchOp(
                    inputs["model_id"], "replace", inputs["path"], spec_to_raw(leaf)
                )
            ]
        )
