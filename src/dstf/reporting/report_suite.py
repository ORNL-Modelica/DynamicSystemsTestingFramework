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

# The report page loads ONE ``interactive.js``; the served name is
# historical, the source is ``report.js``. It paints the embedded figure
# dicts and wires the overlay picker and the error-overlay dropdown —
# nothing else. The tuple survives so a second source part can be added
# without touching the copy path.
INTERACTIVE_JS_PARTS: tuple[str, ...] = ("report.js",)


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
    """Sanitize a variable name for use in a filename.

    Collapses whitespace (including newlines), replaces Modelica
    punctuation, and removes any remaining filesystem-unsafe characters.
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


def _build_figures(context: dict, metric_tree, max_samples: int | None) -> dict:
    """Figure dicts for the page: one per tracked variable, plus diagnostic
    and no-baseline plots. Built from the FULL-resolution context — the view
    decimates under ``max_samples`` — and after the sidecar write, so
    ``comparison_data.json`` keeps its trajectory shape and no figure."""
    from ..comparison.tree_eval import leaf_comparisons_by_path
    from ..figures.plotly import variable_figure
    from ..figures.view import build_view

    budget = max_samples if max_samples and max_samples > 0 else None
    by_path = leaf_comparisons_by_path(metric_tree) if metric_tree is not None else {}
    leaves_by_var: dict[str, list] = {}
    for path, vc in by_path.items():
        leaves_by_var.setdefault(vc.name or f"x[{vc.index}]", []).append((path, vc))

    figures = {}
    for name, var in (context.get("variables_by_name") or {}).items():
        traj = var.get("trajectory") or {}
        view = build_view(
            name,
            act_time=traj.get("act_time"),
            act_values=traj.get("act_values"),
            ref_time=traj.get("ref_time"),
            ref_values=traj.get("ref_values"),
            overlays=var.get("overlays") or [],
            leaves=leaves_by_var.get(name, []),
        )
        figures[name] = variable_figure(view, budget)
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
                act_time=t.get("time"),
                act_values=t.get("values"),
                overlays=t.get("overlays") or [],
            ),
            budget,
        )
        for t in context.get("nobaseline_trajectories") or []
    ]
    return {"figures": figures, "diag_figures": diag, "nb_figures": nb}


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
    )

    # Write comparison_data.json alongside the HTML. This is the
    # full-resolution data artifact for downstream tooling (notebooks,
    # future JS lazy-fetch on zoom). Everything below that thins the data
    # — the self-decimating figure build and the page context that drops
    # the arrays — runs AFTER this write, so the sidecar keeps every
    # sample.
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

    What survives per variable is what the markup dereferences: the
    server-rendered tree, the verdict, and each overlay's name / role for
    the picker checkbox.
    """
    page = dict(context)
    page.pop("trajectories", None)
    page["variables_by_name"] = {
        name: {
            **{k: v for k, v in var.items() if k != "trajectory"},
            "overlays": [_overlay_row(ov) for ov in var.get("overlays") or []],
        }
        for name, var in (context.get("variables_by_name") or {}).items()
    }
    page["diag_trajectories"] = [
        {"name": t.get("name")} for t in context.get("diag_trajectories") or []
    ]
    page["nobaseline_trajectories"] = [
        {
            "name": t.get("name"),
            "overlays": [_overlay_row(ov) for ov in t.get("overlays") or []],
        }
        for t in context.get("nobaseline_trajectories") or []
    ]
    return page


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
    thinned rather than a page-wide total.
    """
    out: list[dict] = []

    def scan(varname, fig: dict) -> None:
        for tr in fig.get("data") or []:
            meta = tr.get("meta") or {}
            full = meta.get("full_samples")
            embedded = int(meta.get("samples", len(tr.get("x") or [])))
            if full is None or full <= embedded:
                continue
            out.append(
                {
                    "variable": varname,
                    "trace": tr.get("name"),
                    "full": int(full),
                    "embedded": embedded,
                }
            )

    for name, fig in (context.get("figures") or {}).items():
        scan(name, fig)
    for traj, fig in zip(
        context.get("diag_trajectories") or [], context.get("diag_figures") or []
    ):
        scan(traj.get("name"), fig)
    for traj, fig in zip(
        context.get("nobaseline_trajectories") or [], context.get("nb_figures") or []
    ):
        scan(traj.get("name"), fig)
    return out


def _render_template(template_name: str, context: dict, output_path: Path) -> None:
    """Render a Jinja2 template with the given context."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template(template_name)
    html = template.render(**context)
    output_path.write_text(html, encoding="utf-8")


def _build_per_test_args(
    comp, results, test_lookup, store, config, manifest_meta_by_model, artifact_files=()
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
    import json as _json

    from .dashboard_render import format_run_metadata

    status_path = work_dir / "status.json"
    try:
        snapshot = _json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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

    # Resolve the backend's artifact file list once up front — class attribute,
    # no backend instantiation needed (avoids triggering the fmpy import if the
    # extra isn't installed).
    from ..backends import get_backend_class

    try:
        artifact_files = tuple(get_backend_class(config).artifact_files)
    except ValueError:
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
