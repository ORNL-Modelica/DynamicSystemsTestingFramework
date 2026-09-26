"""Edit tree (§4.6 table, §4.7): the NODE and VARIABLE placements, exactly
one test selected. At NODE the panel IS that node's controls — add, wrap,
and on a combinator edit (Unwrap inside it), and remove — each form a ROW
under the node's row (`Panel.row`); at VARIABLE it is the "Add a criterion
for x" form.
Every control stages one structural change against the paths the report's
tree already shows. The D66 validator's refusal reaches the page through
``Panel.submit`` — the Session is what it was.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...comparison.registry import DEFAULT_METRIC, combinators, metrics
from ...comparison.seeds import SeedData, seeded_leaf
from ...discovery.plots import tracked_names
from ..capabilities import Panel, Patch, Placement

if TYPE_CHECKING:
    from ..session import SessionView
from ..spec_document import PatchOp
from ..tree_pointer import node_or_none
from ..tree_text import node_text, nodes_of

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

ROOT = "/metrics"

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
        return {"k": int(round(float(inputs["k"] or 0)))}
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
_EDIT_SENTENCE = "Changes this node's kind and fields; its children stay."
_ADD_SENTENCE = (
    "Added as the last child, with values from the data where the metric needs them."
)
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


def seed_data(s: SessionView, model_id: str, variable: str) -> SeedData:
    """The series a new criterion on ``variable`` will score, for its seed.

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
    return SeedData(ref=ref, act=act)


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
        else:
            # The variable's footer: the form itself, no row to open — the
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

    def act(self, s: SessionView, **inputs: Any) -> Patch | None:
        action, model_id, path = inputs["action"], inputs["model_id"], inputs["path"]
        if action == "remove":
            return Patch(ops=[PatchOp(model_id, "remove", path)])
        tree = s.tree(model_id)
        if tree is None:
            raise ValueError(f"{model_id} has no tree to edit")
        node = node_or_none(tree, path)
        if node is None:
            raise ValueError(f"{path} names no node of {model_id}")
        if action == "add":
            # Seeded: a bare range, tube or scalar does not resolve, and a
            # bare dominant-frequency always fails "no peaks declared".
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
                # Beside any other leaf: under its parent combinator.
                under = path.rsplit("/children/", 1)[0]
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
        if action == "edit":
            # Only what moved: an Apply with nothing changed staged the
            # kind and every field again, four ops for no edit. Nothing
            # moved is None — the row has closed, and nothing is staged.
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
        raise ValueError(f"unknown action {action!r}")


def _combinator_fields(
    ui: Any, node: dict | None, sentence: Callable[[str], str], children: int
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
    before, it staged no weights and threshold 0, refused every time."""
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
    kind = (
        ui.select(
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
                ui.select(
                    _DIRECTIONS,
                    label="Direction",
                    value=values["direction"],
                    on_change=keep("direction"),
                ).props("dense").classes("w-full")
        why.set_text(sentence(str(kind.value)))
        gate()

    def guard(button: Any) -> None:
        with button:
            tip = ui.tooltip(_NO_THRESHOLD)
        guarded.append((button, tip))
        gate()

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
    close: Callable[[], None] | None = None,
    added: str | None = None,
) -> None:
    """Variable and metric selects and the apply button (marked ``mark``).
    In a form row (``close`` given) it is the row's: the sentence, Cancel
    and Add, which stages and, once that went through, closes the row and
    reveals ``added`` — the new criterion's path; a refused Add keeps the
    row. The variable's footer (no ``close``) is the form alone, its button
    still ``apply add``: its dialog is its frame.

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
            label="Variable",
            value=variable if variable else (names[0] if names else ""),
        )
        .props("dense")
        .classes("w-full")
        .mark(f"{mark}:variable")
    )
    met = (
        ui.select([d.name for d in metrics()], label="Metric", value=DEFAULT_METRIC)
        .props("dense")
        .classes("w-full")
    )

    def apply() -> None:
        if not ui_panel.submit(
            model_id=model_id,
            action="add",
            path=under,
            variable=var.value,
            metric=met.value,
        ):
            return  # refused: the row stays, with what was chosen
        if close is not None:
            close()
        if added is not None:
            ui_panel.reveal(added)

    if close is None:
        ui.button("apply add", on_click=apply).props("dense no-caps").mark(mark)
        return
    ui.label(_ADD_SENTENCE).classes("dstf-why")
    with ui.row().classes("items-center gap-2"):
        _cancel(ui, close, under)
        ui.button("Add", on_click=apply).props("dense no-caps").mark(mark)


def _cancel(ui: Any, close: Callable[[], None], path: str) -> None:
    """A form row's Cancel: the row goes, nothing is staged."""
    ui.button("Cancel", on_click=close).props("flat dense no-caps").mark(
        f"cancel:{path}"
    )


def _parent_of(path: str) -> str | None:
    return path.rsplit("/children/", 1)[0] if "/children/" in path else None


def _added_path(tree: dict | None, path: str) -> str:
    """Where an Add from ``path``'s row lands, read from the tree as it is
    when the row is built — after the add the count has moved by one:
    under a combinator its next child; beside the root leaf the root's
    second (the root becomes an ``and`` of both); beside any other leaf
    its parent's next child."""
    node = node_or_none(tree, path) or {}
    if "combinator" in node:
        return f"{path}/children/{len(node.get('children') or [])}"
    if path == ROOT:
        return f"{ROOT}/children/1"
    parent = _parent_of(path) or ROOT
    siblings = (node_or_none(tree, parent) or {}).get("children") or []
    return f"{parent}/children/{len(siblings)}"


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
    # `+` adds under a combinator and beside a leaf: the combinator that
    # would take the child decides whether one can be added alone.
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
        return ui_panel.submit(model_id=model_id, action=action, path=path)

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
            if ui_panel.submit(model_id=model_id, action="wrap", path=path, **values()):
                close()

        with ui.row().classes("items-center gap-2"):
            _cancel(ui, close, path)
            guard(
                ui.button("Wrap", on_click=apply)
                .props("dense no-caps")
                .mark(f"apply-wrap:{path}")
            )

    def build_edit(close: Callable[[], None]) -> None:
        at_open = fresh()
        if at_open is None:
            return
        values, guard = _combinator_fields(
            ui,
            at_open,
            lambda _kind: _EDIT_SENTENCE,
            children=len(at_open.get("children") or []),
        )

        def apply() -> None:
            if ui_panel.submit(model_id=model_id, action="edit", path=path, **values()):
                close()

        def unwrap() -> None:
            if act_now("unwrap"):
                close()

        with ui.row().classes("items-center gap-2"):
            unwrap_button = (
                ui.button("Unwrap", on_click=unwrap)
                .props("flat dense no-caps")
                .mark(f"unwrap-in-edit:{path}")
            )
            if path == ROOT and len(at_open.get("children") or []) > 1:
                # Several children take the node's place in its parent; the
                # root has none to take them.
                unwrap_button.disable()
                with unwrap_button:
                    ui.tooltip(_ROOT_UNWRAP)
            _cancel(ui, close, path)
            guard(
                ui.button("Apply", on_click=apply)
                .props("dense no-caps")
                .mark(f"apply-edit:{path}")
            )

    with ui.row().classes("dstf-node-controls items-center no-wrap gap-0"):
        where = "under" if is_combinator else "beside"
        icon(
            "add",
            no_add
            or (
                "Add a criterion under this node"
                if is_combinator
                else "Add a criterion beside this one"
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
        if is_combinator:
            icon(
                "tune",
                "Change this combinator",
                f"edit:{path}",
                lambda: ui_panel.row("Edit this combinator", build_edit),
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
