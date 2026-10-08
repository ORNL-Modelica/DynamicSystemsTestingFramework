"""Edit family (D152): the FAMILY_CRITERION placement — the Indices field
of a folded criteria row's editor. The row is one criterion on several
elements of an array; the field says which, in the syntax the add form's
Indices takes (`families.parse_slice`), and applying it removes the leaf of
every element left out and adds one, with the fold's criterion, for every
element new to it. One gesture stages ONE whole-tree op
(`family_rows.as_one`). The fields beside it are Edit criteria's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..capabilities import Panel, Patch, Placement, plural
from ..family_rows import NOT_A_FOLD, Family, Reindexed, as_one, fold_key, reindex
from ..session import no_tree_error
from ..tree_move import path_map
from ..tree_pointer import node_or_none
from .edit_tree import _family

if TYPE_CHECKING:
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

#: An Indices text that names nothing. The fold is the criterion: taking
#: every element away is removing it, and that is the rows' own `−`.
EMPTY = (
    "Indices is empty. To remove the criterion, unfold this row and remove "
    "its criteria instead."
)


def _variables(tree: dict | None, paths: tuple[str, ...]) -> list[str] | None:
    """The members' variables, in ``paths``' order — None when a path no
    longer names a criterion (an edit staged elsewhere moved the tree)."""
    nodes = [node_or_none(tree, path) for path in paths]
    if not nodes or any(node is None or "metric" not in node for node in nodes):
        return None
    return [str(node["variable"]) for node in nodes if node is not None]


def planned(
    s: SessionView,
    model_id: str,
    tree: dict,
    paths: tuple[str, ...],
    variables: tuple[str, ...],
    text: str,
    family: Family | None = None,
) -> Reindexed:
    """The Indices edit `EditFamily.act` stages, with where the members
    are afterwards — what a panel counts as the text is typed and reveals
    once its ``submit`` went through. ``variables`` are the members as the
    panel read them: a path whose leaf is no longer that member's — an
    edit staged elsewhere moved the tree under the row — refuses the whole
    edit, or it would remove another criterion's leaf.

    ``family`` is the members' (`_family`), read here when not given: every
    element the last run's result file has and every name the test scores
    (`Session.variable_names` lists both), so a member that run did not
    record is an element all the same (D142: the strip asks for a rerun).

    ``ValueError`` with the sentence to show: nothing named (`EMPTY`), a
    text that is no slice, an index the family does not have, an element
    with a criterion of its own (`reindex`), members gone stale."""
    if not text.replace(",", "").strip():
        raise ValueError(EMPTY)
    if not variables or _variables(tree, paths) != list(variables):
        raise ValueError(NOT_A_FOLD)
    if family is None:
        family = _family(s, model_id, variables[0])
    if family is None:
        raise ValueError(NOT_A_FOLD)
    return reindex(model_id, tree, paths, family.chosen(text))


def count_text(plan: Reindexed) -> str:
    """What an Indices text would do, said beside it as it is typed:
    ``→ 5 elements (removes 15)``. `1:20:5` is start:step:end — ONE element
    — and Enter on it took nineteen criteria away with nothing said."""
    did = [
        f"{verb} {len(names)}"
        for verb, names in (("removes", plan.removed), ("adds", plan.added))
        if names
    ]
    after = plural(len(plan.paths), "element")
    return f"→ {after} ({', '.join(did) or 'no change'})"


class EditFamily:
    """Which elements of its array a folded criterion is on."""

    name = "edit-family"
    label = "Edit family"
    placements = frozenset({Placement.FAMILY_CRITERION})

    def caption(self, s: SessionView) -> str:
        # FAMILY_CRITERION is inline: no button reads a caption there.
        return self.label

    def available(self, s: SessionView) -> bool | str:
        sel = s.selection
        model_id = next(iter(sel.tests)) if len(sel.tests) == 1 else None
        if model_id is None or len(sel.leaf_paths) < 2:
            return "select one folded row"
        # Edit criteria's sentence for a tree the Session will not edit:
        # the host says one sentence once.
        _tree, why = s.tree_or_reason(model_id)
        return True if why is None else why

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        sel = s.selection
        model_id = next(iter(sel.tests))
        paths = sel.leaf_paths
        named = _variables(s.tree(model_id), paths)
        family = None if named is None else _family(s, model_id, named[0])
        if named is None or family is None:
            # The panel is rebuilt after edits made elsewhere.
            ui.label(NOT_A_FOLD).classes("dstf-why")
            return
        variables = tuple(named)
        applied = family.indices_of(variables)
        #: The text a large removal was asked to confirm, once.
        asked: dict[str, str | None] = {"text": None}

        def plan_of(text: str) -> Reindexed:
            tree = s.tree(model_id)
            if tree is None:
                raise ValueError(NOT_A_FOLD)
            return planned(s, model_id, tree, paths, variables, text, family)

        def count() -> None:
            """Beside the field, as it is typed: what Enter would do."""
            try:
                said = count_text(plan_of(str(field.value or "")))
            except ValueError as e:
                said = str(e)
            counted.set_text(said)
            counted.classes(remove="text-negative")

        def apply() -> None:
            text = str(field.value or "")
            tree = s.tree(model_id)
            try:
                # Planned BEFORE the submit: afterwards the paths name the
                # new tree.
                plan = plan_of(text)
            except ValueError:  # the submit raises it again, and says it
                plan = None
            if (
                plan is not None
                and 2 * len(plan.removed) > len(paths)
                and asked["text"] != text
            ):
                # More than half of the criteria would go: asked once,
                # inline, for this very text.
                asked["text"] = text
                counted.set_text(
                    f"Removes {len(plan.removed)} of {len(paths)} criteria — "
                    "Apply again to confirm"
                )
                counted.classes(add="text-negative")
                return
            if not ui_panel.submit(
                model_id=model_id, paths=paths, variables=variables, indices=text
            ):
                return
            if tree is None or plan is None or not plan.ops:
                # Nothing moved: the field reads what the fold holds.
                field.set_value(applied)
                return
            ui_panel.edited(path_map(tree, plan.ops))
            # The rows are redrawn (leaves came or went): the fold's editor
            # opens again on what it holds now — or, one element left, that
            # criterion's own.
            first = plan.paths[0]
            ui_panel.reveal(fold_key(first) if len(plan.paths) > 1 else first)

        with ui.row().classes("dstf-fields items-start gap-3 wrap w-full"):
            field = (
                ui.input(label="Indices", value=applied, on_change=count)
                .props("dense")
                .classes("dstf-field")
                # Wide enough for its hint on one line: at a field's 140 px
                # the sentence broke over three (the LOOK, in Chromium).
                .style("min-width: 44ch")
                .mark("edit-family:indices")
            )
            field.props["hint"] = (
                f"Elements of {family.label}. Enter applies, Esc puts back."
            )
            field.on("keydown.enter", apply)
            # Esc: the text applied last, as the fold holds it.
            field.on("keydown.escape", lambda: field.set_value(applied))
            ui.button("Apply", on_click=apply).props("flat dense no-caps").mark(
                "edit-family:apply"
            )
            counted = (
                ui.label().classes("dstf-why self-center").mark("edit-family:count")
            )
        count()

    def act(self, s: SessionView, **inputs: Any) -> Patch | None:
        model_id, paths = inputs["model_id"], tuple(inputs["paths"])
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            raise no_tree_error(model_id, why)
        plan = planned(
            s,
            model_id,
            tree,
            paths,
            tuple(inputs.get("variables") or ()),
            str(inputs["indices"]),
        )
        if not plan.ops:
            # The elements it already holds: nothing happened, nothing staged.
            return None
        return Patch(ops=[as_one(model_id, tree, plan.ops, plan.notes)])
