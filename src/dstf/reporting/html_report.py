"""HTML report generation."""

import html
from pathlib import Path

from ..comparison.comparator import TestComparison


def generate_html_report(
    comparisons: list[TestComparison],
    output_path: Path,
) -> None:
    """Generate an HTML report from test comparisons."""
    n_passed = sum(1 for c in comparisons if c.passed)
    n_failed = sum(1 for c in comparisons if not c.passed)
    n_no_ref = sum(1 for c in comparisons if not c.has_reference)
    n_warned = sum(1 for c in comparisons if c.warnings)
    total = len(comparisons)

    rows = []
    for comp in comparisons:
        if not comp.sim_success:
            status = '<span style="color:red">SIM_FAIL</span>'
        elif not comp.has_reference:
            status = '<span style="color:orange">NO_REF</span>'
        elif comp.passed:
            status = '<span style="color:green">PASS</span>'
        else:
            status = '<span style="color:red">FAIL</span>'

        if comp.warnings:
            status += ' <span style="color:orange" title="Structural changes detected">&#9888;</span>'

        details = ""
        if not comp.passed and comp.variables:
            detail_rows = []
            for var in comp.variables:
                if not var.passed:
                    name = html.escape(var.name or f"x[{var.index}]")
                    detail_rows.append(
                        f"<tr><td>{name}</td>"
                        f"<td>{var.nrmse:.4e}</td>"
                        f"<td>{var.signal_range:.4e}</td>"
                        f"<td>{var.max_abs_error:.4e}</td>"
                        f"<td>{var.max_abs_error_time:g}</td>"
                        f"<td>{var.reference_final:.6e}</td>"
                        f"<td>{var.actual_final:.6e}</td></tr>"
                    )
            if detail_rows:
                details = (
                    '<table class="details"><tr><th>Variable</th>'
                    "<th>NRMSE</th><th>Range</th><th>Max Abs Err</th>"
                    "<th>At Time</th><th>Ref Final</th><th>Act Final</th></tr>"
                    + "".join(detail_rows)
                    + "</table>"
                )
        elif comp.error_message:
            details = f"<em>{html.escape(comp.error_message)}</em>"

        # Structural warnings
        if comp.warnings:
            warn_rows = "".join(
                f"<tr><td>{html.escape(w.field)}</td>"
                f"<td>{html.escape(w.reference_value)}</td>"
                f"<td>{html.escape(w.current_value)}</td></tr>"
                for w in comp.warnings
            )
            details += (
                '<table class="warnings"><tr><th>Field</th>'
                "<th>Reference</th><th>Current</th></tr>" + warn_rows + "</table>"
            )

        model = html.escape(comp.model_id)
        rows.append(f"<tr><td>{status}</td><td>{model}</td><td>{details}</td></tr>")

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>DSTF Test Report</title>
<style>
body {{ font-family: monospace; margin: 2em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
th {{ background: #f0f0f0; }}
.summary {{ margin-bottom: 1em; }}
.details, .warnings {{ font-size: 0.85em; margin: 4px 0; }}
.details th {{ background: #f8f8f8; }}
.warnings th {{ background: #fff3cd; }}
</style>
</head>
<body>
<h1>DSTF Test Report</h1>
<div class="summary">
<strong>{n_passed}</strong> passed,
<strong>{n_failed}</strong> failed,
<strong>{n_no_ref}</strong> no baseline,
<strong>{n_warned}</strong> warnings,
<strong>{total}</strong> total
</div>
<table>
<tr><th>Status</th><th>Model</th><th>Details</th></tr>
{"".join(rows)}
</table>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
