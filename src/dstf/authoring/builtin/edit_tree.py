"""Edit tree (§4.6 table, §4.7): TESTS scope, exactly one test selected.
The raw tree (``SessionView.tree``) as indented rows; wrap, edit, add and
remove each stage one structural change against the paths the report's
tree already shows. The D66 validator's refusal reaches the tab through
``Shell.act`` — the Session is what it was.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...comparison.registry import DEFAULT_METRIC, combinators, metrics
from ..capabilities import Panel, Patch, Scope

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
    node = tree
    for token in [t for t in path.split("/") if t][1:]:
        node = node["children"][int(token)] if token.isdigit() else node[token]
    return node


def _kind_fields(kind: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """The raw fields ``kind`` takes, from the panel's controls."""
    if kind == "k-of-n":
        return {"k": int(inputs["k"] or 0)}
    if kind == "weighted":
        text = str(inputs["weights"] or "")
        return {
            "weights": [float(w) for w in text.replace(",", " ").split()],
            "threshold": float(inputs["threshold"] or 0.0),
        }
    return {}


class EditTree:
    """The metric tree's shape: wrap a node in a combinator, change a
    combinator's kind and its fields, add a leaf, remove a node."""

    name = "edit-tree"
    label = "Edit tree"
    scope = Scope.TESTS

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
        return True

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        model_id = next(iter(s.selection.tests))
        rows = ui.column().classes("w-full")
        form = ui.column().classes("w-full")

        def submit(**inputs: Any) -> None:
            form.clear()
            ui_panel.submit(model_id=model_id, **inputs)
            # The shell refreshes the centre on a staged op, not this tab
            # (rebuilding it would destroy a control being typed in), so the
            # rows redraw themselves — unless the tab was rebuilt already,
            # which is what an act error does.
            if not rows.is_deleted:
                draw()

        def combinator_controls(node: dict | None) -> Callable[[], dict[str, Any]]:
            names = [c.name for c in combinators()]
            current = (node or {}).get("combinator")
            kind = ui.select(
                names,
                label="combinator",
                value=current if current in names else names[0],
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

        def open_combinator_form(action: str, path: str, node: dict | None) -> None:
            form.clear()
            with form:
                ui.label(f"{action} {path}").classes("text-xs")
                values = combinator_controls(node)
                ui.button(
                    f"apply {action}",
                    on_click=lambda: submit(action=action, path=path, **values()),
                ).props("dense no-caps")

        def open_add_form(path: str) -> None:
            form.clear()
            with form:
                ui.label(f"add leaf under {path}").classes("text-xs")
                names = _variable_names(s, model_id)
                variable = ui.select(
                    names or [""],
                    label="leaf variable",
                    value=names[0] if names else "",
                ).props("dense")
                metric = ui.select(
                    [d.name for d in metrics()],
                    label="leaf metric",
                    value=DEFAULT_METRIC,
                ).props("dense")
                ui.button(
                    "apply add",
                    on_click=lambda: submit(
                        action="add",
                        path=path,
                        variable=variable.value,
                        metric=metric.value,
                    ),
                ).props("dense no-caps")

        def node_rows(node: dict, path: str, depth: int) -> None:
            indent = f"ml-{min(depth * 4, 16)}"
            is_combinator = "combinator" in node
            with ui.row().classes(f"items-center no-wrap {indent}"):
                ui.label(_row_text(node, path)).classes("text-xs")
                ui.button(
                    f"wrap {path}",
                    on_click=lambda p=path: open_combinator_form("wrap", p, None),
                ).props("flat dense no-caps").mark(f"wrap:{path}")
                if is_combinator:
                    ui.button(
                        f"edit {path}",
                        on_click=lambda p=path, n=node: open_combinator_form(
                            "edit", p, n
                        ),
                    ).props("flat dense no-caps").mark(f"edit:{path}")
                    ui.button(
                        f"add leaf under {path}",
                        on_click=lambda p=path: open_add_form(p),
                    ).props("flat dense no-caps").mark(f"add:{path}")
                if path != ROOT:  # the root has nothing to be removed from
                    ui.button(
                        f"remove {path}",
                        on_click=lambda p=path: submit(action="remove", path=p),
                    ).props("flat dense no-caps").mark(f"remove:{path}")
            for i, child in enumerate(node.get("children") or []):
                node_rows(child, f"{path}/children/{i}", depth + 1)

        def draw() -> None:
            rows.clear()
            tree = s.tree(model_id)
            with rows:
                if tree is None:  # `available` already says so; a job may race
                    ui.label("no tree to edit (nothing scored)")
                    return
                node_rows(tree, ROOT, 0)

        draw()

    def act(self, s: SessionView, **inputs: Any) -> Patch:
        action, model_id, path = inputs["action"], inputs["model_id"], inputs["path"]
        if action == "remove":
            return Patch(ops=[PatchOp(model_id, "remove", path)])
        if action == "add":
            leaf = {"metric": inputs["metric"], "variable": inputs["variable"]}
            return Patch(ops=[PatchOp(model_id, "add", f"{path}/children/-", leaf)])
        tree = s.tree(model_id)
        if tree is None:
            raise ValueError(f"{model_id} has no tree to edit")
        node = _node_at(tree, path)
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


def _row_text(node: dict, path: str) -> str:
    if "combinator" in node:
        return f"{path}  {node['combinator']} [{len(node.get('children') or [])}]"
    return f"{path}  {node.get('variable', '')} · {node.get('metric', '')}"


def _variable_names(s: SessionView, model_id: str) -> list[str]:
    """What a new leaf may score: the scored names, else the result's."""
    comp = s.comparisons.get(model_id)
    names = [vc.name for vc in comp.variables if vc.name] if comp is not None else []
    if names:
        return names
    project = s.project
    result = project.results.get(model_id) if project is not None else None
    return [v.name for v in result.variables if v.name] if result is not None else []
