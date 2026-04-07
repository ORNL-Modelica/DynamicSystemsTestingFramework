"""Parse Dymola's dslog.txt for translation and simulation statistics."""

import re
from pathlib import Path
from typing import Optional


def parse_dslog(path: Path) -> Optional[dict]:
    """Parse a dslog.txt file and extract all available statistics.

    Dymola writes dslog.txt to the working directory after each simulation.
    It contains translation statistics (equation system sizes, Jacobians)
    split into initialization and simulation phases.

    Returns a dict with 'initialization' and 'simulation' sub-dicts,
    or None if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    stats = {
        "initialization": {},
        "simulation": {},
    }

    # Split into initialization and simulation sections.
    # Dymola logs "Initialization problem" then later the simulation stats.
    init_section, sim_section = _split_sections(text)

    if init_section:
        stats["initialization"] = _extract_stats(init_section)
    if sim_section:
        stats["simulation"] = _extract_stats(sim_section)

    # Number of continuous time states (appears once, applies to simulation)
    m = re.search(r'Number of continuous time states:\s*(\d+)', text)
    if m:
        stats["simulation"]["continuous_time_states"] = int(m.group(1))

    # Also try alternate format
    if "continuous_time_states" not in stats["simulation"]:
        m = re.search(r'(\d+)\s+continuous time states', text)
        if m:
            stats["simulation"]["continuous_time_states"] = int(m.group(1))

    # Translation time
    m = re.search(r'Translation of .+ completed successfully\.\s*\n.*?Elapsed time:\s*([\d.]+)\s*s', text)
    if m:
        stats["translation_time"] = float(m.group(1))

    # Simulation CPU time
    m = re.search(r'CPU-time for (?:integration|simulation)\s*[=:]\s*([\d.eE+\-]+)\s*s', text, re.IGNORECASE)
    if m:
        stats["simulation"]["cpu_time"] = float(m.group(1))

    # Total integration steps
    m = re.search(r'Number of (?:result|integration) intervals:\s*(\d+)', text)
    if m:
        stats["simulation"]["result_intervals"] = int(m.group(1))

    # Number of F-evaluations
    m = re.search(r'Number of F-evaluations:\s*(\d+)', text)
    if m:
        stats["simulation"]["f_evaluations"] = int(m.group(1))

    # Number of H-evaluations
    m = re.search(r'Number of H-evaluations:\s*(\d+)', text)
    if m:
        stats["simulation"]["h_evaluations"] = int(m.group(1))

    # Number of Jacobian evaluations
    m = re.search(r'Number of Jacobian-evaluations:\s*(\d+)', text)
    if m:
        stats["simulation"]["jacobian_evaluations"] = int(m.group(1))

    # Number of state events
    m = re.search(r'Number of state\s+events:\s*(\d+)', text)
    if m:
        stats["simulation"]["state_events"] = int(m.group(1))

    # Number of step events
    m = re.search(r'Number of step\s+events:\s*(\d+)', text)
    if m:
        stats["simulation"]["step_events"] = int(m.group(1))

    # Clean empty sub-dicts
    if not stats["initialization"]:
        del stats["initialization"]
    if not stats["simulation"]:
        del stats["simulation"]

    return stats if stats else None


def _split_sections(text: str) -> tuple[str, str]:
    """Split dslog.txt into initialization and simulation sections."""
    # Look for the initialization marker
    init_match = re.search(r'(?:Initialization problem|integration started)', text, re.IGNORECASE)
    if not init_match:
        return "", text

    # The section before "integration started" or after "Initialization" is init
    # Try to find where simulation section starts
    sim_markers = [
        r'Integration started',
        r'integration started',
        r'Continuous simulation',
    ]

    sim_start = len(text)
    for marker in sim_markers:
        m = re.search(marker, text)
        if m and m.start() < sim_start:
            sim_start = m.start()

    init_section = text[:sim_start]
    sim_section = text[sim_start:]

    return init_section, sim_section


def _extract_stats(section: str) -> dict:
    """Extract equation system statistics from a log section."""
    stats = {}

    # Sizes of nonlinear systems of equations: {N, N, ...}
    # We want "after manipulation" if available, else raw sizes
    m = re.search(r'Sizes after manipulation of the nonlinear systems.*?:\s*\{([^}]*)\}', section)
    if not m:
        m = re.search(r'Sizes of nonlinear systems of equations.*?:\s*\{([^}]*)\}', section)
    if m:
        stats["nonlinear"] = _parse_int_list(m.group(1))

    # Sizes of linear systems
    m = re.search(r'Sizes after manipulation of the linear systems.*?:\s*\{([^}]*)\}', section)
    if not m:
        m = re.search(r'Sizes of linear systems of equations.*?:\s*\{([^}]*)\}', section)
    if m:
        stats["linear"] = _parse_int_list(m.group(1))

    # Number of numerical Jacobians
    m = re.search(r'Number of numerical Jacobians:\s*(\d+)', section)
    if m:
        stats["numerical_jacobians"] = int(m.group(1))

    return stats


def _parse_int_list(text: str) -> str:
    """Parse a comma-separated list from Dymola log, preserving as string.

    Dymola formats these as e.g. '7, 3, 1' — we store as-is for readability
    and backward compatibility with buildingspy format.
    """
    # Clean up whitespace but preserve the comma-separated format
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return ", ".join(parts)
