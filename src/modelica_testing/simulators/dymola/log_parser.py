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

    # Translated Model section — need to separate simulation vs initialization
    # The section structure is:
    #   Translated Model
    #     <simulation-level stats>
    #     Initialization problem
    #       <initialization-level stats>
    trans_match = re.search(r'Translated Model\s*\n((?:\s+.+\n)+)', text)
    if trans_match:
        full_trans_text = trans_match.group(1)

        # Split at "Initialization problem" if present
        init_split = re.split(r'^\s+Initialization problem\s*$', full_trans_text, flags=re.MULTILINE)
        sim_text = init_split[0]
        init_text = init_split[1] if len(init_split) > 1 else ""

        # --- Simulation-level translated model stats ---
        _extract_int(sim_text, r'Constants:\s*(\d+)', stats, "translation", "constants")
        _extract_int(sim_text, r'Free parameters:\s*(\d+)', stats, "translation", "free_parameters")
        _extract_int(sim_text, r'Parameter depending:\s*(\d+)', stats, "translation", "parameter_depending")
        _extract_int(sim_text, r'Outputs:\s*(\d+)', stats, "translation", "outputs")
        _extract_int(sim_text, r'Continuous time states:\s*(\d+)', stats, "translation", "continuous_time_states")
        _extract_int(sim_text, r'Time-varying variables:\s*(\d+)', stats, "translation", "time_varying_variables")
        _extract_int(sim_text, r'Alias variables:\s*(\d+)', stats, "translation", "alias_variables")
        _extract_int(sim_text, r'Number of mixed real/discrete systems of equations:\s*(\d+)',
                     stats, "translation", "mixed_systems")
        _extract_int(sim_text, r'Number of numerical Jacobians:\s*(\d+)',
                     stats, "translation", "numerical_jacobians")

        # Simulation-level equation system sizes
        _extract_system_sizes(sim_text, stats, "translation", prefix="")

        # --- Initialization problem stats ---
        if init_text:
            _extract_int(init_text, r'Number of mixed real/discrete systems of equations:\s*(\d+)',
                         stats, "translation", "init_mixed_systems")
            _extract_int(init_text, r'Number of numerical Jacobians:\s*(\d+)',
                         stats, "translation", "init_numerical_jacobians")

            _extract_system_sizes(init_text, stats, "translation", prefix="init_")

            # Homotopy nonlinear systems (initialization only)
            _extract_system_pair(
                init_text,
                r'Sizes of simplified homotopy nonlinear systems of equations',
                r'Sizes after manipulation of the simplified homotopy nonlinear systems',
                stats, "translation", "init_homotopy_nonlinear",
            )

    # Continuous time states — fallback if not found in Translated Model section
    if "translation" not in stats or "continuous_time_states" not in stats.get("translation", {}):
        m = re.search(r'Continuous time states:\s*(\d+)', text)
        if not m:
            m = re.search(r'(\d+)\s+continuous time states', text)
        if m:
            stats.setdefault("translation", {})
            stats["translation"]["continuous_time_states"] = int(m.group(1))

    # Selected continuous time states (list of state variable names)
    # Capture lines after "Statically selected continuous time states" that look
    # like Modelica variable names (start with a letter, contain dots/brackets).
    # Stop at "Dynamically selected", "Warning:", blank lines, or non-variable lines.
    states_match = re.search(
        r'Statically selected continuous time states\s*\n((?:(?!Dynamically|Warning|There are|From set|=)[a-zA-Z][\w.\[\], ]*\n)+)',
        text
    )
    if states_match:
        state_names = [
            line.strip() for line in states_match.group(1).strip().split("\n")
            if line.strip()
        ]
        if state_names:
            stats.setdefault("translation", {})
            stats["translation"]["state_names"] = state_names


def _extract_system_sizes(
    text: str, stats: dict, category: str, prefix: str,
) -> None:
    """Extract nonlinear and linear system sizes from a log section.

    Parses both original sizes and after-manipulation sizes, stores as
    integer lists with summary fields (count, total, max).
    """
    for sys_type in ("nonlinear", "linear"):
        # Original sizes
        _extract_system_pair_simple(
            text, sys_type, stats, category, f"{prefix}{sys_type}",
        )


def _extract_system_pair_simple(
    text: str,
    sys_type: str,
    stats: dict,
    category: str,
    key_base: str,
) -> None:
    """Extract original + after-manipulation sizes for a system type."""
    # Original sizes: "Sizes of <type> systems of equations: {N, N, ...}"
    m = re.search(
        rf'Sizes of {sys_type} systems of equations\s*:\s*\{{([^}}]*)\}}',
        text
    )
    if m:
        values = _parse_int_list(m.group(1))
        stats.setdefault(category, {})
        stats[category][key_base] = values
        if values:
            stats[category][f"{key_base}_count"] = len(values)
            stats[category][f"{key_base}_total"] = sum(values)
            stats[category][f"{key_base}_max"] = max(values)

    # After manipulation: "Sizes after manipulation of the <type> systems: {N, N, ...}"
    m = re.search(
        rf'Sizes after manipulation of the {sys_type} systems\s*:\s*\{{([^}}]*)\}}',
        text
    )
    if m:
        values = _parse_int_list(m.group(1))
        key = f"{key_base}_after_manipulation"
        stats.setdefault(category, {})
        stats[category][key] = values
        if values:
            stats[category][f"{key}_count"] = len(values)
            stats[category][f"{key}_total"] = sum(values)
            stats[category][f"{key}_max"] = max(values)


def _extract_system_pair(
    text: str,
    original_pattern: str,
    manipulation_pattern: str,
    stats: dict,
    category: str,
    key_base: str,
) -> None:
    """Extract a pair of system size lists (original + after manipulation)."""
    m = re.search(rf'{original_pattern}\s*:\s*\{{([^}}]*)\}}', text)
    if m:
        values = _parse_int_list(m.group(1))
        stats.setdefault(category, {})
        stats[category][key_base] = values
        if values:
            stats[category][f"{key_base}_count"] = len(values)
            stats[category][f"{key_base}_total"] = sum(values)
            stats[category][f"{key_base}_max"] = max(values)

    m = re.search(rf'{manipulation_pattern}\s*:\s*\{{([^}}]*)\}}', text)
    if m:
        values = _parse_int_list(m.group(1))
        key = f"{key_base}_after_manipulation"
        stats.setdefault(category, {})
        stats[category][key] = values
        if values:
            stats[category][f"{key}_count"] = len(values)
            stats[category][f"{key}_total"] = sum(values)
            stats[category][f"{key}_max"] = max(values)


def _parse_simulation_stats(text: str, stats: dict) -> None:
    """Extract simulation runtime statistics from a Dymola log."""

    # CPU time for integration (from dslog — just the integration step,
    # excludes init / output writing. Distinct from the `CPUtime` diagnostic
    # variable's final value which covers the full simulation.)
    m = re.search(
        r'CPU-time for (?:integration|simulation)\s*[=:]\s*([\d.eE+\-]+)\s*s',
        text, re.IGNORECASE
    )
    if m:
        stats.setdefault("simulation", {})
        stats["simulation"]["cpu_time_integration"] = float(m.group(1))

    # All other simulation stats use the same pattern: label with possible
    # whitespace padding before the colon
    _SIM_PATTERNS = [
        (r'Number of result points\s*:\s*(\d+)', "result_points"),
        (r'Number of accepted steps\s*:\s*(\d+)', "accepted_steps"),
        (r'Number of f-evaluations.*?:\s*(\d+)', "f_evaluations"),
        (r'Number of Jacobian-evaluations\s*:\s*(\d+)', "jacobian_evaluations"),
        (r'Number of state\s+events\s*:\s*(\d+)', "state_events"),
        (r'Number of step\s+events\s*:\s*(\d+)', "step_events"),
        (r'Number of model time events\s*:\s*(\d+)', "model_time_events"),
        (r'Number of input time events\s*:\s*(\d+)', "input_time_events"),
    ]

    for pattern, key in _SIM_PATTERNS:
        m = re.search(pattern, text)
        if m:
            stats.setdefault("simulation", {})
            stats["simulation"][key] = int(m.group(1))


def _extract_int(text: str, pattern: str, stats: dict, category: str, key: str) -> None:
    """Extract an integer value and store it in stats[category][key]."""
    m = re.search(pattern, text)
    if m:
        stats.setdefault(category, {})
        stats[category][key] = int(m.group(1))


def _parse_int_list(text: str) -> list[int]:
    """Parse a comma-separated integer list from Dymola log.

    Dymola formats these as e.g. '{7, 3, 1}' — we parse to a list of ints.
    Returns empty list for empty/whitespace-only content (e.g., '{ }').
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            pass
    return result
