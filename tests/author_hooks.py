"""Server-side routes a Playwright test adds to a `dstf author` process it
starts (executed before `main_entry`, never imported by the tool): stage an
edit, and touch the Suite grid element from Python, as a careless caller
would. `async`, so they run on the UI loop, where the Session is mutated.
Not a test module (no `test_` prefix)."""

import gc

from nicegui import app, ui


def _shell():
    from dstf.authoring.shell.shell import Shell

    (shell,) = [o for o in gc.get_objects() if isinstance(o, Shell)]
    return shell


@app.get("/__stage")
async def _stage(model: str = "FakeLib.A") -> dict:
    """LOOSEN on `model`: its implicit tree materialized and its tolerance
    loosened — two pending ops, and a FAIL that rescores PASS."""
    from dstf.authoring.spec_document import PatchOp

    session = _shell().session
    session.stage([PatchOp(model, "add", "/metrics/children/0/tolerance", 0.5)])
    return {"pending": len(session.spec.pending)}


@app.get("/__tree")
async def _tree(model: str = "FakeLib.A") -> dict:
    """The tree ``model`` is scored by — staged edits included — and how
    many ops are pending: what an edit left STAGED, read from the Session
    rather than off the page."""
    session = _shell().session
    return {"tree": session.tree(model), "pending": len(session.spec.pending)}


@ui.page("/__typed_number")
def _typed_number() -> None:
    """The tool's number field with a child in each kind of slot it passes
    through to Quasar's: a tooltip in the default one, an icon appended."""
    from dstf.authoring.typed_number import TypedNumber

    field = TypedNumber(label="slotted", value=1.5)
    with field:
        ui.tooltip("drawn in the field's own slot")
    with field.add_slot("append"):
        ui.icon("help").classes("slotted-icon")


#: One entry per stage `/__slow`'s Session has finished.
_STAGED: list[int] = []


@app.get("/__slow")
async def _slow(ms: int = 250) -> dict:
    """Every `Session.stage` from now on holds the UI loop ``ms`` longer, as
    the rescore of a large test does: what the browser types meanwhile meets
    the server's answer to an EARLIER keystroke."""
    import time

    from dstf.authoring.session import Session

    stage = Session.stage

    def slow(self, ops):
        time.sleep(ms / 1000)
        try:
            return stage(self, ops)
        finally:
            _STAGED.append(ms)

    Session.stage = slow
    return {"slowed": ms}


@app.get("/__staged")
async def _staged() -> dict:
    """How many stages have finished since `/__slow`. Asked during one, it
    answers after it: the loop is held."""
    return {"staged": len(_STAGED)}


@app.get("/__expression")
async def _expression(name: str, expression: str, model: str = "FakeLib.A") -> dict:
    """Stamp ``expression`` on ``name`` of ``model``'s loaded result and
    rescore (`test_session.with_expressions`: FakeLib's Python backend
    carries no expression; only a Modelica UnitTests component writes
    one). The page shows it on its next load."""
    session = _shell().session
    hit = 0
    for var in session.project.results[model].variables:
        if var.name == name:
            var.expression = expression
            hit += 1
    session._score(model)
    return {"stamped": hit}


@app.get("/__say")
async def _say(path: str, block: float = 0.0) -> dict:
    """`TreeCard.say` on every open Test page's tree, then the UI loop held
    ``block`` s, as a long sync holds it: the line's element, its text and
    the live region's change all leave in one message."""
    import time

    from dstf.authoring.shell.test_page import TestPage

    pages = [
        o
        for o in gc.get_objects()
        if isinstance(o, TestPage)
        and o.tree is not None
        and o.tree.targets
        and not o.client.is_deleted
    ]
    for page in pages:
        page.tree.say(path, "Moved.")
    time.sleep(block)
    return {"said": len(pages)}


@app.get("/__touch")
async def _touch() -> list[str]:
    """`.classes(add=…)` on every live Suite grid, Python-side."""
    from dstf.authoring.shell.suite_grid import SuiteGrid

    out = []
    for grid in [o for o in gc.get_objects() if isinstance(o, SuiteGrid)]:
        if grid.is_deleted:
            continue
        grid.classes(add="touched")
        out.append(str(grid.id))
    return out


#: The gates of the jobs `/__lock` started, opened by `/__unlock`.
_GATES: list = []


@app.get("/__lock")
async def _lock(model: str = "FakeLib.A") -> dict:
    """A job holding ``model`` until `/__unlock`: its rows rebuilt under
    the lock, as a Rerun's are. It reloads nothing when it ends."""
    import threading

    from dstf.authoring.capabilities import Job

    gate = threading.Event()
    _GATES.append(gate)
    job = Job(
        run=lambda _ctx: gate.wait(30),
        label="rerun",
        models=frozenset({model}),
        reloads_results=False,
    )
    _shell().session.apply(job)
    return {"locked": model}


@app.get("/__unlock")
async def _unlock() -> dict:
    for gate in _GATES:
        gate.set()
    return {"released": len(_GATES)}
