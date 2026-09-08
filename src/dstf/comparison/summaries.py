"""Per-mode :class:`~.types.ScoreSummary` builders.

Each function turns one mode's ``VariableComparison`` into the pill text and
criterion sentence the report and the terminal review loop show. They are
attached to the mode's :class:`~.registry.MetricDescriptor` and dispatched
by :func:`~.registry.summarize`; a third-party metric supplies its own or
inherits :func:`summarize_default`. No mode name appears outside this file
and the registrations in :mod:`.builtins`.
"""

from __future__ import annotations

import math

from .types import ScoreSummary, VariableComparison


def _verdict(vc: VariableComparison) -> str:
    return "PASS" if vc.passed else "FAIL"


def summarize_default(vc: VariableComparison) -> ScoreSummary:
    """NRMSE-shaped: the score is an error against a tolerance."""
    return ScoreSummary(
        display=f"NRMSE {vc.nrmse:.3e}",
        criterion=(
            f"NRMSE {vc.nrmse:.3e} vs tolerance {vc.tolerance_used:.3e} → {_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="NRMSE",
    )


summarize_nrmse = summarize_default


def summarize_tube(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_points_inside is None:
        return summarize_default(vc)
    pct = vc.tube_points_inside * 100
    return ScoreSummary(
        display=f"{pct:.1f}% in tube",
        criterion=f"{pct:.1f}% inside tube → {_verdict(vc)} (requires 100%)",
        score=float(vc.tube_points_inside),
        score_label="in tube",
    )


def summarize_range(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_worst_violation is None:
        return summarize_default(vc)
    return ScoreSummary(
        display=f"max_viol {vc.tube_worst_violation:.3e}",
        criterion=(
            f"max violation {vc.tube_worst_violation:.3e} → {_verdict(vc)} (requires 0)"
        ),
        score=float(vc.tube_worst_violation),
        score_label="max violation",
    )


def summarize_points(vc: VariableComparison) -> ScoreSummary:
    return ScoreSummary(
        display=f"|err| {vc.max_abs_error:.3e}",
        criterion=(
            f"Final value error {vc.max_abs_error:.3e} vs tolerance "
            f"{vc.tolerance_used:.3e} → {_verdict(vc)}"
        ),
        score=float(vc.max_abs_error),
        score_label="|err|",
    )


def summarize_scalar(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    verdict = _verdict(vc)
    if not math.isfinite(vc.actual_final):
        # Hard-fail sentinel (unresolvable quantity, no samples): there is no
        # scalar to show, so surface the reason instead of "nan vs nan".
        reason = vc.name or "quantity could not be resolved"
        return ScoreSummary(
            display="not scored", criterion=f"Scalar leaf not scored — {reason}"
        )
    lo, hi = diag.get("min_value"), diag.get("max_value")
    if lo is not None or hi is not None:
        lo_s = "-inf" if lo is None else f"{lo:g}"
        hi_s = "inf" if hi is None else f"{hi:g}"
        return ScoreSummary(
            display=f"value {vc.actual_final:.6g}",
            criterion=f"Scalar {vc.actual_final:.6g} within [{lo_s}, {hi_s}] → {verdict}",
            score=float(vc.actual_final),
            score_label="value",
        )
    fallback = bool(diag.get("abs_fallback"))
    rel = diag.get("tolerance_mode") == "rel" and not fallback
    criterion = "relative" if rel else "absolute"
    note = " (|ref|=0 → absolute)" if fallback else ""
    return ScoreSummary(
        display=(f"rel Δ {vc.nrmse:.3e}" if rel else f"|Δ| {vc.nrmse:.3e}"),
        criterion=(
            f"Scalar {vc.actual_final:.6g} vs baseline {vc.reference_final:.6g}: "
            f"{criterion} deviation {vc.nrmse:.3e} vs "
            f"tolerance {vc.tolerance_used:.3e}{note} → {verdict}"
        ),
        score=float(vc.nrmse),
        score_label="rel Δ" if rel else "|Δ|",
    )


def summarize_event_timing(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    ref_n = diag.get("ref_event_count", "?")
    act_n = diag.get("act_event_count", "?")
    tol = diag.get("time_tolerance", 0.0)
    return ScoreSummary(
        display=f"Δt {vc.nrmse:.3e} ({act_n}/{ref_n} events)",
        criterion=(
            f"Max event Δt {vc.nrmse:.3e} vs {tol:.3e} "
            f"({act_n} actual events / {ref_n} reference) → {_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="Δt",
    )


def summarize_dominant_frequency(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    paired = diag.get("paired_peaks") or []
    n_matched = sum(1 for p in paired if p.get("matched_hz") is not None)
    n_declared = len(paired)
    if n_declared == 0:
        return ScoreSummary(
            display="no peaks declared",
            criterion=(
                "No peaks declared — use 'Detect peaks from reference' "
                "to seed the table from the reference spectrum"
            ),
        )
    return ScoreSummary(
        display=f"{n_matched}/{n_declared} peaks matched",
        criterion=(
            f"{n_matched} / {n_declared} declared peaks found in actual "
            f"spectrum within tolerance → {_verdict(vc)}"
        ),
        score=float(n_matched),
        score_label="peaks matched",
    )
