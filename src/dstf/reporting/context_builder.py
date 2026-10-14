"""Build the per-test template context from comparison results.

The pure ``results -> context dict`` side of the per-test report: every
helper here takes already-extracted inputs and returns one named slice of
the context that :mod:`dstf.reporting.report_suite` renders. Nothing in
this module touches Jinja, the filesystem, or the decimation pass.
"""

import logging
import math
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from ..backends.types import VariableText, display_label, variable_texts
from ..comparison.comparator import VariableComparison
from ..comparison.registry import summarize
from .stats_rows import SECTION_TITLES, report_text, statistics_rows

logger = logging.getLogger(__name__)


def test_texts(
    comparisons: Iterable,
    result,
    plot_series: Iterable[str] = (),
) -> dict[str, VariableText]:
    """The TEST's label scope (:func:`~dstf.backends.types.variable_texts`)
    for one page: the result's variables, the scored comparisons' and every
    series of the test's resolved plots.

    The one builder both tiers call — the report through
    :func:`_build_template_context`, the tool through
    ``Session.variable_texts`` — so a variable reads the same in its card
    title, its criteria row, its pill and its legend entry.
    """
    labels: dict[str, str] = {}
    for var in getattr(result, "variables", None) or ():
        if var.name:
            labels[var.name] = display_label(var.name, var.expression)
    for vc in comparisons:
        if vc.name:
            labels.setdefault(vc.name, display_label(vc.name, vc.expression))
    return variable_texts(labels, plot_series)


def _format_value(val) -> str:
    """Format a value for display in HTML tables."""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if val is None or val == "":
        return ""
    return str(val)


def worst_nrmse(comparisons: list) -> tuple[float | None, int]:
    """``(the worst NRMSE among the criteria scored BY nrmse, the count of
    criteria with no score)`` — what the "Worst NRMSE" column, the report
    header and the tool's Suite page show.

    ``VariableComparison.nrmse`` is overloaded: it carries the mode's own
    number (a range's worst violation, an event's Δt), and for a tube the
    NRMSE against the reference — which the tube does not score by. A max
    over every leaf showed ``2.8213e-01`` for a test whose tube passed at
    100 % inside while the criterion that failed, an nrmse, was at
    ``4.264e-02``. Only a criterion whose mode is ``nrmse`` has an NRMSE
    that decided anything; a test with none has no worst one (``None``:
    the column reads "—").

    A sentinel leaf of any mode (a metric config that would not build, no
    data to compare) carries ``inf``/``nan``: it is left out and counted,
    so one of them cannot swallow the real numbers, and the surface can
    say that criteria went unscored.
    """
    scored = [
        vc.nrmse for vc in comparisons if vc.mode == "nrmse" and math.isfinite(vc.nrmse)
    ]
    n_unscored = sum(1 for vc in comparisons if not math.isfinite(vc.nrmse))
    return (max(scored) if scored else None, n_unscored)


def _text_of(texts: dict[str, VariableText] | None, name: str, expression: str) -> str:
    """What the page reads for ``name``, from the test's scope — or its
    plain label when the caller built no scope (a variable the scope never
    saw cannot collide with anything in it)."""
    entry = (texts or {}).get(name)
    return entry.text if entry is not None else display_label(name, expression)


def _in_effect(view: dict, base: tuple[float, str] | None) -> dict:
    """``{field: {"value", "source"}}`` for what a leaf is scored with while
    its field is EMPTY (K3): today the test's base tolerance and its source
    (``base``, `TestComparison.base_tolerance` / ``tolerance_source``), on
    every leaf whose metric's form has the field and does not ignore it — a
    scalar with bounds does. A leaf with a tolerance of its own carries it
    too: its editor shows no hint until that value is cleared, and cleared,
    the base is what applies. None — a caller that has none — shows
    nothing. Known gap: on the flat path a baseline JSON's
    ``variable_overrides`` tolerance scores its variable, while the hint
    names the base."""
    from ..comparison.mode_schema import derive_schema
    from ..comparison.registry import metric

    params = view.get("params") or {}
    desc = metric(view.get("metric", ""))
    if base is None or desc is None:
        return {}
    fields = {f.name for f in derive_schema(desc.config_cls, mode=desc.name).fields}
    if "tolerance" not in fields or {"min_value", "max_value"} & set(params):
        return {}
    value, source = base
    return {"tolerance": {"value": float(value), "source": source}}


def _leaf_tolerance(view: dict, vc: VariableComparison) -> float | dict | None:
    """The tolerance a leaf was scored with, as written (`sides.as_written`),
    for a metric that takes one — its config has the field — and scored by
    it: a scalar with bounds does not. None otherwise: a tube's width, a
    range's bounds and a table's rows are the leaf's own ``params``, and
    the base tolerance the evaluator handed such a mode decided nothing."""
    from ..comparison.mode_schema import derive_schema
    from ..comparison.registry import metric
    from ..comparison.sides import Sides, as_written

    desc = metric(vc.mode or view.get("metric", ""))
    if desc is None or {"min_value", "max_value"} & set(view.get("params") or {}):
        return None
    fields = {f.name for f in derive_schema(desc.config_cls, mode=desc.name).fields}
    if "tolerance" not in fields:
        return None
    used = vc.tolerance_used
    return as_written(used) if isinstance(used, Sides) else float(used)


def _augment_tree_view(
    view: dict,
    comparisons_by_path: dict[str, VariableComparison],
    texts: dict[str, VariableText] | None = None,
    base_tolerance: tuple[float, str] | None = None,
) -> None:
    """Walk ``view`` in place, enriching leaf nodes with what the page shows.

    The template renders the tree server-side and read-only, so a leaf
    carries the CLI's verdict and the CLI's words for it: the effective
    mode, the variable's name, ``summarize``'s display / criterion /
    detail pairs, and the reason a sentinel could not be scored. Nothing
    here is an input to a computation — the page performs none.

    ``texts`` is the TEST's label scope (:func:`test_texts`): a criteria
    row shows no muted name, so it reads the ``text``. Two variables
    sharing one expression — ``x={a.Q, a.Q}`` — gave two rows the
    identical ``nrmse · a.Q``, with nothing on either saying which
    variable it scored.
    """
    if view.get("kind") == "leaf":
        path = view.get("path", "")
        vc = comparisons_by_path.get(path)
        if vc is None:
            return
        var_label = vc.name or view.get("variable", "")
        summary = summarize(vc)
        view.update(
            {
                # The metric the criterion was scored by — the comparison's
                # own `mode`, the ONE source every surface names it from
                # ("" for the one with none: a flat override naming a mode
                # nothing is registered under). `metric` beside it is the
                # spec's: a flat test's default is written `nrmse` there
                # while `default_points` scores it by `points`.
                "mode_effective": vc.mode,
                # The baseline the criterion READ (`VariableComparison.
                # against`), null where it reads none — a `range`, a
                # criterion scored from its own declared values. The spec's
                # `against` said "primary" of each of those.
                "against": vc.against,
                "name": var_label,
                # What the leaf's row READS (`metric · pipe.T[1]`), on both
                # tiers: the report's node macro and the tool's `leaf_name`
                # take it from here, so the two cannot drift. `variable`
                # stays the key a patch op targets.
                "label": _text_of(texts, var_label, vc.expression),
                "score_display": summary.display,
                "criterion": summary.criterion,
                # The mode's own key/value pairs behind the verdict, as the
                # server-rendered ``<dl class="node-details">`` under the leaf.
                # Tuples become lists so the view stays JSON-shaped.
                "details": [list(pair) for pair in summary.details],
                # Reason a sentinel leaf could not be scored. Rendered as the
                # score pill's tooltip plus a muted line beneath it; None on
                # every normally-scored leaf.
                "error": (vc.diagnostics or {}).get("error"),
            }
        )
        tolerance = _leaf_tolerance(view, vc)
        if tolerance is not None:
            # The tolerance the criterion was scored with — its own, else
            # the test's — as data (a number, or a lower/upper pair): what
            # a script reads off `verdicts.json` and the sidecar without
            # parsing the criterion sentence.
            view["tolerance"] = tolerance
        in_effect = _in_effect(view, base_tolerance)
        if in_effect:
            view["in_effect"] = in_effect
        return
    for child in view.get("children", []):
        _augment_tree_view(child, comparisons_by_path, texts, base_tolerance)


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
    / how many criteria scored the run / how many variables the baseline
    stores.
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

    # Two counts of two things, each named for what it is: the criteria
    # this run was scored by (one per leaf — two on one variable are two),
    # and the variables the baseline stores. They read `Tracked Variables |
    # 23 current / 21 reference`, as one quantity then and now.
    if comparisons:
        ref_info.append({"label": "Criteria", "value": str(len(comparisons))})
    if ref_data and ref_data.get("n_vars") is not None:
        ref_info.append(
            {"label": "Reference Variables", "value": str(ref_data["n_vars"])}
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


def _build_statistics_sections(
    ref_stats: dict,
    cur_stats: dict,
    *,
    run_diagnostics: Sequence = (),
) -> list:
    """The Statistics table: :func:`~.stats_rows.statistics_rows` rendered
    into the template's ``{title, rows: [{label, current, reference,
    changed}]}`` sections — the rows the tool's Statistics card shows, in
    the report's order, under one "changed" rule for both; printed in full
    (:func:`~.stats_rows.report_text`), where the card rounds. A section with
    no key is no section. ``run_diagnostics`` are the run's samples: a
    folded diagnostic's row takes its kind from them, as the card's does."""
    sections: dict[str, dict] = {}
    for row in statistics_rows(cur_stats, ref_stats, run_diagnostics=run_diagnostics):
        if row.section not in sections:
            title = (
                SECTION_TITLES.get(row.section) or row.section.replace("_", " ").title()
            )
            if row.section in ("translation", "simulation"):
                title += " Statistics"
            sections[row.section] = {"title": title, "rows": []}
        sections[row.section]["rows"].append(
            {
                "label": row.label,
                "current": report_text(row.kind, row.run),
                "reference": report_text(row.kind, row.baseline),
                "changed": row.changed,
            }
        )
    return list(sections.values())


def build_trajectories(
    comparisons: list,
    result,
    ref_data: dict | None,
    texts: dict[str, VariableText] | None = None,
) -> tuple[list, list, list]:
    """Build the trajectory triple — primary tracked variables, diagnostic
    variables (CPUtime / EventCounter), and the no-baseline pass-through
    used when a test runs without a reference. Diagnostic refs may carry
    a scalar summary instead of a full trajectory; the legacy
    full-array shape is still accepted for back-read.

    ``texts`` is the test's label scope (:func:`test_texts`). Every entry
    carries BOTH readings: ``label`` for the surfaces that show the muted
    name beside it (the section heading, the per-variable status badge),
    ``text`` for the ones that do not (the figure's legend and hover, the
    plot notes).

    Returns ``(trajectories, diag_trajectories, nobaseline_trajectories)``.
    """
    trajectories = []
    ref_time_list = ref_data.get("time", []) if ref_data else []

    if result and result.variables:
        # One entry per variable NAME, not per comparison: several leaves may
        # score the same variable, and a sentinel among them carries
        # ``index=0`` (the evaluator never resolved a variable to pair with).
        # Pairing that by index finds nothing and emits an EMPTY entry under
        # the real name — a second, blank copy beside the healthy one, or the
        # variable's only entry when every leaf on it is a sentinel. So: the
        # actual curve by index when any leaf on the name resolved one, else
        # by name.
        by_name: dict[str, list] = {}
        for vc in comparisons:
            by_name.setdefault(vc.name, []).append(vc)
        # The REFERENCE always by name, as scoring pairs it: a baseline's
        # indices are the order it was accepted in, and the pattern order
        # (D140 item 9) moved them — by index, a baseline accepted under the
        # old sorted order drew another variable's reference, and its error
        # overlays, under this one's name. The first entry of a name wins,
        # as it does for the comparator.
        ref_vars_by_name: dict[str, dict] = {}
        if ref_data:
            for rv in ref_data.get("variables", []):
                if rv.get("name"):
                    ref_vars_by_name.setdefault(rv["name"], rv)
        for name, name_comparisons in by_name.items():
            index = next((vc.index for vc in name_comparisons if vc.index > 0), 0)
            if index > 0:
                act_var = next((v for v in result.variables if v.index == index), None)
            else:
                act_var = next((v for v in result.variables if v.name == name), None)
                index = act_var.index if act_var else 0
            ref_var = ref_vars_by_name.get(name)
            trajectories.append(
                {
                    "index": index,
                    "name": name,
                    # The label beside the name — what the page's titles,
                    # legends and hovers read, and what an overlay whose own
                    # source names the quantity that way is matched by (see
                    # ``attach_overlays_to_trajectories``).
                    "expression": act_var.expression if act_var else "",
                    "label": display_label(name, act_var.expression if act_var else ""),
                    "text": _text_of(
                        texts, name, act_var.expression if act_var else ""
                    ),
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
                    "name": var.name,
                    "expression": var.expression,
                    "label": display_label(var.name, var.expression),
                    "text": _text_of(texts, var.name, var.expression),
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
    is_simulate_only = bool(test_model and test_model.is_simulate_only)
    sim_failed = len(comparisons) == 0 and n_nobaseline == 0 and not is_simulate_only
    return n_passed, n_nobaseline, is_simulate_only, sim_failed


def _build_key_stats(
    comparisons: list,
    ref_stats: dict,
    cur_stats: dict,
    *,
    run_diagnostics: Sequence = (),
) -> list:
    """Top-level summary row: the worst NRMSE (`worst_nrmse`), plus a small
    fixed pick of structural stats (continuous states, nonlinear
    counts) and CPUtime / EventCounter. Format mirrors statistics_sections
    rows so the template renderer is uniform.
    """

    # A statistics pick reads its row: its values (a baseline's empty list
    # reads its count as 0), printed as the table prints them, and the one
    # "changed" rule.
    rows = {
        row.path: row
        for row in statistics_rows(
            cur_stats, ref_stats, run_diagnostics=run_diagnostics
        )
    }

    def _pick(path: str, label: str) -> None:
        row = rows.get(path)
        if row is None or (row.run is None and row.baseline is None):
            return
        key_stats.append(
            {
                "label": label,
                "current": report_text(row.kind, row.run),
                "reference": report_text(row.kind, row.baseline),
                "changed": row.changed,
            }
        )

    key_stats = []
    # "Worst NRMSE": the worst among the criteria scored by nrmse
    # (`worst_nrmse`), the dashboard column's number. A test whose
    # criteria are all of other metrics reads "—": it has none. Sentinel
    # leaves are left out and counted.
    worst_score, n_unscored = worst_nrmse(comparisons)
    # The unscored count rides in ``current`` (the template prefixes
    # ``reference`` with "ref:", which this is not).
    unscored_note = (
        ""
        if not n_unscored
        else (
            " (1 criterion unscored)"
            if n_unscored == 1
            else f" ({n_unscored} criteria unscored)"
        )
    )
    if worst_score is not None:
        key_stats.append(
            {
                "label": "Worst NRMSE",
                "current": f"{worst_score:.4e}{unscored_note}",
                "reference": "",
            }
        )
    elif n_unscored:
        key_stats.append(
            {
                "label": "Worst NRMSE",
                "current": (
                    "n/a (1 unscored criterion)"
                    if n_unscored == 1
                    else f"n/a ({n_unscored} unscored criteria)"
                ),
                "reference": "",
            }
        )
    elif comparisons:
        # Criteria, and none of them scored by nrmse.
        key_stats.append({"label": "Worst NRMSE", "current": "—", "reference": ""})

    for path, label in (
        ("translation.continuous_time_states", "Continuous States"),
        ("translation.nonlinear_count", "Nonlinear Systems"),
        ("translation.nonlinear_max", "Largest Nonlinear"),
        ("CPUtime", "CPU Time"),
        ("EventCounter", "Events"),
    ):
        _pick(path, label)
    return key_stats


def _build_warning_rows(warnings: list | None) -> list:
    """Translate :class:`StructuralWarning` instances into the row dicts
    the template iterates over: a change as its three cells, a statement
    (``is_change`` false) as its one sentence, ``text``.
    """
    return [
        {
            "field": w.field,
            "reference": str(w.reference_value),
            "current": str(w.current_value),
            "is_change": w.is_change,
            "text": w.text,
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


def _view_leaves(node: dict) -> list[dict]:
    """Every leaf view under ``node``, in tree order."""
    if node.get("kind") == "leaf":
        return [node]
    out: list[dict] = []
    for child in node.get("children") or []:
        out.extend(_view_leaves(child))
    return out


def _mark_advisory(node: dict, *, under_warn: bool = False) -> None:
    """Stamp ``advisory: True`` on every leaf sitting under a ``warn``.

    ``warn`` never fails its parent (``comparison/metric_tree.py``), so such
    a leaf's failure cannot change the test's verdict — it informs, it does
    not gate. The summary line and the per-variable pills read this flag to
    keep an advisory failure out of the red verdict and report it as a chip
    beside it instead.
    """
    if node.get("kind") == "leaf":
        node["advisory"] = under_warn
        return
    warn = under_warn or node.get("combinator") == "warn"
    for child in node.get("children") or []:
        _mark_advisory(child, under_warn=warn)


def _tree_summary(view: dict | None) -> dict | None:
    """The pieces the summary line is built from: the leaf tally, the ROOT
    verdict, and how many advisory leaves failed.

    The headline colour follows the ROOT verdict, not the leaf tally — a
    warn-wrapped failure painting the summary red reads as "this test
    failed" for a finding that is advisory by construction. ``root_passed``
    is tri-state: ``None`` means there is no verdict to show.
    """
    if not view:
        return None
    leaves = _view_leaves(view)
    return {
        "n_pass": sum(1 for leaf in leaves if leaf.get("passed")),
        "n_total": len(leaves),
        "root_passed": view.get("passed"),
        "n_advisory_failed": sum(
            1 for leaf in leaves if leaf.get("advisory") and not leaf.get("passed")
        ),
    }


def _spec_for(comparisons: list, test_model):
    """The tree the view walks: the authored one, else the implicit tree
    synthesized from what was scored. Both the tree view and the
    per-variable entries read it, so they agree by construction."""
    from ..comparison.tree_spec import (
        synthesize_implicit_tree as _synthesize_implicit_tree,
    )

    if (
        test_model is not None
        and getattr(test_model, "metric_tree_spec", None) is not None
    ):
        return test_model.metric_tree_spec
    comparison_var_names = [vc.name for vc in comparisons]
    return _synthesize_implicit_tree(
        comparison_var_names,
        variable_overrides=(
            getattr(test_model, "variable_overrides", None) if test_model else None
        ),
    )


def build_tree_view(
    comparisons: list,
    test_model,
    metric_tree,
    texts: dict[str, VariableText] | None = None,
    base_tolerance: tuple[float, str] | None = None,
) -> dict | None:
    """The read-only metric tree as the report renders it — the spec's shape
    (the authored tree, else the implicit one synthesized from what was
    scored) with each leaf carrying the CLI's verdict, ``summarize``'s
    display / criterion / details, the sentinel reason and the advisory
    flag. ``None`` when nothing was compared. The authoring shell shows the
    same dict, built by this function.

    ``texts`` is the TEST's label scope (:func:`test_texts`) — the scored
    variables are only part of it, and a caller that passes none gets each
    leaf's plain label. Both tiers pass one: a criteria row read ``a.Q``
    under a card titled ``a.Q (x)`` when the scope was the scored list
    alone (the b147918 review)."""
    if not comparisons:
        return None

    from ..comparison.tree_eval import flatten_evaluation as _flatten_evaluation
    from ..comparison.tree_spec import (
        spec_to_view as _spec_to_view,
    )

    spec = _spec_for(comparisons, test_model)
    eval_by_path = _flatten_evaluation(metric_tree) if metric_tree is not None else {}

    # Leaves are addressed by the path a patch op would use — stamped by the
    # evaluator on both evaluation routes — never by position in the flat
    # comparison list.
    from ..comparison.tree_eval import leaf_comparisons_by_path

    comparisons_by_path = (
        leaf_comparisons_by_path(metric_tree) if metric_tree is not None else {}
    )

    tree_view = _spec_to_view(spec, evaluation_by_path=eval_by_path)
    # The view walks the spec; the lookup holds what the evaluator stamped.
    # Both routes stamp every leaf the view will show, so a miss is drift
    # between the two walks — a leaf that would render with no pill and no
    # criterion, and nothing else would say so.
    missing = [
        path for path in _view_leaf_paths(tree_view) if path not in comparisons_by_path
    ]
    if missing:
        logger.warning(
            "%s: %d criteria of the spec have no evaluated comparison and render "
            "without scores: %s",
            getattr(test_model, "model_id", "?"),
            len(missing),
            ", ".join(missing),
        )
    _augment_tree_view(tree_view, comparisons_by_path, texts, base_tolerance)
    # Before the per-variable pills read it (`_build_tree_view_and_variables`)
    # and before a plot's criteria rows share these leaf dicts.
    _mark_advisory(tree_view)
    return tree_view


def _build_tree_view_and_variables(
    comparisons: list,
    test_model,
    metric_tree,
    trajectories: list,
    texts: dict[str, VariableText] | None = None,
) -> tuple[dict | None, dict]:
    """Stage-2 single-source-of-truth: returns ``(tree_view,
    variables_by_name)``.

    The reporter's recursive-component UI walks ``tree_view`` directly
    (a serialized SpecNode tree with per-node paths + evaluation results,
    leaves augmented with render artifacts). ``variables_by_name`` carries
    one entry per unique variable — its trajectory + overlays + the leaf
    paths targeting it + its verdict — and a plot's section lists its
    subject's leaves of the same tree
    (:func:`~dstf.reporting.tree_rows.criteria_rows`).

    When there are no comparisons the function returns ``(None, {})`` —
    sim-failed and no-baseline tests render without a tree view at all.
    """
    if not comparisons:
        return None, {}

    from ..comparison.tree_spec import (
        collect_variables as _collect_variables,
    )
    from ..comparison.tree_spec import (
        leaves_for_variable as _leaves_for_variable,
    )

    tree_view = build_tree_view(comparisons, test_model, metric_tree, texts)
    if tree_view is None:
        return None, {}
    spec = _spec_for(comparisons, test_model)
    all_leaves = _view_leaves(tree_view)

    # Look the trajectory up by variable name — ``build_trajectories``
    # emits exactly one entry per name, sentinel leaves included.
    variables_by_name: dict[str, dict] = {}
    for vn in _collect_variables(spec):
        match_traj = next((t for t in trajectories if t["name"] == vn), None)
        var_leaves = [leaf for leaf in all_leaves if leaf.get("variable") == vn]
        # Only the leaves that can actually gate this variable decide its
        # colour; advisory ones are reported beside it as a chip.
        gating = [leaf for leaf in var_leaves if not leaf.get("advisory")]
        variables_by_name[vn] = {
            "name": vn,
            # The variable's own label, from its trajectory (the result is
            # what knows the expression); a variable with no trajectory —
            # every leaf on it a sentinel — reads as its name. ``label``
            # is for the status badge, which shows the muted name beside
            # it; ``text`` for the plot note, which does not.
            "label": (match_traj or {}).get("label") or vn,
            "text": (match_traj or {}).get("text") or _text_of(texts, vn, ""),
            "trajectory": match_traj or {},
            "overlays": (match_traj or {}).get("overlays", []),
            "leaf_paths": [p for _, p in _leaves_for_variable(spec, vn)],
            "passed": bool(var_leaves)
            and all(bool(leaf.get("passed")) for leaf in gating),
            "n_advisory_failed": sum(
                1
                for leaf in var_leaves
                if leaf.get("advisory") and not leaf.get("passed")
            ),
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
    plot_series: Iterable[str] = (),
) -> dict:
    """Build the full template context dict from comparison data.

    Orchestrator over the per-section ``_build_*`` helpers above. Each
    output key in the returned dict is produced by exactly one helper.

    ``plot_series`` is every series name of the test's resolved plots,
    plot-only ones included: they are part of the page's label scope
    (:func:`test_texts`), so a plot-only variable NAMED like another's
    expression is what makes that other one read its name too.
    """
    if cur_stats is None:
        cur_stats = {}
    ref_stats = ref_data.get("statistics", {}) if ref_data else {}
    ref_sim = ref_data.get("simulation", {}) if ref_data else {}

    ref_info = _build_ref_info(comparisons, ref_data, test_dir, ref_file)
    sim_params = _build_sim_params(test_model, ref_sim)
    run_diagnostics = (result.diagnostics or []) if result is not None else []
    statistics_sections = _build_statistics_sections(
        ref_stats, cur_stats, run_diagnostics=run_diagnostics
    )
    texts = test_texts(comparisons, result, plot_series)
    trajectories, diag_trajectories, nobaseline_trajectories = build_trajectories(
        comparisons,
        result,
        ref_data,
        texts,
    )
    diag_summaries = _build_diag_summaries(result, ref_data)
    artifacts = _build_artifacts(test_dir, artifact_files)
    n_passed, _n_nobaseline, is_simulate_only, sim_failed = _compute_summary_flags(
        comparisons,
        result,
        test_model,
    )
    key_stats = _build_key_stats(
        comparisons, ref_stats, cur_stats, run_diagnostics=run_diagnostics
    )
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
        texts,
    )

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
        # The TEST's label scope, stamped once and read by every surface
        # that says a variable's name (`report_suite._context_labels`).
        # Read back off the entries that carry a label of their own — the
        # scored variables and the no-baseline trajectories — it was the
        # scope of those entries, not of the test: a TRACKED variable no
        # criterion scores is in neither list, so its heading, its "is plot
        # only" note, its legend entry and the decimation banner all read
        # its bare `unitTests.x[2]` while the tool read `a.Q (unitTests.x[2])`.
        "variable_texts": {
            name: {"label": entry.label, "text": entry.text}
            for name, entry in texts.items()
        },
        # A2 / idea #50 — companion + soft_check overlays (default off).
        "overlay_rows": overlay_rows,
        # Stage 2 — recursive tree view + per-variable plot grouping.
        "tree_view": tree_view,
        "variables_by_name": variables_by_name,
        # Pieces of the server-rendered summary line — the template builds
        # the markup, so nothing here has to be trusted as HTML.
        "tree_summary": _tree_summary(tree_view),
        # Run provenance banner (backend/simulator/version/os). Same shape as
        # the dashboard's, formatted by dashboard_render.format_run_metadata.
        "run_metadata": run_metadata,
    }
