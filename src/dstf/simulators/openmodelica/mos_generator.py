"""Pure-text builders for OpenModelica .mos scripts.

No I/O beyond string assembly — trivially unit-testable. The runner calls
:func:`build_simulate_mos` per test, writes the result to
``<test_dir>/simulate.mos``, then invokes ``omc`` on it.

The .mos does NOT emit explicit timing output. When ``omc`` runs a script
non-interactively it REPL-echoes the top-level ``simulate(...)`` call as a
``record SimulationResult ... end SimulationResult;`` block that already
contains ``resultFile``, ``messages``, and the ``timeFrontend / Backend /
SimCode / Templates / Compile / Simulation / Total`` fields — everything we
need. ``log_parser.parse_omc_stdout`` pulls them from that block, so the
.mos stays small and the print/sentinel dance proved to be silently
unreliable (``res`` isn't in scope for the follow-up prints).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from ...discovery.test_registry import TestModel
from ..base import _pattern_to_regex


def classify_dependency(entry: str) -> tuple[str, str]:
    """Classify a Config.dependencies entry as loadModel vs loadFile.

    Returns (``"loadModel"``, name) for bare library names (no path
    separators, doesn't look like a file), or (``"loadFile"``,
    absolute_path) for path-like entries (resolves to a package.mo).
    """
    # Path-like if it contains a separator, ends in .mo, or resolves to an
    # existing file.
    looks_like_path = "/" in entry or "\\" in entry or entry.endswith(".mo")
    if not looks_like_path:
        return ("loadModel", entry)

    p = Path(entry)
    if entry.endswith(".mo"):
        resolved = p.resolve() if p.is_absolute() else p.resolve()
        return ("loadFile", str(resolved).replace("\\", "/"))
    # Directory form — append package.mo
    resolved = p.resolve() / "package.mo"
    return ("loadFile", str(resolved).replace("\\", "/"))


def build_variable_filter(
    patterns: Iterable[str],
    diagnostic_vars: Iterable[str],
    extra_names: Iterable[str] = (),
) -> str:
    """Build OM's ``variableFilter`` regex for the given tracked-variable set.

    OM's ``variableFilter`` is a regex over variable names. We escape each
    name literally and join with ``|``. ``time`` is always included; so are
    the diagnostic variables and any ``extra_names`` the runner passes
    (e.g. ``unitTests.x[1]``, ..., ``unitTests.x[N]`` for unit_tests-sourced
    tests). Returning a regex (not ``.*``) keeps the .mat small — OM dumps
    all parameters/aliases/derivatives by default.
    """
    alternatives: list[str] = []
    # Always include time
    alternatives.append(re.escape("time"))
    # Tracked variables: expand globs (*, ?) to regex via the framework's
    # existing glob-to-regex helper, so a pattern like ``pipe.T*`` becomes
    # ``pipe\.T.*`` and the filter actually matches the runtime variable
    # names (post-simulation ``resolve_variable_patterns`` still narrows
    # the set for the pass/fail comparison).
    for pat in patterns:
        alternatives.append(_pattern_to_regex(pat).pattern)
    # Extra literal names (no globs). Runner uses this for UnitTests
    # component variables (``unitTests.x[i]``) that the spec doesn't list.
    for n in extra_names:
        alternatives.append(re.escape(n))
    # Diagnostic variables (literal names — no globs)
    for dv in diagnostic_vars:
        alternatives.append(re.escape(dv))
    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for a in alternatives:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    # OM's variableFilter is a POSIX ERE applied with partial-match semantics
    # by default — an unanchored alternation would over-match (e.g. "x" would
    # hit "phi" or "x_derivative"). Anchor with ^(...)$ to force whole-name
    # match.
    return "^(" + "|".join(unique) + ")$"


def _format_sim_kwarg(key: str, value) -> str:
    """Render one key=value for OM's simulate() call."""
    if isinstance(value, str):
        # OM escape: double-quote and escape backslashes + quotes
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    if isinstance(value, bool):
        return f"{key}={'true' if value else 'false'}"
    if isinstance(value, float):
        # Avoid scientific notation for integer-valued floats, use repr otherwise.
        if value == int(value):
            return f"{key}={value!r}"
        return f"{key}={value!r}"
    return f"{key}={value!r}"


def build_simulate_args(
    test: TestModel,
    diagnostic_vars: Iterable[str],
    extra_filter_names: Iterable[str] = (),
) -> list[str]:
    """Build the ``key=value`` strings for an OM ``simulate(...)`` call.

    Pure string assembly; used both by :func:`build_simulate_mos` (batch
    path) and by the persistent-worker runner, which passes the same args
    to ``session.sendExpression("simulate(<model>, <args>)")``.

    OM's ``simulate()`` has ``numberOfIntervals`` but not ``outputInterval``
    (Dymola's ``simulateModel`` has the latter), so we convert
    ``output_interval`` → ``numberOfIntervals`` when that's what the test
    specified. Solver names are lowercased here so a framework-wide
    Dymola-style ``"Dassl"`` survives the crossing.
    """
    args: list[str] = []
    args.append(f"stopTime={float(test.stop_time)!r}")
    if test.number_of_intervals is not None:
        args.append(f"numberOfIntervals={int(test.number_of_intervals)}")
    elif test.output_interval is not None and test.output_interval > 0:
        n_intervals = max(
            1, int(round(float(test.stop_time) / float(test.output_interval)))
        )
        args.append(f"numberOfIntervals={n_intervals}")
    args.append(f"tolerance={float(test.tolerance)!r}")
    args.append(_format_sim_kwarg("method", (test.method or "dassl").lower()))
    args.append('outputFormat="mat"')
    args.append('fileNamePrefix="result"')
    var_filter = build_variable_filter(
        test.variable_patterns,
        diagnostic_vars,
        extra_names=extra_filter_names,
    )
    args.append(_format_sim_kwarg("variableFilter", var_filter))
    return args


def build_simulate_mos(
    *,
    test: TestModel,
    test_dir: Path,
    library_package_mo: Path,
    dependencies: list[str],
    simulator_setup: list[str],
    diagnostic_vars: list[str],
    std_version: str = "latest",
    extra_filter_names: Iterable[str] = (),
) -> str:
    """Assemble the full per-test .mos script.

    See the module docstring for the shape. All path arguments are emitted
    with forward slashes (OM accepts them on Windows too, and it sidesteps
    Modelica string-escaping of backslashes).
    """

    def fwd(path: Path) -> str:
        return str(path).replace("\\", "/")

    lines: list[str] = []
    lines.append(f'setCommandLineOptions("--std={std_version}");')

    for dep in dependencies:
        kind, arg = classify_dependency(dep)
        if kind == "loadModel":
            lines.append(f"loadModel({arg});")
        else:
            lines.append(f'loadFile("{arg}");')
        lines.append("getErrorString();")

    # Main library
    lines.append(f'loadFile("{fwd(library_package_mo)}");')
    lines.append("getErrorString();")

    # Setup commands (backend-specific; user owns these)
    for cmd in simulator_setup:
        c = cmd.strip()
        if not c.endswith(";"):
            c = c + ";"
        lines.append(c)

    lines.append(f'cd("{fwd(test_dir)}");')

    sim_args = build_simulate_args(test, diagnostic_vars, extra_filter_names)
    # Bare simulate(...) — omc REPL-echoes the SimulationResult record to
    # stdout, which log_parser parses. No ``res := ...`` assignment and no
    # follow-up prints (see module docstring).
    lines.append(f"simulate({test.model_id}, {', '.join(sim_args)});")
    lines.append("getErrorString();")

    return "\n".join(lines) + "\n"
