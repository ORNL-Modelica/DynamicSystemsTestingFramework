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

from .sides import parse_sides, sides_text, signed
from .types import ScoreSummary, VariableComparison


def _verdict(vc: VariableComparison) -> str:
    return "PASS" if vc.passed else "FAIL"


def _at(value: float, time: float) -> str:
    return f"{value:.6e} at t={time:g}"


def _finals(vc: VariableComparison) -> tuple[str, str]:
    return ("final", f"ref={vc.reference_final:.6e}  act={vc.actual_final:.6e}")


def _trajectory_details(vc: VariableComparison) -> tuple[tuple[str, str], ...]:
    """The pairs behind an NRMSE score: what the review loop printed for
    every variable before summaries became mode-aware."""
    if vc.is_constant:
        spread: tuple[str, str] = ("RMSE", f"{vc.rmse:.6e} (constant signal)")
    else:
        spread = ("RMSE", f"{vc.rmse:.6e} (range {vc.signal_range:.4e})")
    return (
        spread,
        ("max |err|", _at(vc.max_abs_error, vc.max_abs_error_time)),
        _finals(vc),
    )


def summarize_default(vc: VariableComparison) -> ScoreSummary:
    """NRMSE-shaped: the score is an error against a tolerance."""
    return ScoreSummary(
        display=f"NRMSE {vc.nrmse:.3e}",
        criterion=(
            f"NRMSE {vc.nrmse:.3e} vs tolerance {sides_text(vc.tolerance_used)} → "
            f"{_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="NRMSE",
        details=_trajectory_details(vc),
    )


summarize_nrmse = summarize_default


def summarize_tube(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_points_inside is None:
        return summarize_default(vc)
    pct = vc.tube_points_inside * 100
    details: list[tuple[str, str]] = []
    if vc.tube_worst_violation is not None and vc.tube_worst_violation > 0:
        details.append(
            (
                "worst violation",
                _at(vc.tube_worst_violation, vc.tube_worst_violation_time or 0.0),
            )
        )
    # The tube comparator computes the NRMSE companions for real.
    details.append(("NRMSE", f"{vc.nrmse:.6e}"))
    details.append(("max |err|", _at(vc.max_abs_error, vc.max_abs_error_time)))
    details.append(_finals(vc))
    return ScoreSummary(
        display=f"{pct:.1f}% in tube",
        criterion=f"{pct:.1f}% inside tube → {_verdict(vc)} (requires 100%)",
        score=float(vc.tube_points_inside),
        score_label="in tube",
        details=tuple(details),
    )


def summarize_range(vc: VariableComparison) -> ScoreSummary:
    if vc.tube_worst_violation is None:
        return summarize_default(vc)
    diag = vc.diagnostics or {}
    lo, hi = diag.get("min_value"), diag.get("max_value")
    lo_s = "-inf" if lo is None else f"{lo:g}"
    hi_s = "inf" if hi is None else f"{hi:g}"
    details: list[tuple[str, str]] = [("bounds", f"[{lo_s}, {hi_s}]")]
    if vc.tube_worst_violation > 0:
        details.append(
            (
                "worst violation",
                _at(vc.tube_worst_violation, vc.tube_worst_violation_time or 0.0),
            )
        )
    if vc.tube_points_inside is not None:
        details.append(("inside", f"{vc.tube_points_inside * 100:.1f}% of samples"))
    return ScoreSummary(
        display=f"max_viol {vc.tube_worst_violation:.3e}",
        criterion=(
            f"max violation {vc.tube_worst_violation:.3e} → {_verdict(vc)} (requires 0)"
        ),
        score=float(vc.tube_worst_violation),
        score_label="max violation",
        details=tuple(details),
    )


def summarize_points(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    details: list[tuple[str, str]] = []
    if "scored_points" in diag:
        details.append(
            (
                "checkpoints",
                f"{diag.get('failed_points', 0)} of {diag['scored_points']} failed",
            )
        )
        if diag.get("worst_delta", 0.0) > 0:
            details.append(
                ("worst delta", _at(diag["worst_delta"], diag.get("worst_time", 0.0)))
            )
        for failure in diag.get("point_failures") or []:
            details.append(
                (f"t={failure.get('time'):g}", str(failure.get("reason", "")))
            )
    else:
        details.append(_finals(vc))
    # Equal sides: the magnitude, as a number always printed. Uneven ones:
    # actual − target, signed, so the reader sees which side applies — of
    # the declared point furthest outside its band, or of the largest |Δ|
    # when none is (recorded by the scorer), or the final value's.
    error = f"{vc.max_abs_error:.3e}"
    if not vc.tolerance_used.equal:
        if "scored_points" in diag:
            error = signed(diag.get("worst_signed_delta", vc.max_abs_error))
        else:
            error = signed(vc.actual_final - vc.reference_final)
    return ScoreSummary(
        display=f"|err| {vc.max_abs_error:.3e}",
        criterion=(
            f"Final value error {error} vs tolerance "
            f"{sides_text(vc.tolerance_used)} → {_verdict(vc)}"
        ),
        score=float(vc.max_abs_error),
        score_label="|err|",
        details=tuple(details),
    )


def summarize_scalar(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    verdict = _verdict(vc)
    if not math.isfinite(vc.actual_final):
        # Hard-fail sentinel (unresolvable quantity, no samples, a reduction
        # that came out non-finite): there is no scalar to show, so surface
        # the reason instead of "nan vs nan". The sentinel keeps the
        # variable's NAME, so the name is never the reason — every route that
        # builds one stamps ``diagnostics["error"]``.
        reason = diag.get("error") or "quantity could not be resolved"
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
            details=(("reduction", str(diag.get("reduction"))),),
        )
    fallback = bool(diag.get("abs_fallback"))
    rel = diag.get("tolerance_mode") == "rel" and not fallback
    criterion = "relative" if rel else "absolute"
    note = " (|ref|=0 → absolute)" if fallback else ""
    # Uneven sides: the signed score, as the scorer judged it (`d / |ref|`
    # in rel) — the same two operations on the same two numbers.
    deviation = f"{vc.nrmse:.3e}"
    if not vc.tolerance_used.equal:
        d = vc.actual_final - vc.reference_final
        deviation = signed(d / abs(vc.reference_final) if rel else d)
    return ScoreSummary(
        display=(f"rel Δ {vc.nrmse:.3e}" if rel else f"|Δ| {vc.nrmse:.3e}"),
        criterion=(
            f"Scalar {vc.actual_final:.6g} vs baseline {vc.reference_final:.6g}: "
            f"{criterion} deviation {deviation} vs "
            f"tolerance {sides_text(vc.tolerance_used)}{note} → {verdict}"
        ),
        score=float(vc.nrmse),
        score_label="rel Δ" if rel else "|Δ|",
        details=(
            ("reduction", str(diag.get("reduction"))),
            ("scalar", f"ref={vc.reference_final:.6g}  act={vc.actual_final:.6g}"),
        ),
    )


def summarize_event_timing(vc: VariableComparison) -> ScoreSummary:
    diag = vc.diagnostics or {}
    ref_n = diag.get("ref_event_count", "?")
    act_n = diag.get("act_event_count", "?")
    sides = parse_sides(diag.get("time_tolerance", 0.0), "time_tolerance")
    tol = sides_text(sides)
    # Uneven sides: Δt signed (late positive) — of the pair furthest
    # outside its window, or of the largest |Δt| when none is — recorded by
    # the scorer.
    delta = f"{vc.nrmse:.3e}"
    if not sides.equal:
        delta = signed(diag.get("max_time_delta_signed", vc.nrmse))
    details: list[tuple[str, str]] = [
        ("events", f"{act_n} actual / {ref_n} reference"),
        ("max Δt", _at(vc.nrmse, vc.max_abs_error_time)),
    ]
    if "counts_match" in diag and not diag["counts_match"]:
        details.append(("event count", "mismatch"))
    return ScoreSummary(
        display=f"Δt {vc.nrmse:.3e} ({act_n}/{ref_n} events)",
        criterion=(
            f"Max event Δt {delta} vs {tol} "
            f"({act_n} actual events / {ref_n} reference) → {_verdict(vc)}"
        ),
        score=float(vc.nrmse),
        score_label="Δt",
        details=tuple(details),
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
                "No peaks declared — declare them in the leaf's `peaks` "
                "list; the reference's detected peaks are in the "
                "diagnostics as `detected_reference_peaks_hz`"
            ),
        )
    details = tuple(
        (
            f"peak {p.get('declared_hz', '?')} Hz",
            (
                f"matched at {p['matched_hz']:.6g} Hz"
                if p.get("matched_hz") is not None
                else "not found"
            ),
        )
        for p in paired
    )
    return ScoreSummary(
        display=f"{n_matched}/{n_declared} peaks matched",
        criterion=(
            f"{n_matched} / {n_declared} declared peaks found in actual "
            f"spectrum within tolerance → {_verdict(vc)}"
        ),
        score=float(n_matched),
        score_label="peaks matched",
        details=details,
    )
