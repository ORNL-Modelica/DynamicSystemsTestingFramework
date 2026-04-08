"""Parse Dymola's dslog.txt and translation_log.txt for statistics."""

import re
from pathlib import Path
from typing import Optional


def parse_dslog(path: Path) -> Optional[dict]:
    """Parse a Dymola log file and extract all available statistics.

    Works with both dslog.txt (simulation runtime stats) and
    translation_log.txt (translation/structural stats).

    Returns a dict with sub-dicts for different stat categories,
    or None if the file cannot be read or has no stats.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    stats = {}

    # --- Translation statistics (from translation_log.txt) ---
    _parse_translation_stats(text, stats)

    # --- Simulation runtime statistics (from dslog.txt) ---
    _parse_simulation_stats(text, stats)

    return stats if stats else None


def _parse_translation_stats(text: str, stats: dict) -> None:
    """Extract translation statistics from a Dymola log."""

    # DAE size
    m = re.search(r'The DAE has (\d+) scalar unknowns and (\d+) scalar equations', text)
    if m:
        stats.setdefault("translation", {})
        stats["translation"]["scalar_unknowns"] = int(m.group(1))
        stats["translation"]["scalar_equations"] = int(m.group(2))

    # Original Model section
    orig = re.search(r'Original Model\s*\n((?:\s+.+\n)+)', text)
    if orig:
        orig_text = orig.group(1)
        _extract_int(orig_text, r'Number of components:\s*(\d+)', stats, "translation", "original_components")
        _extract_int(orig_text, r'Variables:\s*(\d+)', stats, "translation", "original_variables")
        _extract_int(orig_text, r'Unknowns:.*?(\d+)\s+scalars', stats, "translation", "original_unknowns_scalars")
        _extract_int(orig_text, r'Differentiated variables:\s*(\d+)', stats, "translation", "differentiated_variables")
        _extract_int(orig_text, r'Equations:\s*(\d+)', stats, "translation", "original_equations")
        _extract_int(orig_text, r'Nontrivial:\s*(\d+)', stats, "translation", "nontrivial_equations")

    # Translated Model section
    trans = re.search(r'Translated Model\s*\n((?:\s+.+\n)+)', text)
    if trans:
        trans_text = trans.group(1)
        _extract_int(trans_text, r'Constants:\s*(\d+)', stats, "translation", "constants")
        _extract_int(trans_text, r'Outputs:\s*(\d+)', stats, "translation", "outputs")
        _extract_int(trans_text, r'Time-varying variables:\s*(\d+)', stats, "translation", "time_varying_variables")
        _extract_int(trans_text, r'Alias variables:\s*(\d+)', stats, "translation", "alias_variables")
        _extract_int(trans_text, r'Number of mixed real/discrete systems of equations:\s*(\d+)',
                     stats, "translation", "mixed_systems")

    # Continuous time states (from translated model or standalone)
    m = re.search(r'Continuous time states:\s*(\d+)', text)
    if m:
        stats.setdefault("translation", {})
        stats["translation"]["continuous_time_states"] = int(m.group(1))
    # Alternate format
    if "translation" not in stats or "continuous_time_states" not in stats.get("translation", {}):
        m = re.search(r'(\d+)\s+continuous time states', text)
        if m:
            stats.setdefault("translation", {})
            stats["translation"]["continuous_time_states"] = int(m.group(1))

    # Nonlinear/linear system sizes (from translated model section)
    # These appear as "Sizes of nonlinear systems of equations: {N, N}"
    # and "Sizes after manipulation of the nonlinear systems: {N, N}"
    for sys_type in ("nonlinear", "linear"):
        # After manipulation (preferred, more accurate)
        m = re.search(
            rf'Sizes after manipulation of the {sys_type} systems.*?:\s*\{{([^}}]*)\}}',
            text
        )
        if m:
            val = _parse_int_list(m.group(1))
            if val:  # Only store if non-empty
                stats.setdefault("translation", {})
                stats["translation"][f"{sys_type}_after_manipulation"] = val

        # Original sizes
        m = re.search(
            rf'Sizes of {sys_type} systems of equations.*?:\s*\{{([^}}]*)\}}',
            text
        )
        if m:
            val = _parse_int_list(m.group(1))
            if val:
                stats.setdefault("translation", {})
                stats["translation"][f"{sys_type}"] = val

    # Number of numerical Jacobians
    m = re.search(r'Number of numerical Jacobians:\s*(\d+)', text)
    if m:
        stats.setdefault("translation", {})
        stats["translation"]["numerical_jacobians"] = int(m.group(1))

    # Selected continuous time states (list of state variable names)
    states_section = re.search(
        r'(?:Statically|Dynamically) selected continuous time states\s*\n((?:\w[^\n]*\n)+)',
        text
    )
    if states_section:
        state_names = [
            line.strip() for line in states_section.group(1).strip().split("\n")
            if line.strip() and not line.strip().startswith("=")
        ]
        if state_names:
            stats.setdefault("translation", {})
            stats["translation"]["state_names"] = state_names


def _parse_simulation_stats(text: str, stats: dict) -> None:
    """Extract simulation runtime statistics from a Dymola log."""

    # CPU time
    m = re.search(
        r'CPU-time for (?:integration|simulation)\s*[=:]\s*([\d.eE+\-]+)\s*s',
        text, re.IGNORECASE
    )
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["cpu_time"] = float(m.group(1))

    # Result points
    m = re.search(r'Number of result points\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["result_points"] = int(m.group(1))

    # Accepted steps
    m = re.search(r'Number of accepted steps\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["accepted_steps"] = int(m.group(1))

    # F-evaluations
    m = re.search(r'Number of f-evaluations.*?:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["f_evaluations"] = int(m.group(1))

    # Jacobian evaluations
    m = re.search(r'Number of Jacobian-evaluations:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["jacobian_evaluations"] = int(m.group(1))

    # State events
    m = re.search(r'Number of state\s+events\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["state_events"] = int(m.group(1))

    # Step events
    m = re.search(r'Number of step\s+events\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["step_events"] = int(m.group(1))

    # Model time events
    m = re.search(r'Number of model time events\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["model_time_events"] = int(m.group(1))

    # Input time events
    m = re.search(r'Number of input time events\s*:\s*(\d+)', text)
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["input_time_events"] = int(m.group(1))


def _extract_int(text: str, pattern: str, stats: dict, category: str, key: str) -> None:
    """Extract an integer value and store it in stats[category][key]."""
    m = re.search(pattern, text)
    if m:
        stats.setdefault(category, {})
        stats[category][key] = int(m.group(1))


def _parse_int_list(text: str) -> str:
    """Parse a comma-separated list from Dymola log, preserving as string.

    Dymola formats these as e.g. '7, 3, 1' — we store as-is for readability.
    Returns empty string for empty lists (e.g., '{ }').
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return ", ".join(parts)
