"""Python <-> JS scorer parity tests.

The HTML report's live-edit UX (Phase 6 reporter-as-IDE) requires the
browser to recompute pass/fail as the user drags tube widths, edits
tolerances, etc. — without a server round-trip. So ``interactive.js``
re-implements the simple-math scorers in JS (``MODE_SCORERS`` table).
The vision doc (vision.md:106) calls this a deliberate split: simple
algorithms (``nrmse`` / ``tube`` / ``range`` / ``points``) get JS
recompute; numerically-subtle ones (``event-timing``, ``dominant-frequency``)
are CLI-authoritative.

These tests catch *drift* between the Python and JS implementations.
Each fixture builds a ref/act trajectory pair, runs the actual Python
``_compare_*`` function for the authoritative verdict, renders the
fixture into ``interactive.html``, then evaluates ``MODE_SCORERS`` from
the JS side via Playwright and asserts both sides reach the same
verdict.

Skipped when Playwright isn't installed — same gate as
``test_interactive_playwright.py``.

To extend: add a (mode, verdict, ref, act, params) row to ``_PARITY_CASES``
in this file. The same row drives both the Python truth and the JS
verdict, so adding a case is a one-place edit.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, sync_playwright


_JS_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dstf"
    / "reporting"
    / "templates"
    / "interactive.js"
)
_TEMPLATE_DIR = _JS_SRC.parent


# ---------------------------------------------------------------------------
# Fixture trajectories — small synthetic signals with known scores
# ---------------------------------------------------------------------------


def _linspace(n: int = 50) -> np.ndarray:
    return np.linspace(0.0, 1.0, n)


def _trajectory_for(mode: str, verdict: str) -> dict:
    """Return ``{ref_time, ref_values, act_time, act_values}`` for the
    given mode + verdict (``pass`` / ``fail``). Verdict is the *expected*
    outcome under that fixture's params (declared alongside in
    ``_PARITY_CASES``) — so ``pass`` means "Python and JS should both
    agree this passes," not "every conceivable params would pass."
    """
    t = _linspace()
    if mode == "nrmse":
        # ref = ramp; act = ramp + small noise vs large noise
        ref = 0.5 + 0.3 * t
        offset = 1e-4 if verdict == "pass" else 5e-2
        act = ref + offset
        return _traj(t, ref, t, act)
    if mode == "tube":
        # ref = sine shifted +1; act = ref + offset that fits or escapes
        # a 5% relative tube. ref >= 0.5 keeps relative tube meaningful.
        ref = 1.5 + 0.4 * np.sin(2 * np.pi * t)
        offset = 0.02 if verdict == "pass" else 0.5
        act = ref + offset
        return _traj(t, ref, t, act)
    if mode == "range":
        # range mode is baseline-free; only act matters.
        if verdict == "pass":
            act = 0.5 + 0.1 * np.sin(2 * np.pi * t)  # within [0, 1]
        else:
            act = -0.5 + 2.0 * t  # exits [0, 1] at both ends
        ref = act.copy()  # placeholder; range mode ignores ref
        return _traj(t, ref, t, act)
    if mode == "points":
        # Final-value-only flavor (empty points list): compare act[-1] vs
        # ref[-1] within tolerance. ref ends at 0.5; pass = act ends near
        # 0.5; fail = act ends at 1.5.
        ref = 0.5 * np.ones_like(t)
        if verdict == "pass":
            act = ref + 1e-4
        else:
            act = ref.copy()
            act[-1] = 1.5
        return _traj(t, ref, t, act)
    if mode == "dominant-frequency":
        # Pure 5 Hz tone over a 1-second window with 256 samples — gives
        # the FFT a clean main lobe at 5 Hz and a Nyquist cap of 128 Hz.
        # Python and JS both resample to a power of 2 above max(N, 64),
        # so 256 samples lets the bin grid be deterministic on both
        # sides — bit-identical bin frequencies.
        t_fft = np.linspace(0.0, 1.0, 256)
        act = np.sin(2.0 * np.pi * 5.0 * t_fft)
        # Reference is unused by the dominant-frequency live scorer
        # (declared peaks come from spec params, not from ref FFT). Keep
        # it short so signal range computations stay stable.
        ref = act.copy()
        return _traj(t_fft, ref, t_fft, act)
    raise ValueError(f"unknown mode: {mode}")


def _traj(rt: np.ndarray, rv: np.ndarray, at: np.ndarray, av: np.ndarray) -> dict:
    return {
        "ref_time": rt.tolist(),
        "ref_values": rv.tolist(),
        "act_time": at.tolist(),
        "act_values": av.tolist(),
    }


# ---------------------------------------------------------------------------
# Test cases — (mode, verdict, params)
# ---------------------------------------------------------------------------

# Each row generates one leaf in the rendered report. The Python
# authoritative verdict is computed by running the matching ``_compare_*``
# function from ``comparator.py`` against the fixture trajectory; the JS
# verdict comes from evaluating ``MODE_SCORERS[mode](leaf)`` in Playwright.
# Verdicts must match.
_PARITY_CASES: list[dict[str, Any]] = [
    {"mode": "nrmse", "verdict": "pass", "params": {"tolerance": 1e-2}},
    {"mode": "nrmse", "verdict": "fail", "params": {"tolerance": 1e-3}},
    {
        "mode": "tube",
        "verdict": "pass",
        "params": {
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
            "tube_abs": 0,
            "tube_min_width": 0,
        },
    },
    {
        "mode": "tube",
        "verdict": "fail",
        "params": {
            "tube_width_mode": "rel",
            "tube_rel": 0.05,
            "tube_abs": 0,
            "tube_min_width": 0,
        },
    },
    {
        "mode": "range",
        "verdict": "pass",
        "params": {"min_value": 0.0, "max_value": 1.0},
    },
    {
        "mode": "range",
        "verdict": "fail",
        "params": {"min_value": 0.0, "max_value": 1.0},
    },
    {"mode": "points", "verdict": "pass", "params": {"tolerance": 1e-3, "points": []}},
    {"mode": "points", "verdict": "fail", "params": {"tolerance": 1e-3, "points": []}},
    # Dominant-frequency: act is a pure 5 Hz tone. Pass case declares
    # the right peak; fail case declares 12 Hz which isn't there. Python
    # and JS both resample to a power of 2 above max(N, 64) before the
    # FFT (comparator.py:_compute_fft_spectrum / interactive.js:_fftRadix2),
    # so bin frequencies are bit-identical across implementations.
    {
        "mode": "dominant-frequency",
        "verdict": "pass",
        "params": {"peaks": [{"freq": 5.0, "tolerance": 0.5, "tolerance_mode": "abs"}]},
    },
    {
        "mode": "dominant-frequency",
        "verdict": "fail",
        "params": {
            "peaks": [{"freq": 12.0, "tolerance": 0.5, "tolerance_mode": "abs"}]
        },
    },
]


# ---------------------------------------------------------------------------
# Python authoritative scoring — call the actual comparator functions
# ---------------------------------------------------------------------------


def _python_verdict(case: dict) -> bool:
    """Run the Python ``_compare_*`` function for *case* and return the
    pass/fail boolean. Mirrors the path the CLI takes; if drift exists,
    this is the side users get when they run ``dstf run``.
    """
    from dstf.comparison import comparator as cmp

    mode = case["mode"]
    params = case["params"]
    traj = _trajectory_for(mode, case["verdict"])
    rt = np.array(traj["ref_time"])
    rv = np.array(traj["ref_values"])
    at = np.array(traj["act_time"])
    av = np.array(traj["act_values"])

    if mode == "nrmse":
        result = cmp._compare_trajectories(rt, rv, at, av, params["tolerance"])
        return bool(result.passed)
    if mode == "tube":
        result = cmp._compare_tube(rt, rv, at, av, params)
        return bool(result.passed)
    if mode == "range":
        # _compare_range signature: (act_time, act_values, min_value, max_value)
        # range is baseline-free — uses only act + bounds from params.
        result = cmp._compare_range(
            at, av, params.get("min_value"), params.get("max_value")
        )
        return bool(result.passed)
    if mode == "points":
        result = cmp._compare_points(
            rt,
            rv,
            at,
            av,
            points=params.get("points") or [],
            tolerance=params["tolerance"],
        )
        return bool(result.passed)
    if mode == "dominant-frequency":
        result = cmp._compare_dominant_frequency(
            rt,
            rv,
            at,
            av,
            peaks=params.get("peaks") or [],
        )
        return bool(result.passed)
    raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------------
# Render the report — borrow the playwright fixture's structure
# ---------------------------------------------------------------------------


def _build_leaf(idx: int, case: dict, expected: bool) -> dict:
    """Synthesize a leaf dict shaped like what the reporter writes into
    ``window.MT_REPORT.TREE_VIEW.children[i]``. Only the fields the JS
    ``MODE_SCORERS`` actually read need to be accurate — see
    ``interactive.js:112-306``.
    """
    var = f"v{idx}"
    return {
        "kind": "leaf",
        "path": f"/metrics/children/{idx}",
        "metric": case["mode"],
        "variable": var,
        "params": dict(case["params"]),
        "against": "primary",
        "window": {},
        "children": [],
        # Field below set to the Python verdict — JS recompute should
        # arrive at the same answer despite this being merely a hint;
        # we explicitly do NOT use ``leaf.passed`` as a fallback in the
        # parity assertion.
        "passed": expected,
        "score": 0.0,
        "label": var,
        "name": var,
        "mode_effective": case["mode"],
        "nrmse": 0.0,
        "rmse": 0.0,
        "signal_range": 1.0,
        "max_abs_error": 0.0,
        "max_abs_error_time": 0.0,
        "reference_final": 0.0,
        "actual_final": 0.0,
        "is_constant": False,
        "tolerance_used": case["params"].get("tolerance", 1e-4),
        "score_display": "",
        "criterion": "",
        "tube_points_inside": None,
        "tube_worst_violation": None,
        "tube_worst_violation_time": None,
        "mode_values": dict(case["params"]),
        "mode_controls_html": "",
        "window_controls_html": "",
        "window_values": {},
        "cli_authoritative": False,
    }


def _build_fixture_context() -> tuple[dict, list[bool]]:
    """Render context + the list of Python-authoritative verdicts (one
    per leaf, in tree order). The verdicts are returned alongside so the
    test can ground-truth the JS side against them.
    """
    leaves = []
    expected_verdicts = []
    variables_by_name: dict[str, dict] = {}

    for idx, case in enumerate(_PARITY_CASES):
        verdict = _python_verdict(case)
        expected_verdicts.append(verdict)
        leaves.append(_build_leaf(idx, case, verdict))
        var = f"v{idx}"
        variables_by_name[var] = {
            "name": var,
            "trajectory": _trajectory_for(case["mode"], case["verdict"]),
            "overlays": [],
            "leaf_paths": [f"/metrics/children/{idx}"],
        }

    tree = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": all(expected_verdicts),
        "label": f"and[{len(leaves)}]",
        "children": leaves,
    }

    context = {
        "model_id": "Fixture.ScorerParity",
        "n_passed": sum(1 for v in expected_verdicts if v),
        "sim_failed": False,
        "last_run_at": 0,
        "last_run_str": "",
        "warnings": [],
        "key_stats": {},
        "ref_info": [],
        "sim_params": [],
        "statistics_sections": [],
        "diagnostic_summaries": [],
        "artifacts": [],
        "trajectories": [v["trajectory"] for v in variables_by_name.values()],
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "spec_path": "",
        "tree_view": tree,
        "variables_by_name": variables_by_name,
        "mode_schemas": {},
        "overlay_rows": [],
    }
    return context, expected_verdicts


def _render_report(tmp_path: Path) -> tuple[Path, list[bool]]:
    from jinja2 import Environment, FileSystemLoader

    context, expected = _build_fixture_context()
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template("interactive.html").render(**context)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    shutil.copyfile(_JS_SRC, tmp_path / "interactive.js")
    return html_path, expected


# ---------------------------------------------------------------------------
# Playwright fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def playwright_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def parity_page(tmp_path, playwright_browser) -> Iterator[tuple[Page, list[bool]]]:
    html_path, expected = _render_report(tmp_path)
    context = playwright_browser.new_context()
    page = context.new_page()
    page.on(
        "pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False)
    )
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")
    yield page, expected
    context.close()


# ---------------------------------------------------------------------------
# The actual parity assertion
# ---------------------------------------------------------------------------


def test_js_scorers_agree_with_python(parity_page):
    """For every fixture leaf, the JS ``MODE_SCORERS[mode](leaf)`` verdict
    must match the Python ``_compare_<mode>`` verdict on the same data.

    Failure here indicates drift between the two implementations. The
    failure message lists every disagreeing leaf so a single run surfaces
    the full extent of the drift, not just the first case.
    """
    page, expected = parity_page

    js_verdicts = page.evaluate("""() => {
        // Walk the tree, calling MODE_SCORERS for each leaf. Returns
        // [{path, metric, jsVerdict}, ...] in tree order.
        const out = [];
        function walk(node) {
            if (!node) return;
            if (node.kind === 'leaf') {
                const fn = MODE_SCORERS[node.metric];
                const verdict = fn ? !!fn(node) : null;
                out.push({path: node.path, metric: node.metric, jsVerdict: verdict});
            } else if (Array.isArray(node.children)) {
                for (const c of node.children) walk(c);
            }
        }
        walk(TREE_VIEW);
        return out;
    }""")

    assert len(js_verdicts) == len(expected), (
        f"Tree walk returned {len(js_verdicts)} leaves, expected {len(expected)}. "
        f"Got: {js_verdicts}"
    )

    disagreements = []
    for case, py_verdict, js_entry in zip(_PARITY_CASES, expected, js_verdicts):
        if js_entry["jsVerdict"] is None:
            disagreements.append(
                f"{case['mode']} ({case['verdict']}): no JS scorer registered "
                f"for mode (MODE_SCORERS['{case['mode']}'] is undefined)"
            )
        elif js_entry["jsVerdict"] != py_verdict:
            disagreements.append(
                f"{case['mode']} ({case['verdict']}): "
                f"Python={py_verdict}, JS={js_entry['jsVerdict']} "
                f"— params={case['params']}"
            )

    assert not disagreements, "Python <-> JS scorer drift detected:\n  " + "\n  ".join(
        disagreements
    )
