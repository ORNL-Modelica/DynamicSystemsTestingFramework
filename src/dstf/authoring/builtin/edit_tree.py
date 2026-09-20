"""Edit tree (§4.6 table, §4.7): the NODE and VARIABLE placements, exactly
one test selected. At NODE the panel IS that node's controls — add, wrap,
and on a combinator edit and unwrap, and remove — each form in a menu
anchored to its button; at VARIABLE it is the "Add a criterion for x" form.
Every control stages one structural change against the paths the report's
tree already shows. The D66 validator's refusal reaches the page through
``Panel.submit`` — the Session is what it was.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...comparison.registry import DEFAULT_METRIC, combinators, metrics
from ..capabilities import Panel, Patch, Placement

if TYPE_CHECKING:
    from ..session import SessionView
from ..spec_document import PatchOp

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

ROOT = "/metrics"

#: The combinator-only raw keys each kind carries (``tree_spec``); every
#: other one is removed when a node changes kind. ``direction`` has no
#: control — a weighted node keeps the one it has.
_KIND_KEYS: dict[str, tuple[str, ...]] = {
    "k-of-n": ("k",),
    "weighted": ("weights", "threshold", "direction"),
}
_COMBINATOR_KEYS = ("k", "weights", "threshold", "direction")


def _node_at(tree: dict, path: str) -> dict:
    """The raw node at ``path``, a pointer whose first token names the tree
    itself (``/metrics``, ``/metrics/children/0``, ...)."""
    node: Any = tree
    for token in [t for t in path.split("/") if t][1:]:
        # A pointer alternates `children` (a key) and an index (into that
        # list): consume each token as what it is.
        node = node[int(token)] if token.isdigit() else node[token]
    return node


def _kind_fields(kind: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """The raw fields ``kind`` takes, from the panel's controls."""
    if kind == "k-of-n":
        return {"k": int(round(float(inputs["k"] or 0)))}
    if kind == "weighted":
        text = str(inputs["weights"] or "")
        try:
            weights = [float(w) for w in text.replace(",", " ").split()]
        except ValueError:
            raise ValueError(
                f"weights: {text!r} is not a comma-separated list of numbers"
            ) from None
        return {"weights": weights, "threshold": float(inputs["threshold"] or 0.0)}
    return {}


class EditTree:
    """The metric tree's shape: wrap a node in a combinator, change a
    combinator's kind and its fields, add a leaf, remove a node."""

    name = "edit-tree"
    label = "Edit tree"
    placements = frozenset({Placement.NODE, Placement.VARIABLE})

    def caption(self, s: SessionView) -> str:
        sel = s.selection
        if sel.node_path is None and sel.variable:
            return f"Add a criterion for {sel.variable}"
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        if len(sel.tests) != 1:
            return "select exactly one test"
        model_id = next(iter(sel.tests))
        running = s.running
        if running is not None and s.is_locked(model_id):
            return f"{running.label} is running on {model_id}"
        if s.tree(model_id) is None:
            return "no tree to edit (nothing scored)"
        if sel.node_path is None and not sel.variable:
            return "select a tree node or a variable"
        return True

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        sel = s.selection
        model_id = next(iter(sel.tests))
        if sel.node_path is not None:
            _cluster(ui, s, ui_panel, model_id, sel.node_path)
        else:
            # The variable's footer: the form itself, no menu to open — the
            # dialog the VARIABLE placement draws is its frame.
            _add_form(
                ui,
                s,
                ui_panel,
                model_id,
                under=ROOT,
                variable=sel.variable,
                mark=f"apply-add:{sel.variable}",
            )

    def act(self, s: SessionView, **inputs: Any) -> Patch:
        action, model_id, path = inputs["action"], inputs["model_id"], inputs["path"]
        if action == "remove":
            return Patch(ops=[PatchOp(model_id, "remove", path)])
        tree = s.tree(model_id)
        if tree is None:
            raise ValueError(f"{model_id} has no tree to edit")
        node = _node_at(tree, path)
        if action == "add":
            leaf = {"metric": inputs["metric"], "variable": inputs["variable"]}
            if "combinator" in node:
                under = path
            elif path == ROOT:
                # Beside a root leaf: the root becomes an `and` of both, in
                # one op (`add` at /metrics replaces the member). A leaf has
                # no `children` for the patch to append to.
                value = {"combinator": "and", "children": [node, leaf]}
                return Patch(ops=[PatchOp(model_id, "add", ROOT, value)])
            else:
                # Beside any other leaf: under its parent combinator.
                under = path.rsplit("/children/", 1)[0]
            return Patch(ops=[PatchOp(model_id, "add", f"{under}/children/-", leaf)])
        if action == "unwrap":
            # A single-child combinator gives way to its child (the browser
            # editor's unwrap): `warn(leaf)` becomes `leaf`, params kept.
            children = node.get("children") or []
            if len(children) != 1:
                raise ValueError(
                    f"{path} has {len(children)} children; unwrap needs one"
                )
            op = "add" if path == ROOT else "replace"
            return Patch(ops=[PatchOp(model_id, op, path, children[0])])
        kind = inputs["kind"]
        fields = _kind_fields(kind, inputs)
        if action == "wrap":
            # `add` at /metrics: a flat test has no member there yet, and
            # RFC 6902 `replace` on a missing member is an error, while
            # `add` on an existing one replaces it.
            op = "add" if path == ROOT else "replace"
            value = {"combinator": kind, "children": [node], **fields}
            return Patch(ops=[PatchOp(model_id, op, path, value)])
        if action == "edit":
            ops = [PatchOp(model_id, "replace", f"{path}/combinator", kind)]
            for key, value in fields.items():
                ops.append(
                    PatchOp(
                        model_id,
                        "replace" if key in node else "add",
                        f"{path}/{key}",
                        value,
                    )
                )
            keeps = _KIND_KEYS.get(kind, ())
            ops += [
                PatchOp(model_id, "remove", f"{path}/{key}")
                for key in _COMBINATOR_KEYS
                if key in node and key not in keeps
            ]
            return Patch(ops=ops)
        raise ValueError(f"unknown action {action!r}")


def _combinator_controls(ui: Any, node: dict | None) -> Callable[[], dict[str, Any]]:
    """The kind select and the fields every kind may take; the returned
    callable reads them at apply time."""
    names = [c.name for c in combinators()]
    current = (node or {}).get("combinator")
    kind = ui.select(
        names, label="combinator", value=current if current in names else names[0]
    ).props("dense")
    k = ui.number(label="k", value=(node or {}).get("k") or 1).props("dense")
    weights = ui.input(
        label="weights",
        value=", ".join(str(w) for w in (node or {}).get("weights") or []),
    ).props("dense")
    threshold = ui.number(
        label="threshold", value=(node or {}).get("threshold") or 0.0
    ).props("dense")
    return lambda: {
        "kind": kind.value,
        "k": k.value,
        "weights": weights.value,
        "threshold": threshold.value,
    }


def _add_form(
    ui: Any,
    s: SessionView,
    ui_panel: Panel,
    model_id: str,
    *,
    under: str,
    variable: str | None,
    mark: str,
    done: Callable[[], None] | None = None,
) -> None:
    """Variable and metric selects and the apply button (marked ``mark``);
    ``done`` runs first when the form sits in a menu that must close."""
    names = _variable_names(s, model_id)
    if variable and variable not in names:
        names = [variable, *names]
    var = (
        ui.select(
            names or [""],
            label="leaf variable",
            value=variable if variable else (names[0] if names else ""),
        )
        .props("dense")
        .mark(f"{mark}:variable")
    )
    met = ui.select(
        [d.name for d in metrics()], label="leaf metric", value=DEFAULT_METRIC
    ).props("dense")

    def apply() -> None:
        if done is not None:
            done()
        ui_panel.submit(
            model_id=model_id,
            action="add",
            path=under,
            variable=var.value,
            metric=met.value,
        )

    ui.button("apply add", on_click=apply).props("dense no-caps").mark(mark)


def _lazy_menu(ui: Any, button: Any, build: Callable[[Any], None]) -> None:
    """The menu anchored to ``button``, its CONTENT built on the first open.

    A cluster whose three forms are built with it is ~28 elements, and a
    tree row holds one per node: 40 leaves made a 2387-element page with the
    tree open, and every sync re-sent them. What opens a QMenu is a click on
    the element it sits in, which reaches the server as that button's click
    — and is the only open the user simulation performs, having no browser.
    The menu's own value change builds it too, for an open from anywhere
    else (``menu.open()``, a keyboard).
    """
    with button:
        menu = ui.menu()
    state = {"built": False}

    def build_once() -> None:
        if state["built"]:
            return
        state["built"] = True
        with menu, ui.column().classes("q-pa-sm gap-1"):
            build(menu)

    button.on_click(build_once)
    menu.on_value_change(lambda e: build_once() if e.value else None)


def _cluster(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, path: str
) -> None:
    """One node's controls: flat round icon buttons, each form in its
    menu."""
    tree = s.tree(model_id)
    if tree is None:  # `available` already says so; a job may race
        return
    node = _node_at(tree, path)
    is_combinator = "combinator" in node

    def icon(name: str, tip: str, mark: str, on_click: Any = None) -> Any:
        button = ui.button(icon=name, on_click=on_click)
        button.props("flat dense round size=sm").tooltip(tip)
        return button.mark(mark)

    def act_now(action: str) -> None:
        ui_panel.submit(model_id=model_id, action=action, path=path)

    def build_add(menu: Any) -> None:
        _add_form(
            ui,
            s,
            ui_panel,
            model_id,
            under=path,
            variable=s.selection.variable or node.get("variable"),
            mark=f"apply-add:{path}",
            done=menu.close,
        )

    def build_wrap(menu: Any) -> None:
        values = _combinator_controls(ui, None)

        def apply() -> None:
            menu.close()
            ui_panel.submit(model_id=model_id, action="wrap", path=path, **values())

        ui.button("apply wrap", on_click=apply).props("dense no-caps").mark(
            f"apply-wrap:{path}"
        )

    def build_edit(menu: Any) -> None:
        values = _combinator_controls(ui, node)

        def apply() -> None:
            menu.close()
            ui_panel.submit(model_id=model_id, action="edit", path=path, **values())

        ui.button("apply edit", on_click=apply).props("dense no-caps").mark(
            f"apply-edit:{path}"
        )

    with ui.row().classes("dstf-node-controls items-center no-wrap gap-0"):
        _lazy_menu(
            ui,
            icon(
                "add",
                "Add a criterion under this node"
                if is_combinator
                else "Add a criterion beside this one",
                f"add:{path}",
            ),
            build_add,
        )
        _lazy_menu(
            ui,
            icon(
                "data_array",
                "Wrap in a combinator (and / or / k-of-n / warn / weighted)",
                f"wrap:{path}",
            ),
            build_wrap,
        )
        if is_combinator:
            _lazy_menu(
                ui, icon("tune", "Change this combinator", f"edit:{path}"), build_edit
            )
            if len(node.get("children") or []) == 1:
                icon(
                    "unfold_less",
                    "Replace this combinator with its one child",
                    f"unwrap:{path}",
                    lambda: act_now("unwrap"),
                )
        if path != ROOT:  # the root has nothing to be removed from
            icon(
                "remove",
                "Remove this node and everything under it"
                if is_combinator
                else "Remove this criterion",
                f"remove:{path}",
                lambda: act_now("remove"),
            )


def _variable_names(s: SessionView, model_id: str) -> list[str]:
    """What a new leaf may score: the scored names, else the result's."""
    comp = s.comparisons.get(model_id)
    names = (
        list(dict.fromkeys(vc.name for vc in comp.variables if vc.name))
        if comp is not None
        else []
    )
    if names:
        return names
    project = s.project
    result = project.results.get(model_id) if project is not None else None
    return [v.name for v in result.variables if v.name] if result is not None else []
