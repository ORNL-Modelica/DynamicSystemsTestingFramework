"""HTML report generation with optional trajectory plots."""

import html
from pathlib import Path

from ..comparison.comparator import TestComparison


def generate_html_report(
    comparisons: list[TestComparison],
    output_path: Path,
    include_plots: bool = False,
) -> None:
    """Generate an HTML report from test comparisons."""
    n_passed = sum(1 for c in comparisons if c.passed)
    n_failed = sum(1 for c in comparisons if not c.passed)
    total = len(comparisons)

    rows = []
    for comp in comparisons:
        if comp.passed:
            status = '<span style="color:green">PASS</span>'
        elif not comp.sim_success:
            status = '<span style="color:red">SIM_FAIL</span>'
        elif not comp.has_reference:
            status = '<span style="color:orange">NO_REF</span>'
        else:
            status = '<span style="color:red">FAIL</span>'

        details = ""
        if not comp.passed and comp.variables:
            detail_rows = []
            for var in comp.variables:
                if not var.passed:
                    expr = html.escape(var.expression or f"x[{var.index}]")
                    detail_rows.append(
                        f"<tr><td>x[{var.index}]</td><td>{expr}</td>"
                        f"<td>{var.abs_error_rms:.4e}</td>"
                        f"<td>{var.reference_final:.6e}</td>"
                        f"<td>{var.actual_final:.6e}</td></tr>"
                    )
            if detail_rows:
                details = (
                    '<table class="details"><tr><th>Var</th><th>Expr</th>'
                    "<th>RMS Error</th><th>Ref Final</th><th>Act Final</th></tr>"
                    + "".join(detail_rows) + "</table>"
                )
        elif comp.error_message:
            details = f"<em>{html.escape(comp.error_message)}</em>"

        model = html.escape(comp.model_id)
        rows.append(
            f"<tr><td>{status}</td><td>{model}</td><td>{details}</td></tr>"
        )

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>TRANSFORM Test Report</title>
<style>
body {{ font-family: monospace; margin: 2em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
th {{ background: #f0f0f0; }}
.summary {{ margin-bottom: 1em; }}
.details {{ font-size: 0.85em; margin: 4px 0; }}
.details th {{ background: #f8f8f8; }}
</style>
</head>
<body>
<h1>TRANSFORM Test Report</h1>
<div class="summary">
<strong>{n_passed}</strong> passed,
<strong>{n_failed}</strong> failed,
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
