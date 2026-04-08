"""Generate comparison plots and HTML viewer for interactive review."""

import html as html_mod
import logging
import platform
import re
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

from ..comparison.comparator import VariableComparison

logger = logging.getLogger(__name__)


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


def _compare_row(label: str, sim_val: str, ref_val: str, highlight: bool = False) -> str:
    """Generate a table row comparing simulation vs reference values."""
    style = ' style="background:#fff3cd"' if highlight else ""
    return (
        f"<tr{style}>"
        f"<td>{html_mod.escape(label)}</td>"
        f"<td>{html_mod.escape(sim_val)}</td>"
        f"<td>{html_mod.escape(ref_val)}</td>"
        f"</tr>"
    )


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
        ax_rel.axhline(y=tolerance, color="gray", linestyle="--", alpha=0.5,
                       label=f"tolerance ({tolerance:.0e})")
        ax_rel.axvline(ref_time[max_idx], color="#9C27B0", alpha=0.3, linestyle=":")
        ax_rel.set_ylabel("NRMSE Error")
        ax_rel.set_xlabel("Time")
        ax_rel.legend(loc="best", fontsize=8)
        ax_rel.grid(True, alpha=0.3)
        ax_rel.ticklabel_format(axis="y", style="sci", scilimits=(-2, 2))

        passed = True  # Will be overridden by caller if vc available
    else:
        axes[1].text(0.5, 0.5, "No reference data", transform=axes[1].transAxes,
                    ha="center", va="center", color="gray")
        axes[1].set_ylabel("Abs Error")
        axes[2].text(0.5, 0.5, "No reference data", transform=axes[2].transAxes,
                    ha="center", va="center", color="gray")
        axes[2].set_ylabel("Rel Error")
        axes[2].set_xlabel("Time")
        passed = True

    return fig, None, None  # Status set by caller


def generate_comparison_plots(
    model_id: str,
    ref_data: Optional[dict],
    result,
    comparisons: list[VariableComparison],
    plot_dir: Path,
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

    # Generate HTML viewer
    html_path = plot_dir / "comparison.html"
    cur_stats = result.statistics if result else None
    _generate_html_viewer(
        html_path, model_id, png_files, comparisons, ref_data, cur_stats,
        diag_png_files=diag_png_files,
    )

    return html_path


def _generate_html_viewer(
    html_path: Path,
    model_id: str,
    png_files: list[tuple[str, VariableComparison]],
    comparisons: list[VariableComparison],
    ref_data: Optional[dict] = None,
    cur_stats: Optional[dict] = None,
    diag_png_files: Optional[list[tuple[str, str]]] = None,
) -> None:
    """Generate an HTML page showing all plots, stats, and metadata tables."""

    # --- Simulation parameters: reference vs current ---
    ref_sim = ref_data.get("simulation", {}) if ref_data else {}
    if cur_stats is None:
        cur_stats = {}

    metadata_html = ""
    meta_rows = []
    param_fields = [
        ("test_id", "Test ID"),
        ("last_updated", "Reference Last Updated"),
        ("n_vars", "Tracked Variables"),
    ]
    for key, label in param_fields:
        ref_val = ref_data.get(key, "") if ref_data else ""
        cur_val = len(comparisons) if key == "n_vars" else ""
        if key == "test_id":
            cur_val = ""  # No current equivalent
        if key == "last_updated":
            cur_val = ""  # Only reference has this
        meta_rows.append(_compare_row(label, str(ref_val) if ref_val != "" else "", str(cur_val) if cur_val != "" else ""))

    sim_fields = [
        ("stop_time", "Stop Time"),
        ("tolerance", "Tolerance"),
        ("method", "Method"),
        ("number_of_intervals", "Number of Intervals"),
        ("output_interval", "Output Interval"),
    ]
    for key, label in sim_fields:
        ref_val = ref_sim.get(key, "")
        cur_val = ""  # Current sim params aren't stored on TestResult, but we could get from test
        meta_rows.append(_compare_row(label, str(ref_val) if ref_val is not None else "", str(cur_val) if cur_val else ""))

    if any(r for r in meta_rows):
        metadata_html = (
            '<h2>Simulation Parameters</h2>'
            '<table class="meta-table">'
            '<tr><th>Field</th><th>Simulation</th><th>Reference</th></tr>'
            + "".join(meta_rows) +
            '</table>'
        )

    # --- Statistics tables: Translation then Simulation ---
    ref_stats = ref_data.get("statistics", {}) if ref_data else {}

    def _build_stats_table(title: str, category: str) -> str:
        """Build an HTML stats table for a single category."""
        ref_cat = ref_stats.get(category, {}) if isinstance(ref_stats.get(category), dict) else {}
        cur_cat = cur_stats.get(category, {}) if isinstance(cur_stats.get(category), dict) else {}
        all_keys = sorted(set(ref_cat.keys()) | set(cur_cat.keys()))
        if not all_keys:
            return ""
        rows = []
        for key in all_keys:
            label = key.replace("_", " ").title()
            ref_val = ref_cat.get(key, "")
            cur_val = cur_cat.get(key, "")
            if isinstance(ref_val, list):
                ref_val = ", ".join(str(v) for v in ref_val)
            if isinstance(cur_val, list):
                cur_val = ", ".join(str(v) for v in cur_val)
            changed = str(ref_val) != str(cur_val) and str(ref_val) and str(cur_val)
            rows.append(_compare_row(label, str(cur_val) if cur_val != "" else "",
                                     str(ref_val) if ref_val != "" else "", highlight=changed))
        return (
            f'<h2>{title}</h2>'
            '<table class="meta-table">'
            '<tr><th>Metric</th><th>Current</th><th>Reference</th></tr>'
            + "".join(rows) +
            '</table>'
        )

    # Top-level scalar stats (CPUtime, EventCounter) go in simulation table
    # Merge them into simulation category for display
    sim_display = {}
    for k, v in ref_stats.items():
        if not isinstance(v, dict):
            sim_display[k] = v
    for k, v in cur_stats.items():
        if not isinstance(v, dict):
            sim_display.setdefault(k, v)
    # Temporarily inject into cur/ref for the table builder
    ref_sim_merged = {**ref_stats.get("simulation", {})}
    cur_sim_merged = {**cur_stats.get("simulation", {})}
    for k, v in ref_stats.items():
        if not isinstance(v, dict):
            ref_sim_merged[k] = v
    for k, v in cur_stats.items():
        if not isinstance(v, dict):
            cur_sim_merged[k] = v

    # Build tables
    translation_html = _build_stats_table("Translation Statistics", "translation")

    # For simulation, use the merged dicts directly
    sim_keys = sorted(set(ref_sim_merged.keys()) | set(cur_sim_merged.keys()))
    sim_rows = []
    for key in sim_keys:
        label = key.replace("_", " ").title()
        ref_val = ref_sim_merged.get(key, "")
        cur_val = cur_sim_merged.get(key, "")
        if isinstance(ref_val, list):
            ref_val = ", ".join(str(v) for v in ref_val)
        if isinstance(cur_val, list):
            cur_val = ", ".join(str(v) for v in cur_val)
        changed = str(ref_val) != str(cur_val) and str(ref_val) and str(cur_val)
        sim_rows.append(_compare_row(label, str(cur_val) if cur_val != "" else "",
                                     str(ref_val) if ref_val != "" else "", highlight=changed))
    simulation_html = ""
    if sim_rows:
        simulation_html = (
            '<h2>Simulation Statistics</h2>'
            '<table class="meta-table">'
            '<tr><th>Metric</th><th>Current</th><th>Reference</th></tr>'
            + "".join(sim_rows) +
            '</table>'
        )

    stats_html = translation_html + simulation_html

    # --- Variable comparison table ---
    table_rows = []
    for vc in comparisons:
        status = '<span style="color:#4CAF50">PASS</span>' if vc.passed else '<span style="color:#f44336">FAIL</span>'
        name = html_mod.escape(vc.name or f"x[{vc.index}]")
        const_tag = " (const)" if vc.is_constant else ""
        table_rows.append(
            f"<tr>"
            f"<td>{status}</td>"
            f"<td>{name}</td>"
            f"<td>{vc.nrmse:.4e}</td>"
            f"<td>{vc.rmse:.4e}</td>"
            f"<td>{vc.signal_range:.4e}{const_tag}</td>"
            f"<td>{vc.max_abs_error:.4e}</td>"
            f"<td>{vc.max_abs_error_time:g}</td>"
            f"<td>{vc.reference_final:.6e}</td>"
            f"<td>{vc.actual_final:.6e}</td>"
            f"</tr>"
        )

    # Diagnostic plot sections (shown first)
    diag_sections = []
    if diag_png_files:
        for png_name, diag_name in diag_png_files:
            name = html_mod.escape(diag_name)
            diag_sections.append(
                f'<div class="plot-section">'
                f'<h3><span style="color:#607D8B">INFO</span> {name}</h3>'
                f'<img src="{png_name}" alt="{name}" style="max-width:100%">'
                f'</div>'
            )

    # Compared variable plot sections
    plot_sections = []
    for png_name, vc in png_files:
        name = html_mod.escape(vc.name or f"x[{vc.index}]")
        status = "PASS" if vc.passed else "FAIL"
        color = "#4CAF50" if vc.passed else "#f44336"
        plot_sections.append(
            f'<div class="plot-section">'
            f'<h3><span style="color:{color}">{status}</span> {name}</h3>'
            f'<img src="{png_name}" alt="{name}" style="max-width:100%">'
            f'</div>'
        )

    n_passed = sum(1 for vc in comparisons if vc.passed)
    n_total = len(comparisons)

    diag_html = ""
    if diag_sections:
        diag_html = "<h2>Diagnostics</h2>\n" + "\n".join(diag_sections)

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Comparison: {html_mod.escape(model_id)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 2em; background: #fafafa; }}
h1 {{ color: #333; }}
h2 {{ color: #555; border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }}
h3 {{ color: #444; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; background: white; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 0.9em; }}
th {{ background: #f5f5f5; font-weight: 600; }}
tr:hover {{ background: #f9f9f9; }}
.summary {{ margin-bottom: 1.5em; font-size: 1.1em; }}
.plot-section {{ margin-bottom: 2em; background: white; padding: 1em; border: 1px solid #eee; border-radius: 4px; }}
.plot-section img {{ display: block; margin: 0 auto; }}
.meta-table {{ width: auto; min-width: 400px; }}
.meta-table td:first-child {{ font-weight: 500; white-space: nowrap; }}
</style>
</head>
<body>
<h1>{html_mod.escape(model_id)}</h1>
<div class="summary">
<strong>{n_passed}</strong> / <strong>{n_total}</strong> variables passed
</div>

{metadata_html}
{stats_html}

{diag_html}

<h2>Variable Comparison</h2>
<table>
<tr>
<th>Status</th><th>Variable</th><th>NRMSE</th><th>RMSE</th>
<th>Range</th><th>Max Abs Err</th><th>At Time</th>
<th>Ref Final</th><th>Act Final</th>
</tr>
{"".join(table_rows)}
</table>

<h2>Trajectory Comparisons</h2>
{"".join(plot_sections)}

</body>
</html>"""

    html_path.write_text(page, encoding="utf-8")


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
