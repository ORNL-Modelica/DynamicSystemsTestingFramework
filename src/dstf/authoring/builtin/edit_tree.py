"""Edit tree (§4.6 table, §4.7): the NODE and VARIABLE placements, exactly
one test selected. At NODE the panel IS that node's controls — add, wrap,
edit on a k-of-n or weighted node, and remove — each form a ROW under the
node's row (`Panel.row`), and on a combinator its kind as a dropdown in
the row's lead place (`Panel.lead`), unwrap its last entry; at VARIABLE —
a plot's criteria header — it is one `+` whose "Add a criterion for x"
form is a row under the header, the new criterion's editor open after
Add; at STATISTIC — a Statistics row no criterion reads yet — one `+`
that adds a requirement on the row's quantity at once, and opens its
editor.
Every control stages one structural change against the paths the report's
tree already shows. The D66 validator's refusal reaches the page through
``Panel.submit`` — the Session is what it was.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ...backends.types import DatasetType
from ...comparison.registry import DEFAULT_METRIC, combinators, metrics
from ...comparison.seeds import (
    SeedData,
    quantity_kind,
    seed_diagnostic,
    seed_quantity,
    seeded_leaf,
)
from ...comparison.sides import parse_sides, signed_sides
from ..capabilities import Panel, Patch, Placement
from ..session import SIMULATE_ONLY, no_tree_error

if TYPE_CHECKING:
    from ..session import SessionView
from ..spec_document import PatchOp
from ..tree_move import (
    BEFORE,
    WHERE_IT_IS,
    Dest,
    Moved,
    move_destinations,
    path_map,
    plan_move,
    validator_for,
    zone_dest,
    zone_map,
)
from ..tree_pointer import node_or_none
from ..tree_text import node_text, nodes_of

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

ROOT = "/metrics"

#: What `EditTree.act` does; anything else is refused before its inputs are
#: read.
_ACTIONS = ("remove", "move", "add", "unwrap", "wrap", "edit")

#: The combinator-only raw keys each kind carries (``tree_spec``); every
#: other one is removed when a node changes kind.
_KIND_KEYS: dict[str, tuple[str, ...]] = {
    "k-of-n": ("k",),
    "weighted": ("weights", "threshold", "direction"),
}
_COMBINATOR_KEYS = ("k", "weights", "threshold", "direction")


def _kind_fields(kind: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """The raw fields ``kind`` takes, from the panel's controls. A weighted
    node's threshold is never read as 0 when none was typed: 0 with `less`
    always fails."""
    if kind == "k-of-n":
        # A cleared field stays None, for the parser to say "k is empty"
        # rather than refuse a 0 nobody typed.
        k = inputs["k"]
        return {"k": int(round(float(k))) if _typed(k) else None}
    if kind == "weighted":
        text = str(inputs["weights"] or "")
        try:
            weights = [float(w) for w in text.replace(",", " ").split()]
        except ValueError:
            raise ValueError(
                f"weights: {text!r} is not a comma-separated list of numbers"
            ) from None
        if not _typed(inputs["threshold"]):
            raise ValueError(_NO_THRESHOLD)
        return {
            "weights": weights,
            "threshold": float(inputs["threshold"]),
            "direction": str(inputs.get("direction") or "less"),
        }
    return {}


def _typed(value: Any) -> bool:
    """Whether a number field holds a number: an emptied `ui.number` reads
    None, an untouched one here too."""
    return value is not None and value != ""


#: What each Wrap does to the tree, one sentence per kind (the 2026-09-21
#: board, 1C). ``{what}`` is "criterion" for a leaf, "node" for a
#: combinator.
_WRAP_SENTENCES = {
    "and": "The {what} becomes the only child of a new and; add siblings to it "
    "with its +.",
    "or": "The {what} becomes the only child of a new or; add siblings to it "
    "with its +.",
    "k-of-n": "The {what} becomes the only child of a new k-of-n; add siblings "
    "to it with its +.",
    "warn": "A failure of this {what} becomes a warning; the test still passes.",
    "weighted": "The {what} becomes the only child of a new weighted node, with "
    "the one weight above.",
}
#: The Edit form's sentence: on the node's own kind it sets the kind's
#: fields; preset by the dropdown to another kind, the kind changes too.
_EDIT_SENTENCE = "Changes this node's fields; its children stay."
_EDIT_KIND_SENTENCE = "Changes this node's kind and fields; its children stay."
#: The kind dropdown's value for its last entry, which is not a kind: no
#: combinator is named this.
_UNWRAP_VALUE = "unwrap…"
#: What that entry reads, below a separator.
_UNWRAP_TEXT = "unwrap — its children take its place"
#: The kinds a node cannot take without fields: the dropdown opens the Edit
#: form preset to one of them, and nothing is staged until Apply. And, or
#: and warn have none — only these nodes' rows open it on a click (U5).
_NEEDS_FIELDS = ("k-of-n", "weighted")
#: The dropdown's option slot: the unwrap entry below a separator.
_KIND_OPTION = (
    "<q-separator v-if=\"props.opt.label.startsWith('unwrap')\" />"
    '<q-item v-bind="props.itemProps"><q-item-section>'
    "<q-item-label>{{ props.opt.label }}</q-item-label>"
    "</q-item-section></q-item>"
)
#: Under a weighted node's Threshold, following its Direction (the pass
#: rule of `metric_tree`'s weighted node).
_THRESHOLD_HINTS = {
    "less": "Passes when Σ weight × score over the children is below the "
    "threshold (lower is better).",
    "greater": "Passes when Σ weight × score over the children is above the "
    "threshold (higher is better).",
}
#: The form row's title says where the criterion lands; this, where its
#: values come from.
_ADD_SENTENCE = "Values come from the data where the metric needs them."
#: Why ``+`` is grey where a child cannot be added alone: the validator
#: refuses it by name whatever the form says, so the form is not offered.
_NO_ADD = {
    "warn": "a warn takes exactly one child",
    "weighted": "a weighted node has one weight per child; a child cannot be "
    "added alone",
}
_ROOT_UNWRAP = "the root has no parent to take its children"
#: Why a weighted form's action is grey: its threshold starts EMPTY, not 0
#: (0 with `less` always fails), and has to be typed.
_NO_THRESHOLD = "enter a threshold"
#: The direction a weighted node has when it names none (`tree_spec`); an
#: Edit leaves it unwritten rather than stage the default.
_DEFAULT_DIRECTION = "less"
#: A weighted node's ``direction``, as the Direction select reads it.
_DIRECTIONS = {"less": "lower is better", "greater": "higher is better"}
#: The Move to… icon's tooltip and accessible name (U4).
_MOVE_TO = "Move to…"


def seed_data(
    s: SessionView, model_id: str, variable: str, window: dict | None = None
) -> SeedData:
    """The series a new criterion on ``variable`` will score, for its seed:
    only the samples inside ``window`` (a leaf's ``start`` / ``end``) when
    it has one.

    Read from the variable's view, so a variable no criterion scores has no
    reference there even when the baseline file holds one: it seeds from
    its actual result (a scalar then gets bounds, not a tolerance). A
    variable without a result gives neither, and the leaf stays bare. Only
    an unknown test raises (``KeyError``); an unknown variable is an empty
    view."""
    try:
        view = s.view(model_id, variable)
    except KeyError:
        return SeedData(ref=None, act=None)
    ref = (
        (view.ref_time, view.ref_values)
        if view.ref_time is not None and view.ref_values is not None
        else None
    )
    act = (
        (view.act_time, view.act_values)
        if view.act_time is not None and view.act_values is not None
        else None
    )
    return SeedData(ref=ref, act=act).within(window)


class EditTree:
    """The metric tree's shape: wrap a node in a combinator, change a
    combinator's kind and its fields, add a leaf, remove a node, move a
    node (`tree_move`)."""

    name = "edit-tree"
    label = "Edit tree"
    placements = frozenset({Placement.NODE, Placement.VARIABLE, Placement.STATISTIC})

    def caption(self, s: SessionView) -> str:
        # NODE and VARIABLE are inline: no button reads a caption there.
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        if len(sel.tests) != 1:
            return "select exactly one test"
        model_id = next(iter(sel.tests))
        if sel.quantity is not None:
            # A Statistics row's `+` is drawn grey with the add form's
            # reason where nothing can be added (A9), a job's lock included:
            # a why-line in place of it, on every row, said the same thing
            # twenty times (57 on a TRANSFORM card).
            return True
        locked = _locked_by(s, model_id)
        if locked is not None:
            return locked
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            return why
        if sel.node_path is not None and node_or_none(tree, sel.node_path) is None:
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
        elif sel.quantity is not None:
            _statistic_plus(ui, s, ui_panel, model_id, sel.quantity)
        else:
            # The plot's criteria header (Q3 A): one `+`, its form a row
            # under the header, the new criterion's editor open after Add.
            _variable_plus(ui, s, ui_panel, model_id, str(sel.variable))

    def act(self, s: SessionView, **inputs: Any) -> Patch | None:
        action, model_id, path = inputs["action"], inputs["model_id"], inputs["path"]
        if action not in _ACTIONS:
            # First: an action this does not know is the error, not the
            # input its last branch would read.
            raise ValueError(f"unknown action {action!r}")
        if action == "remove":
            return _patch(s, None, inputs)
        if action == "move":
            # The whole new tree as ONE `add /metrics` (V1) — the root add
            # a wrap uses: a flat test materializes nothing first, and two
            # moves in a row fold into one pending op (`Session.stage`).
            moved = planned_move(s, model_id, path, inputs["dest"])
            if moved is None:
                return None
            return Patch(ops=[PatchOp(model_id, "add", ROOT, moved.tree)])
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            raise no_tree_error(model_id, why)
        return _patch(s, tree, inputs)


def _patch(s: SessionView, tree: dict | None, inputs: dict[str, Any]) -> Patch | None:
    """What `EditTree.act` stages for every action but a move, built from
    ``tree`` — the test's raw tree as it is now, read by the caller, so a
    value that carries one of its nodes carries THAT node (`path_map`). A
    remove reads none: the Session's stage says why a path names nothing."""
    action, model_id, path = inputs["action"], inputs["model_id"], inputs["path"]
    if action == "remove":
        return Patch(ops=[PatchOp(model_id, "remove", path)])
    assert tree is not None  # read by the caller for every other action
    node = node_or_none(tree, path)
    if node is None:
        raise ValueError(f"{path} names no node of {model_id}")
    if action == "add":
        into = _into(tree, node, path)
        diagnostics = _diagnostics(s, model_id)
        quantity = inputs.get("quantity")
        if quantity is not None and inputs.get("variable") is not None:
            raise ValueError("add takes a quantity or a variable, not both")
        if quantity is not None:
            leaf = _quantity_leaf(diagnostics, quantity, into)
        elif inputs["variable"] in diagnostics:
            # A diagnostic is one stored number (K1): its final through
            # a quantity path, never a trajectory — a `{"reduce": ...}`
            # scalar on one never scores.
            if inputs["metric"] not in _metrics_for(diagnostic=True):
                raise ValueError(
                    f"{inputs['variable']} is a diagnostic: scalar scores it"
                )
            leaf = seed_diagnostic(
                inputs["variable"], diagnostics[inputs["variable"]], into=into
            )
        else:
            # Seeded: a bare range, tube or scalar does not resolve, and
            # a bare dominant-frequency always fails "no peaks declared".
            leaf = seeded_leaf(
                inputs["metric"],
                inputs["variable"],
                seed_data(s, model_id, inputs["variable"]),
            )
        if "combinator" in node:
            under = path
        elif path == ROOT:
            # Beside a root leaf: the root becomes an `and` of both, in
            # one op (`add` at /metrics replaces the member). A leaf has
            # no `children` for the patch to append to.
            value = {"combinator": "and", "children": [node, leaf]}
            return Patch(ops=[PatchOp(model_id, "add", ROOT, value)])
        else:
            # Beside any other leaf: right AFTER it, under its parent
            # combinator (Q3 A) — where the `+` that was clicked is.
            parent, index = path.rsplit("/children/", 1)
            at = f"{parent}/children/{int(index) + 1}"
            return Patch(ops=[PatchOp(model_id, "add", at, leaf)])
        return Patch(ops=[PatchOp(model_id, "add", f"{under}/children/-", leaf)])
    if action == "unwrap":
        # A single-child combinator gives way to its child (the browser
        # editor's unwrap): `warn(leaf)` becomes `leaf`, params kept.
        children = node.get("children") or []
        if len(children) == 1:
            op = "add" if path == ROOT else "replace"
            return Patch(ops=[PatchOp(model_id, op, path, children[0])])
        # Several children take the node's place in its parent, in
        # order. A `warn` or `weighted` parent refuses that by name, in
        # the validator, like any other op.
        if path == ROOT:
            raise ValueError(_ROOT_UNWRAP)
        if not children:
            raise ValueError(f"{path} has no children to take its place")
        parent, index = path.rsplit("/children/", 1)
        ops = [PatchOp(model_id, "remove", path)]
        ops += [
            PatchOp(model_id, "add", f"{parent}/children/{int(index) + j}", child)
            for j, child in enumerate(children)
        ]
        return Patch(ops=ops)
    kind = inputs["kind"]
    fields = _kind_fields(kind, inputs)
    if action == "wrap":
        # `add` at /metrics: a flat test has no member there yet, and
        # RFC 6902 `replace` on a missing member is an error, while
        # `add` on an existing one replaces it.
        op = "add" if path == ROOT else "replace"
        value = {"combinator": kind, "children": [node], **fields}
        return Patch(ops=[PatchOp(model_id, op, path, value)])
    # edit: only what moved. An Apply with nothing changed staged the
    # kind and every field again, four ops for no edit. Nothing moved
    # is None — the row has closed, and nothing is staged.
    ops = []
    if node.get("combinator") != kind:
        ops.append(PatchOp(model_id, "replace", f"{path}/combinator", kind))
    for key, value in fields.items():
        if key in node:
            if node[key] != value:
                ops.append(PatchOp(model_id, "replace", f"{path}/{key}", value))
        elif not (key == "direction" and value == _DEFAULT_DIRECTION):
            ops.append(PatchOp(model_id, "add", f"{path}/{key}", value))
    keeps = _KIND_KEYS.get(kind, ())
    ops += [
        PatchOp(model_id, "remove", f"{path}/{key}")
        for key in _COMBINATOR_KEYS
        if key in node and key not in keeps
    ]
    return Patch(ops=ops) if ops else None


def _combinator_fields(
    ui: Any,
    node: dict | None,
    sentence: Callable[[str], str],
    children: int,
    fixed: str | None = None,
) -> tuple[Callable[[], dict[str, Any]], Callable[[Any], None]]:
    """The kind select and ONLY the chosen kind's fields (board 1B), in a
    column rebuilt on the select's change; a value typed for a kind is kept
    for when the user returns to it. Under them, ``sentence(kind)`` — what
    the action does for that kind (`dstf-why`). ``node`` prefills them: the
    node's own kind and fields for Edit, none for Wrap.

    Returns the reader the action calls at apply time, and ``guard``: the
    action button handed to it is grey, with `enter a threshold` as its
    tooltip, while the kind is weighted and no threshold is typed. The
    threshold starts EMPTY unless the node has one, and the weights start
    as one 1 per child — ``children``, the node's count after the action
    (1 for a wrap) — so an untouched weighted form stages what it shows:
    before, it staged no weights and threshold 0, refused every time.

    ``fixed`` draws no kind select: the form is that kind's (the Edit form,
    whose kind is the node's or the one its dropdown chose)."""
    names = [c.name for c in combinators()]
    node = node or {}
    current = node.get("combinator")
    weights = node.get("weights") or [1] * children
    values: dict[str, Any] = {
        "k": node.get("k") or 1,
        "weights": ", ".join(str(w) for w in weights),
        "threshold": node.get("threshold"),
        "direction": node.get("direction") or _DEFAULT_DIRECTION,
    }
    kind: Any = (
        SimpleNamespace(value=fixed)
        if fixed is not None
        else ui.select(
            names, label="Combinator", value=current if current in names else names[0]
        )
        .props("dense")
        .classes("w-full")
    )
    fields = ui.column().classes("w-full gap-1")
    why = ui.label("").classes("dstf-why")
    #: The action buttons `guard` was handed, each with its tooltip.
    guarded: list[tuple[Any, Any]] = []

    def gate() -> None:
        waiting = kind.value == "weighted" and not _typed(values["threshold"])
        for button, tip in guarded:
            button.set_enabled(not waiting)
            tip.set_visibility(waiting)

    def keep(key: str) -> Callable[[Any], None]:
        def kept(e: Any) -> None:
            values[key] = e.value
            gate()

        return kept

    def rebuild() -> None:
        fields.clear()
        with fields:
            if kind.value == "k-of-n":
                ui.number(
                    label="k — how many must pass",
                    value=values["k"],
                    on_change=keep("k"),
                ).props("dense").classes("w-full")
            elif kind.value == "weighted":
                ui.input(
                    label="Weights, one per child",
                    value=values["weights"],
                    on_change=keep("weights"),
                ).props("dense").classes("w-full")
                ui.number(
                    label="Threshold",
                    value=values["threshold"],
                    on_change=keep("threshold"),
                ).props("dense").classes("w-full")
                hint = (
                    ui.label(_THRESHOLD_HINTS[str(values["direction"])])
                    .classes("dstf-why")
                    .mark("threshold-hint")
                )

                def direction(e: Any) -> None:
                    keep("direction")(e)
                    hint.set_text(_THRESHOLD_HINTS[str(e.value)])

                ui.select(
                    _DIRECTIONS,
                    label="Direction",
                    value=values["direction"],
                    on_change=direction,
                ).props("dense").classes("w-full")
        why.set_text(sentence(str(kind.value)))
        gate()

    def guard(button: Any) -> None:
        with button:
            tip = ui.tooltip(_NO_THRESHOLD)
        guarded.append((button, tip))
        gate()

    if fixed is None:
        kind.on_value_change(lambda _e: rebuild())
    rebuild()
    return (lambda: {"kind": kind.value, **values}), guard


def _add_form(
    ui: Any,
    s: SessionView,
    ui_panel: Panel,
    model_id: str,
    *,
    under: str,
    variable: str | None,
    mark: str,
    close: Callable[[], None],
    added: str,
) -> None:
    """A form row's variable picker and metric select, the sentence, Cancel
    and Add (marked ``mark``), which stages and, once that went through,
    closes the row and reveals ``added`` — the new criterion's path; a
    refused Add keeps the row.

    The picker (Q8) offers EVERY variable of the result file, tracked or
    not — a criterion on one is what tracks it (the next run records it) —
    in sections: the plot's own series, the rest, the diagnostics the run
    recorded. Its values are result-file names — a leaf is written against
    the name — and a row reads the label, with the name beside it. A list
    of ``unitTests.x[1..12]`` named nothing a person could choose between.
    The names are read when the form OPENS, never when the `+` is drawn."""
    from ..picker_rows import picker_rows
    from ..shell.picker import VariablePicker

    names = s.variable_names(model_id)
    if variable and variable not in names:
        names = [variable, *names]
    on_plot = _plot_series(s, model_id, s.selection.variable)
    diagnostics = list(_diagnostics(s, model_id))
    texts = s.variable_texts(model_id)
    first = variable or (on_plot or names or [""])[0]

    #: The metric chosen on an ordinary variable while a diagnostic —
    #: which offers scalar alone — is picked: back on an ordinary one, it is
    #: chosen again, not the first metric of the list.
    kept: dict[str, str] = {}

    def follow(name: str) -> None:
        """The Metric select offers what can score ``name`` (Q2)."""
        options = _metrics_for(diagnostic=name in diagnostics)
        value = met.value
        if name in diagnostics:
            if list(met.options) != options:  # leaving an ordinary variable
                kept["metric"] = met.value
        else:
            value = kept.pop("metric", value)
        met.set_options(options, value=value if value in options else options[0])

    var = VariablePicker(
        lambda keep: picker_rows(
            on_plot=on_plot,
            names=names,
            diagnostics=diagnostics,
            texts=texts,
            keep=keep,
        ),
        value=first,
        text_of=lambda name: texts[name].text if name in texts else name,
        on_change=follow,
        marker=f"{mark}:variable",
    )
    options = _metrics_for(diagnostic=first in diagnostics)
    met = (
        ui.select(
            options,
            label="Metric",
            value=DEFAULT_METRIC if DEFAULT_METRIC in options else options[0],
        )
        .props("dense")
        .classes("w-full")
    )

    def apply() -> None:
        if not _submit(
            s,
            ui_panel,
            model_id=model_id,
            action="add",
            path=under,
            variable=var.value,
            metric=met.value,
        ):
            return  # refused: the row stays, with what was chosen
        close()
        ui_panel.reveal(_first_leaf(s.tree(model_id), added))

    # A criterion staged on a simulate-only test is never scored (the
    # comparator short-circuits it): Add is grey there, tree or not.
    simulate_only = _simulate_only(s, model_id)
    ui.label(SIMULATE_ONLY if simulate_only else _ADD_SENTENCE).classes("dstf-why")
    with ui.row().classes("items-center gap-2"):
        _cancel(ui, close, under)
        # A disabled Quasar button takes no pointer events: the reason's
        # tooltip is on a span around it.
        with ui.element("span"):
            add = ui.button("Add", on_click=apply).props("dense no-caps").mark(mark)
            if simulate_only:
                add.disable()
                ui.tooltip(SIMULATE_ONLY)


def _simulate_only(s: SessionView, model_id: str) -> bool:
    project = s.project
    tests = project.tests if project is not None else ()
    return any(t.simulate_only for t in tests if t.model_id == model_id)


def _into(tree: dict | None, node: dict, path: str) -> str:
    """The kind of the combinator an add at ``path`` lands in — a root
    leaf becomes an `and` of both: a time warns only in an `and`."""
    if "combinator" in node:
        return str(node["combinator"])
    if path == ROOT:
        return "and"
    parent_node = node_or_none(tree, path.rsplit("/children/", 1)[0])
    return str((parent_node or {}).get("combinator"))


def _quantity_leaf(diagnostics: dict[str, Any], quantity: str, into: str) -> dict:
    """The criterion an add on ``quantity`` stages, landing in ``into``: a
    stored number, no variable. One seed per path — a diagnostic's final
    is the add form's diagnostic criterion, its kind read from its samples,
    and a top-level statistic naming a diagnostic reads them too, as its
    ``X.final`` would."""
    name, _, field_name = quantity.rpartition(".")
    if field_name == "final" and name in diagnostics:
        return seed_diagnostic(name, diagnostics[name], into=into)
    return seed_quantity(
        quantity, quantity_kind(quantity, diagnostics.get(quantity)), into=into
    )


def _requirement(leaf: dict) -> str:
    """What a Statistics row's `+` adds, in the words of its seed — the
    tolerance is the seed's, never a second copy of it. Read as any
    tolerance is (`parse_sides`): a number, or a lower/upper pair, whose
    two sides — where they differ — read as its two edges."""
    inner = leaf["children"][0] if "combinator" in leaf else leaf
    written = inner.get("tolerance")
    tolerance = parse_sides(0 if written is None else written, "tolerance")
    rel = inner.get("tolerance_mode") == "rel"
    if not tolerance.equal:
        edges = (
            signed_sides(tolerance, "g", scale=100, unit="%")
            if rel
            else signed_sides(tolerance, "g")
        )
        what = f"within {edges}"
    elif rel:
        what = f"within {tolerance.upper * 100:g}%"
    else:
        what = "exact match" if tolerance.upper == 0 else f"within ±{tolerance.upper:g}"
    warning = ", as a warning" if leaf.get("combinator") == "warn" else ""
    return f"Add a requirement: {what}{warning}"


def _cancel(ui: Any, close: Callable[[], None], path: str) -> None:
    """A form row's Cancel: the row goes, nothing is staged."""
    ui.button("Cancel", on_click=close).props("flat dense no-caps").mark(
        f"cancel:{path}"
    )


def _parent_of(path: str) -> str | None:
    return path.rsplit("/children/", 1)[0] if "/children/" in path else None


def _first_leaf(tree: dict | None, path: str) -> str:
    """The first leaf at or under ``path``: what an Add reveals. A seed may
    be a combinator around its leaf — CPU time is added to an ``and``
    inside a ``warn`` (`seed_diagnostic`) — and the editor opens on the
    criterion."""
    node = node_or_none(tree, path)
    while node is not None and node.get("children"):
        path, node = f"{path}/children/0", node["children"][0]
    return path


def _added_path(tree: dict | None, path: str) -> str:
    """Where an Add from ``path``'s row lands, read from the tree as it is
    when the row is built — after the add the count has moved by one:
    under a combinator its next child (the end); beside the root leaf the
    root's second (the root becomes an ``and`` of both); beside any other
    leaf the place right after it."""
    node = node_or_none(tree, path) or {}
    if "combinator" in node:
        return f"{path}/children/{len(node.get('children') or [])}"
    if path == ROOT:
        return f"{ROOT}/children/1"
    parent, index = path.rsplit("/children/", 1)
    return f"{parent}/children/{int(index) + 1}"


def _cluster(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, path: str
) -> None:
    """One node's controls: flat round icon buttons, each form a ROW under
    the node's row (`Panel.row`, the 2026-09-21 board, 1A-1D).

    A form is built on its icon's CLICK, from the tree as it is then, and
    cleared on Cancel, on an action that went through (a refused one keeps
    the row and what was typed, with the refusal under the node's row,
    above the form), or when the page closes the row; the page rebuilds an
    open one when an edit staged elsewhere moves its node (`Panel.row`'s
    contract), so a form never shows a value the node no longer has — a
    combinator edited and then Discarded reopened on its old ``k``. Nothing
    but the icons exists until then: a cluster whose three forms were built
    with it was ~28 elements, and a tree row holds one per node — 40 leaves
    made a 2387-element page with the tree open, and every sync re-sent
    them. The title is read inside the click too: a `tree_view` read per
    cluster at build cost one read per node on every rebuild of the rows
    (the page's cost tests)."""
    tree = s.tree(model_id)
    node = node_or_none(tree, path)
    if node is None:  # `available` already says so; a job may race
        return
    is_combinator = "combinator" in node
    # `+` adds at the end of a combinator, right after a leaf: the
    # combinator that would take the child decides whether one can be
    # added alone.
    parent = _parent_of(path)
    taker = node if is_combinator else node_or_none(tree, parent or "")
    no_add = _NO_ADD.get(str((taker or {}).get("combinator")))

    def icon(
        name: str, tip: str, mark: str, on_click: Any = None, enabled: bool = True
    ) -> Any:
        button = ui.button(icon=name, on_click=on_click)
        button.props("flat dense round size=sm")
        with button:
            # Above the icon: below it (Quasar's default) the tooltip covers
            # the title of the form row the same click opens under the row.
            ui.tooltip(tip).props('anchor="top middle" self="bottom middle"')
        # Its accessible name is the tooltip: the icon's ligature ("add",
        # "data_array") names nothing. Set as an item, not through the props
        # string, whose parser a quote in a node's text would break.
        button.props["aria-label"] = tip
        if not enabled:
            button.disable()
        return button.mark(mark)

    def text() -> str:
        """What the node's row reads, for a form's title — read at the
        icon's click, never while the cluster is built."""
        seen = next(
            (n for n in nodes_of(s.tree_view(model_id)) if n.get("path") == path),
            None,
        )
        return path if seen is None else node_text(seen)

    def act_now(action: str) -> bool:
        return _submit(s, ui_panel, model_id=model_id, action=action, path=path)

    def fresh() -> dict | None:
        """The node as it is at THIS build; the sentence in the row when the
        tree has moved out from under the path since the cluster was drawn."""
        at_open = node_or_none(s.tree(model_id), path)
        if at_open is None:
            ui.label(f"{path} names no node of {model_id}").classes("text-negative")
        return at_open

    def build_add(close: Callable[[], None]) -> None:
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
            close=close,
            # From the tree as it is NOW, before the add moves the count.
            added=_added_path(s.tree(model_id), path),
        )

    def build_wrap(close: Callable[[], None]) -> None:
        if fresh() is None:
            return
        what = "node" if is_combinator else "criterion"
        # The wrapped node is the new combinator's one child.
        values, guard = _combinator_fields(
            ui,
            None,
            lambda kind: _WRAP_SENTENCES.get(kind, "").format(what=what),
            children=1,
        )

        def apply() -> None:
            if _submit(
                s, ui_panel, model_id=model_id, action="wrap", path=path, **values()
            ):
                close()

        with ui.row().classes("items-center gap-2"):
            _cancel(ui, close, path)
            guard(
                ui.button("Wrap", on_click=apply)
                .props("dense no-caps")
                .mark(f"apply-wrap:{path}")
            )

    def build_edit(close: Callable[[], None], kind: str | None = None) -> None:
        """The Edit form: ``kind``'s fields — the node's own kind, or the
        one its dropdown chose (k-of-n, weighted: they need fields before
        anything can be staged). No kind select: the dropdown is where the
        kind changes, and unwrap is its last entry."""
        at_open = fresh()
        if at_open is None:
            return
        own = str(at_open.get("combinator"))
        fixed = kind or own
        values, guard = _combinator_fields(
            ui,
            at_open,
            lambda _kind: _EDIT_SENTENCE if fixed == own else _EDIT_KIND_SENTENCE,
            children=len(at_open.get("children") or []),
            fixed=fixed,
        )

        def apply() -> None:
            if _submit(
                s, ui_panel, model_id=model_id, action="edit", path=path, **values()
            ):
                close()

        with ui.row().classes("items-center gap-2"):
            _cancel(ui, close, path)
            guard(
                ui.button("Apply", on_click=apply)
                .props("dense no-caps")
                .mark(f"apply-edit:{path}")
            )

    _drag(s, ui_panel, model_id, path)
    if node.get("combinator") in _NEEDS_FIELDS:
        # A click on the node's row opens its fields (U5, Q6) — the root's
        # too, or a k-of-n root's k could not be edited at all. and / or /
        # warn have none: their kind is all there is, and the dropdown
        # changes it, so their row's click does nothing.
        ui_panel.on_click(lambda: ui_panel.row("Edit this combinator", build_edit))
    if is_combinator:
        ui_panel.lead(
            partial(
                _kind_select,
                ui,
                s,
                ui_panel,
                model_id,
                path,
                node,
                lambda kind: ui_panel.row(
                    "Edit this combinator", partial(build_edit, kind=kind)
                ),
            )
        )

    with ui.row().classes("dstf-node-controls items-center no-wrap gap-0"):
        # A combinator takes the new criterion as its last child; a leaf
        # has it right after itself (Q3 A).
        where = "at the end of" if is_combinator else "after"
        icon(
            "add",
            no_add
            or (
                f"Add a criterion at the end of this {node['combinator']}"
                if is_combinator
                else "Add a criterion after this one"
            ),
            f"add:{path}",
            lambda: ui_panel.row(f"Add a criterion {where} {text()}", build_add),
            enabled=no_add is None,
        )
        icon(
            "data_array",
            "Wrap in a combinator (and / or / k-of-n / warn / weighted)",
            f"wrap:{path}",
            lambda: ui_panel.row(f"Wrap {text()} in a combinator", build_wrap),
        )
        if path != ROOT and ui_panel.can_move:
            # Moving is the tree card's (V2); the root has nowhere to go.
            _move_menu(ui, s, ui_panel, model_id, path)
        if path != ROOT:  # the root has nothing to be removed from
            icon(
                "remove",
                "Remove this node and everything under it"
                if is_combinator
                else "Remove this criterion",
                f"remove:{path}",
                lambda: act_now("remove"),
            )


def _variable_plus(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, variable: str
) -> None:
    """The plot's `+`: an Add row for ``variable`` (its select offers the
    test's other variables too), landing at the end of the root combinator
    — or, under a root leaf, as ``and[leaf, new]`` — and revealing the new
    criterion's editor. Grey, with the reason, under a root that takes no
    child alone. A watch plot's variable the run did not record is offered
    too: a criterion names it, and that tracks it (U2).

    It reads the variable as the PAGE does — the scope handed down with
    the selection (`Selection.texts`: the expression its model wrote, with
    the name beside it when another variable of that test shares it). The
    scope is not derived here: a caption per card that built it for itself
    was the page's whole rebuild cost. A selection carrying none reads the
    name; the form still writes the leaf against the NAME."""
    entry = s.selection.texts.get(variable)
    text = variable if entry is None else entry.text
    no_add = _NO_ADD.get(str((s.tree(model_id) or {}).get("combinator")))
    tip = no_add or f"Add a criterion for {text}"

    def build_add(close: Callable[[], None]) -> None:
        _add_form(
            ui,
            s,
            ui_panel,
            model_id,
            under=ROOT,
            variable=variable,
            mark=f"apply-add:{variable}",
            close=close,
            # From the tree as it is NOW, before the add moves the count.
            added=_added_path(s.tree(model_id), ROOT),
        )

    button = ui.button(
        icon="add",
        on_click=lambda: ui_panel.row(f"Add a criterion for {text}", build_add),
    )
    button.props("flat dense round size=sm")
    # Its accessible name is the sentence: the icon's ligature says "add".
    # Set as an item, not through the props string, whose parser a quote
    # in an expression would break.
    button.props["aria-label"] = tip
    with button.mark(f"add-for:{variable}"):
        ui.tooltip(tip).props('anchor="top middle" self="bottom middle"')
    if no_add is not None:
        button.disable()


def planned_move(s: SessionView, model_id: str, src: str, dest: Dest) -> Moved | None:
    """The move `EditTree.act` stages, with where the node lands and what
    else changed — what a panel reveals and says after its ``submit`` went
    through; `act` stages this plan's tree, so the two cannot disagree.
    Compute it BEFORE `submit`: afterwards ``src`` names another node (in
    ``[x, y]``, child 1 to the front gives ``[y, x]``; once that went
    through, the same call moves x back). Checked against the test's
    baseline roles as `Session.stage` checks it, so a refusal is the
    validator's sentence, raised here."""
    tree, why = s.tree_or_reason(model_id)
    if tree is None:
        assert why is not None  # `tree` is None
        raise no_tree_error(model_id, why)
    return plan_move(tree, src, dest, validate=_validator(s, model_id))


def _validator(s: SessionView, model_id: str) -> Callable[[dict], None]:
    """What a move's new tree is checked against: the test's baseline
    roles, as `Session.stage` checks them."""
    project = s.project
    assert project is not None  # a tree came from it
    return validator_for(project.role_lookup(model_id))


def moved_text(notes: tuple[str, ...]) -> str:
    """What a move's row says (V4): "Moved." and each other change as a
    sentence of its own — "Moved. The empty warn was removed." A note that
    starts with a field's name keeps it as written: "Moved. k 3 → 2." """

    def sentence(note: str) -> str:
        return note if note.startswith("k ") else note[:1].upper() + note[1:]

    return " ".join(["Moved.", *(f"{sentence(n)}." for n in notes)])


def _submit_move(
    s: SessionView, ui_panel: Panel, model_id: str, src: str, dest: Dest
) -> None:
    """Stage the move; once it went through, the page closes what is open
    and says where the node landed and what else changed (`Panel.moved`).
    Planned BEFORE the submit: afterwards ``src`` names another node."""
    try:
        moved = planned_move(s, model_id, src, dest)
    except Exception:  # the submit raises it again, and says or logs it
        moved = None
    went = ui_panel.submit(model_id=model_id, action="move", path=src, dest=dest)
    if went and moved is not None:
        ui_panel.moved(moved.path, moved_text(moved.notes), moved.paths)


def _submit(s: SessionView, ui_panel: Panel, **inputs: Any) -> bool:
    """`Panel.submit` for every action but a move, and once it went through
    where each node it kept went (`Panel.edited`, U5 R1): mapped BEFORE the
    submit, from the ops `act` builds over the tree as it is now — as a
    move is planned before its submit: afterwards the paths name the new
    tree. An act that raises maps nothing; the submit raises it again, and
    says or logs it."""
    paths: dict[str, str] | None = None
    try:
        tree = s.tree(inputs["model_id"])
        patch = None if tree is None else _patch(s, tree, inputs)
    except Exception:
        tree = patch = None
    if tree is not None and patch is not None:
        paths = path_map(tree, patch.ops)
    went = ui_panel.submit(**inputs)
    if went and paths is not None:
        ui_panel.edited(paths)
    return went


def _drag(s: SessionView, ui_panel: Panel, model_id: str, path: str) -> None:
    """The node's part in the host's drag (U4): a drop on its row is a
    move to that zone's place (`zone_dest`); a drag from it — never from
    the root — asks the tree as it is at that moment which zones refuse,
    and why (`zone_map`, once per drag)."""

    def on_drop(src: str, zone: str) -> None:
        dest = zone_dest(path, zone)
        if dest is not None:  # a zone the row has not got moves nothing
            _submit_move(s, ui_panel, model_id, src, dest)

    def zones() -> dict[str, dict[str, str | None]]:
        tree = s.tree(model_id)
        if tree is None:
            return {}
        return zone_map(tree, path, validate=_validator(s, model_id))

    ui_panel.movable(on_drop, None if path == ROOT else zones)


def _move_menu(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, path: str
) -> None:
    """Move to… (U4): an icon whose menu lists every place the node can go
    (`move_destinations`, in tree order) — BUILT AT THE CLICK, from the tree
    as it is then, like a form: the list is every place of the tree, and
    one per row built with the rows was ~20 s of rebuild on a 134-node
    tree (Q6). A refused place is greyed with its reason as a second line,
    where the node is greyed with none; the arrow keys skip both. Each
    place reads its node's text cut in the middle, then where in it — the
    row a "before" names cut in the middle too."""
    from ..shell.drawer_controls import menu_keys
    from ..shell.widgets import MiddleCut

    button = ui.button(icon="open_with").props("flat dense round size=sm")
    # Its accessible name is the tooltip; set as items, like the icons'.
    button.props["aria-label"] = _MOVE_TO
    button.props["aria-haspopup"] = "menu"
    button.props["aria-expanded"] = "false"
    with button:
        tip = ui.tooltip(_MOVE_TO).props('anchor="top middle" self="bottom middle"')
        # Opened by `fill`, not by the button's own click: it is filled
        # first. Below the row's right end, and scrolling within its own
        # height: a menu of every place in the tree is long.
        menu = (
            ui.menu()
            .props(
                'no-parent-event role="menu" anchor="bottom right" '
                'self="top right" max-height="40vh"'
            )
            .classes("dstf-move-menu")
            .mark(f"move-menu:{path}")
        )
    menu.on("keydown", js_handler=menu_keys(menu))

    def toggled(e: Any) -> None:
        # No tooltip while the menu is open: it covered the first place.
        button.props["aria-expanded"] = "true" if e.value else "false"
        button.update()
        tip.set_visibility(not e.value)

    menu.on_value_change(toggled)

    def choose(dest: Dest) -> None:
        menu.close()
        _submit_move(s, ui_panel, model_id, path, dest)

    def fill() -> None:
        menu.clear()
        tree = s.tree(model_id)
        if tree is None:  # `available` already says why; a job may race
            return
        places = move_destinations(
            tree, path, validate=_validator(s, model_id), names=s.labels(model_id)
        )
        with menu:
            for i, (text, where, dest, reason) in enumerate(places):
                item = (
                    ui.menu_item(on_click=partial(choose, dest), auto_close=False)
                    .props('dense role="menuitem" tabindex="-1"')
                    .mark(f"move-dest:{path}:{i}")
                )
                with item, ui.item_section():
                    with ui.item_label().classes("dstf-move-label"):
                        MiddleCut(text)
                        with ui.element("span").classes("dstf-move-where"):
                            if where.startswith(f"{BEFORE} "):
                                # The row it names, cut in the middle too:
                                # whole, a long one ran past the menu.
                                ui.label(f"— {BEFORE}")
                                MiddleCut(where[len(BEFORE) + 1 :])
                            else:
                                ui.label(f"— {where}")
                    if reason is not None:
                        ui.item_label(reason).props("caption").mark("move-why")
                if reason is not None or where == WHERE_IT_IS:
                    item.disable()
                    item.props["aria-disabled"] = "true"
        menu.open()

    button.on_click(fill)
    button.mark(f"move:{path}")


def _locked_by(s: SessionView, model_id: str) -> str | None:
    """Why nothing may edit ``model_id`` now — the job holding it — or None."""
    running = s.running
    if running is not None and s.is_locked(model_id):
        return f"{running.label} is running on {model_id}"
    return None


def _statistic_plus(
    ui: Any, s: SessionView, ui_panel: Panel, model_id: str, quantity: str
) -> None:
    """A Statistics row's `+` (U3): no form — the requirement is the seed
    (a count exact, a time within its tolerance, inside a `warn` where it
    lands in an `and`), added at the END of the root (Q6 b), or as
    ``and[leaf, new]`` under a root leaf; the new criterion's editor opens
    after it. Grey with the add form's reason where nothing can take it:
    a job holding the test, a simulate-only test, a test with no tree to
    add to (the Session's `tree_or_reason`), a root that takes no child
    alone."""
    tree, why = s.tree_or_reason(model_id)
    locked = _locked_by(s, model_id)
    if locked is not None:
        reason: str | None = locked
    elif _simulate_only(s, model_id):
        # Tree or not: the comparator scores nothing on it.
        reason = SIMULATE_ONLY
    elif tree is None:
        # The Session's one reason: nothing scored, a stale run, or a tree
        # it refuses.
        reason = why
    else:
        reason = _NO_ADD.get(str(tree.get("combinator")))
    tip = reason
    if tip is None:
        assert tree is not None  # `reason` said so
        into = _into(tree, tree, ROOT)
        tip = _requirement(_quantity_leaf(_diagnostics(s, model_id), quantity, into))

    def add() -> None:
        # From the tree as it is NOW, before the add moves the count.
        added = _added_path(s.tree(model_id), ROOT)
        if _submit(
            s, ui_panel, model_id=model_id, action="add", path=ROOT, quantity=quantity
        ):
            ui_panel.reveal(_first_leaf(s.tree(model_id), added))

    # A disabled Quasar button takes no pointer events: the reason's
    # tooltip is on a span around it.
    with ui.element("span"):
        button = ui.button(icon="add", on_click=add)
        button.props("flat dense round size=sm")
        # Its accessible name is the sentence: the icon's ligature says
        # "add". Set as an item, not through the props string.
        button.props["aria-label"] = tip
        button.mark(f"add-statistic:{quantity}")
        ui.tooltip(tip).props('anchor="top middle" self="bottom middle"')
        if reason is not None:
            button.disable()


def _kind_select(
    ui: Any,
    s: SessionView,
    ui_panel: Panel,
    model_id: str,
    path: str,
    node: dict,
    open_edit: Callable[[str], None],
) -> None:
    """A combinator's kind as a dropdown in its row (Q3 A), changed in
    place: and / or / warn stage at once; k-of-n / weighted open the Edit
    form preset to that kind (``open_edit``); `unwrap…`, below a separator,
    unwraps. A refused change says why in the row, and the select goes
    back to the node's kind.

    Unwrap is not offered where `act` refuses it by rule — a root of
    several children has no parent to take them: an option cannot carry a
    why-tooltip."""
    current = str(node.get("combinator"))
    options = {c.name: c.name for c in combinators()}
    children = len(node.get("children") or [])
    if not (path == ROOT and children > 1):
        options[_UNWRAP_VALUE] = _UNWRAP_TEXT
    select = (
        ui.select(options, value=current)
        .props('dense borderless options-dense aria-label="Combinator kind"')
        .classes("dstf-kind")
        .mark(f"kind:{path}")
    )
    select.add_slot("option", _KIND_OPTION)

    def chosen(e: Any) -> None:
        value = e.value
        if value == current:
            return
        if value in _NEEDS_FIELDS:
            select.set_value(current)  # nothing is staged until Apply
            open_edit(str(value))
            return
        if value == _UNWRAP_VALUE:
            done = _submit(s, ui_panel, model_id=model_id, action="unwrap", path=path)
        else:
            done = _submit(
                s, ui_panel, model_id=model_id, action="edit", path=path, kind=value
            )
        if not done:
            select.set_value(current)

    select.on_value_change(chosen)


def _diagnostics(s: SessionView, model_id: str) -> dict[str, Any]:
    """``{name: values}`` of the diagnostics the last run RECORDED — the
    picker's "Diagnostics" (B4). Not `diagnostic_variables` against the
    file's names: a JSON-driver run records none, and a `CPUtime` column in
    its file is a variable a `CPUtime.final` quantity path never reads."""
    project = s.project
    result = project.results.get(model_id) if project is not None else None
    if result is None:
        return {}
    return {d.name: d.values for d in result.diagnostics or [] if d.name}


def _metrics_for(*, diagnostic: bool) -> list[str]:
    """What the Metric select offers (Q2): a diagnostic is one stored
    number, scored by a metric that takes a ``quantity``; any other
    variable, every metric that scores a time series."""
    return [
        d.name
        for d in metrics()
        if (
            d.accepts_quantity
            if diagnostic
            else DatasetType.TIME_SERIES in d.accepted_datasets
        )
    ]


def _plot_series(s: SessionView, model_id: str, variable: str | None) -> list[str]:
    """Every series of the plot ``variable`` is the subject of — the
    picker's "On this plot" — or none, where no plot is (a tree row)."""
    if not variable:
        return []
    for plot in s.plots(model_id):
        if variable in plot.subject:
            return list(plot.series)
    return [variable]
