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
from ...discovery.plots import tracked_names
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


def _node_or_none(tree: dict | None, path: str) -> dict | None:
    """The node at ``path``, or ``None`` when the pointer names none of
    ``tree`` — a stale selection, or one naming a raw key (``/metrics/
    combinator`` resolves to a word, which is no node)."""
    if tree is None:
        return None
    try:
        node = _node_at(tree, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return node if isinstance(node, dict) else None


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
            # Read as what the PAGE reads for the variable — the scope it
            # handed down with the selection (`Selection.texts`: the
            # expression its model wrote, with the name beside it when
            # another variable of that test shares it), the same text as
            # its criteria row above. The form the button opens still
            # writes the leaf against the NAME.
            #
            # The scope is not derived here: a caption per card that built
            # it for itself was the page's whole rebuild cost. A selection
            # that carries none reads the name — and since no test is read
            # to find a label, a selection of two (refused at VARIABLE by
            # `available`) cannot show one of them's expression either.
            entry = sel.texts.get(sel.variable)
            text = sel.variable if entry is None else entry.text
            return f"Add a criterion for {text}"
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        if len(sel.tests) != 1:
            return "select exactly one test"
        model_id = next(iter(sel.tests))
        running = s.running
        if running is not None and s.is_locked(model_id):
            return f"{running.label} is running on {model_id}"
        tree = s.tree(model_id)
        if tree is None:
            return "no tree to edit (nothing scored)"
        if sel.node_path is not None and _node_or_none(tree, sel.node_path) is None:
            # A selection the tree moved under: the panel would index a list
            # that is shorter now.
            return f"{sel.node_path} names no node of {model_id}"
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
        node = _node_or_none(tree, path)
        if node is None:
            raise ValueError(f"{path} names no node of {model_id}")
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
    kind = (
        ui.select(
            names, label="combinator", value=current if current in names else names[0]
        )
        .props("dense")
        .classes("w-full")
    )
    k = (
        ui.number(label="k", value=(node or {}).get("k") or 1)
        .props("dense")
        .classes("w-full")
    )
    weights = (
        ui.input(
            label="weights",
            value=", ".join(str(w) for w in (node or {}).get("weights") or []),
        )
        .props("dense")
        .classes("w-full")
    )
    threshold = (
        ui.number(label="threshold", value=(node or {}).get("threshold") or 0.0)
        .props("dense")
        .classes("w-full")
    )
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
    ``done`` runs first when the form sits in a menu that must close.

    The variable select's VALUES are result-file names — a leaf is written
    against the name — and its option TEXT reads the label, with the name
    beside it when two of this test's variables share one. A list of
    ``unitTests.x[1..12]`` named nothing a person could choose between."""
    names = _variable_names(s, model_id)
    if variable and variable not in names:
        names = [variable, *names]
    labels = s.labels(model_id)
    var = (
        ui.select(
            {n: labels.get(n, n) for n in names} or {"": ""},
            label="leaf variable",
            value=variable if variable else (names[0] if names else ""),
        )
        .props("dense")
        .classes("w-full")
        .mark(f"{mark}:variable")
    )
    met = (
        ui.select(
            [d.name for d in metrics()], label="leaf metric", value=DEFAULT_METRIC
        )
        .props("dense")
        .classes("w-full")
    )

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
    """The menu anchored to ``button``, its CONTENT built when the menu
    OPENS — its value going True — and cleared when it closes.

    Nothing but an empty column exists inside a menu that is not open: a
    cluster whose three forms are built with it is ~28 elements, and a tree
    row holds one per node — 40 leaves made a 2387-element page with the
    tree open, and every sync re-sent them. Each open builds the column
    again, from the tree as it is at that moment, so a form never shows a
    value the node no longer has (a combinator edited and then Discarded
    reopened on its old ``k``).

    The menu's value is the ONE hook: whatever opens it in a browser — a
    click on the anchor, a keyboard — reaches the server as that change,
    and so does ``menu.open()``. The anchor's click is not a second hook;
    building there needed a once-per-open flag that the user simulation
    never cleared (a click moves no value), which is how a form went stale
    and an apply from it staged the node's old value back (the Task 4
    review's probe). The column carries the menu's width: with none, Quasar
    takes the anchor's, and an icon button's is 88 px with every select
    label cropped.
    """
    with button:
        menu = ui.menu()
    with menu:
        column = ui.column().classes("q-pa-sm gap-1").style("min-width: 220px")

    def rebuild() -> None:
        column.clear()
        with column:
            build(menu)

    menu.on_value_change(lambda e: rebuild() if e.value else column.clear())


def _cluster(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, path: str
) -> None:
    """One node's controls: flat round icon buttons, each form in its
    menu."""
    node = _node_or_none(s.tree(model_id), path)
    if node is None:  # `available` already says so; a job may race
        return
    is_combinator = "combinator" in node

    def icon(name: str, tip: str, mark: str, on_click: Any = None) -> Any:
        button = ui.button(icon=name, on_click=on_click)
        button.props("flat dense round size=sm")
        with button:
            # Above the icon: below it (Quasar's default) the tooltip covers
            # the first control of the menu the same click opens.
            ui.tooltip(tip).props('anchor="top middle" self="bottom middle"')
        return button.mark(mark)

    def act_now(action: str) -> None:
        ui_panel.submit(model_id=model_id, action=action, path=path)

    def fresh() -> dict | None:
        """The node as it is at THIS open; the sentence in the menu when the
        tree has moved out from under the path since the cluster was drawn."""
        at_open = _node_or_none(s.tree(model_id), path)
        if at_open is None:
            ui.label(f"{path} names no node of {model_id}").classes("text-negative")
        return at_open

    def build_add(menu: Any) -> None:
        at_open = fresh()
        if at_open is None:
            return
        _add_form(
            ui,
            s,
            ui_panel,
            model_id,
            under=path,
            variable=s.selection.variable or at_open.get("variable"),
            mark=f"apply-add:{path}",
            done=menu.close,
        )

    def build_wrap(menu: Any) -> None:
        if fresh() is None:
            return
        values = _combinator_controls(ui, None)

        def apply() -> None:
            menu.close()
            ui_panel.submit(model_id=model_id, action="wrap", path=path, **values())

        ui.button("apply wrap", on_click=apply).props("dense no-caps").mark(
            f"apply-wrap:{path}"
        )

    def build_edit(menu: Any) -> None:
        at_open = fresh()
        if at_open is None:
            return
        values = _combinator_controls(ui, at_open)

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
    """What a new leaf may score: the scored names, then the rest of the
    result's — `Session.tracked`'s list, through the same function and for
    the same reason. A variable whose last criterion was just removed is
    still the test's, and the add forms are the way back to it."""
    comp = s.comparisons.get(model_id)
    scored = [vc.name for vc in comp.variables] if comp is not None else []
    project = s.project
    result = project.results.get(model_id) if project is not None else None
    return tracked_names(scored, result)
