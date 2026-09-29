"""Server-side routes a Playwright test adds to a `dstf author` process it
starts (executed before `main_entry`, never imported by the tool): stage an
edit, and touch the Suite grid element from Python, as a careless caller
would. `async`, so they run on the UI loop, where the Session is mutated.
Not a test module (no `test_` prefix)."""

import gc

from nicegui import app


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
