"""Edit criteria (§4.6 table, §4.7): the CRITERION placement; a mode switch,
the mode's form, the window, the ``against`` selector — and the
STATISTIC_CRITERION one, a statistics criterion's editor under its
Statistics row, where the mode, the window and the quantity are no fields —
and the TREE_CRITERION one, any criterion's editor under its tree row (U5). Each change stages one
``replace`` of the whole leaf and the test rescores. The Session collapses
consecutive replaces of one leaf into one pending op, so the pending count
and the exported envelope carry one edit, never a keystroke log (the
preview drawer shows the entry's diff either way).
"""

from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING, Any

from ...comparison.mode_schema import Schema, derive_schema
from ...comparison.registry import metric, metrics
from ...comparison.seeds import seeded_leaf
from ...comparison.tree_spec import (
    LeafSpec,
    parse_metric_tree,
    reads_a_statistic,
    spec_to_raw,
    spec_to_view,
)
from ..capabilities import Panel, Patch, Placement
from ..tree_pointer import node_or_none
from ..unscorable import (
    MIN_WIDTH,
    TOLERANCES,
    WIDTHS,
    refuse_bounds,
    refuse_negative,
    refuse_negative_floor,
    refuse_negative_width,
)

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


def _authored_leaf(tree: dict | None, path: str) -> dict | None:
    """The leaf at ``path`` of the tree as AUTHORED (`SessionView.tree`,
    staged edits included), shaped as the tree view's leaf — its metric,
    variable, params, baseline and window, and ``advisory`` when a ``warn``
    sits above it; None where ``path`` names no leaf. Not the view's: a
    test with nothing compared (no baseline) has no view, and the editor
    of the criterion just staged there drew no field."""
    raw = node_or_none(tree, path)
    if raw is None or "metric" not in raw:
        return None
    leaf = spec_to_view(parse_metric_tree(raw), root=path)
    parts = path.split("/children/")
    leaf["advisory"] = any(
        (node_or_none(tree, "/children/".join(parts[:i])) or {}).get("combinator")
        == "warn"
        for i in range(1, len(parts))
    )
    return leaf


def refuse_unscorable(name: str, params: dict) -> None:
    """Raise ``ValueError`` for a criterion of metric ``name`` whose
    ``params`` can never score (`..unscorable`): a negative tolerance, its
    sides named as the mode's own fields name them, a tube's width whose
    sides sum below zero over no positive minimum width, or bounds out of
    order — and a tube's minimum width below zero, which floors nothing."""
    desc = metric(name)
    sides: dict[str, Any] = {}
    if desc is not None and dataclasses.is_dataclass(desc.config_cls):
        sides = {
            f.name: f.metadata.get("side_labels")
            for f in dataclasses.fields(desc.config_cls)
        }
    for key in TOLERANCES:
        refuse_negative(key, params.get(key), sides.get(key))
    for key in WIDTHS:
        refuse_negative_width(key, params.get(key), params.get(MIN_WIDTH))
    refuse_negative_floor(MIN_WIDTH, params.get(MIN_WIDTH))
    refuse_bounds(name, params)


class EditCriteria:
    """One leaf's metric, its mode's parameters, its window and the baseline
    it scores against."""

    name = "edit-criteria"
    label = "Edit criteria"
    placements = frozenset(
        {
            Placement.CRITERION,
            Placement.STATISTIC_CRITERION,
            Placement.TREE_CRITERION,
        }
    )

    def caption(self, s: SessionView) -> str:
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        model_id = next(iter(sel.tests)) if len(sel.tests) == 1 else None
        if model_id is None or sel.leaf_path is None:
            return "select one leaf"
        # A compared test whose tree the Session will not materialize: its
        # leaves are on the plots, but every edit is refused — the Session's
        # reason, as the editor's why-line, not the view's leaf as if
        # editable. Said here and not in the panel: Edit on plot is refused
        # for the same reason, and the host says a sentence once.
        _tree, why = s.tree_or_reason(model_id)
        return True if why is None else why

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        from ..forms import BOTTOM, SidesControl, render_form
        from ..typed_number import TypedNumber

        sel = s.selection
        model_id = next(iter(sel.tests))
        path = sel.leaf_path or ""
        tree = s.tree(model_id)
        assert tree is not None  # `available` said so, on this same view
        leaf = _authored_leaf(tree, path)
        # The panel is rebuilt after edits made elsewhere: Edit tree wrapping
        # the selected leaf leaves the selection's path naming a combinator.
        if leaf is None:
            ui.label("this leaf is not in the scored tree")
            return
        # What the leaf was scored with: only a view says — none before a
        # compare (no baseline), and then the fields show no hint.
        scored = _leaf_node(s.tree_view(model_id), path) or {}
        state: dict[str, Any] = {
            "metric": leaf["metric"],
            "variable": leaf["variable"],
            "params": dict(leaf.get("params") or {}),
            "against": leaf.get("against") or "primary",
            "window": dict(leaf.get("window") or {}),
        }
        # A statistics criterion (U3): only `scalar` scores a stored number
        # (no mode select, and `set_metric`'s reseed never runs on one), a
        # stored number has no window, and the Statistics row the editor
        # sits under names the quantity — none of the three is a field.
        statistic = reads_a_statistic(state["metric"], state["params"])
        # No heading: the criterion's own row sits directly above this panel
        # and names it, and a second name there made the editor read as two
        # panels rather than one (brief §3.2).
        # Nor an error line: a leaf the scorer could not score says why in
        # the criteria ROW directly above this panel, which reads out the
        # criterion ("scalar leaf not scored — …"). Drawing it on the form
        # itself (§4.7 "inline on its form") is sub-project 4's.

        # What the leaf holds — the state its last accepted submit was made
        # from — and what that was when the field being edited was ENTERED
        # (`enter`): where a refused edit puts the leaf back.
        taken: dict[str, Any] = {
            "leaf": copy.deepcopy(state),
            "entered": copy.deepcopy(state),
            "field": None,
        }

        def enter(field: str | None) -> None:
            """A field took the focus (``None``: the row heard it, whichever
            it is) — or ``field`` is the first edited since another was,
            where no focus was heard (a value set from Python)."""
            taken["field"], taken["entered"] = field, copy.deepcopy(taken["leaf"])

        def submit(values: dict) -> bool:
            if not ui_panel.submit(model_id=model_id, path=path, **values):
                return False
            taken["leaf"] = copy.deepcopy(values)
            return True

        def emit(field: str) -> None:
            """Stage the leaf as the fields now say, ``field`` being the
            one just edited. A value typed passes through others on the
            way — `-0.5` over a window end is `-` first, no number, which
            UNSETS the end — and each that the leaf takes is staged; so a
            refused one left the last of those staged, beside a field and
            an error line saying something else. Refused, the leaf goes
            back to what it held when the field was entered: one staged
            state, as if it had not been touched. The field keeps what was
            typed, and the refusal its sentence."""
            if field != taken["field"]:
                enter(field)
            if submit(state) or taken["leaf"] == taken["entered"]:
                return
            submit(taken["entered"])
            # The put-back went through, which hid the refusal: said again,
            # by the submit that earned it.
            submit(state)

        # A tolerance with one side typed is in no leaf: its control hands
        # nothing over, so the fields show what the spec does not hold. This
        # panel is rebuilt by edits it did not make — a drag on the plot,
        # a cell of the table beside it — and, built from the leaf alone,
        # the blank side came back as its old value, unmarked. So a half
        # pair is written where it outlives the panel (`Panel.kept`), as
        # the tables write theirs: field -> ``{"base": what the leaf holds
        # there, "shown": the half pair}``, read back when the form is
        # built. One whose leaf has since moved under it — that tolerance
        # dragged on the plot — is dropped, and the fields show the spec's.
        kept = ui_panel.kept

        def remember(name: str, group: SidesControl) -> None:
            """After every edit of the two-sided field ``name``, once the
            leaf has had what the control handed over."""
            if group.half():
                base = copy.deepcopy(taken["leaf"]["params"].get(name))
                kept[name] = {"base": base, "shown": group.shown()}
            else:
                kept.pop(name, None)

        def build_form() -> None:
            form_slot.clear()
            desc = metric(state["metric"])
            with form_slot:
                if desc is None:
                    ui.label(f"unknown metric {state['metric']!r}")
                    return
                schema = derive_schema(desc.config_cls, mode=desc.name)
                if statistic:
                    schema = Schema(
                        schema.mode, [f for f in schema.fields if f.name != "quantity"]
                    )
                for name in [
                    n for n in kept if kept[n]["base"] != state["params"].get(n)
                ]:
                    del kept[name]  # the leaf moved under it
                render_form(
                    schema,
                    state["params"],
                    on_change=set_params,
                    classes="dstf-field",
                    shown={name: cell["shown"] for name, cell in kept.items()},
                    on_edit=remember,
                    # The mode the leaf was scored in: a switched mode's
                    # fields were never applied, so nothing is in effect.
                    in_effect=(
                        scored.get("in_effect")
                        if state["metric"] == scored.get("metric")
                        else None
                    ),
                )

        def set_params(values: dict) -> None:
            if statistic:
                # The form's values ARE the params: with its quantity field
                # left out, the path goes back in, or the replace staged a
                # scalar that reads no statistic — which the Session takes.
                values = {**values, "quantity": state["params"]["quantity"]}
            changed = sorted(
                key
                for key in {*values, *state["params"]}
                if values.get(key) != state["params"].get(key)
            )
            state["params"] = values
            emit("params:" + ",".join(changed))

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
            # Another mode's fields are other fields, whatever their names:
            # a half pair typed in the old one is not shown in them.
            kept.clear()
            build_form()
            emit("metric")

        def set_window(key: str, value: float | None) -> None:
            if value is None:
                state["window"].pop(key, None)
            else:
                state["window"][key] = value
            emit(f"window:{key}")

        def set_against(name: str) -> None:
            state["against"] = name
            emit("against")

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
        fields = ui.row().classes("dstf-fields items-end gap-3 wrap w-full")
        # Whichever field it is: what the leaf holds now is its start.
        fields.on("focusin", lambda _e: enter(None), []).mark("edit-criteria:fields")
        with fields:
            if not statistic:
                ui.select(
                    [d.name for d in metrics()],
                    label="mode",
                    value=state["metric"],
                    on_change=lambda e: set_metric(e.value),
                ).props(f"dense {BOTTOM}").classes("dstf-field")
            form_slot = ui.column().classes("dstf-form")
            if not statistic:
                TypedNumber(
                    label="window start",
                    value=state["window"].get("start"),
                    on_change=lambda e: set_window("start", e.value),
                ).props(f"dense {BOTTOM}").classes("dstf-field")
                TypedNumber(
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
        params = dict(inputs["params"])
        # A value that can never score is refused here, before the stage —
        # as the stage refuses a window that ends before it starts, and the
        # panel puts the leaf back the same way (`emit`).
        refuse_unscorable(inputs["metric"], params)
        leaf = LeafSpec(
            metric=inputs["metric"],
            variable=inputs["variable"],
            params=params,
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
