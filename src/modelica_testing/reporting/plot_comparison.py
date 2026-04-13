"""Generate comparison plots and HTML viewer for interactive review."""

import html as html_mod
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


def _build_stats_section(title: str, ref_cat: dict, cur_cat: dict) -> Optional[dict]:
    """Build a statistics section dict for the template."""
    all_keys = sorted(set(ref_cat.keys()) | set(cur_cat.keys()))
    if not all_keys:
        return None
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


def _build_template_context(
    model_id: str,
    png_files: list[tuple[str, VariableComparison]],
    comparisons: list[VariableComparison],
    ref_data: Optional[dict],
    cur_stats: Optional[dict],
    diag_png_files: Optional[list[tuple[str, str]]],
    nobaseline_png_files: Optional[list[tuple[str, str]]],
    test_dir: Optional[Path],
    test_model=None,
    result=None,
    ref_file: Optional[Path] = None,
    warnings: Optional[list] = None,
) -> dict:
    """Build the full template context dict from comparison data."""
    if cur_stats is None:
        cur_stats = {}
    ref_stats = ref_data.get("statistics", {}) if ref_data else {}
    ref_sim = ref_data.get("simulation", {}) if ref_data else {}

    # --- Reference info rows ---
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

    # Add link to reference file
    if ref_data and ref_data.get("test_id"):
        from ..storage.reference_store import RefIndex
        ref_filename = RefIndex.ref_filename(ref_data["test_id"])
        row = {"label": "Reference File", "value": ref_filename}
        if ref_file and ref_file.exists():
            row["link"] = ref_file.resolve().as_uri()
        ref_info.append(row)

    # Add test directory key (e.g., test_0051)
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

    # --- Simulation parameters (current vs reference) ---
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

    # --- Statistics sections (auto-detected) ---
    statistics_sections = []

    # Translation
    ref_translation = ref_stats.get("translation", {})
    cur_translation = cur_stats.get("translation", {})
    if isinstance(ref_translation, dict) or isinstance(cur_translation, dict):
        section = _build_stats_section(
            "Translation Statistics",
            ref_translation if isinstance(ref_translation, dict) else {},
            cur_translation if isinstance(cur_translation, dict) else {},
        )
        if section:
            statistics_sections.append(section)

    # Simulation + top-level scalar stats (CPUtime, EventCounter)
    ref_sim_stats = {**(ref_stats.get("simulation", {}) if isinstance(ref_stats.get("simulation"), dict) else {})}
    cur_sim_stats = {**(cur_stats.get("simulation", {}) if isinstance(cur_stats.get("simulation"), dict) else {})}
    for k, v in ref_stats.items():
        if not isinstance(v, dict):
            ref_sim_stats[k] = v
    for k, v in cur_stats.items():
        if not isinstance(v, dict):
            cur_sim_stats[k] = v
    section = _build_stats_section("Simulation Statistics", ref_sim_stats, cur_sim_stats)
    if section:
        statistics_sections.append(section)

    # --- Variable comparison data ---
    variables = []
    for vc in comparisons:
        mode = vc.mode or "nrmse"
        if mode == "tube" and vc.tube_points_inside is not None:
            criterion = (
                f"{vc.tube_points_inside * 100:.1f}% inside tube "
                f"→ {'PASS' if vc.passed else 'FAIL'} (requires 100%)"
            )
        elif mode == "final_only":
            criterion = (
                f"Final value error {vc.max_abs_error:.3e} vs tolerance "
                f"{vc.tolerance_used:.3e} → {'PASS' if vc.passed else 'FAIL'}"
            )
        else:
            criterion = (
                f"NRMSE {vc.nrmse:.3e} vs tolerance {vc.tolerance_used:.3e} "
                f"→ {'PASS' if vc.passed else 'FAIL'}"
            )
        variables.append({
            "name": vc.name or f"x[{vc.index}]",
            "passed": bool(vc.passed),
            "nrmse": float(vc.nrmse),
            "rmse": float(vc.rmse),
            "signal_range": float(vc.signal_range),
            "max_abs_error": float(vc.max_abs_error),
            "max_abs_error_time": float(vc.max_abs_error_time),
            "reference_final": float(vc.reference_final),
            "actual_final": float(vc.actual_final),
            "is_constant": bool(vc.is_constant),
            "tolerance_used": float(vc.tolerance_used),
            "mode": mode,
            "criterion": criterion,
            "tube_points_inside": float(vc.tube_points_inside) if vc.tube_points_inside is not None else None,
            "tube_worst_violation": float(vc.tube_worst_violation) if vc.tube_worst_violation is not None else None,
            "tube_worst_violation_time": float(vc.tube_worst_violation_time) if vc.tube_worst_violation_time is not None else None,
        })

    # --- Trajectory data for interactive plots ---
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
            traj = {
                "index": vc.index,
                "name": vc.name or f"x[{vc.index}]",
                "act_time": act_var.time.tolist() if act_var else [],
                "act_values": act_var.values.tolist() if act_var else [],
                "ref_time": ref_time_list,
                "ref_values": ref_var["values"] if ref_var else [],
            }
            trajectories.append(traj)

    # Diagnostic trajectories
    diag_trajectories = []
    if result and result.diagnostics:
        ref_diags_by_name = {}
        if ref_data:
            for rd in ref_data.get("diagnostics", []):
                ref_diags_by_name[rd["name"]] = rd

        for diag in result.diagnostics:
            ref_diag = ref_diags_by_name.get(diag.name)
            diag_trajectories.append({
                "name": diag.name,
                "act_time": diag.time.tolist(),
                "act_values": diag.values.tolist(),
                "ref_time": ref_time_list,
                "ref_values": ref_diag["values"] if ref_diag else [],
            })

    # No-baseline trajectories
    nobaseline_trajectories = []
    if not comparisons and result and result.variables:
        for var in result.variables:
            nobaseline_trajectories.append({
                "index": var.index,
                "name": var.name or f"x[{var.index}]",
                "time": var.time.tolist(),
                "values": var.values.tolist(),
            })

    # --- Plot references ---
    diagnostic_plots = [
        {"png": png_name, "name": name}
        for png_name, name in (diag_png_files or [])
    ]

    compared_plots = [
        {"png": png_name, "name": vc.name or f"x[{vc.index}]", "passed": vc.passed}
        for png_name, vc in png_files
    ]

    nobaseline_plots = [
        {"png": png_name, "name": name}
        for png_name, name in (nobaseline_png_files or [])
    ]

    # --- Artifact links ---
    artifacts = []
    if test_dir and test_dir.exists():
        artifact_files = [
            ("dslog.txt", "Simulation log"),
            ("translation_log.txt", "Translation log"),
            ("dsin.txt", "Simulation input"),
            ("dsfinal.txt", "Final values"),
            ("simulate.mos", "Simulation script"),
            ("dsres.mat", "Result file"),
        ]
        for fname, label in artifact_files:
            fpath = test_dir / fname
            if fpath.exists():
                artifacts.append({"uri": fpath.resolve().as_uri(), "label": label})

    n_passed = sum(1 for vc in comparisons if vc.passed)
    n_nobaseline = len(nobaseline_plots)
    sim_failed = len(comparisons) == 0 and n_nobaseline == 0

    # --- Key stats for top-level summary ---
    # Pull out the most important metrics from both current and reference
    def _get_stat(source: dict, category: str, key: str):
        cat = source.get(category, {})
        if isinstance(cat, dict):
            return cat.get(key)
        return None

    def _get_scalar(source: dict, key: str):
        val = source.get(key)
        return val if not isinstance(val, dict) else None

    key_stats = []
    worst_nrmse = max((vc.nrmse for vc in comparisons), default=None)
    if worst_nrmse is not None:
        key_stats.append({"label": "Worst NRMSE", "current": f"{worst_nrmse:.4e}", "reference": ""})

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

    warning_rows = []
    for w in warnings or []:
        warning_rows.append({
            "field": w.field,
            "reference": str(w.reference_value),
            "current": str(w.current_value),
        })

    return {
        "model_id": model_id,
        "n_passed": n_passed,
        "sim_failed": sim_failed,
        "warnings": warning_rows,
        "key_stats": key_stats,
        "ref_info": ref_info,
        "sim_params": sim_params,
        "statistics_sections": statistics_sections,
        "variables": variables,
        "diagnostic_plots": diagnostic_plots,
        "compared_plots": compared_plots,
        "nobaseline_plots": nobaseline_plots,
        "artifacts": artifacts,
        "trajectories": trajectories,
        "diag_trajectories": diag_trajectories,
        "nobaseline_trajectories": nobaseline_trajectories,
    }


def _plot_variable(
    plt,
    act_time, act_values, name, index,
    ref_time=None, ref_values=None,
    is_diagnostic=False,
    tolerance=1e-4,
) -> tuple:
    """Generate a comparison plot for one variable.

    For diagnostic variables: single panel (trajectory only, no error panels).
    For compared variables: 3 panels (trajectory, abs error, normalized error).

    Returns (fig, status_text, status_color).
    """
    has_ref = ref_time is not None and ref_values is not None

    if is_diagnostic:
        fig, ax = plt.subplots(1, 1, figsize=(12, 4))
        ax.plot(act_time, act_values, label="Actual", color="#2196F3", linewidth=1)
        if has_ref:
            ax.plot(ref_time, ref_values, label="Reference", color="#FF9800",
                    linewidth=1, linestyle="--")
        ax.set_ylabel("Value")
        ax.set_xlabel("Time")
        ax.set_title(f"{name} (diagnostic)")
        ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)
        return fig, "INFO", "#607D8B"

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1, 1]})

    ax_traj = axes[0]
    ax_traj.plot(act_time, act_values, label="Actual", color="#2196F3", linewidth=1)
    if has_ref:
        ax_traj.plot(ref_time, ref_values, label="Reference", color="#FF9800",
                    linewidth=1, linestyle="--")
    ax_traj.set_ylabel("Value")
    ax_traj.set_title(f"{name}")
    ax_traj.legend(loc="best", fontsize=9)
    ax_traj.grid(True, alpha=0.3)

    if has_ref:
        actual_interp = np.interp(ref_time, act_time, act_values)
        abs_error = np.abs(actual_interp - ref_values)

        signal_range = float(np.max(ref_values) - np.min(ref_values))
        if signal_range > 100 * np.finfo(np.float64).eps:
            rel_error = abs_error / signal_range
        else:
            rel_error = abs_error

        ax_abs = axes[1]
        ax_abs.plot(ref_time, abs_error, color="#f44336", linewidth=0.8)
        max_idx = int(np.argmax(abs_error))
        ax_abs.axvline(ref_time[max_idx], color="#f44336", alpha=0.3, linestyle=":")
        ax_abs.set_ylabel("Abs Error")
        ax_abs.grid(True, alpha=0.3)
        ax_abs.ticklabel_format(axis="y", style="sci", scilimits=(-2, 2))

        ax_rel = axes[2]
        ax_rel.plot(ref_time, rel_error, color="#9C27B0", linewidth=0.8)
        avg_err = float(np.mean(rel_error))
        ymax = max(float(np.max(rel_error)), tolerance) * 1.1
        ax_rel.axhspan(tolerance, ymax, color="#f44336", alpha=0.08)
        ax_rel.axhline(y=tolerance, color="gray", linestyle="--", alpha=0.6,
                       label=f"tolerance ({tolerance:.2e})")
        ax_rel.axhline(y=avg_err, color="#4CAF50", linestyle="-.", alpha=0.6,
                       linewidth=0.8, label=f"avg ({avg_err:.2e})")
        ax_rel.axvline(ref_time[max_idx], color="#9C27B0", alpha=0.3, linestyle=":")
        ax_rel.set_ylabel("NRMSE Error")
        ax_rel.set_xlabel("Time")
        ax_rel.legend(loc="best", fontsize=8)
        ax_rel.grid(True, alpha=0.3)
        ax_rel.ticklabel_format(axis="y", style="sci", scilimits=(-2, 2))
    else:
        axes[1].text(0.5, 0.5, "No reference data", transform=axes[1].transAxes,
                    ha="center", va="center", color="gray")
        axes[1].set_ylabel("Abs Error")
        axes[2].text(0.5, 0.5, "No reference data", transform=axes[2].transAxes,
                    ha="center", va="center", color="gray")
        axes[2].set_ylabel("Rel Error")
        axes[2].set_xlabel("Time")

    return fig, None, None  # Status set by caller


def generate_comparison_plots(
    model_id: str,
    ref_data: Optional[dict],
    result,
    comparisons: list[VariableComparison],
    plot_dir: Path,
    test_dir: Optional[Path] = None,
    test_model=None,
    spec_path: Optional[Path] = None,
    ref_file: Optional[Path] = None,
    warnings: Optional[list] = None,
) -> Optional[Path]:
    """Generate per-variable comparison PNGs and an HTML viewer.

    Returns the path to the HTML file, or None if matplotlib is unavailable.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed — install with: uv pip install matplotlib")
        return None

    if plot_dir.exists():
        shutil.rmtree(plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    # Get reference time and variables
    ref_time = None
    ref_vars = {}
    ref_diags = {}
    if ref_data:
        shared_time = ref_data.get("time")
        if shared_time is not None:
            ref_time = np.array(shared_time)
        for rv in ref_data.get("variables", []):
            ref_vars[rv["index"]] = rv
        for rd in ref_data.get("diagnostics", []):
            ref_diags[rd["name"]] = rd

    # --- Diagnostic variable plots (shown first) ---
    diag_png_files = []
    if result and result.diagnostics:
        for diag in result.diagnostics:
            safe_name = _sanitize_filename(diag.name)
            png_name = f"diag_{safe_name}.png"
            png_path = plot_dir / png_name

            ref_diag = ref_diags.get(diag.name)
            ref_d_values = np.array(ref_diag["values"]) if ref_diag else None

            fig, _, _ = _plot_variable(
                plt,
                diag.time, diag.values, diag.name, 0,
                ref_time=ref_time, ref_values=ref_d_values,
                is_diagnostic=True,
            )

            plt.tight_layout()
            fig.savefig(str(png_path), dpi=100, bbox_inches="tight")
            plt.close(fig)
            diag_png_files.append((png_name, diag.name))

    # --- Compared variable plots ---
    png_files = []
    for vc in comparisons:
        act_var = None
        if result and result.variables:
            for v in result.variables:
                if v.index == vc.index:
                    act_var = v
                    break

        if act_var is None:
            continue

        ref_var = ref_vars.get(vc.index)
        has_ref = ref_var is not None and ref_time is not None

        safe_name = _sanitize_filename(vc.name or f"x_{vc.index}")
        png_name = f"var_{vc.index:03d}_{safe_name}.png"
        png_path = plot_dir / png_name

        ref_v = np.array(ref_var["values"]) if has_ref else None

        fig, _, _ = _plot_variable(
            plt,
            act_var.time, act_var.values, vc.name or f"x[{vc.index}]", vc.index,
            ref_time=ref_time if has_ref else None,
            ref_values=ref_v,
        )

        # Add pass/fail annotation
        status_text = "PASS" if vc.passed else "FAIL"
        status_color = "#4CAF50" if vc.passed else "#f44336"
        ax_top = fig.axes[0]
        ax_top.annotate(
            status_text, xy=(0.98, 0.95), xycoords="axes fraction",
            fontsize=14, fontweight="bold", color=status_color,
            ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=status_color, alpha=0.8),
        )

        plt.tight_layout()
        fig.savefig(str(png_path), dpi=100, bbox_inches="tight")
        plt.close(fig)
        png_files.append((png_name, vc))

    # --- No-baseline variable plots (actual only, no comparison) ---
    nobaseline_png_files = []
    if not comparisons and result and result.variables:
        for var in result.variables:
            safe_name = _sanitize_filename(var.name or f"x_{var.index}")
            png_name = f"var_{var.index:03d}_{safe_name}.png"
            png_path = plot_dir / png_name

            fig, ax = plt.subplots(1, 1, figsize=(12, 4))
            ax.plot(var.time, var.values, label="Actual", color="#2196F3", linewidth=1)
            ax.set_ylabel("Value")
            ax.set_xlabel("Time")
            ax.set_title(var.name or f"x[{var.index}]")
            ax.legend(loc="best", fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.annotate(
                "NEW", xy=(0.98, 0.95), xycoords="axes fraction",
                fontsize=14, fontweight="bold", color="#FF9800",
                ha="right", va="top",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          edgecolor="#FF9800", alpha=0.8),
            )

            plt.tight_layout()
            fig.savefig(str(png_path), dpi=100, bbox_inches="tight")
            plt.close(fig)
            nobaseline_png_files.append((png_name, var.name or f"x[{var.index}]"))

    # Build template context and render
    cur_stats = result.statistics if result else None
    context = _build_template_context(
        model_id, png_files, comparisons, ref_data, cur_stats,
        diag_png_files, nobaseline_png_files, test_dir, test_model, result,
        ref_file=ref_file, warnings=warnings,
    )

    # Add spec path for "Save to Spec" functionality
    context["spec_path"] = str(spec_path.resolve()) if spec_path else ""

    # Write comparison_data.json alongside the HTML
    data_path = plot_dir / "comparison_data.json"
    data_path.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")

    # Render static HTML (matplotlib PNGs)
    html_path = plot_dir / "comparison.html"
    _render_template("comparison.html", context, html_path)

    # Render interactive HTML (Plotly, inline data)
    interactive_path = plot_dir / "interactive.html"
    _render_template("interactive.html", context, interactive_path)

    return interactive_path


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


def generate_report_suite(
    comparisons: list,
    results: dict,
    tests: list,
    store,
    config,
) -> Path:
    """Generate per-test comparison reports and an index page.

    Returns the path to the index HTML file.
    """
    report_dir = config.work_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    test_lookup = {t.model_id: t for t in tests}
    index_tests = []

    for comp in comparisons:
        model_id = comp.model_id
        result = results.get(model_id)
        test = test_lookup.get(model_id)
        ref_data = store.get_reference(model_id)

        # Determine status
        if not comp.sim_success:
            status_text = "SIM_FAIL"
            status_class = "sim-fail"
        elif not comp.has_reference:
            status_text = "NO_REF"
            status_class = "no-ref"
        elif comp.passed:
            status_text = "PASS"
            status_class = "pass"
        else:
            status_text = "FAIL"
            status_class = "fail"

        # Compute summary metrics
        worst_nrmse = None
        n_vars = len(comp.variables) if comp.variables else 0
        n_vars_passed = sum(1 for v in comp.variables if v.passed) if comp.variables else 0
        if comp.variables:
            worst_nrmse = max(v.nrmse for v in comp.variables)

        # Find test directory
        test_key = None
        manifest_paths = sorted(config.work_dir.glob("batch_manifest.json"))
        if manifest_paths:
            from ..simulators import BatchManifest
            for mp in manifest_paths:
                bm = BatchManifest.load(mp)
                for tk, entry in bm.manifest.items():
                    if entry["model_id"] == model_id:
                        test_key = tk
                        break
                if test_key:
                    break

        test_dir = config.work_dir / test_key if test_key else None

        # Resolve reference file path for clickable link in report
        ref_file = None
        test_id = store.index.get_id(model_id)
        if test_id:
            from ..storage.reference_store import RefIndex
            ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

        # Generate per-test report
        # Use ref_NNNN for tests with a reference, test_NNNN for no-baseline.
        # Both are short, avoiding Windows 260-char path limits.
        report_path = None
        if comp.test_id:
            report_id = f"ref_{comp.test_id}"
        elif test_key:
            report_id = test_key
        else:
            report_id = _sanitize_filename(model_id)
        plot_dir = report_dir / report_id

        html_path = generate_comparison_plots(
            model_id=model_id,
            ref_data=ref_data,
            result=result,
            comparisons=comp.variables,
            plot_dir=plot_dir,
            test_dir=test_dir,
            test_model=test,
            ref_file=ref_file,
            warnings=comp.warnings,
        )

        if html_path:
            report_path = f"{report_id}/interactive.html"

        ref_id = f"ref_{comp.test_id}" if comp.test_id else None

        index_tests.append({
            "model_id": model_id,
            "status_text": status_text,
            "status_class": status_class,
            "ref_id": ref_id,
            "test_key": test_key,
            "worst_nrmse": worst_nrmse,
            "n_vars": n_vars,
            "n_vars_passed": n_vars_passed,
            "n_warnings": len(comp.warnings) if comp.warnings else 0,
            "report_path": report_path,
        })

    # Build index context
    n_total = len(comparisons)
    index_context = {
        "title": "Test Report",
        "n_passed": sum(1 for t in index_tests if t["status_class"] == "pass"),
        "n_failed": sum(1 for t in index_tests if t["status_class"] == "fail"),
        "n_sim_failed": sum(1 for t in index_tests if t["status_class"] == "sim-fail"),
        "n_no_ref": sum(1 for t in index_tests if t["status_class"] == "no-ref"),
        "n_warnings": sum(1 for t in index_tests if t["n_warnings"] > 0),
        "n_total": n_total,
        "tests": index_tests,
    }

    index_path = report_dir / "index.html"
    _render_template("index.html", index_context, index_path)

    return index_path


def open_in_browser(path: Path) -> None:
    """Open a file in the system's default browser/viewer."""
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["start", "", str(path)], shell=True)
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        print(f"  Could not open browser. View manually: {path}")
