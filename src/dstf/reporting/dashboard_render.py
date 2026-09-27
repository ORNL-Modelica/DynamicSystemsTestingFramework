"""Unified dashboard renderer.

One Jinja template (`dashboard.html`) feeds both live progress
during a run and the post-comparison report. The template renders
gracefully whether comparison data is present (post-run) or absent
(during run).

Live mode re-renders the whole page to disk on every state change
(driven by ProgressReporter) and emits `<meta http-equiv="refresh"
content="5">`, so the browser full-reloads every 5s. JS-fetch was
tried first and is silently blocked on `file://` URLs — see the
comment in `templates/dashboard.js`. Between reloads a 1s JS timer
ticks the elapsed counters so they don't look frozen.

Final mode is rendered by the command that ends a run (cli.cmd_run /
cmd_compare, the tool's Rerun); it drops the refresh meta tag (the page
becomes static) and adds each test's verdict and numbers (from the
comparisons the caller holds), per-test report links + post-run columns
(worst score, warnings, translate/sim/total wall times) from the
sidecars.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..backends.base import kept_run_result
from ..backends.common.result_variables import run_timing
from ..backends.progress import read_status
from ..comparison.types import TestComparison
from .rows import COLUMNS, TestRow, enrich_row, row_from_status, summary_fields
from .status import STATUS_CLASSES, STATUS_LABELS, STATUS_ORDER
from .theme import css_variables

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)
_env.globals["dstf_css"] = css_variables()
# A sort value as a data-sort-* attribute: None is the empty string, which
# dashboard.js sorts last in both directions.
_env.filters["sortval"] = lambda v: "" if v is None else v


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


def build_rerun_prefix(config) -> str:
    """Build the CLI prefix the dashboard uses in its rerun-command builder.

    Produces e.g. `dstf --config "/abs/path/testing.json"` — the global
    flags only, NO subcommand: the dashboard's command menu picks the
    subcommand (`run`, `compare`, `compare --accept`,
    `run --rerun failed,sim-failed`) and appends ` --filter ...`, so the
    user can paste the result into any terminal regardless of CWD.
    Prefers --config when available; otherwise falls back to
    --source-path (+ optional --reference-root).
    """

    def q(p) -> str:
        s = str(p)
        return f'"{s}"' if " " in s else s

    if getattr(config, "config_file", None):
        return f"dstf --config {q(config.config_file)}"

    parts = ["dstf"]
    if getattr(config, "source_path", None):
        parts += ["--source-path", q(config.source_path)]
    if getattr(config, "reference_root", None):
        parts += ["--reference-root", q(config.reference_root)]
    return " ".join(parts)


def format_run_metadata(meta: dict | None) -> dict | None:
    """Turn the raw ``status.json`` ``metadata`` block into a template-friendly
    provenance dict, or ``None`` when no metadata was recorded.

    Adds a display ``label`` (the configured backend name, or "unknown"
    when a stale stamp does not record one) and a human-readable ``generated_str`` from the epoch
    timestamp. Kept separate from ``build_dashboard_context`` so the per-test
    interactive report can reuse the exact same shaping.
    """
    if not meta:
        return None
    label = meta.get("backend_name") or "unknown"
    generated_at = meta.get("generated_at")
    generated_str = None
    if generated_at:
        try:
            generated_str = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(generated_at)
            )
        except Exception:  # pragma: no cover — defensive against bad epoch
            generated_str = None
    # Surface the Modelica Standard Library version prominently (the second
    # drift suspect); keep the full library_versions dict for completeness.
    lib_versions = meta.get("library_versions") or {}
    msl = lib_versions.get("Modelica") if isinstance(lib_versions, dict) else None
    return {
        "label": label,
        "backend": meta.get("backend"),
        "os": meta.get("os"),
        "tool_version": meta.get("tool_version"),
        "dstf_version": meta.get("dstf_version"),
        "generated_str": generated_str,
        "msl": msl,
        "library_versions": lib_versions or None,
    }


def _with_kept_timing(work_dir: Path, row: TestRow) -> TestRow:
    """*row* with the phase walls its run kept in
    ``<work_dir>/<test_key>/run_stats.json``, through the same
    :func:`run_timing` a result is read with; *row* unchanged when the run
    kept none (a run from before the file existed)."""
    assert row.test_key
    kept = kept_run_result(work_dir / row.test_key, row.model_id, row.test_key)
    if kept is None:
        return row
    timing = run_timing(kept)
    return replace(
        row,
        translation_wall=timing.get("translation_wall"),
        sim_wall=timing.get("sim_wall"),
        total_wall=timing.get("total_wall"),
    )


def _with_verdict(row: TestRow, comp: TestComparison) -> TestRow:
    """*row* with *comp*'s verdict and score numbers, through
    ``summary_fields`` — the computation the sidecar is written with.

    Applied AFTER a sidecar's enrichment: the comparison held in hand is
    this invocation's verdict, and a sidecar from an earlier ``--report``
    (fresh against this run's start, because nothing re-ran) says what the
    spec said then. The verdict, its numbers and the baseline it was scored
    against are the comparison's; what only the sidecar or the run's kept
    file knows (the phase walls) and what only the live row knows (its
    provenance) survive.

    The ref_id is the comparison's when it names one — ``compare --backend
    <other>`` on another backend's work dir scores against a baseline the
    report_dir and the sidecar do not name. The sidecar's reference link
    survives only when its ref_id is the comparison's: otherwise it opens
    the other baseline. A comparison with no stored baseline names none,
    and the row keeps whatever it had.
    """
    summary = summary_fields(comp)
    ref_id, ref_file = row.ref_id, row.ref_file
    if comp.test_id:
        ref_id = f"ref_{comp.test_id}"
        if ref_id != row.ref_id:
            ref_file = None
    return replace(
        row,
        status=summary["status"],
        worst_score=summary["worst_nrmse"],
        n_unscored=summary["n_unscored"],
        n_vars=summary["n_vars"],
        n_vars_passed=summary["n_vars_passed"],
        n_warnings=summary["n_warnings"],
        ref_id=ref_id,
        ref_file=ref_file,
    )


def _manifest_rows(work_dir: Path) -> dict[str, TestRow]:
    """``{model_id: row}`` for every test ``batch_manifest.json`` keys, in
    its order: the work-dir key and the baseline the run named, and the
    report dir a run gives such a row (``ref_NNNN``, else the key). Read as
    data — never quarantined, as ``BatchManifest.load`` does a corrupt one:
    a renderer moves no file. A model under two keys takes the last, as
    ``last_test_keys`` does."""
    try:
        entries = json.loads(
            (work_dir / "batch_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    rows: dict[str, TestRow] = {}
    for key, entry in entries.items() if isinstance(entries, dict) else ():
        model_id = entry.get("model_id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str):
            continue
        ref_id = entry.get("ref_id")
        ref_id = ref_id if isinstance(ref_id, str) else None
        rows[model_id] = TestRow(
            model_id=model_id, test_key=key, ref_id=ref_id, report_dir=ref_id or key
        )
    return rows


def build_dashboard_context(
    work_dir: Path,
    mode: str,
    rerun_prefix: str | None = None,
    comparisons: Iterable[TestComparison] = (),
) -> dict:
    """Build the Jinja context for dashboard.html.

    mode='live' — auto_refresh=True, post-run fields stay None
    mode='final' — auto_refresh=False, a fresh sidecar (when --report wrote
    one) enriches each row — a row without one takes the phase walls its
    run kept (``run_stats.json``) — then its comparison (when the caller
    holds one) sets its verdict and numbers. The rows are status.json's —
    the last run's scope, which each run rewrites — then every other test
    the caller holds a comparison for, in ``batch_manifest.json``'s order,
    then by model id (D136's amendment: ``compare`` after a ``--filter``
    run, a ``--merge`` and the tool's Rerun list every test they
    scored). Such a row takes its key and baseline from the manifest.

    The same template renders both; JS reads `DASHBOARD_MODE` to
    decide whether to start the fetch loop.
    """
    snapshot = read_status(work_dir) or {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }

    # Wall-clock anchor for the stale-sidecar guard. Sidecars written
    # before this run's start are ignored by `rows.enrich_row` so a prior
    # run's verdict can't override the current run's sim result.
    # Falls back to None when status.json predates the field — guard
    # silently passes through (legacy sidecars never get filtered).
    snapshot_start_wall = snapshot.get("start_wall")

    # `dstf run` / `compare` without --report write no sidecars; the
    # comparisons they hold are what gives such a page its verdicts.
    by_model = {c.model_id: c for c in comparisons} if mode == "final" else {}
    base = [row_from_status(t) for t in snapshot.get("tests", [])]
    listed = {r.model_id for r in base}
    rest = set(by_model) - listed
    if rest:
        manifest = _manifest_rows(work_dir)
        base += [row for m, row in manifest.items() if m in rest]
        base += [TestRow(model_id=m) for m in sorted(rest - set(manifest))]
    rows = []
    for row in base:
        before = row
        if mode == "final" and row.report_dir:
            sidecar = _read_comparison_sidecar(work_dir, row.report_dir)
            if sidecar:
                # Sidecars written before the summary block carry the
                # fields at the top level.
                row = enrich_row(
                    row, sidecar.get("summary", sidecar), start_wall=snapshot_start_wall
                )
        # enrich_row hands back the same row when a guard refuses the
        # sidecar. With no fresh sidecar (a run without --report, or a stale
        # one), the walls are the ones the run kept beside the result
        # (storage.run_stats).
        if mode == "final" and row is before and row.test_key:
            row = _with_kept_timing(work_dir, row)
        comp = by_model.get(row.model_id)
        if comp is not None:
            row = _with_verdict(row, comp)
        rows.append(row)

    # rerun_prefix precedence: explicit kwarg > snapshot's prefix > "dstf"
    # fallback. The kwarg path lets the CLI override the snapshot prefix even
    # when status.json was written by a prior run with a different config
    # (e.g. dstf compare reading work_dir from a different invocation).
    prefix = rerun_prefix or snapshot.get("rerun_prefix") or "dstf"

    return {
        "mode": mode,
        "auto_refresh": mode == "live",
        "title": "Test progress" if mode == "live" else "Test report",
        # A final page counts the rows it lists (status.json's scope and
        # every test scored); a live one, the run's.
        "total": len(rows) if mode == "final" else snapshot.get("total", 0),
        "elapsed": snapshot.get("elapsed", 0.0),
        "eta_seconds": snapshot.get("eta_seconds"),
        "counts": snapshot.get("counts", {}),
        "tests": rows,
        "columns": COLUMNS,
        # The count pills, in STATUS_ORDER: [status class, the word a
        # person reads]. The class is the pill's key and the row's
        # data-status.
        "statuses": [[STATUS_CLASSES[s], STATUS_LABELS[s]] for s in STATUS_ORDER],
        "rerun_prefix": prefix,
        "updated_at": snapshot.get("updated_at", time.time()),
        # Wall-clock anchor for the dashboard's live elapsed clock. JS reads
        # SNAPSHOT_WALL (= updated_at, when the snapshot was written) and
        # SNAPSHOT_ELAPSED (= elapsed at that moment) to compute a live-
        # ticking elapsed = SNAPSHOT_ELAPSED + (Date.now()/1000 - SNAPSHOT_WALL).
        # This works between meta-refresh ticks (when the snapshot is stale)
        # and right after a refresh (when it's fresh).
        "start_wall": snapshot.get("start_wall"),
        # Identity of the report this page IS: work dir + the wall clock
        # the run started at. The dashboard's localStorage state is keyed
        # by location.pathname, which two successive runs into the same
        # work_dir share — so a selection made in run N was restored into
        # run N+1's page and silently joined the rerun command the user
        # built there. JS scopes the selection to this key and drops a
        # selection stamped with any other.
        "report_key": (
            f"{work_dir.resolve().as_posix()}@"
            f"{snapshot.get('start_wall') or snapshot.get('updated_at') or 0}"
        ),
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


def _render(
    work_dir: Path,
    mode: str,
    rerun_prefix: str | None = None,
    output_path: Path | None = None,
    comparisons: Iterable[TestComparison] = (),
) -> None:
    ctx = build_dashboard_context(
        work_dir, mode=mode, rerun_prefix=rerun_prefix, comparisons=comparisons
    )
    template = _env.get_template("dashboard.html")
    html = template.render(**ctx)
    dest = output_path or (work_dir / "dashboard.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, html)


def render_live(work_dir: Path, rerun_prefix: str | None = None) -> None:
    """Render dashboard.html in live mode (auto-refreshes via meta tag)."""
    _render(work_dir, mode="live", rerun_prefix=rerun_prefix)


def render_final(
    work_dir: Path,
    rerun_prefix: str | None = None,
    comparisons: Iterable[TestComparison] = (),
) -> None:
    """Render dashboard.html in final mode (refresh stripped, the
    comparisons' verdicts and the sidecars merged)."""
    _render(work_dir, mode="final", rerun_prefix=rerun_prefix, comparisons=comparisons)


def render_final_to(
    work_dir: Path,
    output_path: Path,
    rerun_prefix: str | None = None,
    comparisons: Iterable[TestComparison] = (),
) -> None:
    """Render the final-mode dashboard page to a caller-chosen path.

    Backs ``--report-format html``: the same page ``render_final`` writes,
    just to ``output_path``. The page is self-contained (dashboard.js is
    ``{% include %}``d), but its per-test links are relative to ``work_dir``
    — they only resolve when the output lands inside the work dir.
    """
    _render(
        work_dir,
        mode="final",
        rerun_prefix=rerun_prefix,
        output_path=output_path,
        comparisons=comparisons,
    )
