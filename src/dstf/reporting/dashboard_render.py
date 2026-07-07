"""Unified dashboard renderer.

One Jinja template (`dashboard.html`) feeds both live progress
during a run and the post-comparison report. The template renders
gracefully whether comparison data is present (post-run) or absent
(during run); JS-fetch on the client side keeps the page fresh
without full-page reloads.

Live mode is triggered every state change by ProgressReporter.
Final mode is triggered after comparison from cli.cmd_run /
cmd_compare; it strips the JS-fetch poll and adds per-test report
links + post-run columns (worst_nrmse, warnings, translate/sim/total
wall times).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _read_status(work_dir: Path) -> dict | None:
    p = work_dir / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_comparison_sidecar(work_dir: Path, report_dir: str) -> dict | None:
    """Read per-test comparison_data.json if present.

    Per-test reports are at <work_dir>/reports/<report_dir>/comparison_data.json
    (matches generate_comparison_plots layout).
    """
    p = work_dir / "reports" / report_dir / "comparison_data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _enrich_row_from_comparison(
    row: dict,
    comp: dict,
    snapshot_start_wall: float | None,
) -> bool:
    """Copy post-run fields from a per-test comparison_data.json into a row.

    The sidecar puts row-summary fields under a `summary` block alongside
    the per-variable context. Fall back to top-level for backward compat
    with sidecars written before the summary block was added.

    Two defensive guards prevent stale sidecars from overriding fresh
    verdicts (D91 — see ``cli._wipe_stale_state_for_scope``):

    1. **model_id mismatch.** If the sidecar's ``summary.model_id`` doesn't
       match the row's ``model_id``, the sidecar belongs to a different
       test (bookkeeping drift between batch_manifest and reports/) — skip
       enrichment entirely.
    2. **Stale timestamp.** If the sidecar's ``summary.written_at`` is
       older than the snapshot's ``start_wall`` (i.e., written before this
       run started), the sidecar is from a prior run that the wipe failed
       to clear (or from a code path that bypasses the wipe). Skip
       enrichment so the live-mode verdict (from the fresh sim) wins.

    Returns True when enrichment was applied; False when guarded off.
    Both guards print a one-line warning to stderr so silent overrides
    are visible.
    """
    summary = comp.get("summary", comp)

    sidecar_model_id = summary.get("model_id")
    expected_model_id = row.get("model_id")
    if sidecar_model_id and expected_model_id and sidecar_model_id != expected_model_id:
        print(
            f"# WARN: sidecar model_id mismatch (expected {expected_model_id!r}, "
            f"sidecar has {sidecar_model_id!r}); ignoring enrichment.",
            file=sys.stderr,
        )
        return False

    written_at = summary.get("written_at")
    if (
        snapshot_start_wall is not None
        and written_at is not None
        and written_at < snapshot_start_wall
    ):
        print(
            f"# WARN: stale sidecar for {expected_model_id!r} "
            f"(written_at={written_at} < start_wall={snapshot_start_wall}); "
            f"ignoring enrichment.",
            file=sys.stderr,
        )
        return False

    for key in (
        "worst_nrmse",
        "n_vars",
        "n_vars_passed",
        "n_warnings",
        "translation_wall",
        "sim_wall",
        "total_wall",
        "ref_id",
        "ref_file",
        "field_sources",
        # Comparison-derived status overrides live-mode status when present.
        # The compare phase distinguishes pass / fail / sim-fail / no-ref;
        # live mode only knows passed / failed / timed_out.
        "status_text",
        "status_class",
    ):
        if key in summary:
            row[key] = summary[key]
    return True


# Live-mode TestStatus.status (queued/running/passed/failed/timed_out) →
# (uppercase pretty status_text, filter-vocab status_class). Filter-vocab
# matches the buttons in dashboard.html (pass/fail/sim-fail/no-ref/queued/
# running/timed-out) so a single filter applies live and final.
_LIVE_STATUS_MAP = {
    "queued": ("QUEUED", "queued"),
    "running": ("RUNNING", "running"),
    "passed": ("PASS", "pass"),
    "failed": ("FAIL", "fail"),
    "timed_out": ("TIMED OUT", "timed-out"),
}


def build_rerun_prefix(config) -> str:
    """Build the CLI prefix the dashboard uses in its rerun-command builder.

    Produces e.g. `dstf --config "/abs/path/testing.json" run` so the
    dashboard JS can append ` --filter ... --merge --report` and the
    user can paste the result into any terminal regardless of CWD.
    Prefers --config when available; otherwise falls back to
    --source-path (+ optional --reference-root).
    """

    def q(p) -> str:
        s = str(p)
        return f'"{s}"' if " " in s else s

    if getattr(config, "config_file", None):
        return f"dstf --config {q(config.config_file)} run"

    parts = ["dstf"]
    if getattr(config, "source_path", None):
        parts += ["--source-path", q(config.source_path)]
    if getattr(config, "reference_root", None):
        parts += ["--reference-root", q(config.reference_root)]
    parts.append("run")
    return " ".join(parts)


def format_run_metadata(meta: dict | None) -> dict | None:
    """Turn the raw ``status.json`` ``metadata`` block into a template-friendly
    provenance dict, or ``None`` when no metadata was recorded.

    Adds a display ``label`` (the configured simulator, falling back to the
    backend family) and a human-readable ``generated_str`` from the epoch
    timestamp. Kept separate from ``build_dashboard_context`` so the per-test
    interactive report can reuse the exact same shaping.
    """
    if not meta:
        return None
    label = meta.get("simulator") or meta.get("backend") or "unknown"
    generated_at = meta.get("generated_at")
    generated_str = None
    if generated_at:
        try:
            generated_str = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(generated_at)
            )
        except Exception:  # pragma: no cover — defensive against bad epoch
            generated_str = None
    return {
        "label": label,
        "backend": meta.get("backend"),
        "os": meta.get("os"),
        "tool_version": meta.get("tool_version"),
        "dstf_version": meta.get("dstf_version"),
        "generated_str": generated_str,
    }


def build_dashboard_context(
    work_dir: Path, mode: str, rerun_prefix: str | None = None
) -> dict:
    """Build the Jinja context for dashboard.html.

    mode='live' — auto_refresh=True, post-run fields stay None
    mode='final' — auto_refresh=False, fields enriched from sidecars

    The same template renders both; JS reads `DASHBOARD_MODE` to
    decide whether to start the fetch loop.
    """
    snapshot = _read_status(work_dir) or {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }

    # Wall-clock anchor for the stale-sidecar guard. Sidecars written
    # before this run's start are ignored by `_enrich_row_from_comparison`
    # so a prior run's verdict can't override the current run's sim result.
    # Falls back to None when status.json predates the field — guard
    # silently passes through (legacy sidecars never get filtered).
    snapshot_start_wall = snapshot.get("start_wall")

    rows = []
    for t in snapshot.get("tests", []):
        raw_status = t.get("status", "queued")
        status_text, status_class = _LIVE_STATUS_MAP.get(
            raw_status,
            (raw_status.upper(), raw_status.replace("_", "-")),
        )
        # Live-mode ref_id can be derived from report_dir when it follows
        # the "ref_NNNN" naming (set by cmd_run pre-populating
        # runner.ref_id_map). For tests without a baseline yet, report_dir
        # is the live test_key and ref_id stays None.
        live_ref_id = None
        rd = t.get("report_dir") or ""
        if rd.startswith("ref_"):
            live_ref_id = rd
        row = {
            "test_key": t.get("test_key"),
            "model_id": t.get("model_id"),
            "status_text": status_text,
            "status_class": status_class,
            "elapsed": t.get("elapsed"),
            "started_wall": t.get(
                "started_wall"
            ),  # epoch — JS uses for live "running for Ns"
            "worker_id": t.get("worker_id"),
            "report_dir": t.get("report_dir") or t.get("test_key"),
            "phase": t.get("phase"),
            "detail": t.get("detail"),
            "ref_id": live_ref_id,
            "ref_file": None,  # Populated from sidecar in final mode
            # Post-run fields default to None; populated below in final mode
            "worst_nrmse": None,
            "n_vars": None,
            "n_vars_passed": None,
            "n_warnings": None,
            "translation_wall": None,
            "sim_wall": None,
            "total_wall": None,
            "field_sources": t.get("field_sources") or {},
        }
        if mode == "final" and row["report_dir"]:
            comp = _read_comparison_sidecar(work_dir, row["report_dir"])
            if comp:
                _enrich_row_from_comparison(row, comp, snapshot_start_wall)
        rows.append(row)

    # rerun_prefix precedence: explicit kwarg > snapshot's prefix > "dstf run"
    # fallback. The kwarg path lets the CLI override the snapshot prefix even
    # when status.json was written by a prior run with a different config
    # (e.g. dstf compare reading work_dir from a different invocation).
    prefix = rerun_prefix or snapshot.get("rerun_prefix") or "dstf run"

    return {
        "mode": mode,
        "auto_refresh": mode == "live",
        "title": "Test progress" if mode == "live" else "Test report",
        "total": snapshot.get("total", 0),
        "elapsed": snapshot.get("elapsed", 0.0),
        "eta_seconds": snapshot.get("eta_seconds"),
        "counts": snapshot.get("counts", {}),
        "tests": rows,
        "rerun_prefix": prefix,
        "updated_at": snapshot.get("updated_at", time.time()),
        # Wall-clock anchor for the dashboard's live elapsed clock. JS reads
        # SNAPSHOT_WALL (= updated_at, when the snapshot was written) and
        # SNAPSHOT_ELAPSED (= elapsed at that moment) to compute a live-
        # ticking elapsed = SNAPSHOT_ELAPSED + (Date.now()/1000 - SNAPSHOT_WALL).
        # This works between meta-refresh ticks (when the snapshot is stale)
        # and right after a refresh (when it's fresh).
        "start_wall": snapshot.get("start_wall"),
        # Run provenance (backend/simulator/version/os) for the banner. None
        # for snapshots written before this field existed.
        "run_metadata": format_run_metadata(snapshot.get("metadata")),
    }


def _atomic_write(path: Path, text: str) -> None:
    """Atomic file write — Windows file-locking workaround.

    Same retry logic as ProgressReporter._atomic_write — uses unique
    tmp name so concurrent writers can't share the same tmp path.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    last_err: OSError | None = None
    for delay in (0, 0.05, 0.1, 0.2, 0.5):
        if delay:
            time.sleep(delay)
        try:
            tmp.replace(path)
            return
        except OSError as e:
            last_err = e
    try:
        tmp.unlink()
    except OSError:
        pass
    if not (path.parent / path.name).exists():
        raise last_err


def _render(work_dir: Path, mode: str, rerun_prefix: str | None = None) -> None:
    ctx = build_dashboard_context(work_dir, mode=mode, rerun_prefix=rerun_prefix)
    template = _env.get_template("dashboard.html")
    html = template.render(**ctx)
    _atomic_write(work_dir / "dashboard.html", html)


def render_live(work_dir: Path, rerun_prefix: str | None = None) -> None:
    """Render dashboard.html in live mode (auto-refreshes via meta tag)."""
    _render(work_dir, mode="live", rerun_prefix=rerun_prefix)


def render_final(work_dir: Path, rerun_prefix: str | None = None) -> None:
    """Render dashboard.html in final mode (refresh stripped, sidecars merged)."""
    _render(work_dir, mode="final", rerun_prefix=rerun_prefix)
