"""Build the per-test template context from comparison results.

The pure ``results -> context dict`` side of the per-test report: every
helper here takes already-extracted inputs and returns one named slice of
the context that :mod:`dstf.reporting.report_suite` renders. Nothing in
this module touches Jinja, the filesystem, or the decimation pass.
"""

import logging
import math
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from ..backends.types import VariableText, display_label, variable_texts
from ..comparison.comparator import VariableComparison
from ..comparison.registry import summarize

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
    return variable_texts(plain_labels(comparisons, result), plot_series)


def plain_labels(comparisons: Iterable, result) -> dict[str, str]:
    """``{name: its plain label}`` — the expression, else the name — for the
    result's variables and the scored comparisons, with no scope.

    What an array family folds by
    (:func:`~dstf.discovery.plots.derive_plots`). Not the scope's texts:
    the scope holds every series of the test's plots, so the plots are
    derived BEFORE it — `Session.variable_texts` calls `Session.plots` —
    and a fold that read the scope would call itself."""
    labels: dict[str, str] = {}
    for var in getattr(result, "variables", None) or ():
        if var.name:
            labels[var.name] = display_label(var.name, var.expression)
    for vc in comparisons:
        if vc.name:
            labels.setdefault(vc.name, display_label(vc.name, vc.expression))
    return labels


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
    key_order: list[str] | None = None,
) -> dict | None:
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
        rows.append(
            {
                "label": key.replace("_", " ").title(),
                "current": cur_val,
                "reference": ref_val,
                "changed": changed,
            }
        )
    return {"title": title, "rows": rows}


def worst_finite_score(comparisons: list) -> tuple[float | None, int]:
    """Return ``(worst finite score, count of unscored leaves)``.

    ``VariableComparison.nrmse`` carries the mode's score, and a sentinel
    leaf (metric config that would not build, no data to compare) sets it
    to ``inf``/``nan``. A plain ``max()`` over the leaves therefore returns
    a non-finite value for the whole test, which the strict-JSON pass then
    writes as ``null`` — the dashboard's "Worst NRMSE" read "—" for a test
    whose other leaves scored perfectly well. Report the worst among the
    leaves that HAVE a score, and count the rest so the surface can say so
    instead of implying there was nothing to measure.
    """
    finite = [vc.nrmse for vc in comparisons if math.isfinite(vc.nrmse)]
    n_unscored = sum(1 for vc in comparisons if not math.isfinite(vc.nrmse))
    return (max(finite) if finite else None, n_unscored)


def _text_of(texts: dict[str, VariableText] | None, name: str, expression: str) -> str:
    """What the page reads for ``name``, from the test's scope — or its
    plain label when the caller built no scope (a variable the scope never
    saw cannot collide with anything in it)."""
    entry = (texts or {}).get(name)
    return entry.text if entry is not None else display_label(name, expression)


def _augment_tree_view(
    view: dict,
    comparisons_by_path: dict[str, VariableComparison],
    texts: dict[str, VariableText] | None = None,
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
        mode = vc.mode or "nrmse"
        var_label = vc.name or view.get("variable", "")
        summary = summarize(vc)
        view.update(
            {
                "mode_effective": mode,  # runtime mode (e.g., "points"); not the spec metric
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
        return
    for child in view.get("children", []):
        _augment_tree_view(child, comparisons_by_path, texts)


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

    ref_info.append(
        {
            "label": "Tracked Variables",
            "value": f"{len(comparisons)} current"
            + (f" / {ref_data.get('n_vars', '?')} reference" if ref_data else "")
            if comparisons
            else "",
        }
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
            section = _build_stats_section(
                "Simulation Statistics", ref_scalars, cur_scalars
            )
            if section:
                statistics_sections.append(section)
    return statistics_sections


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
    ref_vars_by_idx = {}
    if ref_data:
        for rv in ref_data.get("variables", []):
            ref_vars_by_idx[rv["index"]] = rv

    if result and result.variables:
        # One entry per variable NAME, not per comparison: several leaves may
        # score the same variable, and a sentinel among them carries
        # ``index=0`` (the evaluator never resolved a variable to pair with).
        # Pairing that by index finds nothing and emits an EMPTY entry under
        # the real name — a second, blank copy beside the healthy one, or the
        # variable's only entry when every leaf on it is a sentinel. So: pair
        # by index when any leaf on the name resolved one, else by name.
        by_name: dict[str, list] = {}
        for vc in comparisons:
            by_name.setdefault(vc.name, []).append(vc)
        ref_vars_by_name = {}
        if ref_data:
            for rv in ref_data.get("variables", []):
                if rv.get("name"):
                    ref_vars_by_name[rv["name"]] = rv
        for name, name_comparisons in by_name.items():
            index = next((vc.index for vc in name_comparisons if vc.index > 0), 0)
            if index > 0:
                act_var = next((v for v in result.variables if v.index == index), None)
                ref_var = ref_vars_by_idx.get(index)
            else:
                act_var = next((v for v in result.variables if v.name == name), None)
                ref_var = ref_vars_by_name.get(name)
                index = act_var.index if act_var else 0
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
    is_simulate_only = bool(test_model and getattr(test_model, "simulate_only", False))
    sim_failed = len(comparisons) == 0 and n_nobaseline == 0 and not is_simulate_only
    return n_passed, n_nobaseline, is_simulate_only, sim_failed


def _build_key_stats(
    comparisons: list,
    ref_stats: dict,
    cur_stats: dict,
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
    # Sentinel leaves (inf/nan score) are excluded and counted instead —
    # one unscored leaf used to swallow every real number on the test.
    worst_score, n_unscored = worst_finite_score(comparisons)
    # The unscored count rides in ``current`` (the template prefixes
    # ``reference`` with "ref:", which this is not).
    unscored_note = (
        ""
        if not n_unscored
        else (
            " (1 leaf unscored)"
            if n_unscored == 1
            else f" ({n_unscored} leaves unscored)"
        )
    )
    if worst_score is not None:
        key_stats.append(
            {
                "label": "Worst Score",
                "current": f"{worst_score:.4e}{unscored_note}",
                "reference": "",
            }
        )
    elif n_unscored:
        key_stats.append(
            {
                "label": "Worst Score",
                "current": (
                    "n/a (1 unscored leaf)"
                    if n_unscored == 1
                    else f"n/a ({n_unscored} unscored leaves)"
                ),
                "reference": "",
            }
        )

    stat_picks = [
        ("translation", "continuous_time_states", "Continuous States"),
        ("translation", "nonlinear_count", "Nonlinear Systems"),
        ("translation", "nonlinear_max", "Largest Nonlinear"),
    ]
    for category, key, label in stat_picks:
        cur_val = _get_stat(cur_stats, category, key)
        ref_val = _get_stat(ref_stats, category, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append(
                {
                    "label": label,
                    "current": _format_value(cur_val),
                    "reference": _format_value(ref_val),
                    "changed": cur_val is not None
                    and ref_val is not None
                    and str(cur_val) != str(ref_val),
                }
            )

    scalar_picks = [
        ("CPUtime", "CPU Time"),
        ("EventCounter", "Events"),
    ]
    for key, label in scalar_picks:
        cur_val = _get_scalar(cur_stats, key)
        ref_val = _get_scalar(ref_stats, key)
        if cur_val is not None or ref_val is not None:
            key_stats.append(
                {
                    "label": label,
                    "current": _format_value(cur_val),
                    "reference": _format_value(ref_val),
                    "changed": cur_val is not None
                    and ref_val is not None
                    and str(cur_val) != str(ref_val),
                }
            )
    return key_stats


def _build_warning_rows(warnings: list | None) -> list:
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


def tree_for_variable(view: dict | None, name: str) -> dict | None:
    """``view`` filtered to the leaves scoring ``name``, ancestors kept.

    Returns ``None`` when no leaf under ``view`` targets the variable, which
    is what drops a combinator whose whole subtree belongs to some other
    variable. Leaves and combinators are shallow-copied: the per-variable
    mount renders the same leaf dicts the full tree does.
    """
    if not view:
        return None
    if view.get("kind") == "leaf":
        return dict(view) if view.get("variable") == name else None
    kept = [
        sub
        for sub in (
            tree_for_variable(child, name) for child in view.get("children") or []
        )
        if sub is not None
    ]
    if not kept:
        return None
    out = dict(view)
    out["children"] = kept
    return out


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
    per-variable mounts read it, so they agree by construction."""
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
            "%s: %d spec leaf path(s) have no evaluated comparison and render "
            "without scores: %s",
            getattr(test_model, "model_id", "?"),
            len(missing),
            ", ".join(missing),
        )
    _augment_tree_view(tree_view, comparisons_by_path, texts)
    # Must run before the per-variable subtrees are cut: they shallow-copy
    # the leaf dicts, so a flag stamped afterwards would reach the full-tree
    # mount only.
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
    paths targeting it — so per-variable plot sections mount a filtered
    view of the same tree.

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
            # The tree as this variable's mount renders it.
            "tree": tree_for_variable(tree_view, vn),
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
    statistics_sections = _build_statistics_sections(ref_stats, cur_stats)
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
