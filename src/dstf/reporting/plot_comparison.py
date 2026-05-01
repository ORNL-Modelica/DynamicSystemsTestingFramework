"""Generate comparison plots and HTML viewer for interactive review."""

import json
import logging
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

from ..comparison.comparator import VariableComparison

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _sanitize_filename(name: str) -> str:
    """Sanitize a variable name for use in a filename.

    Collapses whitespace (including newlines), replaces Modelica
    punctuation, and removes any remaining filesystem-unsafe characters.
    """
    # Collapse all whitespace (newlines, spaces, tabs) into single underscore
    name = re.sub(r'\s+', '_', name)
    # Replace common Modelica punctuation
    name = name.replace("[", "_").replace("]", "").replace(".", "_")
    name = name.replace(",", "_").replace("(", "").replace(")", "")
    # Remove anything that's not alphanumeric, underscore, or hyphen
    name = re.sub(r'[^\w\-]', '', name)
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name).strip('_')
    return name


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
    key_order: Optional[list[str]] = None,
) -> Optional[dict]:
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
        rows.append({
            "label": key.replace("_", " ").title(),
            "current": cur_val,
            "reference": ref_val,
            "changed": changed,
        })
    return {"title": title, "rows": rows}


def _extract_mode_values(
    vc: VariableComparison, mode: str,
    spec_params: Optional[dict] = None,
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
            "min_value": pick("min_value", "min"),
            "max_value": pick("max_value", "max"),
        }
    if mode == "event-timing":
        return {
            "time_tolerance": float(pick("time_tolerance", default=1e-3) or 1e-3),
            "count_must_match": bool(pick("count_must_match", default=True)),
        }
    if mode == "dominant-frequency":
        return {
            "peaks": list(pick("peaks", default=None)
                          or diag.get("peaks_declared") or []),
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


def _leaf_score_display(vc: VariableComparison) -> tuple[str, str]:
    """Return ``(score_display, criterion)`` for a leaf's UI labels.

    Both strings are mode-aware; callers plug them into the variable-table
    cell (legacy path) and the Stage-2 tree-node render (new path).
    Extracted here so the two renderers agree on wording.
    """
    mode = vc.mode or "nrmse"
    if mode == "tube" and vc.tube_points_inside is not None:
        return (
            f"{vc.tube_points_inside * 100:.1f}% in tube",
            f"{vc.tube_points_inside * 100:.1f}% inside tube "
            f"→ {'PASS' if vc.passed else 'FAIL'} (requires 100%)",
        )
    if mode == "range" and vc.tube_worst_violation is not None:
        return (
            f"max_viol {vc.tube_worst_violation:.3e}",
            f"max violation {vc.tube_worst_violation:.3e} "
            f"→ {'PASS' if vc.passed else 'FAIL'} (requires 0)",
        )
    if mode == "points":
        return (
            f"|err| {vc.max_abs_error:.3e}",
            f"Final value error {vc.max_abs_error:.3e} vs tolerance "
            f"{vc.tolerance_used:.3e} → {'PASS' if vc.passed else 'FAIL'}",
        )
    if mode == "event-timing":
        diag = vc.diagnostics or {}
        ref_n = diag.get("ref_event_count", "?")
        act_n = diag.get("act_event_count", "?")
        tol = diag.get("time_tolerance", 0.0)
        return (
            f"Δt {vc.nrmse:.3e} ({act_n}/{ref_n} events)",
            f"Max event Δt {vc.nrmse:.3e} vs {tol:.3e} "
            f"({act_n} actual events / {ref_n} reference) "
            f"→ {'PASS' if vc.passed else 'FAIL'}",
        )
    if mode == "dominant-frequency":
        diag = vc.diagnostics or {}
        paired = diag.get("paired_peaks") or []
        n_matched = sum(1 for p in paired if p.get("matched_hz") is not None)
        n_declared = len(paired)
        if n_declared == 0:
            return (
                "no peaks declared",
                "No peaks declared — use 'Detect peaks from reference' "
                "to seed the table from the reference spectrum",
            )
        return (
            f"{n_matched}/{n_declared} peaks matched",
            f"{n_matched} / {n_declared} declared peaks found in actual "
            f"spectrum within tolerance → "
            f"{'PASS' if vc.passed else 'FAIL'}",
        )
    return (
        f"NRMSE {vc.nrmse:.3e}",
        f"NRMSE {vc.nrmse:.3e} vs tolerance {vc.tolerance_used:.3e} "
        f"→ {'PASS' if vc.passed else 'FAIL'}",
    )


def _augment_tree_view(
    view: dict,
    comparisons_by_path: dict[str, VariableComparison],
    *,
    time_bounds_by_variable: Optional[dict[str, tuple[float, float]]] = None,
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
        window_values = _extract_window_values(vc)
        var_label = vc.name or view.get("variable", "")
        score_display, criterion = _leaf_score_display(vc)
        bounds = (time_bounds_by_variable or {}).get(var_label)
        t_start, t_end = (bounds if bounds else (None, None))
        view.update({
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
                float(vc.tube_points_inside) if vc.tube_points_inside is not None else None
            ),
            "tube_worst_violation": (
                float(vc.tube_worst_violation) if vc.tube_worst_violation is not None else None
            ),
            "tube_worst_violation_time": (
                float(vc.tube_worst_violation_time) if vc.tube_worst_violation_time is not None else None
            ),
            "mode_values": mode_values,
            "mode_controls_html": _render_mode_controls(var_label, mode, mode_values),
            "window_controls_html": _render_window_controls(
                var_label, window_values,
                time_start=t_start, time_end=t_end,
            ),
            "window_values": window_values,
            # Dominant-frequency now has a live JS scorer (D75) — only
            # event-timing remains CLI-authoritative (event pairing stays
            # Python-side).
            "cli_authoritative": mode == "event-timing",
            # Dominant-frequency leaves carry their spectrum arrays so the
            # reporter's editor-slot subplot can render without recomputing.
            # Empty dict for other modes; the JS editor short-circuits.
            "spectrum": _extract_spectrum(vc) if mode == "dominant-frequency" else None,
        })
        return
    for child in view.get("children", []):
        _augment_tree_view(
            child, comparisons_by_path,
            time_bounds_by_variable=time_bounds_by_variable,
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


def _extract_window_values(vc: VariableComparison) -> dict:
    """Pull ``{"start": ..., "end": ...}`` from a leaf's recorded window.

    ``tree_eval._evaluate_leaf`` stashes the window on the leaf's
    ``diagnostics['window']`` dict when the LeafSpec declared one. Empty
    dict when no window — the reporter omits the window UI in that case.
    """
    diag = vc.diagnostics or {}
    window = diag.get("window")
    if not isinstance(window, dict):
        return {}
    out = {}
    if window.get("start") is not None:
        out["start"] = float(window["start"])
    if window.get("end") is not None:
        out["end"] = float(window["end"])
    return out


def _render_window_controls(
    variable: str,
    values: dict,
    *,
    time_start: Optional[float] = None,
    time_end: Optional[float] = None,
) -> str:
    """Render the universal window inputs for a tree-backed leaf.

    ``time_start`` / ``time_end`` populate the inputs' ``placeholder``
    attribute with the variable's simulation range as a hint.
    """
    from .ui.mode_controls import render_window_controls_html

    return render_window_controls_html(
        variable=variable, values=values,
        time_start=time_start, time_end=time_end,
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
    ref_data: Optional[dict],
    test_dir: Optional[Path],
    ref_file: Optional[Path],
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
        from ..storage.reference_store import RefIndex
        ref_filename = RefIndex.ref_filename(ref_data["test_id"])
        row = {"label": "Reference File", "value": ref_filename}
        if ref_file and ref_file.exists():
            row["link"] = ref_file.resolve().as_uri()
        ref_info.append(row)

    if test_dir and test_dir.exists():
        ref_info.append({
            "label": "Test Directory",
            "value": test_dir.name,
            "link": test_dir.resolve().as_uri(),
        })

    ref_info.append({
        "label": "Tracked Variables",
        "value": f"{len(comparisons)} current" + (
            f" / {ref_data.get('n_vars', '?')} reference" if ref_data else ""
        ) if comparisons else "",
    })
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
            sim_params.append({
                "label": label,
                "current": cur_str,
                "reference": ref_str,
                "changed": cur_str != "" and ref_str != "" and cur_str != ref_str,
            })
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
            section = _build_stats_section("Simulation Statistics", ref_scalars, cur_scalars)
            if section:
                statistics_sections.append(section)
    return statistics_sections


def _build_trajectories(
    comparisons: list,
    result,
    ref_data: Optional[dict],
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
            trajectories.append({
                "index": vc.index,
                "name": vc.name or f"x[{vc.index}]",
                "act_time": act_var.time.tolist() if act_var else [],
                "act_values": act_var.values.tolist() if act_var else [],
                "ref_time": ref_time_list,
                "ref_values": ref_var["values"] if ref_var else [],
            })

    diag_trajectories = []
    if result and result.diagnostics:
        ref_diags_by_name = {}
        if ref_data:
            for rd in ref_data.get("diagnostics", []):
                ref_diags_by_name[rd["name"]] = rd
        for diag in result.diagnostics:
            ref_diag = ref_diags_by_name.get(diag.name)
            ref_values = ref_diag.get("values", []) if ref_diag else []
            diag_trajectories.append({
                "name": diag.name,
                "act_time": diag.time.tolist(),
                "act_values": diag.values.tolist(),
                "ref_time": ref_time_list if ref_values else [],
                "ref_values": ref_values,
            })

    nobaseline_trajectories = []
    if not comparisons and result and result.variables:
        for var in result.variables:
            nobaseline_trajectories.append({
                "index": var.index,
                "name": var.name or f"x[{var.index}]",
                "time": var.time.tolist(),
                "values": var.values.tolist(),
            })
    return trajectories, diag_trajectories, nobaseline_trajectories


def _build_diag_summaries(result, ref_data: Optional[dict]) -> list:
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
        diag_summaries.append({
            "name": diag.name,
            "current": _format_value(cur_final) if cur_final is not None else "",
            "reference": _format_value(ref_final) if ref_final is not None else "",
            "changed": (
                cur_final is not None and ref_final is not None
                and cur_final != ref_final
            ),
        })
    return diag_summaries


def _build_artifacts(
    test_dir: Optional[Path],
    artifact_files: tuple[tuple[str, str], ...],
) -> list:
    """Walk the runner-declared :attr:`SimulatorRunner.artifact_files`
    list, return entries for files that exist on disk. Backend-agnostic
    — no hardcoded Dymola/OM/etc. names here.
    """
    if not (test_dir and test_dir.exists()):
        return []
    artifacts = []
    for fname, label in (artifact_files or ()):
        fpath = test_dir / fname
        if fpath.exists():
            artifacts.append({"uri": fpath.resolve().as_uri(), "label": label})
    return artifacts


def _compute_summary_flags(
    comparisons: list, result, test_model,
) -> tuple[int, int, bool, bool]:
    """Top-level pass/fail summary flags for the report header.

    Returns ``(n_passed, n_nobaseline, is_simulate_only, sim_failed)``.
    ``simulate_only`` tests legitimately have no comparisons and no
    variables — the pass signal is "the simulation ran", so this guards
    against rendering a misleading "Simulation failed" banner for them.
    """
    n_passed = sum(1 for vc in comparisons if vc.passed)
    n_nobaseline = (
        len(result.variables) if (not comparisons and result and result.variables) else 0
    )
    is_simulate_only = bool(test_model and getattr(test_model, "simulate_only", False))
    sim_failed = (
        len(comparisons) == 0 and n_nobaseline == 0 and not is_simulate_only
    )
    return n_passed, n_nobaseline, is_simulate_only, sim_failed


def _build_key_stats(
    comparisons: list, ref_stats: dict, cur_stats: dict,
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
    worst_score = max((vc.nrmse for vc in comparisons), default=None)
    if worst_score is not None:
        key_stats.append({"label": "Worst Score", "current": f"{worst_score:.4e}", "reference": ""})

    stat_picks = [
        ("translation", "continuous_time_states", "Continuous States"),
        ("translation", "nonlinear_count", "Nonlinear Systems"),
        ("translation", "nonlinear_max", "Largest Nonlinear"),
    ]
    for category, key, label in stat_picks:
        cur_val = _get_stat(cur_stats, category, key)
        ref_val = _get_stat(ref_stats, category, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append({
                "label": label,
                "current": _format_value(cur_val),
                "reference": _format_value(ref_val),
                "changed": cur_val is not None and ref_val is not None and str(cur_val) != str(ref_val),
            })

    scalar_picks = [
        ("CPUtime", "CPU Time"),
        ("EventCounter", "Events"),
    ]
    for key, label in scalar_picks:
        cur_val = _get_scalar(cur_stats, key)
        ref_val = _get_scalar(ref_stats, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append({
                "label": label,
                "current": _format_value(cur_val),
                "reference": _format_value(ref_val),
                "changed": cur_val is not None and ref_val is not None and str(cur_val) != str(ref_val),
            })
    return key_stats


def _build_warning_rows(warnings: Optional[list]) -> list:
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


def _build_tree_view_and_variables(
    comparisons: list,
    test_model,
    metric_tree,
    trajectories: list,
) -> tuple[Optional[dict], dict]:
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

    from ..comparison.tree_spec import (
        collect_leaf_paths as _collect_leaf_paths,
        collect_variables as _collect_variables,
        leaves_for_variable as _leaves_for_variable,
        spec_to_view as _spec_to_view,
        synthesize_implicit_tree as _synthesize_implicit_tree,
    )
    from ..comparison.tree_eval import flatten_evaluation as _flatten_evaluation

    comparison_var_names = [
        vc.name or f"x[{vc.index}]" for vc in comparisons
    ]
    if test_model is not None and getattr(test_model, "metric_tree_spec", None) is not None:
        spec = test_model.metric_tree_spec
    else:
        spec = _synthesize_implicit_tree(
            comparison_var_names,
            variable_overrides=(
                getattr(test_model, "variable_overrides", None)
                if test_model else None
            ),
        )

    eval_by_path = (
        _flatten_evaluation(metric_tree) if metric_tree is not None else {}
    )

    # Map path → VariableComparison so _augment_tree_view can enrich each
    # leaf with render artifacts. Length mismatch = spec/eval drift; skip
    # augmentation rather than misaddress per-leaf data.
    leaf_paths_full = _collect_leaf_paths(spec)
    if len(leaf_paths_full) == len(comparisons):
        comparisons_by_path = dict(zip(leaf_paths_full, comparisons))
    else:
        comparisons_by_path = {}

    # Collect per-variable simulation bounds so window inputs show the
    # full-trajectory range as placeholder hints.
    time_bounds: dict[str, tuple[float, float]] = {}
    for traj in trajectories:
        ref_time = traj.get("ref_time") or traj.get("act_time") or []
        if ref_time:
            time_bounds[traj["name"]] = (
                float(ref_time[0]), float(ref_time[-1]),
            )

    tree_view = _spec_to_view(spec, evaluation_by_path=eval_by_path)
    _augment_tree_view(
        tree_view, comparisons_by_path,
        time_bounds_by_variable=time_bounds,
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
    ref_data: Optional[dict],
    cur_stats: Optional[dict],
    test_dir: Optional[Path],
    test_model=None,
    result=None,
    ref_file: Optional[Path] = None,
    warnings: Optional[list] = None,
    last_run_at: Optional[float] = None,
    metric_tree=None,
    artifact_files: tuple[tuple[str, str], ...] = (),
    overlays: Optional[list] = None,
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
        comparisons, result, ref_data,
    )
    diag_summaries = _build_diag_summaries(result, ref_data)
    artifacts = _build_artifacts(test_dir, artifact_files)
    n_passed, _n_nobaseline, is_simulate_only, sim_failed = _compute_summary_flags(
        comparisons, result, test_model,
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
        comparisons, test_model, metric_tree, trajectories,
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
    }



def generate_comparison_plots(
    model_id: str,
    ref_data: Optional[dict],
    result,
    comparisons: list[VariableComparison],
    plot_dir: Path,
    test_dir: Optional[Path] = None,
    test_model=None,
    ref_file: Optional[Path] = None,
    warnings: Optional[list] = None,
    last_run_at: Optional[float] = None,
    metric_tree=None,
    artifact_files: tuple[tuple[str, str], ...] = (),
    max_embedded_samples: int = 2000,
    overlays: Optional[list] = None,
    status_text: Optional[str] = None,
    status_class: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> Optional[Path]:
    """Render this test's interactive.html (Plotly) + comparison_data.json sidecar.

    Returns the path to the rendered interactive.html.
    """
    if plot_dir.exists():
        shutil.rmtree(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    # Stage 5 — static matplotlib per-variable PNGs were retired along
    # with ``comparison.html``. Plotly inside interactive.html provides
    # every plot the PNGs showed plus interactivity; nothing linked to
    # the PNGs anymore.
    cur_stats = result.statistics if result else None
    context = _build_template_context(
        model_id, comparisons, ref_data, cur_stats,
        test_dir, test_model, result,
        ref_file=ref_file, warnings=warnings, last_run_at=last_run_at,
        metric_tree=metric_tree, artifact_files=artifact_files,
        overlays=overlays,
    )

    # Write comparison_data.json alongside the HTML. This is the
    # full-resolution data artifact for downstream tooling (notebooks,
    # future JS lazy-fetch on zoom). Decimation below only touches the
    # in-memory context that flows into interactive.html — the sidecar
    # stays untouched.
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
    context["summary"] = {
        "model_id": model_id,
        "written_at": _time.time(),
        "status_text": status_text,
        "status_class": status_class,
        "ref_id": ref_id,
        # file:// URL so the dashboard's Reference column can hyperlink
        # directly to the stored reference JSON.
        "ref_file": ref_file.as_uri() if ref_file else None,
        "worst_nrmse": (max((v.nrmse for v in comparisons), default=None)
                        if comparisons else None),
        "n_vars": len(comparisons) if comparisons else 0,
        "n_vars_passed": (sum(1 for v in comparisons if v.passed)
                          if comparisons else 0),
        "n_warnings": len(warnings) if warnings else 0,
        "translation_wall": (cur_stats.get("timing", {}).get("translation_wall")
                             if isinstance(cur_stats, dict) else None),
        "sim_wall": (cur_stats.get("timing", {}).get("sim_wall")
                     if isinstance(cur_stats, dict) else None),
        "total_wall": (cur_stats.get("timing", {}).get("total_wall")
                       if isinstance(cur_stats, dict) else None),
        "field_sources": (test_model.field_sources
                          if test_model and hasattr(test_model, "field_sources")
                          else {}),
    }
    data_path.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")

    # Decimate trajectory arrays embedded into interactive.html. This
    # caps the HTML payload for Plotly rendering; pass/fail scoring,
    # stored baselines, and comparison_data.json are unaffected.
    _decimate_context_for_html(context, max_embedded_samples)

    # Render interactive HTML (Plotly, inline data — now decimated).
    # Stage 5: the static ``comparison.html`` path was retired. The index
    # page linked only to interactive.html; the per-variable PNGs still
    # render next to it for direct reference.
    interactive_path = plot_dir / "interactive.html"
    _render_template("interactive.html", context, interactive_path)

    # Copy the standalone interactive.js next to the HTML so the
    # `<script src="interactive.js">` tag resolves. The JS is pure-static
    # (no Jinja interpolations) — the HTML marshals the per-report data
    # into window.MT_REPORT; the JS reads from there.
    shutil.copyfile(_TEMPLATE_DIR / "interactive.js", plot_dir / "interactive.js")

    return interactive_path


def _decimate_context_for_html(context: dict, max_samples: int) -> None:
    """LTTB-decimate trajectory arrays embedded into interactive.html.

    Mutates ``context`` in place. Only touches ``trajectories``,
    ``diag_trajectories``, and ``nobaseline_trajectories`` — everything
    else (scores, statistics, ref info) passes through unchanged.
    """
    from .decimate import decimate_pair

    if max_samples is None or max_samples <= 0:
        return

    for traj in context.get("trajectories", []) or []:
        traj["act_time"], traj["act_values"] = decimate_pair(
            traj.get("act_time"), traj.get("act_values"), max_samples
        )
        traj["ref_time"], traj["ref_values"] = decimate_pair(
            traj.get("ref_time"), traj.get("ref_values"), max_samples
        )
        # A2 — decimate overlay trajectories too. The embedded HTML already
        # enforces a tight payload budget; overlays must share that cap
        # rather than piggybacking a parallel full-resolution copy.
        for ov in traj.get("overlays", []) or []:
            ov["time"], ov["values"] = decimate_pair(
                ov.get("time"), ov.get("values"), max_samples
            )

    # Stage-2 per-variable dict carries its own trajectory copy + overlays;
    # decimate in place so the Stage-2 UI path shares the same budget.
    for var_data in (context.get("variables_by_name") or {}).values():
        traj = var_data.get("trajectory") or {}
        if "act_time" in traj:
            traj["act_time"], traj["act_values"] = decimate_pair(
                traj.get("act_time"), traj.get("act_values"), max_samples
            )
            traj["ref_time"], traj["ref_values"] = decimate_pair(
                traj.get("ref_time"), traj.get("ref_values"), max_samples
            )
        for ov in var_data.get("overlays", []) or []:
            ov["time"], ov["values"] = decimate_pair(
                ov.get("time"), ov.get("values"), max_samples
            )

    for traj in context.get("diag_trajectories", []) or []:
        traj["act_time"], traj["act_values"] = decimate_pair(
            traj.get("act_time"), traj.get("act_values"), max_samples
        )
        traj["ref_time"], traj["ref_values"] = decimate_pair(
            traj.get("ref_time"), traj.get("ref_values"), max_samples
        )

    for traj in context.get("nobaseline_trajectories", []) or []:
        traj["time"], traj["values"] = decimate_pair(
            traj.get("time"), traj.get("values"), max_samples
        )


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


def _build_per_test_args(comp, results, test_lookup, store, config, manifest_meta_by_model, artifact_files=()):
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

    if not comp.sim_success:
        status_text, status_class = "SIM_FAIL", "sim-fail"
    elif test and getattr(test, "simulate_only", False):
        # simulate_only tests pass iff the simulation ran — presence of a
        # baseline is irrelevant. Keep the PASS/FAIL signal instead of
        # falling through to NO_REF.
        status_text, status_class = (
            ("PASS", "pass") if comp.passed else ("FAIL", "fail")
        )
    elif not comp.has_reference:
        status_text, status_class = "NO_REF", "no-ref"
    elif comp.passed:
        status_text, status_class = "PASS", "pass"
    else:
        status_text, status_class = "FAIL", "fail"

    n_vars = len(comp.variables) if comp.variables else 0
    n_vars_passed = sum(1 for v in comp.variables if v.passed) if comp.variables else 0
    worst_nrmse = max((v.nrmse for v in comp.variables), default=None)

    test_dir = config.work_dir / test_key if test_key else None

    ref_file = None
    test_id = store.index.get_id(model_id)
    if test_id:
        from ..storage.reference_store import RefIndex
        ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

    if comp.test_id:
        report_id = f"ref_{comp.test_id}"
    elif test_key:
        report_id = test_key
    else:
        report_id = _sanitize_filename(model_id)

    # Pull phase-timing breakdown out of stats if present (runner stashes it)
    cur_stats = result.statistics if result and result.statistics else {}
    timing = cur_stats.get("timing") if isinstance(cur_stats.get("timing"), dict) else {}

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
        max_embedded_samples=args.get("max_embedded_samples", 2000),
        overlays=args.get("overlays"),
        status_text=args.get("status_text"),
        status_class=args.get("status_class"),
        ref_id=args.get("ref_id"),
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
        "n_warnings": args["n_warnings"],
        "last_run_at": args["last_run_at"],
        "translation_wall": args.get("translation_wall"),
        "sim_wall": args.get("sim_wall"),
        "total_wall": args.get("total_wall"),
        "report_path": f'{args["report_id"]}/interactive.html' if html_path else None,
        "_render_elapsed": render_elapsed,
    }


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
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    report_wall_start = _time.monotonic()
    report_dir = config.work_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    test_lookup = {t.model_id: t for t in tests}

    # Resolve the backend's artifact file list once up front — class attribute,
    # no runner instantiation needed (avoids triggering the fmpy import if the
    # extra isn't installed).
    from ..simulators import get_runner_class
    try:
        artifact_files = tuple(get_runner_class(config).artifact_files)
    except ValueError:
        artifact_files = ()

    # Cache test_key + last_run_at lookup once instead of scanning manifests per test
    manifest_meta_by_model: dict[str, dict] = {}
    manifest_paths = sorted(config.work_dir.glob("batch_manifest.json"))
    if manifest_paths:
        from ..simulators import BatchManifest
        for mp in manifest_paths:
            bm = BatchManifest.load(mp)
            for tk, entry in bm.manifest.items():
                manifest_meta_by_model.setdefault(entry["model_id"], {
                    "test_key": tk,
                    "last_run_at": entry.get("last_run_at"),
                })

    # Resolve per-test args sequentially (cheap dict/store lookups), then
    # render reports in parallel
    work = [
        _build_per_test_args(comp, results, test_lookup, store, config, manifest_meta_by_model, artifact_files)
        for comp in comparisons
    ]

    n_workers = max(1, min(getattr(config, "parallel", 1) or 1, len(work)))
    index_tests: list[dict] = []
    total_reports = len(work)

    from ..simulators.base import _print_progress
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
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="report") as pool:
            futures = {pool.submit(_render_one_test, args, report_dir): args["model_id"] for args in work}
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
