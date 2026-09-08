"""Build the per-test template context from comparison results.

The pure ``results -> context dict`` side of the per-test report: every
helper here takes already-extracted inputs and returns one named slice of
the context that :mod:`dstf.reporting.report_suite` renders. Nothing in
this module touches Jinja, the filesystem, or the decimation pass.
"""

import logging
import math
from pathlib import Path

import numpy as np

from ..comparison.comparator import VariableComparison
from ..comparison.registry import summarize

logger = logging.getLogger(__name__)


def _format_value(val) -> str:
    """Format a value for display in HTML tables."""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if val is None or val == "":
        return ""
    return str(val)


def _build_stats_section(
    title: str,
    ref_cat: dict,
    cur_cat: dict,
    key_order: list[str] | None = None,
) -> dict | None:
    """Build a statistics section dict for the template.

    If `key_order` is provided, keys appear in that order; unrecognized keys
    append alphabetically. If omitted, all keys appear alphabetically.
    """
    all_keys_set = set(ref_cat.keys()) | set(cur_cat.keys())
    if not all_keys_set:
        return None
    if key_order:
        all_keys = [k for k in key_order if k in all_keys_set]
        all_keys += sorted(all_keys_set - set(key_order))
    else:
        all_keys = sorted(all_keys_set)
    rows = []
    for key in all_keys:
        ref_val = _format_value(ref_cat.get(key, ""))
        cur_val = _format_value(cur_cat.get(key, ""))
        changed = ref_val != cur_val and ref_val and cur_val
        rows.append(
            {
                "label": key.replace("_", " ").title(),
                "current": cur_val,
                "reference": ref_val,
                "changed": changed,
            }
        )
    return {"title": title, "rows": rows}


def _extract_mode_values(
    vc: VariableComparison,
    mode: str,
    spec_params: dict | None = None,
) -> dict:
    """Produce the per-mode config-value dict for the UI panel + JS scorer.

    Mirrors the fields each :class:`ComparisonMode` Config dataclass exposes.
    Source preference, in order:

    1. **Spec params** (``spec_params``) — the authored config from the
       LeafSpec, round-tripped through :func:`spec_to_view`. What the user
       wrote. Always preferred for config-side fields (tube_rel, tube_abs,
       count_must_match, ...) so the reporter's mode-values reflect the
       spec, not mode-defaults.
    2. **Diagnostics** (``vc.diagnostics``) — values stashed by the
       comparator. Source of truth for derived fields that aren't in the
       spec (range bounds re-emit + detected-reference-peaks for
       dominant-frequency).
    3. **Hard defaults** — only for fields the caller can't leave blank.

    Fixes D75 regression where tube leaves' ``tube_rel``/``tube_abs``/
    ``tube_min_width`` came back as mode-dataclass-default ``0.0`` from
    diag (which never carries them), overwriting spec values in
    ``leafState.params`` on the JS side and producing noisy ``add`` ops
    in export patches.
    """
    diag = vc.diagnostics or {}
    sp = spec_params or {}

    def pick(*keys, default=None):
        """First non-None value from (spec_params, diag)."""
        for k in keys:
            if k in sp and sp[k] is not None:
                return sp[k]
        for k in keys:
            if k in diag and diag[k] is not None:
                return diag[k]
        return default

    if mode == "nrmse":
        return {"tolerance": float(vc.tolerance_used)}
    if mode == "points":
        return {"tolerance": float(vc.tolerance_used)}
    if mode == "tube":
        return {
            "tube_width_mode": pick("tube_width_mode"),
            "tube_abs": float(pick("tube_abs", default=0.0) or 0.0),
            "tube_rel": float(pick("tube_rel", default=0.0) or 0.0),
            "tube_min_width": float(pick("tube_min_width", default=0.0) or 0.0),
            "tube_interpolation": pick("tube_interpolation", default="linear"),
        }
    if mode == "range":
        # Range bounds live in diagnostics because _compare_range re-stashes
        # them for consistent reporting; spec overrides if the user edited.
        return {
            "min_value": pick("min_value"),
            "max_value": pick("max_value"),
        }
    if mode == "event-timing":
        return {
            "time_tolerance": float(pick("time_tolerance", default=1e-3) or 1e-3),
            "count_must_match": bool(pick("count_must_match", default=True)),
        }
    if mode == "dominant-frequency":
        return {
            "peaks": list(
                pick("peaks", default=None) or diag.get("peaks_declared") or []
            ),
        }
    if mode == "scalar":
        return {
            "quantity": pick("quantity"),
            "tolerance": float(vc.tolerance_used),
            "tolerance_mode": pick("tolerance_mode", default="abs"),
            "min_value": pick("min_value"),
            "max_value": pick("max_value"),
        }
    return {}


def _render_mode_controls(variable: str, mode: str, values: dict) -> str:
    """Render the 6.1.1 auto-derived UI panel for this mode + values.

    Returns an empty string when no UI is registered for the mode (caller
    falls back to the hint span). Unknown / implicit mode defaults to
    ``"nrmse"``.
    """
    from .ui.mode_controls import get_mode_ui

    lookup = mode or "nrmse"
    ui = get_mode_ui(lookup)
    if ui is None:
        return ""
    return ui.render(variable=variable, values=values)


# Metrics the template JS can live-rescore (interactive.js MODE_SCORERS —
# keep in sync with that table). Anything else is badged CLI-authoritative:
# event-timing (its optimal matcher stays Python-side), scalar (its stats-path
# quantities read data the report never embeds), and every metric a third
# party registers Python-side only. The JS scorer lookup would keep the CLI
# verdict for those anyway; the badge tells the user why.
_JS_SCORED_METRICS = frozenset(
    {"nrmse", "tube", "points", "range", "dominant-frequency"}
)


def worst_finite_score(comparisons: list) -> tuple[float | None, int]:
    """Return ``(worst finite score, count of unscored leaves)``.

    ``VariableComparison.nrmse`` carries the mode's score, and a sentinel
    leaf (metric config that would not build, no data to compare) sets it
    to ``inf``/``nan``. A plain ``max()`` over the leaves therefore returns
    a non-finite value for the whole test, which the strict-JSON pass then
    writes as ``null`` — the dashboard's "Worst NRMSE" read "—" for a test
    whose other leaves scored perfectly well. Report the worst among the
    leaves that HAVE a score, and count the rest so the surface can say so
    instead of implying there was nothing to measure.
    """
    finite = [vc.nrmse for vc in comparisons if math.isfinite(vc.nrmse)]
    n_unscored = sum(1 for vc in comparisons if not math.isfinite(vc.nrmse))
    return (max(finite) if finite else None, n_unscored)


def _augment_tree_view(
    view: dict,
    comparisons_by_path: dict[str, VariableComparison],
    *,
    time_bounds_by_variable: dict[str, tuple[float, float]] | None = None,
    embedded_baselines_by_variable: dict[str, set[str]] | None = None,
) -> None:
    """Walk ``view`` in place, enriching leaf nodes with render artifacts.

    Stage-2 JS consumes the tree as the single source of truth for the
    per-variable node UI. Each leaf needs: ``mode_controls_html``,
    ``window_controls_html``, ``score_display``, ``criterion``,
    ``mode_values``, ``cli_authoritative``, ``tolerance_used``, and the
    raw numeric summary (``nrmse``, ``max_abs_error``, ...) used by the
    JS scorer registry.

    ``time_bounds_by_variable`` feeds ``(t_start, t_end)`` into each
    window control's ``placeholder`` so users see the simulation range
    as a hint without auto-committing it.

    ``embedded_baselines_by_variable`` names, per variable, the soft_check
    baselines whose trajectory is embedded in the page. It decides whether
    an ``against``-leaf can be live-scored (see ``cli_authoritative``).
    """
    if view.get("kind") == "leaf":
        path = view.get("path", "")
        vc = comparisons_by_path.get(path)
        if vc is None:
            return
        mode = vc.mode or "nrmse"
        # Spec params (from the authored LeafSpec, round-tripped via
        # spec_to_view) are the source of truth for config-side fields;
        # extract_mode_values prefers them over diagnostic fallbacks.
        spec_params = view.get("params") or {}
        mode_values = _extract_mode_values(vc, mode, spec_params=spec_params)
        # Seed the window inputs from the AUTHORED window on the spec view
        # (spec_to_view emits it from the LeafSpec). The old helper read
        # vc.diagnostics['window'], which nothing ever set — the evaluator
        # stashes the window on the MetricResult, not the
        # VariableComparison — so authored windows never pre-filled the
        # inputs, masked by browser fixtures hand-setting window_values
        # (found eyeballing the D103 demo report).
        window_values = dict(view.get("window") or {})
        var_label = vc.name or view.get("variable", "")
        summary = summarize(vc)
        score_display, criterion = summary.display, summary.criterion
        bounds = (time_bounds_by_variable or {}).get(var_label)
        t_start, t_end = bounds if bounds else (None, None)
        # An against-leaf is live-scorable exactly when the named
        # baseline's curve for THIS variable rode into the page as an
        # overlay — that is the array the JS scorer swaps in for the
        # primary reference. Absent (overlay missing / didn't carry this
        # variable), there is nothing to score against and the CLI verdict
        # stands.
        against = view.get("against") or "primary"
        against_unavailable = against != "primary" and against not in (
            (embedded_baselines_by_variable or {}).get(var_label, frozenset())
        )
        view.update(
            {
                "mode_effective": mode,  # runtime mode (e.g., "points"); not the spec metric
                "name": var_label,
                "nrmse": float(vc.nrmse),
                "rmse": float(vc.rmse),
                "signal_range": float(vc.signal_range),
                "max_abs_error": float(vc.max_abs_error),
                "max_abs_error_time": float(vc.max_abs_error_time),
                "reference_final": float(vc.reference_final),
                "actual_final": float(vc.actual_final),
                "is_constant": bool(vc.is_constant),
                "tolerance_used": float(vc.tolerance_used),
                "score_display": score_display,
                "criterion": criterion,
                "tube_points_inside": (
                    float(vc.tube_points_inside)
                    if vc.tube_points_inside is not None
                    else None
                ),
                "tube_worst_violation": (
                    float(vc.tube_worst_violation)
                    if vc.tube_worst_violation is not None
                    else None
                ),
                "tube_worst_violation_time": (
                    float(vc.tube_worst_violation_time)
                    if vc.tube_worst_violation_time is not None
                    else None
                ),
                "mode_values": mode_values,
                "mode_controls_html": _render_mode_controls(
                    var_label, mode, mode_values
                ),
                "window_controls_html": _render_window_controls(
                    var_label,
                    window_values,
                    time_start=t_start,
                    time_end=t_end,
                ),
                "window_values": window_values,
                # event-timing is always CLI-authoritative (event pairing
                # stays Python-side). Dominant-frequency has a live JS scorer
                # (D75) whose FFT is bit-identical with Python's ONLY on the
                # full-resolution trajectory; leaves whose embedded arrays got
                # LTTB-decimated are flipped to CLI-authoritative afterwards by
                # report_suite._mark_decimated_cli_authoritative (decimation
                # happens later, so it can't be decided here). A leaf scored
                # against a NAMED baseline used to be pinned unconditionally
                # for the same reason; the page embeds each soft_check's
                # trajectory as an overlay, and the JS scorers now resolve an
                # against-leaf to that curve, so the pin narrows to the case
                # where the named overlay is genuinely absent for this
                # variable (D105's remaining half).
                # So is a leaf whose mode never built (``config_error``):
                # there is no live-scoring state for the JS to re-derive, and
                # a `range` sentinel would have flipped to PASS on the next
                # refresh back when absent bounds read as unbounded. Gated on
                # ``config_error``, NOT on ``error`` alone — a data-shaped
                # sentinel (empty window, empty trajectory) must stay live so
                # dragging the window back over the samples re-scores it.
                "cli_authoritative": (
                    mode not in _JS_SCORED_METRICS
                    or against_unavailable
                    or bool((vc.diagnostics or {}).get("config_error"))
                ),
                # Reason a sentinel leaf could not be scored. Rendered as the
                # score pill's tooltip plus a muted line beneath it; None on
                # every normally-scored leaf.
                "error": (vc.diagnostics or {}).get("error"),
                # Dominant-frequency leaves carry their spectrum arrays so the
                # reporter's editor-slot subplot can render without recomputing.
                # Empty dict for other modes; the JS editor short-circuits.
                "spectrum": _extract_spectrum(vc)
                if mode == "dominant-frequency"
                else None,
            }
        )
        return
    for child in view.get("children", []):
        _augment_tree_view(
            child,
            comparisons_by_path,
            time_bounds_by_variable=time_bounds_by_variable,
            embedded_baselines_by_variable=embedded_baselines_by_variable,
        )


def _extract_spectrum(vc: VariableComparison) -> dict:
    """Pull the FFT spectrum + declared/matched peaks from a dominant-
    frequency leaf's diagnostics. Consumed by the reporter's editor-slot
    subplot.

    ``detected_reference_peaks_hz`` is the reference spectrum's top peaks,
    always present — the reporter's "Detect peaks from reference" button
    reads from it to bootstrap a declared-peaks table on a fresh test.

    The comparator caps spectrum length at ``_SPECTRUM_EMBED_CAP`` (512
    bins) precisely so this dict doesn't balloon the HTML payload.
    """
    diag = vc.diagnostics or {}
    return {
        "ref_freq": list(diag.get("ref_spectrum_freq") or []),
        "ref_mag": list(diag.get("ref_spectrum_mag") or []),
        "act_freq": list(diag.get("act_spectrum_freq") or []),
        "act_mag": list(diag.get("act_spectrum_mag") or []),
        "peaks_declared": list(diag.get("peaks_declared") or []),
        "paired_peaks": list(diag.get("paired_peaks") or []),
        "detected_reference_peaks_hz": list(
            diag.get("detected_reference_peaks_hz") or []
        ),
    }


def _render_window_controls(
    variable: str,
    values: dict,
    *,
    time_start: float | None = None,
    time_end: float | None = None,
) -> str:
    """Render the universal window inputs for a tree-backed leaf.

    ``time_start`` / ``time_end`` populate the inputs' ``placeholder``
    attribute with the variable's simulation range as a hint.
    """
    from .ui.mode_controls import render_window_controls_html

    return render_window_controls_html(
        variable=variable,
        values=values,
        time_start=time_start,
        time_end=time_end,
    )


# ---------------------------------------------------------------------------
# Per-section context builders
# ---------------------------------------------------------------------------
# _build_template_context (below) is an orchestrator over the helpers in this
# section. Each helper produces one named slice of the output context dict
# (ref_info / sim_params / statistics_sections / etc.) so per-section edits
# don't muddy git blame on unrelated sections, and each piece is independently
# testable by feeding it a small input dict + asserting on the returned slice.
# Shared invariants:
#   * Every helper accepts already-extracted inputs (ref_stats, ref_sim) so
#     the orchestrator owns the "if ref_data else {}" sentinel-handling.
#   * Helpers return values; mutation of caller-owned state happens only at
#     the orchestrator level (overlay attach is the one in-place step).


def _build_ref_info(
    comparisons: list,
    ref_data: dict | None,
    test_dir: Path | None,
    ref_file: Path | None,
) -> list:
    """Build the test-metadata rows shown in the report's "Reference Info"
    section: test_id / status / dates / reference-file link / test-dir link
    / tracked-variable count.
    """
    ref_info = []
    meta_fields = [
        ("test_id", "Test ID"),
        ("status", "Status"),
        ("date_added", "Date Added"),
        ("last_updated", "Last Updated"),
    ]
    for key, label in meta_fields:
        ref_val = ref_data.get(key, "") if ref_data else ""
        if ref_val:
            ref_info.append({"label": label, "value": _format_value(ref_val)})

    if ref_data and ref_data.get("test_id"):
        from ..storage.ref_index import RefIndex

        ref_filename = RefIndex.ref_filename(ref_data["test_id"])
        row = {"label": "Reference File", "value": ref_filename}
        if ref_file and ref_file.exists():
            row["link"] = ref_file.resolve().as_uri()
        ref_info.append(row)

    if test_dir and test_dir.exists():
        ref_info.append(
            {
                "label": "Test Directory",
                "value": test_dir.name,
                "link": test_dir.resolve().as_uri(),
            }
        )

    ref_info.append(
        {
            "label": "Tracked Variables",
            "value": f"{len(comparisons)} current"
            + (f" / {ref_data.get('n_vars', '?')} reference" if ref_data else "")
            if comparisons
            else "",
        }
    )
    return ref_info


def _build_sim_params(test_model, ref_sim: dict) -> list:
    """Build the current-vs-reference simulation-parameter table (stop_time,
    tolerance, method, ...). Each row carries a ``changed`` flag the
    template uses to highlight drift.
    """
    sim_params = []
    sim_fields = [
        ("stop_time", "Stop Time"),
        ("tolerance", "Tolerance"),
        ("method", "Method"),
        ("number_of_intervals", "Number of Intervals"),
        ("output_interval", "Output Interval"),
    ]
    for key, label in sim_fields:
        ref_val = ref_sim.get(key)
        cur_val = getattr(test_model, key, None) if test_model else None
        ref_str = _format_value(ref_val) if ref_val is not None else ""
        cur_str = _format_value(cur_val) if cur_val is not None else ""
        if ref_str or cur_str:
            sim_params.append(
                {
                    "label": label,
                    "current": cur_str,
                    "reference": ref_str,
                    "changed": cur_str != "" and ref_str != "" and cur_str != ref_str,
                }
            )
    return sim_params


def _build_statistics_sections(ref_stats: dict, cur_stats: dict) -> list:
    """Auto-detect and render every top-level dict in stats as its own
    collapsible section, plus mop up top-level scalars under "Simulation
    Statistics" for back-compat with older reference shapes. Future
    categories (e.g. ``"timing"``) drop in for free since the dispatch
    is by dict key, not a hardcoded enum.
    """
    SECTION_TITLES = {
        "translation": "Translation Statistics",
        "simulation": "Simulation Statistics",
        "timing": "Timing",
    }
    section_keys: list[str] = []
    seen: set[str] = set()
    for src in (ref_stats, cur_stats):
        for k, v in src.items():
            if isinstance(v, dict) and k not in seen:
                seen.add(k)
                section_keys.append(k)

    preferred = ["translation", "simulation", "timing"]
    ordered_keys = [k for k in preferred if k in seen] + [
        k for k in section_keys if k not in preferred
    ]

    statistics_sections = []
    for key in ordered_keys:
        ref_cat = ref_stats.get(key, {}) if isinstance(ref_stats.get(key), dict) else {}
        cur_cat = cur_stats.get(key, {}) if isinstance(cur_stats.get(key), dict) else {}
        if key == "simulation":
            for k, v in ref_stats.items():
                if not isinstance(v, dict):
                    ref_cat.setdefault(k, v)
            for k, v in cur_stats.items():
                if not isinstance(v, dict):
                    cur_cat.setdefault(k, v)
        title = SECTION_TITLES.get(key, key.replace("_", " ").title())
        key_order = None
        if key == "timing":
            key_order = ["translation_wall", "sim_wall", "other_wall", "total_wall"]
        section = _build_stats_section(title, ref_cat, cur_cat, key_order=key_order)
        if section:
            statistics_sections.append(section)

    if "simulation" not in seen:
        ref_scalars = {k: v for k, v in ref_stats.items() if not isinstance(v, dict)}
        cur_scalars = {k: v for k, v in cur_stats.items() if not isinstance(v, dict)}
        if ref_scalars or cur_scalars:
            section = _build_stats_section(
                "Simulation Statistics", ref_scalars, cur_scalars
            )
            if section:
                statistics_sections.append(section)
    return statistics_sections


def _build_trajectories(
    comparisons: list,
    result,
    ref_data: dict | None,
) -> tuple[list, list, list]:
    """Build the trajectory triple — primary tracked variables, diagnostic
    variables (CPUtime / EventCounter), and the no-baseline pass-through
    used when a test runs without a reference. Diagnostic refs may carry
    a scalar summary instead of a full trajectory; the legacy
    full-array shape is still accepted for back-read.

    Returns ``(trajectories, diag_trajectories, nobaseline_trajectories)``.
    """
    trajectories = []
    ref_time_list = ref_data.get("time", []) if ref_data else []
    ref_vars_by_idx = {}
    if ref_data:
        for rv in ref_data.get("variables", []):
            ref_vars_by_idx[rv["index"]] = rv

    if result and result.variables:
        for vc in comparisons:
            act_var = None
            for v in result.variables:
                if v.index == vc.index:
                    act_var = v
                    break
            ref_var = ref_vars_by_idx.get(vc.index)
            trajectories.append(
                {
                    "index": vc.index,
                    "name": vc.name or f"x[{vc.index}]",
                    "act_time": act_var.time.tolist() if act_var else [],
                    "act_values": act_var.values.tolist() if act_var else [],
                    "ref_time": ref_time_list,
                    "ref_values": ref_var["values"] if ref_var else [],
                }
            )

    diag_trajectories = []
    if result and result.diagnostics:
        ref_diags_by_name = {}
        if ref_data:
            for rd in ref_data.get("diagnostics", []):
                ref_diags_by_name[rd["name"]] = rd
        for diag in result.diagnostics:
            ref_diag = ref_diags_by_name.get(diag.name)
            ref_values = ref_diag.get("values", []) if ref_diag else []
            diag_trajectories.append(
                {
                    "name": diag.name,
                    "act_time": diag.time.tolist(),
                    "act_values": diag.values.tolist(),
                    "ref_time": ref_time_list if ref_values else [],
                    "ref_values": ref_values,
                }
            )

    nobaseline_trajectories = []
    if not comparisons and result and result.variables:
        for var in result.variables:
            nobaseline_trajectories.append(
                {
                    "index": var.index,
                    "name": var.name or f"x[{var.index}]",
                    "time": var.time.tolist(),
                    "values": var.values.tolist(),
                }
            )
    return trajectories, diag_trajectories, nobaseline_trajectories


def _build_diag_summaries(result, ref_data: dict | None) -> list:
    """Diagnostic-variable summary rows (current final/min/max vs reference
    summary). Replaces the pre-D78 full-trajectory comparison for
    diagnostics — the trajectory is informational; the summary values
    are the actual regression signal (final CPUtime, total event count).
    """
    if not (result and result.diagnostics):
        return []
    ref_diags_by_name = {}
    if ref_data:
        for rd in ref_data.get("diagnostics", []):
            ref_diags_by_name[rd["name"]] = rd
    diag_summaries = []
    for diag in result.diagnostics:
        values = np.asarray(diag.values)
        cur_final = float(values[-1]) if values.size else None
        ref_entry = ref_diags_by_name.get(diag.name, {})
        # Accept either new summary shape (final/min/max) or legacy full-
        # trajectory shape (values) for back-read during transition.
        if "final" in ref_entry:
            ref_final = ref_entry.get("final")
        elif "values" in ref_entry and ref_entry["values"]:
            ref_final = float(ref_entry["values"][-1])
        else:
            ref_final = None
        diag_summaries.append(
            {
                "name": diag.name,
                "current": _format_value(cur_final) if cur_final is not None else "",
                "reference": _format_value(ref_final) if ref_final is not None else "",
                "changed": (
                    cur_final is not None
                    and ref_final is not None
                    and cur_final != ref_final
                ),
            }
        )
    return diag_summaries


def _build_artifacts(
    test_dir: Path | None,
    artifact_files: tuple[tuple[str, str], ...],
) -> list:
    """Walk the backend-declared :attr:`Backend.artifact_files`
    list, return entries for files that exist on disk. Backend-agnostic
    — no hardcoded Dymola/OM/etc. names here.
    """
    if not (test_dir and test_dir.exists()):
        return []
    artifacts = []
    for fname, label in artifact_files or ():
        fpath = test_dir / fname
        if fpath.exists():
            artifacts.append({"uri": fpath.resolve().as_uri(), "label": label})
    return artifacts


def _compute_summary_flags(
    comparisons: list,
    result,
    test_model,
) -> tuple[int, int, bool, bool]:
    """Top-level pass/fail summary flags for the report header.

    Returns ``(n_passed, n_nobaseline, is_simulate_only, sim_failed)``.
    ``simulate_only`` tests legitimately have no comparisons and no
    variables — the pass signal is "the simulation ran", so this guards
    against rendering a misleading "Simulation failed" banner for them.
    """
    n_passed = sum(1 for vc in comparisons if vc.passed)
    n_nobaseline = (
        len(result.variables)
        if (not comparisons and result and result.variables)
        else 0
    )
    is_simulate_only = bool(test_model and getattr(test_model, "simulate_only", False))
    sim_failed = len(comparisons) == 0 and n_nobaseline == 0 and not is_simulate_only
    return n_passed, n_nobaseline, is_simulate_only, sim_failed


def _build_key_stats(
    comparisons: list,
    ref_stats: dict,
    cur_stats: dict,
) -> list:
    """Top-level summary row: worst score across leaves, plus a small
    fixed pick of structural stats (continuous states, nonlinear
    counts) and CPUtime / EventCounter. Format mirrors statistics_sections
    rows so the template renderer is uniform.
    """

    def _get_stat(source: dict, category: str, key: str):
        cat = source.get(category, {})
        if isinstance(cat, dict):
            return cat.get(key)
        return None

    def _get_scalar(source: dict, key: str):
        val = source.get(key)
        return val if not isinstance(val, dict) else None

    key_stats = []
    # "Worst Score" — for NRMSE leaves this is the max NRMSE (lower is
    # better); for range/tube leaves the nrmse field carries mode-specific
    # content (max_violation / fraction-inside). Mixed-mode tests get the
    # worst across whatever leaves they contain.
    # Sentinel leaves (inf/nan score) are excluded and counted instead —
    # one unscored leaf used to swallow every real number on the test.
    worst_score, n_unscored = worst_finite_score(comparisons)
    # The unscored count rides in ``current`` (the template prefixes
    # ``reference`` with "ref:", which this is not).
    unscored_note = (
        ""
        if not n_unscored
        else (
            " (1 leaf unscored)"
            if n_unscored == 1
            else f" ({n_unscored} leaves unscored)"
        )
    )
    if worst_score is not None:
        key_stats.append(
            {
                "label": "Worst Score",
                "current": f"{worst_score:.4e}{unscored_note}",
                "reference": "",
            }
        )
    elif n_unscored:
        key_stats.append(
            {
                "label": "Worst Score",
                "current": (
                    "n/a (1 unscored leaf)"
                    if n_unscored == 1
                    else f"n/a ({n_unscored} unscored leaves)"
                ),
                "reference": "",
            }
        )

    stat_picks = [
        ("translation", "continuous_time_states", "Continuous States"),
        ("translation", "nonlinear_count", "Nonlinear Systems"),
        ("translation", "nonlinear_max", "Largest Nonlinear"),
    ]
    for category, key, label in stat_picks:
        cur_val = _get_stat(cur_stats, category, key)
        ref_val = _get_stat(ref_stats, category, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append(
                {
                    "label": label,
                    "current": _format_value(cur_val),
                    "reference": _format_value(ref_val),
                    "changed": cur_val is not None
                    and ref_val is not None
                    and str(cur_val) != str(ref_val),
                }
            )

    scalar_picks = [
        ("CPUtime", "CPU Time"),
        ("EventCounter", "Events"),
    ]
    for key, label in scalar_picks:
        cur_val = _get_scalar(cur_stats, key)
        ref_val = _get_scalar(ref_stats, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append(
                {
                    "label": label,
                    "current": _format_value(cur_val),
                    "reference": _format_value(ref_val),
                    "changed": cur_val is not None
                    and ref_val is not None
                    and str(cur_val) != str(ref_val),
                }
            )
    return key_stats


def _build_warning_rows(warnings: list | None) -> list:
    """Translate :class:`StructuralWarning` instances into the row dicts
    the template iterates over. Trivial wrapper, kept as a helper for
    symmetry with the other ``_build_*`` extractors.
    """
    return [
        {
            "field": w.field,
            "reference": str(w.reference_value),
            "current": str(w.current_value),
        }
        for w in (warnings or [])
    ]


def _view_leaf_paths(node: dict) -> list[str]:
    """Leaf paths of a spec view, in tree order."""
    if node.get("kind") == "leaf":
        return [node["path"]]
    out: list[str] = []
    for child in node.get("children") or []:
        out.extend(_view_leaf_paths(child))
    return out


def _build_tree_view_and_variables(
    comparisons: list,
    test_model,
    metric_tree,
    trajectories: list,
) -> tuple[dict | None, dict]:
    """Stage-2 single-source-of-truth: returns ``(tree_view,
    variables_by_name)``.

    The reporter's recursive-component UI walks ``tree_view`` directly
    (a serialized SpecNode tree with per-node paths + evaluation results,
    leaves augmented with render artifacts). ``variables_by_name`` carries
    one entry per unique variable — its trajectory + overlays + the leaf
    paths targeting it — so per-variable plot sections mount a filtered
    view of the same tree.

    When there are no comparisons the function returns ``(None, {})`` —
    sim-failed and no-baseline tests render without a tree view at all.
    """
    if not comparisons:
        return None, {}

    from ..comparison.tree_eval import flatten_evaluation as _flatten_evaluation
    from ..comparison.tree_spec import (
        collect_variables as _collect_variables,
    )
    from ..comparison.tree_spec import (
        leaves_for_variable as _leaves_for_variable,
    )
    from ..comparison.tree_spec import (
        spec_to_view as _spec_to_view,
    )
    from ..comparison.tree_spec import (
        synthesize_implicit_tree as _synthesize_implicit_tree,
    )

    comparison_var_names = [vc.name or f"x[{vc.index}]" for vc in comparisons]
    if (
        test_model is not None
        and getattr(test_model, "metric_tree_spec", None) is not None
    ):
        spec = test_model.metric_tree_spec
    else:
        spec = _synthesize_implicit_tree(
            comparison_var_names,
            variable_overrides=(
                getattr(test_model, "variable_overrides", None) if test_model else None
            ),
        )

    eval_by_path = _flatten_evaluation(metric_tree) if metric_tree is not None else {}

    # Leaves are addressed by the path a patch op would use — stamped by the
    # evaluator on both evaluation routes — never by position in the flat
    # comparison list.
    from ..comparison.tree_eval import leaf_comparisons_by_path

    comparisons_by_path = (
        leaf_comparisons_by_path(metric_tree) if metric_tree is not None else {}
    )

    # Collect per-variable simulation bounds so window inputs show the
    # full-trajectory range as placeholder hints.
    time_bounds: dict[str, tuple[float, float]] = {}
    for traj in trajectories:
        ref_time = traj.get("ref_time") or traj.get("act_time") or []
        if ref_time:
            time_bounds[traj["name"]] = (
                float(ref_time[0]),
                float(ref_time[-1]),
            )

    # Which named baselines actually rode into the page for each variable.
    # Companions are excluded on purpose: the validator forbids scoring
    # against them, so a companion sharing a soft_check's name must never
    # make an against-leaf look live-scorable.
    embedded_baselines: dict[str, set[str]] = {}
    for traj in trajectories:
        embedded_baselines[traj["name"]] = {
            ov["name"]
            for ov in (traj.get("overlays") or [])
            if ov.get("role") == "soft_check" and ov.get("name")
        }

    tree_view = _spec_to_view(spec, evaluation_by_path=eval_by_path)
    # The view walks the spec; the lookup holds what the evaluator stamped.
    # Both routes stamp every leaf the view will show, so a miss is drift
    # between the two walks — a leaf that would render with no pill and no
    # controls, and nothing else would say so.
    missing = [
        path for path in _view_leaf_paths(tree_view) if path not in comparisons_by_path
    ]
    if missing:
        logger.warning(
            "%s: %d spec leaf path(s) have no evaluated comparison and render "
            "without scores: %s",
            getattr(test_model, "model_id", "?"),
            len(missing),
            ", ".join(missing),
        )
    _augment_tree_view(
        tree_view,
        comparisons_by_path,
        time_bounds_by_variable=time_bounds,
        embedded_baselines_by_variable=embedded_baselines,
    )

    # Dedupe trajectories by variable name into a per-variable dict.
    # Overlays already attached per-trajectory-entry; first match wins.
    variables_by_name: dict[str, dict] = {}
    for vn in _collect_variables(spec):
        match_traj = next((t for t in trajectories if t["name"] == vn), None)
        variables_by_name[vn] = {
            "name": vn,
            "trajectory": match_traj or {},
            "overlays": (match_traj or {}).get("overlays", []),
            "leaf_paths": [p for _, p in _leaves_for_variable(spec, vn)],
        }
    return tree_view, variables_by_name


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _build_template_context(
    model_id: str,
    comparisons: list[VariableComparison],
    ref_data: dict | None,
    cur_stats: dict | None,
    test_dir: Path | None,
    test_model=None,
    result=None,
    ref_file: Path | None = None,
    warnings: list | None = None,
    last_run_at: float | None = None,
    metric_tree=None,
    artifact_files: tuple[tuple[str, str], ...] = (),
    overlays: list | None = None,
    run_metadata: dict | None = None,
) -> dict:
    """Build the full template context dict from comparison data.

    Orchestrator over the per-section ``_build_*`` helpers above. Each
    output key in the returned dict is produced by exactly one helper.
    """
    if cur_stats is None:
        cur_stats = {}
    ref_stats = ref_data.get("statistics", {}) if ref_data else {}
    ref_sim = ref_data.get("simulation", {}) if ref_data else {}

    ref_info = _build_ref_info(comparisons, ref_data, test_dir, ref_file)
    sim_params = _build_sim_params(test_model, ref_sim)
    statistics_sections = _build_statistics_sections(ref_stats, cur_stats)
    trajectories, diag_trajectories, nobaseline_trajectories = _build_trajectories(
        comparisons,
        result,
        ref_data,
    )
    diag_summaries = _build_diag_summaries(result, ref_data)
    artifacts = _build_artifacts(test_dir, artifact_files)
    n_passed, _n_nobaseline, is_simulate_only, sim_failed = _compute_summary_flags(
        comparisons,
        result,
        test_model,
    )
    key_stats = _build_key_stats(comparisons, ref_stats, cur_stats)
    warning_rows = _build_warning_rows(warnings)

    last_run_str = ""
    if last_run_at:
        from datetime import datetime

        last_run_str = datetime.fromtimestamp(last_run_at).isoformat(timespec="seconds")

    # Stamp overlays onto each trajectory by variable name. The nobaseline
    # list gets the same pass so NO_REF tests can still show
    # sibling-backend / companion / soft_check overlays — useful for the
    # pre-accept cross-check story on a brand-new backend / OS. Missing
    # or invalid overlays drop off the per-plot path but stay in
    # ``overlay_rows`` so the report surfaces them as "not rendered".
    from .overlay_loader import attach_overlays_to_trajectories, overlay_summary

    overlays = overlays or []
    attach_overlays_to_trajectories(trajectories, overlays)
    attach_overlays_to_trajectories(nobaseline_trajectories, overlays)
    overlay_rows = overlay_summary(overlays)

    tree_view, variables_by_name = _build_tree_view_and_variables(
        comparisons,
        test_model,
        metric_tree,
        trajectories,
    )

    from .ui.mode_controls import emit_mode_schemas as _emit_mode_schemas

    return {
        "model_id": model_id,
        "n_passed": n_passed,
        "sim_failed": sim_failed,
        "is_simulate_only": is_simulate_only,
        "last_run_at": last_run_at,
        "last_run_str": last_run_str,
        "warnings": warning_rows,
        "key_stats": key_stats,
        "ref_info": ref_info,
        "sim_params": sim_params,
        "statistics_sections": statistics_sections,
        "diagnostic_summaries": diag_summaries,
        "artifacts": artifacts,
        "trajectories": trajectories,
        "diag_trajectories": diag_trajectories,
        "nobaseline_trajectories": nobaseline_trajectories,
        # A2 / idea #50 — companion + soft_check overlays (default off).
        "overlay_rows": overlay_rows,
        # Stage 2 — recursive tree view + per-variable plot grouping.
        "tree_view": tree_view,
        "variables_by_name": variables_by_name,
        "mode_schemas": _emit_mode_schemas(),
        # Run provenance banner (backend/simulator/version/os). Same shape as
        # the dashboard's, formatted by dashboard_render.format_run_metadata.
        "run_metadata": run_metadata,
    }
