"""Edit criteria (§4.6 table, §4.7): the CRITERION placement; a mode switch,
the mode's form, the window, the ``against`` selector. Each change stages one
``replace`` of the whole leaf and the test rescores — the drawer reads
"leaf -> new leaf", never a keystroke log (the Session collapses the
consecutive replaces).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...comparison.mode_schema import derive_schema
from ...comparison.registry import metric, metrics
from ...comparison.tree_spec import LeafSpec, spec_to_raw
from ..capabilities import Panel, Patch, Placement

if TYPE_CHECKING:
    from ..session import SessionView
from ..spec_document import PatchOp

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

        from ..forms import render_form

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
        # The leaf's config error is drawn by the shell directly above this
        # panel and refreshed on every rescore (§4.7 "inline on its form").

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
                )

        def set_params(values: dict) -> None:
            state["params"] = values
            emit()

        def set_metric(name: str) -> None:
            state["metric"] = name
            state["params"] = {}  # a new mode starts from its defaults
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
            ).props("dense").classes("dstf-field")
            form_slot = ui.column().classes("dstf-form")
            ui.number(
                label="window start",
                value=state["window"].get("start"),
                on_change=lambda e: set_window("start", e.value),
            ).props("dense").classes("dstf-field")
            ui.number(
                label="window end",
                value=state["window"].get("end"),
                on_change=lambda e: set_window("end", e.value),
            ).props("dense").classes("dstf-field")
            ui.select(
                names or ["primary"],
                label="against",
                value=state["against"],
                on_change=lambda e: set_against(e.value),
            ).props("dense").classes("dstf-field")
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
