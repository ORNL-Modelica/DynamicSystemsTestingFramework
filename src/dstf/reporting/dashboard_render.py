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
cmd_compare, the tool's queue as it drains); it drops the refresh meta tag (the page
becomes static) and adds each test's verdict and numbers (from the
comparisons the caller holds), per-test report links + post-run columns
(worst score, warnings, translate/sim/total wall times) from the
sidecars.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..backends.base import kept_run_result
from ..backends.common.result_variables import run_timing
from ..backends.progress import read_status
from ..backends.types import TestResult
from ..comparison.types import TestComparison
from .rows import (
    COLUMNS,
    FILTER_HINTS,
    TestRow,
    cpu_time,
    enrich_row,
    row_from_status,
    summary_fields,
)
from .status import (
    CANCELLED,
    PILL_STATUSES,
    STATUS_CLASSES,
    STATUS_LABELS,
    refusal,
    scored_status,
)
from .theme import css_variables

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)
# A column filter's placeholder and tooltip, by kind — the Suite table's too.
_env.globals["filter_hints"] = FILTER_HINTS
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


#: The flag each of `Config.overrides` is given back by, in the order a
#: built command writes them.
_OVERRIDE_FLAGS = (
    ("source_path", "--source-path"),
    ("reference_root", "--reference-root"),
    ("test_spec_file", "--test-spec"),
    ("work_dir", "--work-dir"),
    ("backend_name", "--backend"),
)

#: The flags `dstf author` reads: it opens the project a testing.json names,
#: with the work dir and backend it is given, and nothing else.
_AUTHOR_READS = frozenset({"--config", "--work-dir", "--backend"})


def build_rerun_flags(config) -> str:
    """The flags the dashboard's command builder writes after each command
    — `dstf <kind> <flags> --filter ...`, where the Command menu picks the
    kind (`run`, `compare`, `compare --accept`, `run --rerun
    failed,sim-failed`, `author`) — so that the command does the same thing
    to the same project the run did, pasted into a terminal in any
    directory.

    They name the testing.json the run read or wrote, and every place of
    the project the run did not take from it (`Config.overrides`: a flag it
    was given, or a default the current directory decided), each path
    absolute. They go after the command because `--work-dir` and
    `--backend` are offered there only. A Config built with no testing.json
    (`run_pipeline` called as a library) is named by its library and
    reference root instead, and then the dashboard offers no `author`
    (:func:`offers_author`)."""

    def q(value) -> str:
        s = str(value)
        return f'"{s}"' if " " in s else s

    names = set(config.overrides)
    parts = []
    if config.config_found is not None:
        parts += ["--config", q(Path(config.config_found).resolve())]
    else:
        names |= {"source_path", "reference_root"}
    for name, flag in _OVERRIDE_FLAGS:
        if name not in names:
            continue
        value = getattr(config, name)
        parts += [flag, q(value if name == "backend_name" else Path(value).resolve())]
    return " ".join(parts)


def offers_author(flags: str) -> bool:
    """Whether the dashboard can build an `author` command with ``flags``:
    they name a testing.json and nothing `author` does not read."""
    named = {t for t in shlex.split(flags, posix=False) if t.startswith("--")}
    return "--config" in named and named <= _AUTHOR_READS


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


#: A tool version string → its short form, first match wins: Dymola's
#: ``DymolaVersion()`` ("Dymola Version 2026x, 2025-10-10") and omc's
#: ``getVersion()`` ("OpenModelica 1.27.0~dev-...", "OpenModelica v1.27.0").
_SHORT_TOOL_VERSION = (
    (re.compile(r"^Dymola Version ([^,]+)"), "Dymola {}"),
    (re.compile(r"^OpenModelica v?(\d+(?:\.\d+)*)"), "OpenModelica {}"),
)


def short_tool_version(
    tool_version: str | None, backend_name: str | None
) -> str | None:
    """What a narrow place names the backend by: the tool and its release
    from *tool_version* ("Dymola 2026x", "OpenModelica 1.27.0"), or
    *backend_name* when the string is none of those — a version this does
    not know how to shorten reads as the backend's name, never cut."""
    for pattern, form in _SHORT_TOOL_VERSION:
        m = pattern.match(tool_version or "")
        if m:
            return form.format(m.group(1).strip())
    return backend_name


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

    The Message is the run's (status.json's ``detail``) — but for a test
    the comparison refused, whose reason is the one thing to read of it.

    The status is the verdict as a row reads it (``scored_status``): a row
    whose run was stopped keeps CANCELLED over the NOT RUN it scores.
    """
    summary = summary_fields(comp)
    ref_id, ref_file = row.ref_id, row.ref_file
    if comp.test_id:
        ref_id = f"ref_{comp.test_id}"
        if ref_id != row.ref_id:
            ref_file = None
    return replace(
        row,
        status=scored_status(summary["status"], cancelled=row.status == CANCELLED),
        worst_score=summary["worst_nrmse"],
        n_unscored=summary["n_unscored"],
        n_vars=summary["n_vars"],
        n_vars_passed=summary["n_vars_passed"],
        n_warnings=summary["n_warnings"],
        ref_id=ref_id,
        ref_file=ref_file,
        message=refusal(comp) or row.message,
    )


def _manifest_rows(work_dir: Path) -> dict[str, tuple[TestRow, float | None]]:
    """``{model_id: (row, last_run_at)}`` for every test
    ``batch_manifest.json`` keys, in its order: the work-dir key and the
    baseline the run named, and the report dir a run gives such a row
    (``ref_NNNN``, else the key); beside it the wall clock that test's last
    run started at (``assign_test_keys`` stamps ``last_run_at`` then, with
    the clock a sidecar's ``written_at`` uses), ``None`` when the entry
    holds no number. Read as data — never quarantined, as
    ``BatchManifest.load`` does a corrupt one: a renderer moves no file. A
    model under two keys takes the last, as ``last_test_keys`` does."""
    try:
        entries = json.loads(
            (work_dir / "batch_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    rows: dict[str, tuple[TestRow, float | None]] = {}
    for key, entry in entries.items() if isinstance(entries, dict) else ():
        model_id = entry.get("model_id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str):
            continue
        ref_id = entry.get("ref_id")
        ref_id = ref_id if isinstance(ref_id, str) else None
        last_run_at = entry.get("last_run_at")
        if isinstance(last_run_at, bool) or not isinstance(last_run_at, int | float):
            last_run_at = None
        rows[model_id] = (
            TestRow(
                model_id=model_id, test_key=key, ref_id=ref_id, report_dir=ref_id or key
            ),
            last_run_at,
        )
    return rows


def build_dashboard_context(
    work_dir: Path,
    mode: str,
    rerun_flags: str | None = None,
    comparisons: Iterable[TestComparison] = (),
    results: Mapping[str, TestResult] | None = None,
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
    run, a ``--merge`` and the tool's queue list every test they
    scored). Such a row takes its key and baseline from the manifest.
    A row whose result the caller holds (``results``, by model id) takes
    its CPU time from it: Sim (s) shows that, marked, where no wall time
    was kept (``TestRow.sim_source``).

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
    # (row, the wall clock its sidecar must postdate, what that clock is)
    base = [
        (row_from_status(t), snapshot_start_wall, "start_wall")
        for t in snapshot.get("tests", [])
    ]
    listed = {r.model_id for r, _, _ in base}
    rest = set(by_model) - listed
    if rest:
        # A row the last run did not run is judged against ITS OWN last run
        # — the start its manifest entry keeps: against the last run's
        # start, every sidecar outside its scope read stale (638 warnings
        # and no Reference link on a 331-test page).
        manifest = _manifest_rows(work_dir)
        base += [
            (row, last_run_at, "last_run_at")
            for m, (row, last_run_at) in manifest.items()
            if m in rest
        ]
        base += [
            (TestRow(model_id=m), None, "last_run_at")
            for m in sorted(rest - set(manifest))
        ]
    rows = []
    for row, start_wall, anchor in base:
        before = row
        comp = by_model.get(row.model_id)
        # Where this test's report page may be. The suite writes it under
        # the baseline it was compared against (``ref_NNNN``), else under
        # its test key — a failed simulation's too. The name its RUN gave
        # it is a guess made before either was known: a test accepted
        # since has a baseline its run did not name.
        compared = f"ref_{comp.test_id}" if comp is not None and comp.test_id else None
        places = dict.fromkeys(d for d in (compared, row.report_dir, row.test_key) if d)
        for place in places if mode == "final" else ():
            sidecar = _read_comparison_sidecar(work_dir, place)
            if not sidecar:
                continue
            # Sidecars written before the summary block carry the
            # fields at the top level.
            row = enrich_row(
                row,
                sidecar.get("summary", sidecar),
                start_wall=start_wall,
                anchor=anchor,
            )
            if row is not before:
                # A sidecar of THIS run's verdict was read: the page it
                # was written beside is there to open. Nothing else says
                # a page exists — a run wipes its tests' report dirs.
                row = replace(row, report_page=place)
                break
        # enrich_row hands back the same row when a guard refuses the
        # sidecar. With no fresh sidecar (a run without --report, or a stale
        # one), the walls are the ones the run kept beside the result
        # (storage.run_stats).
        if mode == "final" and row is before and row.test_key:
            row = _with_kept_timing(work_dir, row)
        if comp is not None:
            row = _with_verdict(row, comp)
        result = (results or {}).get(row.model_id) if mode == "final" else None
        cpu = cpu_time(result.statistics) if result is not None else None
        if cpu is not None:
            row = replace(row, sim_cpu=cpu)
        rows.append(row)

    # rerun_flags precedence: explicit kwarg > snapshot's flags > none.
    # The kwarg path lets the CLI override the snapshot's flags even when
    # status.json was written by a prior run with a different config (e.g.
    # dstf compare reading work_dir from a different invocation).
    flags = rerun_flags or snapshot.get("rerun_flags") or ""

    return {
        "mode": mode,
        "auto_refresh": mode == "live",
        "title": "Test progress" if mode == "live" else "Test report",
        # A final page counts the rows it lists (status.json's scope and
        # every test scored); a live one, the run's.
        "total": len(rows) if mode == "final" else snapshot.get("total", 0),
        # What the last run ran, for a final page that lists more: its
        # status bar says so rather than "N tests · ran for 0s".
        "run_total": len(snapshot.get("tests", [])),
        "elapsed": snapshot.get("elapsed", 0.0),
        "eta_seconds": snapshot.get("eta_seconds"),
        "counts": snapshot.get("counts", {}),
        "tests": rows,
        "columns": COLUMNS,
        # The count pills, in PILL_STATUSES' order: [status class, the
        # word a person reads]. The class is the pill's key and the row's
        # data-status.
        "statuses": [[STATUS_CLASSES[s], STATUS_LABELS[s]] for s in PILL_STATUSES],
        "rerun_flags": flags,
        "offers_author": offers_author(flags),
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
    rerun_flags: str | None = None,
    output_path: Path | None = None,
    comparisons: Iterable[TestComparison] = (),
    results: Mapping[str, TestResult] | None = None,
    theme: str = "default",
) -> None:
    ctx = build_dashboard_context(
        work_dir,
        mode=mode,
        rerun_flags=rerun_flags,
        comparisons=comparisons,
        results=results,
    )
    template = _env.get_template("dashboard.html")
    # The token block per render, never at import: the theme is the
    # project's (`Config.theme`), and every caller in `src` says it —
    # `tests/test_themes.py` reads the call sites.
    html = template.render(**ctx, dstf_css=css_variables(theme))
    dest = output_path or (work_dir / "dashboard.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, html)


def render_live(
    work_dir: Path, rerun_flags: str | None = None, theme: str = "default"
) -> None:
    """Render dashboard.html in live mode (auto-refreshes via meta tag)."""
    _render(work_dir, mode="live", rerun_flags=rerun_flags, theme=theme)


def render_final(
    work_dir: Path,
    rerun_flags: str | None = None,
    comparisons: Iterable[TestComparison] = (),
    results: Mapping[str, TestResult] | None = None,
    theme: str = "default",
) -> None:
    """Render dashboard.html in final mode (refresh stripped, the
    comparisons' verdicts and the sidecars merged; the results' CPU
    times where no wall time was kept)."""
    _render(
        work_dir,
        mode="final",
        rerun_flags=rerun_flags,
        comparisons=comparisons,
        results=results,
        theme=theme,
    )


def render_final_to(
    work_dir: Path,
    output_path: Path,
    rerun_flags: str | None = None,
    comparisons: Iterable[TestComparison] = (),
    results: Mapping[str, TestResult] | None = None,
    theme: str = "default",
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
        rerun_flags=rerun_flags,
        output_path=output_path,
        comparisons=comparisons,
        results=results,
        theme=theme,
    )
