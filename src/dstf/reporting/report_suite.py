"""Render per-test interactive reports and orchestrate the report suite.

The rendering half of the per-test report: Jinja env, the figure dicts the
page paints, the ``comparison_data.json`` sidecar + size budget, and the
threaded per-suite orchestration. The context it renders is built by
:mod:`dstf.reporting.context_builder`.
"""

import json
import math
import os
import platform
import re
import shutil
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..comparison.comparator import VariableComparison
from ..comparison.types import TestComparison
from ..config_shape import default
from ..discovery.test_registry import diagnostic_names
from ..storage.baseline import ReferenceFile
from .context_builder import _build_template_context
from .rows import summary_fields
from .status import classify_with_class, refusal
from .tree_rows import criteria_rows

_TEMPLATE_DIR = Path(__file__).parent / "templates"

#: Vendored browser dependencies, shipped in the wheel under
#: ``reporting/templates/vendor/``. The report page used to pull Plotly
#: from a CDN, which renders a blank page in an air-gapped lab (and in
#: Playwright's offline mode).
#:
#: ONE copy per report suite, at the reports root — the directory holding
#: the per-test report dirs — reached from each page as
#: ``../plotly.min.js``. A copy per test dir cost 1.25 MB × N: 15 MB for
#: the 12-test demo, ~414 MB for the owner's 331-test TRANSFORM suite.
_VENDOR_DIR = _TEMPLATE_DIR / "vendor"
VENDOR_ASSETS: tuple[str, ...] = ("plotly.min.js",)

# The report page loads ONE ``interactive.js``: the axes script first (it
# defines what report.js calls), then report.js. The served name is
# historical.
INTERACTIVE_JS_PARTS: tuple[str, ...] = ("axes.js", "report.js")


def interactive_js_source() -> str:
    """Return the concatenated ``interactive.js`` served with a report."""
    return "".join(
        (_TEMPLATE_DIR / part).read_text(encoding="utf-8")
        for part in INTERACTIVE_JS_PARTS
    )


def copy_vendor_assets(dest_root: Path) -> list[Path]:
    """Copy the vendored browser libraries into *dest_root*, once.

    *dest_root* is the REPORTS ROOT (the directory holding the per-test
    report dirs), not a page directory: every page reaches the asset as
    ``../plotly.min.js``.

    Idempotent and safe to call from the report thread pool — every
    per-test render calls it for the same destination. A same-size file
    already there is left alone, and the copy itself lands on a unique
    temp name before an atomic ``os.replace``, so a concurrent reader can
    never see a half-written library.
    """
    written = []
    dest_root.mkdir(parents=True, exist_ok=True)
    for name in VENDOR_ASSETS:
        src = _VENDOR_DIR / name
        dest = dest_root / name
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            written.append(dest)
            continue
        tmp = dest.with_name(f"{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        written.append(dest)
    return written


def write_report_assets(page_dir: Path) -> Path:
    """Stage everything a page in *page_dir* loads by relative path.

    The concatenated ``interactive.js`` lands IN *page_dir* (the page
    loads it as ``interactive.js``); the vendored Plotly lands in
    ``page_dir.parent``, the reports root shared by every page of the
    suite (``../plotly.min.js``). Single entry point for both the
    production report copy and the test fixtures that stage a page for a
    browser: a page written without one of these is a blank report, and a
    second copy of the asset list is a second thing to forget.

    Returns the JS path.
    """
    dest = page_dir / "interactive.js"
    # LF on every platform. Unlike the rest of a report, this
    # file is byte-stable across runs, so an archived report tree
    # regenerated from the other OS would otherwise diff wholly for
    # nothing.
    dest.write_text(interactive_js_source(), encoding="utf-8", newline="\n")
    copy_vendor_assets(page_dir.parent)
    return dest


def _json_sanitize(obj):
    """Recursively replace non-finite floats (inf / -inf / nan) with None.

    review 2026-07-06 finding 68: ``comparison_data.json`` is the documented
    downstream artifact and the same context is embedded into
    interactive.html — dumping with ``allow_nan=True`` injected literal
    ``Infinity`` / ``NaN`` tokens (e.g. from missing-baseline sentinel
    comparisons), which is invalid JSON for every strict consumer. Returns
    new containers; the input is not mutated.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]
    return obj


def _sanitize_filename(name: str) -> str:
    """Sanitize a MODEL ID for use in a directory name.

    Collapses whitespace, replaces Modelica punctuation, and removes any
    remaining filesystem-unsafe character. It took variable names once,
    when a name could still be the text a multi-line `x={...}` spelled
    (D132 made a variable's name the result file's); the one caller now
    passes a model id.
    """
    # Collapse all whitespace (newlines, spaces, tabs) into single underscore
    name = re.sub(r"\s+", "_", name)
    # Replace common Modelica punctuation
    name = name.replace("[", "_").replace("]", "").replace(".", "_")
    name = name.replace(",", "_").replace("(", "").replace(")", "")
    # Remove anything that's not alphanumeric, underscore, or hyphen
    name = re.sub(r"[^\w\-]", "", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name).strip("_")
    return name


def plot_inputs(test, comp_variables, result, backend_cls, test_dir, diagnostics):
    """``(plots, extra_series)`` for one test's page: the plots it draws and
    every series of theirs the page's scored and no-baseline entries do not
    answer — context series and watch plots.

    The plots are :func:`~dstf.discovery.plots.plots_of_test`' — the ONE rule
    the authoring tool's `Session.plots` draws by, so both tiers draw the
    same plots: derived from the criteria tree, decorated by the entry's
    ``plots`` extras, none for a failed run.

    A test with no extras — or no backend class or test dir to read a
    result file through — opens no result FILE: every derived subject is a
    scored or tracked variable the render context already carries, so 331
    TRANSFORM reports pay nothing, and what the result carries is what the
    plots can draw. An extra's series is taken from the result in memory
    when it is there, and read from the file only when it is not.

    ``diagnostics`` are the names the test lists (`diagnostic_names`): a
    number one stored summary of one answers is on no plot.
    """
    from ..discovery.plots import plots_of_test

    if test is None:
        return (), {}
    extras = tuple(test.plots or ())
    readable = bool(extras) and backend_cls is not None and test_dir is not None
    plots = plots_of_test(
        test,
        [v.name for v in comp_variables or ()],
        result,
        listed=backend_cls.result_names(test_dir) if readable else None,
        diagnostics=diagnostics,
    )
    derived = {name for plot in plots if not plot.watch for name in plot.subject}
    wanted = [
        name
        for plot in plots
        for name in plot.series
        if name not in derived and name not in plot.missing
    ]
    in_memory = {
        var.name: (var.time, var.values)
        for var in getattr(result, "variables", None) or ()
        if var.name in wanted
    }
    extra_series = {
        name: in_memory[name] for name in dict.fromkeys(wanted) if name in in_memory
    }
    unread = [name for name in dict.fromkeys(wanted) if name not in in_memory]
    if readable and unread:
        extra_series.update(backend_cls.result_series(test_dir, unread))
    return plots, extra_series


def _context_labels(context: dict, key: str = "label") -> dict[str, str]:
    """Name → what a render context reads for every series it can draw.

    ``key`` picks which of the two readings
    (:func:`~dstf.reporting.context_builder.test_texts`): ``"label"`` for
    the surfaces that show the muted name beside it — the section heading,
    the per-variable status badge — and ``"text"`` for every other one.
    Element ids, ``data-variable``, the series lists and ``meta.id`` are
    always the NAME.

    ONE source: the ``variable_texts`` the context stamped, which is the
    whole TEST's scope — every variable of the result, every scored
    comparison and every plot series. Re-deriving it here from the entries
    that happen to carry a label (the scored variables, the no-baseline
    trajectories) gave a narrower scope that left a TRACKED but unscored
    variable out, and it read its bare name on four surfaces of a page
    where the tool read its expression. A context with no stamp — a
    hand-built one — reads names, which is what a variable with no
    expression reads anyway.
    """
    return {
        name: entry.get(key) or name
        for name, entry in (context.get("variable_texts") or {}).items()
    }


def plot_page_keys(
    context,
    plots,
    extra_series,
    metric_tree,
    max_samples,
    theme: str = "default",
    diagnostics: Sequence[str] = (),
) -> dict:
    """``plots`` (the sidecar's record), ``plot_figures``, ``plot_series``
    and ``plot_sections`` from a render context, one of each per plot of
    ``plots`` (:func:`plot_inputs`), in order. The figures are drawn in the
    bundled theme ``theme`` — baked into the page, as its stylesheet is.

    THREE sources answer a series name, in order: the scored variables
    (``variables_by_name``), the no-baseline trajectories — a test with no
    reference scores nothing, so its curves live only there — and
    ``extra_series``, the context and watch series read out of the result.
    A name in none of the three draws nothing and joins ``missing``; so does
    one the plot already calls missing, even when a source answers it — a
    tree leaf the result lacks is scored (and fails), and its entry has no
    curve to draw.

    A SUBJECT draws as the variable it is — its reference, its criteria'
    geometry and its overlays; a CONTEXT series as its actual curve alone,
    grey, even when it is scored on a plot of its own (its criteria are
    there).

    The ONE producer of the page's plot keys: the template loops over plot
    sections, so a caller or a fixture that hand-rolled them would be a
    second layout nobody renders through. ``diagnostics`` (the names the
    test lists) keep a number read from a listed name's summary out of
    every section's criteria (`criteria_rows`): the metric tree lists it.
    """
    from ..figures.assemble import leaves_by_variable, view_from_trajectory
    from ..figures.families import family_label, group_families
    from ..figures.plotly import plot_figure
    from ..figures.view import build_view

    by_name = context.get("variables_by_name") or {}
    nobaseline = {
        t["name"]: t
        for t in context.get("nobaseline_trajectories") or []
        if t.get("name")
    }
    arrays = extra_series or {}
    vidx = {n: i for i, n in enumerate(by_name)}
    # Two readings of the same scope: `labels` where the muted name rides
    # beside (the section heading), `texts` everywhere else.
    labels = _context_labels(context)
    texts = _context_labels(context, "text")
    leaves_by_var = leaves_by_variable(metric_tree)
    budget = max_samples if max_samples and max_samples > 0 else None
    tree_view = context.get("tree_view")

    def curve(n: str) -> tuple:
        """``(time, values)`` of a series' actual curve, from whichever
        source answers it."""
        if n in by_name:
            traj = by_name[n].get("trajectory") or {}
            return traj.get("act_time"), traj.get("act_values")
        if n in nobaseline:
            return nobaseline[n].get("time"), nobaseline[n].get("values")
        return arrays[n][0], arrays[n][1]

    def subject_view(n: str):
        if n in by_name:
            return view_from_trajectory(
                n, by_name[n].get("trajectory") or {}, leaves_by_var.get(n, [])
            )
        if n in nobaseline:
            t = nobaseline[n]
            return build_view(
                n,
                label=texts.get(n, n),
                act_time=t.get("time"),
                act_values=t.get("values"),
                overlays=t.get("overlays") or [],
            )
        time, values = curve(n)
        return build_view(n, label=texts.get(n, n), act_time=time, act_values=values)

    def context_view(n: str):
        time, values = curve(n)
        return build_view(n, label=texts.get(n, n), act_time=time, act_values=values)

    def drawable(n: str) -> bool:
        return n in by_name or n in nobaseline or n in arrays

    records: list[dict] = []
    figures: list[dict | None] = []
    series: list[list[str]] = []
    sections: list[dict] = []
    for position, plot in enumerate(plots):
        gone = set(plot.missing)
        subject = [n for n in plot.subject if n not in gone and drawable(n)]
        context_names = [n for n in plot.context if n not in gone and drawable(n)]
        drawn = subject + context_names
        missing = list(
            dict.fromkeys([*plot.missing, *(n for n in plot.series if n not in drawn)])
        )
        # No drawable SUBJECT, no chart (the template's `plot.subject`),
        # whatever the context: no figure either. The null keeps the
        # section's place — report.js paints `PLOT_FIGURES[idx]` into
        # `plot-${idx}`.
        figures.append(
            plot_figure(
                [subject_view(n) for n in subject],
                budget,
                context=[context_view(n) for n in context_names],
                theme=theme,
            )
            if subject
            else None
        )
        variables = [
            {
                "name": n,
                "vidx": vidx[n],
                **{k: v for k, v in by_name[n].items() if k != "trajectory"},
                "overlays": [
                    _overlay_row(ov) for ov in by_name[n].get("overlays") or []
                ],
            }
            # Every scored member's verdict, drawn or not: a leaf on a
            # variable the result lacks failed, and its badge says so.
            for n in plot.subject
            if n in by_name
        ]
        # Overlays and the error select: a subject of ONE variable (the
        # context does not count), drawn. A no-baseline variable has
        # overlays and no reference to take an error against.
        single = len(plot.subject) == 1
        lone = subject[0] if single and subject else None
        overlays = (
            by_name[lone].get("overlays")
            if lone in by_name
            else nobaseline[lone].get("overlays")
            if lone in nobaseline
            else None
        ) or []
        criteria = criteria_rows(tree_view, plot.subject, diagnostics)
        records.append(
            {
                "kind": plot.kind,
                "series": drawn,
                "subject": list(plot.subject),
                "context": list(plot.context),
                "watch": plot.watch,
                "missing": missing,
            }
        )
        series.append(drawn)
        sections.append(
            {
                # By POSITION, not by ``plot.index``: report.js paints
                # ``PLOT_FIGURES[idx]`` into ``plot-${idx}``, so a section
                # numbered by anything else renders a blank chart.
                "index": position,
                # The title READS the plain labels of the SUBJECT; the
                # names ride muted beside it, and only when one of them
                # differs from its label — otherwise every title would say
                # its variable twice. The muted name is what tells two
                # series sharing one expression apart here, so the title
                # does not repeat it. The fold is over the texts, as the
                # tool's card title folds (`plot_card.folded`).
                "title": ", ".join(
                    family_label(key, members, labels)
                    for key, members in group_families(list(plot.subject), texts)
                ),
                "names": ""
                if all(labels.get(n, n) == n for n in plot.subject)
                else ", ".join(
                    family_label(key, members)
                    for key, members in group_families(list(plot.subject))
                ),
                "series": drawn,
                "subject": subject,
                "missing": missing,
                "single": single,
                # The error select: ONE scored variable, drawn.
                "errors": lone in by_name,
                "watch": plot.watch,
                "variables": variables,
                "overlays": [_overlay_row(ov) for ov in overlays],
                "criteria": criteria,
                # Every crumb as wide as the longest, in ch: the leaf names
                # start in one column.
                "crumb_ch": max((len(row["crumbs"]) for row in criteria), default=0),
                # Read as the texts: no muted name rides beside them.
                "context": [texts.get(n, n) for n in plot.context],
            }
        )
    return {
        "plots": records,
        "plot_figures": figures,
        "plot_series": series,
        "plot_sections": sections,
    }


def _build_figures(context: dict, max_samples: int | None, theme: str) -> dict:
    """The page's diagnostic figures, in the theme the page is generated
    in; the plots' are :func:`plot_page_keys`'.
    Built from the FULL-resolution context — the view decimates under
    ``max_samples`` — and after the sidecar write, so
    ``comparison_data.json`` keeps its trajectory shape and no figure."""
    from ..figures.plotly import variable_figure
    from ..figures.view import build_view

    budget = max_samples if max_samples and max_samples > 0 else None
    diag = [
        variable_figure(
            build_view(
                t["name"],
                act_time=t.get("act_time"),
                act_values=t.get("act_values"),
                ref_time=t.get("ref_time"),
                ref_values=t.get("ref_values"),
            ),
            budget,
            theme=theme,
        )
        for t in context.get("diag_trajectories") or []
    ]
    return {"diag_figures": diag}


def generate_comparison_plots(
    model_id: str,
    ref_data: ReferenceFile | None,
    result,
    comparisons: list[VariableComparison],
    plot_dir: Path,
    test_dir: Path | None = None,
    test_model=None,
    ref_file: Path | None = None,
    warnings: list | None = None,
    last_run_at: float | None = None,
    metric_tree=None,
    artifact_files: tuple[tuple[str, str], ...] = (),
    # Config.max_embedded_samples: both production callers pass it. None —
    # a direct caller that says none — is testing.json's default.
    max_embedded_samples: int | None = None,
    # Config.theme, said by both production callers as the budget is: the
    # bundled theme the page is generated in (D148).
    theme: str = "default",
    overlays: list | None = None,
    comparison: TestComparison | None = None,
    ref_id: str | None = None,
    run_metadata: dict | None = None,
    plots=None,
    extra_series=None,
    # The names the test lists (`diagnostic_names`): both production callers
    # pass them. A number read from one's summary is in the metric tree,
    # never in a plot section's criteria.
    diagnostics: Sequence[str] = (),
) -> Path | None:
    """Render this test's interactive.html (Plotly) + comparison_data.json sidecar.

    *comparison* is the test's ``TestComparison``: the sidecar's verdict
    comes from it. Without one (a direct caller holding only the leaves)
    the sidecar carries the numbers and no verdict.

    *plots* / *extra_series* are :func:`plot_inputs`' — the suite and the
    review path resolve them with the backend's result-file readers; a
    direct caller that passes none gets them from the same function over
    *test_model*, with no file to read.

    Returns the path to the rendered interactive.html. With neither
    *plots* nor *test_model* there is nothing to derive the plots from, and
    a page with none would say nothing about it: ValueError.
    """
    if plots is None:
        if test_model is None:
            raise ValueError(
                "generate_comparison_plots needs plots= or the test_model "
                "they derive from"
            )
        plots, extra_series = plot_inputs(
            test_model, comparisons, result, None, None, diagnostics
        )
    if plot_dir.exists():
        shutil.rmtree(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    cur_stats = result.statistics if result else None
    context = _build_template_context(
        model_id,
        comparisons,
        ref_data,
        cur_stats,
        test_dir,
        test_model,
        result,
        ref_file=ref_file,
        warnings=warnings,
        last_run_at=last_run_at,
        metric_tree=metric_tree,
        artifact_files=artifact_files,
        overlays=overlays,
        run_metadata=run_metadata,
        # The page's label scope takes the plots too: a context or watch
        # series named like another variable's expression is what decides
        # whether that other one reads its name beside its label. Missing
        # names included, as the tool's `Session.variable_texts` includes
        # them: the plot still names them ("Not in this result: z").
        plot_series=[n for p in plots for n in p.series],
    )

    if max_embedded_samples is None:
        max_embedded_samples = default("max_embedded_samples")

    # The page's plot keys, from the full-resolution context. Only the
    # LAYOUT — names, no arrays — joins the sidecar; the figures are popped
    # out and put back after the dump, where _build_figures is called.
    page_keys = plot_page_keys(
        context,
        plots,
        extra_series,
        metric_tree,
        max_embedded_samples,
        theme=theme,
        diagnostics=diagnostics,
    )
    context["plots"] = page_keys.pop("plots")

    # Write comparison_data.json alongside the HTML. This is the
    # full-resolution data artifact for downstream tooling (notebooks,
    # future JS lazy-fetch on zoom). ORDER CONSTRAINT: nothing that THINS
    # the data may be IN the context at this point. The figures are built
    # above (the layout record has to be in the sidecar, and it comes out
    # of the same call) but are held OUT of ``context`` until after this
    # write; only ``plots`` — names, no arrays — joins it. The page
    # context, which drops the trajectory arrays, is built after it too.
    data_path = plot_dir / "comparison_data.json"
    # Summary block for the unified dashboard. The full context dict is
    # the per-variable rendering input; the dashboard only needs row-level
    # summary fields, exposed under "summary" so build_dashboard_context
    # can read them without parsing the heavy variable arrays.
    # `written_at` + `model_id` form a defensive double-entry-bookkeeping
    # check for the dashboard's enricher: stale sidecars from a prior run
    # (written_at < snapshot start_wall) and bookkeeping drift (sidecar's
    # model_id ≠ row's model_id) are both filtered out so they can't
    # override a fresh verdict.
    import time as _time

    # The one computation of a row's numbers (reporting.rows), shared with
    # the dashboard's rows and, in the tool, the Suite table.
    fields = summary_fields(
        comparison
        if comparison is not None
        else TestComparison(
            model_id=model_id, passed=True, variables=list(comparisons or ())
        ),
        warnings=warnings,
        stats=cur_stats if isinstance(cur_stats, dict) else None,
        diagnostics=(result.diagnostics or []) if result is not None else (),
        ref_id=ref_id,
        ref_file=ref_file,
        field_sources=getattr(test_model, "field_sources", None) or {},
    )
    if comparison is None:
        fields.update(status=None, status_text=None, status_class=None)
    context["summary"] = {
        "model_id": model_id,
        "written_at": _time.time(),
        **fields,
    }
    # review 2026-07-06 finding 68: strict JSON — sanitize non-finite
    # floats to null and forbid NaN/Infinity tokens at dump time.
    data_path.write_text(
        json.dumps(_json_sanitize(context), indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )

    # Figures come from the full-resolution context and decimate themselves;
    # they are added AFTER the sidecar write so the sidecar's shape (the
    # handoff artifact dashboard_render reads) does not change.
    context.update(page_keys)
    context.update(_build_figures(context, max_embedded_samples, theme))
    # Why the comparison refused the test, for the summary line: the page's
    # own flags read a test with no scored variable as one with no baseline.
    context["refused"] = refusal(comparison) if comparison is not None else None

    # ORDER CONSTRAINT: the page context is built LAST — after the sidecar
    # write (which needs the full-resolution arrays) and after the figure
    # build (which reads them, and whose ``full_samples`` stamps are what
    # ``decimated`` is computed from). Stripping any earlier would empty
    # the artifact or the figures instead of just the page.
    page_context = _page_context(context)
    page_context["decimated"] = _decimated_traces(context)

    # review 2026-07-06 finding 68: the embedded page context must be
    # strict JSON too — Jinja's |tojson would otherwise emit Infinity/NaN
    # literals into window.MT_REPORT.
    page_context = _json_sanitize(page_context)

    # Render interactive HTML (Plotly, inline figure dicts).
    # Stage 5: the static ``comparison.html`` path was retired. The index
    # page linked only to interactive.html; the per-variable PNGs still
    # render next to it for direct reference.
    interactive_path = plot_dir / "interactive.html"
    _render_template("interactive.html", page_context, interactive_path, theme)

    # Write the standalone interactive.js beside the HTML and the vendored
    # Plotly one level up (shared by the suite) so the `<script src=...>`
    # tags resolve. The JS is pure-static (no Jinja interpolations) — the
    # HTML marshals the per-report data into window.MT_REPORT; the JS reads
    # from there. Plotly ships with the report rather than loading from a
    # CDN: an air-gapped lab otherwise gets no plots and no error.
    write_report_assets(plot_dir)

    return interactive_path


def _overlay_row(ov: dict) -> dict:
    """An overlay reduced to what the picker markup needs: its identity."""
    return {"name": ov.get("name"), "role": ov.get("role"), "kind": ov.get("kind")}


def _page_context(context: dict) -> dict:
    """The page's copy of the render context, with no trajectory arrays.

    The page draws figure dicts, so the arrays would only be a second
    full-resolution copy of data the figures already carry. Built as a
    COPY, after the sidecar write — ``comparison_data.json`` is the
    documented downstream artifact and keeps its trajectory shape.

    ``variables_by_name`` is dropped whole: the page is laid out per PLOT,
    and each section already carries its variables' verdicts, criteria and
    overlay rows (:func:`plot_page_keys`). A no-baseline trajectory keeps
    only its name: the summary line counts them, and its plot's section
    carries the rest.
    """
    page = dict(context)
    page.pop("trajectories", None)
    page.pop("variables_by_name", None)
    page["diag_trajectories"] = [
        {"name": t.get("name")} for t in context.get("diag_trajectories") or []
    ]
    page["nobaseline_trajectories"] = [
        {"name": t.get("name")} for t in context.get("nobaseline_trajectories") or []
    ]
    return page


def _trace_label(tr: dict, texts: Mapping[str, str] | None) -> str | None:
    """What the banner calls one trace.

    On a plot of one subject the trace's own name is right, and the banner
    has always printed it; ``texts`` is None there. On a FAMILY's plot it
    is not: a family's lead member carries the family's LEGEND label, so
    the banner read ``pipe.T[1:4]`` where one member belonged, and a family
    of one printed its name twice (once as the plot's title, once as the
    trace). There the series comes off ``meta.id`` — the NAME — and
    ``texts`` turns it back into what the rest of the page calls it, so one
    banner line does not pair a plot full of expressions with a trace
    spelled as a result-file name. ``texts`` is the TEST's scope, like the
    plot's own name in the row beside it. A derived trace — a band, an
    error series, a spectrum — and a context trace keep their own names.
    """
    name = tr.get("name")
    if texts is None:
        return name
    trace_id = (tr.get("meta") or {}).get("id")
    if not isinstance(trace_id, str):
        return name
    for prefix, suffix in (("actual/", ""), ("reference/", " (reference)")):
        if trace_id.startswith(prefix):
            series = trace_id[len(prefix) :]
            return f"{texts.get(series, series)}{suffix}"
    return name


def _decimated_traces(context: dict) -> list[dict]:
    """Every figure trace the sample budget shortened, named.

    The figure layer stamps each trace's pre-decimation count in
    ``meta.full_samples`` and the count it actually carries in
    ``meta.samples``; a trace whose stamp exceeds what it carries was
    decimated. Both counts are samples, not points on the wire — a band
    trace is a closed polygon holding twice its sample count — so the
    embedded side reads ``meta.samples`` and falls back to the x length
    only for a trace the figure layer does not decimate. The template
    renders this list as the banner, so the reader sees WHICH curves were
    thinned rather than a page-wide total. A plot's trace is placed by its
    PLOT's SUBJECT, folded the way the section heading folds it but read as
    the TEXTS: the banner shows no muted name, so two plots of two
    variables sharing one expression would otherwise be two identical rows.
    A context trace is a trace of that plot, under its own name.
    """
    out: list[dict] = []
    texts = _context_labels(context, "text")

    def scan(varname, fig: dict, *, texts: Mapping[str, str] | None = None) -> None:
        for tr in fig.get("data") or []:
            meta = tr.get("meta") or {}
            full = meta.get("full_samples")
            embedded = int(meta.get("samples", len(tr.get("x") or [])))
            if full is None or full <= embedded:
                continue
            out.append(
                {
                    "variable": varname,
                    "trace": _trace_label(tr, texts),
                    "full": int(full),
                    "embedded": embedded,
                }
            )

    from ..figures.families import family_label, group_families

    for section, fig in zip(
        context.get("plot_sections") or [], context.get("plot_figures") or []
    ):
        if fig is None:
            continue  # a section with no chart
        subject = section.get("subject") or []
        scan(
            ", ".join(
                family_label(key, members, texts)
                for key, members in group_families(subject, texts)
            ),
            fig,
            texts=texts if len(subject) > 1 else None,
        )
    for traj, fig in zip(
        context.get("diag_trajectories") or [], context.get("diag_figures") or []
    ):
        scan(traj.get("name"), fig)
    return out


def jinja_env(theme: str = "default"):
    """The per-test report's Jinja environment: the templates dir, autoescape,
    and the token block every page starts its stylesheet with, in the
    bundled theme ``theme``. One constructor, so a test that renders a page
    gets the same globals."""
    from jinja2 import Environment, FileSystemLoader

    from .theme import css_variables

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    env.globals["dstf_css"] = css_variables(theme)
    return env


def _render_template(
    template_name: str, context: dict, output_path: Path, theme: str
) -> None:
    """Render a Jinja2 template with the given context."""
    template = jinja_env(theme).get_template(template_name)
    html = template.render(**context)
    output_path.write_text(html, encoding="utf-8")


def _build_per_test_args(
    comp,
    results,
    test_lookup,
    store,
    config,
    manifest_meta_by_model,
    artifact_files=(),
    backend_cls=None,
):
    """Resolve all per-test inputs needed to render that test's report."""
    from .overlay_loader import load_overlays

    model_id = comp.model_id
    result = results.get(model_id)
    test = test_lookup.get(model_id)
    ref_data = store.get_reference(model_id)
    meta = manifest_meta_by_model.get(model_id, {})
    test_key = meta.get("test_key")
    last_run_at = meta.get("last_run_at")
    # A2 — load every registered overlay for this model (soft_checks +
    # companions). Failures are absorbed into Overlay.status so the
    # report never breaks on a moved/renamed companion file.
    # Passing ``config`` also auto-discovers peer-backend refs as
    # sibling-backend companions (visual-only pre-accept cross-check).
    overlays = load_overlays(store, model_id, config=config)

    # One classifier for every surface (reporting.status). The private copy
    # that used to live here existed only because simulate_only was readable
    # from the TestModel and not from the comparison; TestComparison carries
    # it now, so the carve-out is gone (D117 closed).
    status_text, status_class = classify_with_class(comp)

    test_dir = config.work_dir / test_key if test_key else None

    # The page's plots, through the one helper (it derives them from the
    # tree itself, so this path and cli/review.py cannot drift).
    diagnostics = (
        diagnostic_names(test, config.diagnostic_variables) if test is not None else []
    )
    plots, extra_series = plot_inputs(
        test, comp.variables, result, backend_cls, test_dir, diagnostics
    )

    ref_file = None
    test_id = store.index.get_id(model_id)
    if test_id:
        from ..storage.ref_index import RefIndex

        ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

    if comp.test_id:
        report_id = f"ref_{comp.test_id}"
    elif test_key:
        report_id = test_key
    else:
        report_id = _sanitize_filename(model_id)

    # The test's baseline, for its row's Reference: the one it was compared
    # against, else the one the store holds for it. A comparison that
    # compared nothing (a failed simulation's, a test's with no result)
    # names none, and its sidecar said null beside that baseline's file.
    ref_number = comp.test_id or test_id

    return {
        "model_id": model_id,
        "ref_data": ref_data,
        "result": result,
        "test": test,
        "test_dir": test_dir,
        "ref_file": ref_file,
        "warnings": comp.warnings,
        "report_id": report_id,
        "test_key": test_key,
        "status_text": status_text,
        "status_class": status_class,
        "ref_id": f"ref_{ref_number}" if ref_number else None,
        "last_run_at": last_run_at,
        "comparison": comp,
        "comp_variables": comp.variables,
        "metric_tree": comp.metric_tree,
        "artifact_files": artifact_files,
        "max_embedded_samples": config.max_embedded_samples,
        "theme": config.theme,
        "overlays": overlays,
        "plots": plots,
        "extra_series": extra_series,
        "diagnostics": diagnostics,
    }


def _render_one_test(args: dict, report_dir: Path) -> dict:
    """Render a single test's report. Returns the index entry."""
    import time as _time

    t0 = _time.monotonic()
    plot_dir = report_dir / args["report_id"]
    html_path = generate_comparison_plots(
        model_id=args["model_id"],
        ref_data=args["ref_data"],
        result=args["result"],
        comparisons=args["comp_variables"],
        plot_dir=plot_dir,
        test_dir=args["test_dir"],
        test_model=args["test"],
        ref_file=args["ref_file"],
        warnings=args["warnings"],
        last_run_at=args["last_run_at"],
        metric_tree=args.get("metric_tree"),
        artifact_files=args.get("artifact_files", ()),
        max_embedded_samples=args["max_embedded_samples"],
        theme=args["theme"],
        overlays=args.get("overlays"),
        comparison=args.get("comparison"),
        ref_id=args.get("ref_id"),
        run_metadata=args.get("run_metadata"),
        plots=args.get("plots"),
        extra_series=args.get("extra_series"),
        diagnostics=args.get("diagnostics", ()),
    )
    render_elapsed = _time.monotonic() - t0
    return {
        "model_id": args["model_id"],
        "status_text": args["status_text"],
        "status_class": args["status_class"],
        "ref_id": args["ref_id"],
        "test_key": args["test_key"],
        "last_run_at": args["last_run_at"],
        "report_path": f"{args['report_id']}/interactive.html" if html_path else None,
        "_render_elapsed": render_elapsed,
    }


def _read_run_metadata(work_dir: Path) -> dict | None:
    """Read + shape the run provenance from ``work_dir/status.json`` for the
    per-test banner. Returns None (banner omitted) when status.json is absent,
    unreadable, or predates the ``metadata`` field. Never raises."""
    from ..backends.progress import read_status
    from .dashboard_render import format_run_metadata

    snapshot = read_status(work_dir)
    if snapshot is None:
        return None
    return format_run_metadata(snapshot.get("metadata"))


def generate_report_suite(
    comparisons: list,
    results: dict,
    tests: list,
    store,
    config,
) -> Path:
    """Generate per-test comparison reports.

    Per-test report rendering runs on a thread pool sized by config.parallel.
    The hot work is Plotly trace JSON serialization + comparison_data.json
    dump + decimation; numpy/json release the GIL well enough that threads
    give a meaningful speedup without the pickling cost of a process pool.

    Returns the path to the unified dashboard HTML file (rendered separately
    by ``dashboard_render.render_final``).
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    report_wall_start = _time.monotonic()
    report_dir = config.work_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    test_lookup = {t.model_id: t for t in tests}

    # Resolve the backend CLASS once up front — its artifact file list and
    # the result-file readers a test's plot extras need are both class
    # attributes, so no backend is instantiated (which would trigger the
    # fmpy import if the extra isn't installed). An unregistered backend
    # leaves both at their empty defaults rather than raising per test.
    from ..backends import get_backend_class

    try:
        backend_cls = get_backend_class(config)
        artifact_files = tuple(backend_cls.artifact_files)
    except ValueError:
        backend_cls = None
        artifact_files = ()

    # Cache test_key + last_run_at lookup once instead of scanning manifests per test
    manifest_meta_by_model: dict[str, dict] = {}
    manifest_paths = sorted(config.work_dir.glob("batch_manifest.json"))
    if manifest_paths:
        from ..backends import BatchManifest

        for mp in manifest_paths:
            bm = BatchManifest.load(mp)
            for tk, entry in bm.manifest.items():
                manifest_meta_by_model.setdefault(
                    entry["model_id"],
                    {
                        "test_key": tk,
                        "last_run_at": entry.get("last_run_at"),
                    },
                )

    # Run provenance for the per-test banner — read once from status.json and
    # shaped by the same formatter the dashboard uses, so both surfaces agree.
    # None when status.json predates the metadata field (banner is omitted).
    run_metadata = _read_run_metadata(config.work_dir)

    # Resolve per-test args sequentially (cheap dict/store lookups), then
    # render reports in parallel
    work = [
        _build_per_test_args(
            comp,
            results,
            test_lookup,
            store,
            config,
            manifest_meta_by_model,
            artifact_files,
            backend_cls,
        )
        for comp in comparisons
    ]
    for args in work:
        args["run_metadata"] = run_metadata

    n_workers = max(1, min(getattr(config, "parallel", 1) or 1, len(work)))
    index_tests: list[dict] = []
    total_reports = len(work)

    from ..backends.base import _print_progress

    print(f"Generating {total_reports} reports (parallel={n_workers})...")

    if n_workers <= 1 or len(work) <= 1:
        for i, args in enumerate(work, 1):
            index_tests.append(_render_one_test(args, report_dir))
            short = args["model_id"].rsplit(".", 1)[-1]
            _print_progress(i, total_reports, short, "ok")
    else:
        # Preserve original test order in the index
        results_by_model: dict[str, dict] = {}
        completed = 0
        with ThreadPoolExecutor(
            max_workers=n_workers, thread_name_prefix="report"
        ) as pool:
            futures = {
                pool.submit(_render_one_test, args, report_dir): args["model_id"]
                for args in work
            }
            for future in as_completed(futures):
                entry = future.result()
                results_by_model[entry["model_id"]] = entry
                completed += 1
                short = entry["model_id"].rsplit(".", 1)[-1]
                _print_progress(completed, total_reports, short, "ok")
        index_tests = [results_by_model[args["model_id"]] for args in work]

    # Per-test interactive.html + comparison_data.json sidecars are
    # written above. The unified work_dir/dashboard.html is rendered
    # by cli._generate_report_suite via dashboard_render.render_final;
    # the standalone index.html that used to live here is retired.

    # Phase timing — exposes whether parallelism is helping
    wall = _time.monotonic() - report_wall_start
    elapsed = [t.get("_render_elapsed", 0.0) for t in index_tests]
    total_work = sum(elapsed)
    if elapsed:
        slowest = max(elapsed)
        avg = total_work / len(elapsed)
        speedup = (total_work / wall) if wall > 0 else 0.0
        print(
            f"Report phase: {wall:.0f}s wall, {total_work:.0f}s total work, "
            f"{speedup:.1f}x parallel speedup (avg {avg:.1f}s/test, slowest {slowest:.1f}s)"
        )
    return config.work_dir / "dashboard.html"


def open_in_browser(path: Path, *, launch: bool = True) -> None:
    """Open a file in the system's default browser/viewer.

    Always prints a ``file://`` URL so the user has a clickable fallback
    (most terminals make it clickable); ``launch=False`` — ``run`` and
    ``compare`` under ``--no-browser`` — stops there.
    """
    print(f"View: {path.resolve().as_uri()}")
    if launch:
        _launch_opener(path)


def _launch_opener(path: Path) -> None:
    """Hand ``path`` to the system's opener. Subprocess stdout/stderr are
    silenced — on headless Linux/WSL ``xdg-open`` prints a long list of
    "Permission denied" candidates when no browser is registered, and
    that noise has no actionable signal for the user.

    The one place a process is started for a browser: the test suite
    replaces it (``tests/conftest.py``), so no test opens one.
    """
    system = platform.system()
    silent = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        if system == "Windows":
            subprocess.Popen(["start", "", str(path)], shell=True, **silent)
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)], **silent)
        else:
            subprocess.Popen(["xdg-open", str(path)], **silent)
    except OSError:
        pass
