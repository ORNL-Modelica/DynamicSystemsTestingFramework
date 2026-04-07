"""Verify migration from buildingspy .txt to JSON reference format.

Compares original .txt files against migrated .json files.
Produces:
  1. A summary report (CSV + text) with error statistics for all tests
  2. Per-variable comparison plots (optional, requires matplotlib)
"""

import json
import re
import sys
from pathlib import Path

import numpy as np


def _parse_txt_from_string(text: str) -> dict | None:
    """Parse a buildingspy-format .txt reference from string content."""
    result = {"time": None, "variables": {}}

    m = re.search(r'time=\[([^\]]+)\]', text)
    if m:
        result["time"] = _parse_float_array(m.group(1))

    for m in re.finditer(r'unitTests\.x\[(\d+)\]=\[([^\]]+)\]', text):
        idx = int(m.group(1))
        values = _parse_float_array(m.group(2))
        result["variables"][idx] = values

    if not result["variables"]:
        return None
    return result


def _parse_float_array(text: str) -> list[float]:
    """Parse comma-separated float array."""
    parts = text.split(",")
    values = []
    for p in parts:
        p = p.strip().rstrip(".")
        if p:
            try:
                values.append(float(p))
            except ValueError:
                values.append(0.0)
    return values


def _load_txt_files(source_dir: Path) -> dict[str, str]:
    """Load all .txt files from a directory."""
    txt_files = {}
    for f in sorted(source_dir.glob("*.txt")):
        try:
            txt_files[f.name] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return txt_files


def _load_json_files(json_dir: Path) -> dict[str, dict]:
    """Load all migrated JSON files."""
    result = {}
    for f in sorted(json_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result[f.name] = data
        except (json.JSONDecodeError, OSError):
            pass
    return result


def _build_txt_to_json_map(
    txt_files: dict[str, str],
    json_files: dict[str, dict],
) -> list[tuple[str, str, str]]:
    """Map .txt filenames to .json filenames via model_id.

    Returns list of (txt_filename, json_filename, model_id).
    """
    # Build model_id -> json_filename map
    json_by_model = {}
    for jname, jdata in json_files.items():
        model_id = jdata.get("model_id", "")
        json_by_model[model_id] = jname

    pairs = []
    for txt_name in sorted(txt_files.keys()):
        stem = Path(txt_name).stem
        # Find matching model_id: replace dots with underscores and compare
        matched = False
        for model_id, jname in json_by_model.items():
            if model_id.replace(".", "_") == stem:
                pairs.append((txt_name, jname, model_id))
                matched = True
                break
        if not matched:
            pairs.append((txt_name, None, stem))

    return pairs


def verify_migration(
    source_dir: Path,
    json_dir: Path,
    output_dir: Path,
    plot: bool = False,
):
    """Compare old .txt reference files against migrated .json files.

    Args:
        source_dir: Directory containing old buildingspy .txt files
        json_dir: Directory containing migrated .json files
        output_dir: Where to write summary and optional plots
        plot: Generate per-variable comparison plots (requires matplotlib)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading old .txt files from {source_dir}...")
    txt_files = _load_txt_files(source_dir)
    print(f"  Found {len(txt_files)} .txt files")

    print(f"Loading migrated .json files from {json_dir}...")
    json_files = _load_json_files(json_dir)
    print(f"  Found {len(json_files)} .json files")

    print("Mapping .txt -> .json...")
    pairs = _build_txt_to_json_map(txt_files, json_files)
    print(f"  Matched {sum(1 for _, j, _ in pairs if j)} pairs")
    unmatched = [(t, m) for t, j, m in pairs if j is None]
    if unmatched:
        print(f"  WARNING: {len(unmatched)} .txt files with no matching .json:")
        for t, m in unmatched[:5]:
            print(f"    {t}")

    # Compute errors and generate plots
    summary_rows = []
    all_errors = []

    for txt_name, json_name, model_id in pairs:
        if json_name is None:
            summary_rows.append({
                "model_id": model_id,
                "status": "NO_MATCH",
                "n_vars": 0,
                "min_error": None,
                "max_error": None,
                "avg_error": None,
            })
            continue

        # Parse old .txt
        txt_data = _parse_txt_from_string(txt_files[txt_name])
        if txt_data is None:
            summary_rows.append({
                "model_id": model_id,
                "status": "TXT_PARSE_FAIL",
                "n_vars": 0,
                "min_error": None,
                "max_error": None,
                "avg_error": None,
            })
            continue

        # Load new .json
        json_data = json_files[json_name]
        json_vars = {v["index"]: v for v in json_data.get("variables", [])}
        json_shared_time = json_data.get("time")

        time_old = txt_data.get("time")
        if time_old is None or len(time_old) < 2:
            summary_rows.append({
                "model_id": model_id,
                "status": "NO_TIME",
                "n_vars": 0,
                "min_error": None,
                "max_error": None,
                "avg_error": None,
            })
            continue

        t_start, t_end = time_old[0], time_old[-1]
        var_errors = []

        for idx in sorted(txt_data["variables"].keys()):
            old_values = np.array(txt_data["variables"][idx])
            n_old = len(old_values)
            old_time = np.linspace(t_start, t_end, n_old)

            if idx not in json_vars:
                var_errors.append({
                    "index": idx,
                    "status": "MISSING_IN_JSON",
                    "max_abs_error": None,
                    "rms_error": None,
                })
                continue

            jvar = json_vars[idx]
            new_time = np.array(json_shared_time if json_shared_time is not None else jvar["time"])
            new_values = np.array(jvar["values"])

            # Interpolate old values to new time grid for comparison
            if len(old_time) == len(new_time) and np.allclose(old_time, new_time, atol=1e-12):
                errors = np.abs(old_values - new_values)
            else:
                old_interp = np.interp(new_time, old_time, old_values)
                errors = np.abs(old_interp - new_values)

            max_err = float(np.max(errors))
            min_err = float(np.min(errors))
            avg_err = float(np.mean(errors))
            rms_err = float(np.sqrt(np.mean(errors**2)))

            var_errors.append({
                "index": idx,
                "status": "OK",
                "min_abs_error": min_err,
                "max_abs_error": max_err,
                "avg_abs_error": avg_err,
                "rms_error": rms_err,
                "n_points_old": n_old,
                "n_points_new": len(new_values),
            })

            all_errors.append(max_err)

        # Aggregate per-test errors
        ok_vars = [v for v in var_errors if v["status"] == "OK"]
        if ok_vars:
            test_min = min(v["min_abs_error"] for v in ok_vars)
            test_max = max(v["max_abs_error"] for v in ok_vars)
            test_avg = np.mean([v["avg_abs_error"] for v in ok_vars])
            status = "PASS" if test_max < 1e-10 else "WARN"
        else:
            test_min = test_max = test_avg = None
            status = "NO_VARS"

        summary_rows.append({
            "model_id": model_id,
            "status": status,
            "n_vars": len(ok_vars),
            "min_error": test_min,
            "max_error": test_max,
            "avg_error": float(test_avg) if test_avg is not None else None,
            "var_details": var_errors,
        })

        # Generate plots
        if plot and ok_vars and _has_matplotlib():
            _generate_plots(
                model_id, txt_data, json_vars, json_shared_time,
                t_start, t_end, output_dir,
            )

    # Write summary
    _write_summary(summary_rows, all_errors, output_dir)


def _has_matplotlib() -> bool:
    try:
        import matplotlib
        return True
    except ImportError:
        return False


def _generate_plots(
    model_id: str,
    txt_data: dict,
    json_vars: dict,
    json_shared_time: list | None,
    t_start: float,
    t_end: float,
    output_dir: Path,
):
    """Generate comparison plots for each variable in a test."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plots_dir = output_dir / "plots"
    plots_dir.mkdir(exist_ok=True)

    safe_name = model_id.replace(".", "_")

    for idx in sorted(txt_data["variables"].keys()):
        if idx not in json_vars:
            continue

        old_values = np.array(txt_data["variables"][idx])
        n_old = len(old_values)
        old_time = np.linspace(t_start, t_end, n_old)

        jvar = json_vars[idx]
        new_time = np.array(json_shared_time if json_shared_time is not None else jvar["time"])
        new_values = np.array(jvar["values"])

        old_interp = np.interp(new_time, old_time, old_values)
        errors = old_interp - new_values

        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

        ax1 = axes[0]
        ax1.plot(old_time, old_values, "b-", linewidth=1.5, label="Original (.txt)", alpha=0.8)
        ax1.plot(new_time, new_values, "r--", linewidth=1.0, label="Migrated (.json)", alpha=0.8)
        ax1.set_ylabel("Value")
        ax1.set_title(f"{model_id} — x[{idx}]")
        ax1.legend(loc="best", fontsize=8)
        ax1.grid(True, alpha=0.3)

        ax2 = axes[1]
        ax2.plot(new_time, errors, "k-", linewidth=0.8)
        ax2.axhline(y=0, color="gray", linewidth=0.5)
        max_err = np.max(np.abs(errors))
        ax2.set_ylabel("Error (old - new)")
        ax2.set_xlabel("Time")
        ax2.set_title(f"Max |error| = {max_err:.2e}")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = plots_dir / f"{safe_name}_x{idx}.png"
        fig.savefig(plot_path, dpi=100)
        plt.close(fig)


def _write_summary(
    rows: list[dict],
    all_errors: list[float],
    output_dir: Path,
):
    """Write summary report as both CSV and text."""
    import csv

    # CSV summary
    csv_path = output_dir / "migration_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model_id", "status", "n_vars",
            "min_abs_error", "max_abs_error", "avg_abs_error",
        ])
        for r in sorted(rows, key=lambda x: x["model_id"]):
            writer.writerow([
                r["model_id"],
                r["status"],
                r["n_vars"],
                f"{r['min_error']:.2e}" if r["min_error"] is not None else "",
                f"{r['max_error']:.2e}" if r["max_error"] is not None else "",
                f"{r['avg_error']:.2e}" if r["avg_error"] is not None else "",
            ])
    print(f"\nCSV summary written to: {csv_path}")

    # Text summary
    txt_path = output_dir / "migration_summary.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        n_pass = sum(1 for r in rows if r["status"] == "PASS")
        n_warn = sum(1 for r in rows if r["status"] == "WARN")
        n_no_match = sum(1 for r in rows if r["status"] == "NO_MATCH")
        n_fail = sum(1 for r in rows if r["status"] in ("TXT_PARSE_FAIL", "NO_TIME", "NO_VARS"))
        n_total = len(rows)

        f.write("=" * 100 + "\n")
        f.write("MIGRATION VERIFICATION SUMMARY\n")
        f.write("=" * 100 + "\n\n")
        f.write(f"Total tests:     {n_total}\n")
        f.write(f"  PASS:          {n_pass}  (max error < 1e-10)\n")
        f.write(f"  WARN:          {n_warn}  (max error >= 1e-10)\n")
        f.write(f"  NO_MATCH:      {n_no_match}  (no corresponding .json found)\n")
        f.write(f"  OTHER:         {n_fail}  (parse failures, etc.)\n\n")

        if all_errors:
            f.write(f"Overall error statistics (across all variables of all tests):\n")
            f.write(f"  Min error:     {min(all_errors):.6e}\n")
            f.write(f"  Max error:     {max(all_errors):.6e}\n")
            f.write(f"  Mean error:    {np.mean(all_errors):.6e}\n")
            f.write(f"  Median error:  {np.median(all_errors):.6e}\n\n")

        f.write(f"{'Model ID':<90} {'Status':>6} {'Vars':>4} {'Max Error':>12}\n")
        f.write("-" * 115 + "\n")
        for r in sorted(rows, key=lambda x: x["model_id"]):
            max_e = f"{r['max_error']:.2e}" if r["max_error"] is not None else "N/A"
            f.write(f"{r['model_id']:<90} {r['status']:>6} {r['n_vars']:>4} {max_e:>12}\n")

        warnings = [r for r in rows if r["status"] == "WARN"]
        if warnings:
            f.write(f"\n{'=' * 100}\n")
            f.write("TESTS WITH NON-ZERO ERROR (status=WARN):\n")
            f.write(f"{'=' * 100}\n\n")
            for r in sorted(warnings, key=lambda x: -(x["max_error"] or 0)):
                f.write(f"  {r['model_id']}\n")
                f.write(f"    max_error={r['max_error']:.6e}  avg_error={r['avg_error']:.6e}\n")
                if "var_details" in r:
                    for v in r["var_details"]:
                        if v["status"] == "OK" and v["max_abs_error"] > 1e-10:
                            f.write(f"    x[{v['index']}]: max={v['max_abs_error']:.6e} "
                                    f"rms={v['rms_error']:.6e} "
                                    f"pts={v['n_points_old']}->{v['n_points_new']}\n")

    print(f"Text summary written to: {txt_path}")

    # Print to console
    print(f"\n{'=' * 80}")
    print("MIGRATION VERIFICATION RESULTS")
    print(f"{'=' * 80}")
    print(f"  Total: {n_total}  |  PASS: {n_pass}  |  WARN: {n_warn}  |  NO_MATCH: {n_no_match}  |  OTHER: {n_fail}")
    if all_errors:
        print(f"  Overall max error: {max(all_errors):.6e}")
        print(f"  Overall avg error: {np.mean(all_errors):.6e}")
    if n_warn > 0:
        print(f"\n  Tests with non-zero error:")
        for r in sorted(warnings, key=lambda x: -(x["max_error"] or 0))[:10]:
            print(f"    {r['model_id']:80s}  max={r['max_error']:.2e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify migration from buildingspy .txt to JSON references"
    )
    parser.add_argument(
        "source",
        help="Path to directory containing old .txt reference files",
    )
    parser.add_argument(
        "json_dir",
        help="Path to directory containing migrated .json files",
    )
    parser.add_argument(
        "--output", "-o",
        default="migration_verify",
        help="Output directory for summary and plots (default: ./migration_verify)",
    )
    parser.add_argument(
        "--plots",
        action="store_true",
        help="Generate per-variable comparison plots (requires matplotlib)",
    )

    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"ERROR: {source} does not exist")
        sys.exit(1)

    json_dir = Path(args.json_dir)
    if not json_dir.exists():
        print(f"ERROR: {json_dir} does not exist")
        sys.exit(1)

    output_dir = Path(args.output)

    print(f"Source .txt dir:  {source}")
    print(f"JSON directory:   {json_dir}")
    print(f"Output:           {output_dir}")
    print()

    verify_migration(
        source_dir=source,
        json_dir=json_dir,
        output_dir=output_dir,
        plot=args.plots,
    )


if __name__ == "__main__":
    main()
