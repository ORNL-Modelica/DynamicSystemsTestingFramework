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
from collections.abc import Mapping
from pathlib import Path

from ..comparison.comparator import VariableComparison
from .context_builder import _build_template_context, worst_finite_score
from .status import classify_with_class

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


def plot_inputs(test, comp_variables, result, backend_cls, test_dir):
    """``(plots, extra_series)`` for one test's page: the plots the page
    draws and every series of theirs the scored variables do not answer.

    The tracked list — what the page HAS a section for by default — comes
    from :func:`discovery.plots.tracked_names`, the ONE rule the authoring
    tool's `Session.tracked` draws by: the scored variables in order, then
    the rest of the loaded result's. The report drew one plot per variable
    the TREE scored, so a tracked variable no criterion covers had no plot
    on this tier and a plot on the other (the owner's ruling: both tiers
    draw the same plots).

    A test with no ``plots`` key — or no backend class or test dir to read
    a result file through — still opens no result FILE: the default layout
    is one plot per tracked name and ``result`` already holds every one of
    them in memory, so 331 TRANSFORM reports pay nothing.

    Nothing scored and no ``plots`` key is the one case with NO default
    layout: the page's "Simulated Variables (No Baseline)" block already
    draws the whole result, so a section per tracked name drew every
    variable of that page twice (the final review). A test that ASKED for a
    layout still gets it, drawn from the same no-baseline trajectories.
    """
    from ..discovery.plots import resolved_plots, tracked_names

    if test is None:
        return None, {}
    if test.plots is None and not comp_variables:
        return None, {}
    tracked = tracked_names([v.name for v in (comp_variables or [])], result)
    if test.plots is None or backend_cls is None or test_dir is None:
        plots = resolved_plots(test, tracked, available=None)
        return plots, _tracked_series(result, comp_variables, test, tracked)
    available = backend_cls.result_names(test_dir)
    plots = resolved_plots(test, tracked, available=available)
    wanted = sorted({n for p in plots for n in p.series} - set(tracked))
    return plots, {
        **_tracked_series(result, comp_variables, test, tracked),
        **backend_cls.result_series(test_dir, wanted),
    }


def _tracked_series(result, comp_variables, test, tracked):
    """The trajectories of the tracked variables the page's per-variable
    sections do NOT carry — the ones no leaf of the tree scores. They have
    a plot (they are tracked) and no criteria, so `plot_page_keys` finds
    them in neither ``variables_by_name`` nor the no-baseline list; without
    them the section says the series is missing while the variable sits in
    the test's spec. Read from ``result``, never from disk."""
    from ..comparison.tree_spec import collect_variables
    from .context_builder import _spec_for

    # Nothing scored: the page's no-baseline trajectories already carry
    # every variable of the result, under the same names.
    if result is None or not getattr(result, "variables", None) or not comp_variables:
        return {}
    covered = set(collect_variables(_spec_for(list(comp_variables), test)))
    wanted = {name for name in tracked if name not in covered}
    if not wanted:
        return {}
    series = {}
    for var in result.variables:
        if var.name in wanted:
            series[var.name] = (var.time, var.values)
    return series


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


def plot_page_keys(context, plots, extra_series, metric_tree, max_samples) -> dict:
    """``plots`` (the sidecar's record), ``plot_figures``, ``plot_series``
    and ``plot_sections`` from a render context. ``plots=None`` is one plot
    per key of ``variables_by_name``, in its order.

    THREE sources answer a series name, in order: the scored variables
    (``variables_by_name``), the no-baseline trajectories the same context
    carries — a test with no reference scores nothing, so its tracked
    curves live only there, and reading the first two sources alone called
    every series of its layout missing while the same curve painted below
    — and ``extra_series``, the plot-only names a backend read out of the
    result file. A name in none of the three draws nothing and joins
    ``missing``.

    The ONE producer of the page's plot keys: the template loops over plot
    sections, so a caller or a fixture that hand-rolled them would be a
    second layout nobody renders through.
    """
    from ..discovery.plots import ResolvedPlot
    from ..figures.assemble import leaves_by_variable, view_from_trajectory
    from ..figures.families import family_label, group_families
    from ..figures.plotly import plot_figure
    from ..figures.view import build_view

    by_name = context.get("variables_by_name") or {}
    nobaseline = {
        t["name"]: (t.get("time") or [], t.get("values") or [])
        for t in context.get("nobaseline_trajectories") or []
        if t.get("name")
    }
    # Disjoint by construction: no-baseline trajectories exist only when
    # nothing was scored, and then `plot_inputs` resolves a layout only for
    # a test that DECLARED one — taking the result's own list as tracked and
    # reading only what is not in it.
    arrays = {**(extra_series or {}), **nobaseline}
    if plots is None:
        plots = tuple(
            ResolvedPlot(index=i, kind="time", series=(n,), missing=())
            for i, n in enumerate(by_name)
        )
    vidx = {n: i for i, n in enumerate(by_name)}
    # Two readings of the same scope: `labels` where the muted name rides
    # beside (the section heading), `texts` everywhere else.
    labels = _context_labels(context)
    texts = _context_labels(context, "text")
    leaves_by_var = leaves_by_variable(metric_tree)
    budget = max_samples if max_samples and max_samples > 0 else None

    records: list[dict] = []
    figures: list[dict] = []
    series: list[list[str]] = []
    sections: list[dict] = []
    # A variable may sit on several plots; its criteria tree is rendered
    # under the FIRST one, or the page carries the same element id twice.
    tree_rendered: set[str] = set()
    for position, plot in enumerate(plots):
        drawn = [n for n in plot.series if n in by_name or n in arrays]
        missing = [*plot.missing, *(n for n in plot.series if n not in drawn)]
        views = [
            view_from_trajectory(
                n, by_name[n].get("trajectory") or {}, leaves_by_var.get(n, [])
            )
            if n in by_name
            else build_view(
                n,
                label=texts.get(n, n),
                act_time=arrays[n][0],
                act_values=arrays[n][1],
            )
            for n in drawn
        ]
        variables: list[dict] = []
        for n in drawn:
            if n not in by_name:
                continue
            variables.append(
                {
                    "name": n,
                    "vidx": vidx[n],
                    **{k: v for k, v in by_name[n].items() if k != "trajectory"},
                    "overlays": [
                        _overlay_row(ov) for ov in by_name[n].get("overlays") or []
                    ],
                    # After the spread: this is the SECTION's fact, not the
                    # variable's, and the context must not be able to set it.
                    "tree_here": n not in tree_rendered,
                }
            )
            tree_rendered.add(n)
        untracked = [n for n in drawn if n not in by_name]
        records.append({"kind": plot.kind, "series": drawn, "missing": missing})
        figures.append(plot_figure(views, budget))
        series.append(drawn)
        sections.append(
            {
                # By POSITION, not by ``plot.index``: report.js paints
                # ``PLOT_FIGURES[idx]`` into ``plot-${idx}``, so a section
                # numbered by anything else renders a blank chart.
                "index": position,
                # The title READS the plain labels; the names ride muted
                # beside it, and only when one of them differs from its
                # label — otherwise every title on every existing report
                # would say its variable twice. The muted name is what
                # tells two series sharing one expression apart here, so
                # the title does not repeat it.
                #
                # The FOLD is over the texts: two series whose labels
                # collide belong to no family (their texts carry a name in
                # parentheses), so `pipe.T[1]` tracked beside an `x[1]`
                # whose expression is `pipe.T[1]` cannot read `pipe.T[1,1]`.
                # Within any family of several, text and label agree — a
                # colliding label folds into nothing — so the rendered
                # slice is the same either way.
                "title": ", ".join(
                    family_label(key, members, labels)
                    for key, members in group_families(drawn, texts)
                ),
                "names": ""
                if all(labels.get(n, n) == n for n in drawn)
                else ", ".join(
                    family_label(key, members) for key, members in group_families(drawn)
                ),
                "series": drawn,
                "missing": missing,
                "single": len(drawn) == 1,
                "variables": variables,
                # The heading's tail: every untracked name on the plot,
                # array families folded to one label. No muted name rides
                # beside these, so they read the texts.
                "plot_only_label": ", ".join(
                    family_label(key, members, texts)
                    for key, members in group_families(untracked, texts)
                ),
                # One "…is plot only" line per FAMILY — four members used
                # to print four sentences. A no-baseline series is left
                # out: it has no criteria because it has no baseline, and
                # the no-baseline block below already says so.
                "plot_only_lines": [
                    family_label(key, members, texts)
                    for key, members in group_families(
                        [n for n in untracked if n not in nobaseline], texts
                    )
                ],
            }
        )
    return {
        "plots": records,
        "plot_figures": figures,
        "plot_series": series,
        "plot_sections": sections,
    }


def _build_figures(context: dict, metric_tree, max_samples: int | None) -> dict:
    """The page's diagnostic and no-baseline figures. The plots a test
    declares — one per tracked variable by default — are
    :func:`plot_page_keys`'. Built from the FULL-resolution context — the
    view decimates under ``max_samples`` — and after the sidecar write, so
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
        )
        for t in context.get("diag_trajectories") or []
    ]
    nb = [
        variable_figure(
            build_view(
                t["name"],
                # The heading above this figure reads the plain label with
                # the muted name beside it; the hover has no name beside
                # it, so it reads the text.
                label=t.get("text") or t.get("label") or t["name"],
                act_time=t.get("time"),
                act_values=t.get("values"),
                overlays=t.get("overlays") or [],
            ),
            budget,
        )
        for t in context.get("nobaseline_trajectories") or []
    ]
    return {"diag_figures": diag, "nb_figures": nb}


def generate_comparison_plots(
    model_id: str,
    ref_data: dict | None,
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
    # Must track Config.max_embedded_samples — both production callers pass
    # it explicitly, so this default only fires for direct callers (tests).
    max_embedded_samples: int = 1000,
    overlays: list | None = None,
    status_text: str | None = None,
    status_class: str | None = None,
    ref_id: str | None = None,
    run_metadata: dict | None = None,
    plots=None,
    extra_series=None,
) -> Path | None:
    """Render this test's interactive.html (Plotly) + comparison_data.json sidecar.

    Returns the path to the rendered interactive.html.
    """
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
        # The page's label scope takes the plots too: a plot-only series
        # named like another variable's expression is what decides whether
        # that other one reads its name beside its label.
        plot_series=[n for p in plots or () for n in p.series],
    )

    # The page's plot keys, from the full-resolution context. Only the
    # LAYOUT — names, no arrays — joins the sidecar; the figures are popped
    # out and put back after the dump, where _build_figures is called.
    page_keys = plot_page_keys(
        context, plots, extra_series, metric_tree, max_embedded_samples
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

    worst_score, n_unscored = worst_finite_score(comparisons)
    context["summary"] = {
        "model_id": model_id,
        "written_at": _time.time(),
        "status_text": status_text,
        "status_class": status_class,
        "ref_id": ref_id,
        # file:// URL so the dashboard's Reference column can hyperlink
        # directly to the stored reference JSON.
        "ref_file": ref_file.as_uri() if ref_file else None,
        # Worst score across the leaves that HAVE one: a sentinel leaf
        # carries inf/nan, and a plain max() let it swallow every real
        # number on the test (the dashboard then showed "—" for a test
        # whose other leaves scored fine). ``n_unscored`` is what the
        # column says instead when nothing finite is left.
        "worst_nrmse": worst_score,
        "n_unscored": n_unscored,
        "n_vars": len(comparisons) if comparisons else 0,
        "n_vars_passed": (
            sum(1 for v in comparisons if v.passed) if comparisons else 0
        ),
        "n_warnings": len(warnings) if warnings else 0,
        "translation_wall": (
            cur_stats.get("timing", {}).get("translation_wall")
            if isinstance(cur_stats, dict)
            else None
        ),
        "sim_wall": (
            cur_stats.get("timing", {}).get("sim_wall")
            if isinstance(cur_stats, dict)
            else None
        ),
        "total_wall": (
            cur_stats.get("timing", {}).get("total_wall")
            if isinstance(cur_stats, dict)
            else None
        ),
        "field_sources": (
            test_model.field_sources
            if test_model and hasattr(test_model, "field_sources")
            else {}
        ),
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
    context.update(_build_figures(context, metric_tree, max_embedded_samples))

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
    _render_template("interactive.html", page_context, interactive_path)

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

    ``variables_by_name`` is dropped whole: the page is laid out per PLOT
    now, and each section already carries its variables' trees, verdicts
    and overlay rows (:func:`plot_page_keys`). What survives per
    no-baseline trajectory is its name, its LABEL — the heading above its
    figure reads it, and cutting it left every NEW heading spelling the
    name with an empty label beside it — and each overlay's name / role
    for the picker checkbox.
    """
    page = dict(context)
    page.pop("trajectories", None)
    page.pop("variables_by_name", None)
    page["diag_trajectories"] = [
        {"name": t.get("name")} for t in context.get("diag_trajectories") or []
    ]
    page["nobaseline_trajectories"] = [
        {
            "name": t.get("name"),
            "label": t.get("label") or t.get("name"),
            "overlays": [_overlay_row(ov) for ov in t.get("overlays") or []],
        }
        for t in context.get("nobaseline_trajectories") or []
    ]
    return page


def _trace_label(tr: dict, texts: Mapping[str, str] | None) -> str | None:
    """What the banner calls one trace.

    On a one-series plot the trace's own name is right, and the banner has
    always printed it; ``texts`` is None there. On a plot of SEVERAL series
    it is not: a family's lead member carries the family's LEGEND label, so
    the banner read ``pipe.T[1:4]`` where one member belonged, and a family
    of one printed its name twice (once as the plot's title, once as the
    trace). There the series comes off ``meta.id`` — the NAME — and
    ``texts`` turns it back into what the rest of the page calls it, so one
    banner line does not pair a plot full of expressions with a trace
    spelled as a result-file name. ``texts`` is the TEST's scope, like the
    plot's own name in the row beside it. A derived trace — a band, an
    error series, a spectrum — has no such id and keeps its own name.
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
    thinned rather than a page-wide total. A comparison trace is placed by
    its PLOT, folded the way the section heading folds it but read as the
    TEXTS: the banner shows no muted name, so two plots of two variables
    sharing one expression would otherwise be two identical rows.
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
        series = section.get("series") or []
        scan(
            ", ".join(
                family_label(key, members, texts)
                for key, members in group_families(series, texts)
            ),
            fig,
            texts=texts if len(series) > 1 else None,
        )
    for traj, fig in zip(
        context.get("diag_trajectories") or [], context.get("diag_figures") or []
    ):
        scan(traj.get("name"), fig)
    for traj, fig in zip(
        context.get("nobaseline_trajectories") or [], context.get("nb_figures") or []
    ):
        scan(traj.get("name"), fig)
    return out


def jinja_env():
    """The per-test report's Jinja environment: the templates dir, autoescape,
    and the token block every page starts its stylesheet with. One
    constructor, so a test that renders a page gets the same globals."""
    from jinja2 import Environment, FileSystemLoader

    from .theme import css_variables

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    env.globals["dstf_css"] = css_variables()
    return env


def _render_template(template_name: str, context: dict, output_path: Path) -> None:
    """Render a Jinja2 template with the given context."""
    template = jinja_env().get_template(template_name)
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

    n_vars = len(comp.variables) if comp.variables else 0
    n_vars_passed = sum(1 for v in comp.variables if v.passed) if comp.variables else 0
    worst_nrmse, n_unscored = worst_finite_score(comp.variables)

    test_dir = config.work_dir / test_key if test_key else None

    # The page's layout, through the one helper (it derives the tracked
    # list itself, so this path and cli/review.py cannot drift).
    plots, extra_series = plot_inputs(
        test, comp.variables, result, backend_cls, test_dir
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

    # Pull phase-timing breakdown out of stats if present (the backend stashes it)
    cur_stats = result.statistics if result and result.statistics else {}
    timing = (
        cur_stats.get("timing") if isinstance(cur_stats.get("timing"), dict) else {}
    )

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
        "n_vars": n_vars,
        "n_vars_passed": n_vars_passed,
        "worst_nrmse": worst_nrmse,
        "n_unscored": n_unscored,
        "n_warnings": len(comp.warnings) if comp.warnings else 0,
        "ref_id": f"ref_{comp.test_id}" if comp.test_id else None,
        "last_run_at": last_run_at,
        "translation_wall": timing.get("translation_wall"),
        "sim_wall": timing.get("sim_wall"),
        "total_wall": timing.get("total_wall"),
        "comp_variables": comp.variables,
        "metric_tree": comp.metric_tree,
        "artifact_files": artifact_files,
        "max_embedded_samples": config.max_embedded_samples,
        "overlays": overlays,
        "plots": plots,
        "extra_series": extra_series,
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
        overlays=args.get("overlays"),
        status_text=args.get("status_text"),
        status_class=args.get("status_class"),
        ref_id=args.get("ref_id"),
        run_metadata=args.get("run_metadata"),
        plots=args.get("plots"),
        extra_series=args.get("extra_series"),
    )
    render_elapsed = _time.monotonic() - t0
    return {
        "model_id": args["model_id"],
        "status_text": args["status_text"],
        "status_class": args["status_class"],
        "ref_id": args["ref_id"],
        "test_key": args["test_key"],
        "worst_nrmse": args["worst_nrmse"],
        "n_vars": args["n_vars"],
        "n_vars_passed": args["n_vars_passed"],
        "n_unscored": args["n_unscored"],
        "n_warnings": args["n_warnings"],
        "last_run_at": args["last_run_at"],
        "translation_wall": args.get("translation_wall"),
        "sim_wall": args.get("sim_wall"),
        "total_wall": args.get("total_wall"),
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
    # the result-file readers a declared plot layout needs are both class
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


def open_in_browser(path: Path) -> None:
    """Open a file in the system's default browser/viewer.

    Always prints a ``file://`` URL so the user has a clickable fallback
    (most terminals make it clickable). Subprocess stdout/stderr are
    silenced — on headless Linux/WSL ``xdg-open`` prints a long list of
    "Permission denied" candidates when no browser is registered, and
    that noise has no actionable signal for the user.
    """
    print(f"View: {path.resolve().as_uri()}")
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
