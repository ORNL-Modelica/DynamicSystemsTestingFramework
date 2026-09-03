"""Browser tests for live scoring against a NAMED baseline (soft_check).

D105 pinned every ``against`` leaf CLI-authoritative on the grounds that
the page carried only the primary reference. It carries each soft_check's
trajectory too — those are the plot overlays — so the scorers resolve an
against-leaf to that curve and re-score live. What stays pinned is the
case where the named overlay is genuinely absent for the variable.

Every fixture here is built so the primary and the soft_check imply
OPPOSITE verdicts: a scorer that quietly used the primary would fail the
test rather than pass it by accident.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.playwright


from test_interactive_playwright import _fixture_context  # noqa: E402
from test_interactive_range_window import _render_with_context  # noqa: E402

# Actual == the soft_check curve exactly; the primary sits 1.0 below it.
# Against the soft_check: NRMSE 0 → PASS at any tolerance.
# Against the primary:    NRMSE ≈ 1/range → FAIL at any sane tolerance.
_PRIMARY_OFFSET = 1.0


def _against_context(*, embed_overlay: bool = True, tolerance: float = 1e-6):
    ctx = _fixture_context()
    import numpy as np

    t = np.linspace(0, 3, 50).tolist()
    soft = [1.0 + 0.5 * x for x in t]  # the soft_check curve
    primary = [v - _PRIMARY_OFFSET for v in soft]
    act = list(soft)  # actual matches the soft_check exactly

    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = "nrmse"
    leaf["mode_effective"] = "nrmse"
    leaf["variable"] = "h"
    leaf["against"] = "experiment"
    leaf["params"] = {"tolerance": tolerance}
    leaf["mode_values"] = {"tolerance": tolerance}
    # The stamp the Python side would emit for this shape (mirrored by
    # _leafIsCliAuthoritative JS-side): live when the overlay is embedded.
    leaf["cli_authoritative"] = not embed_overlay
    leaf["passed"] = False  # CLI verdict deliberately different from live

    ctx["variables_by_name"]["h"]["trajectory"] = {
        "index": 1,
        "name": "h",
        "act_time": t,
        "act_values": act,
        "ref_time": t,
        "ref_values": primary,
    }
    overlays = (
        [
            {
                "name": "experiment",
                "role": "soft_check",
                "kind": "json",
                "time": t,
                "values": soft,
            }
        ]
        if embed_overlay
        else []
    )
    ctx["variables_by_name"]["h"]["overlays"] = overlays
    ctx["variables_by_name"]["h"]["trajectory"]["overlays"] = overlays
    ctx["trajectories"] = [
        ctx["variables_by_name"][k]["trajectory"] for k in ctx["variables_by_name"]
    ]
    ctx["overlay_rows"] = [
        {
            "name": "experiment",
            "role": "soft_check",
            "kind": "json",
            "status": "loaded",
            "note": "",
            "variables": ["h"],
        }
    ]
    return ctx


def _open(tmp_path, playwright_browser, ctx):
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.on(
        "pageerror", lambda exc: pytest.fail(f"page JS error: {exc}", pytrace=False)
    )
    page.goto(html_path.as_uri())
    page.wait_for_function("window.MT_REPORT && window.MT_REPORT.TREE_VIEW != null")
    return page


def test_slice_resolves_the_named_baselines_curve(tmp_path, playwright_browser):
    """The one seam every scorer goes through: _sliceLeafTrajectory hands
    back the soft_check's arrays, not the primary's.
    """
    page = _open(tmp_path, playwright_browser, _against_context())
    out = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            const s = _sliceLeafTrajectory(leaf, traj);
            const base = _leafBaselineCurve(leaf, traj);
            return {
                first: s.refValues[0],
                last: s.refValues[s.refValues.length - 1],
                primaryFirst: traj.ref_values[0],
                baselineName: base.name,
                embedded: base.embedded,
            };
        }
    """)
    page.close()
    assert out["baselineName"] == "experiment"
    assert out["embedded"] is True
    assert out["first"] == pytest.approx(1.0), (
        "sliced reference is the primary, not the named soft_check "
        f"(got {out['first']}, primary starts at {out['primaryFirst']})"
    )
    assert out["last"] == pytest.approx(2.5)


def test_against_leaf_scores_against_the_soft_check(tmp_path, playwright_browser):
    """actual == soft_check ⇒ PASS. Scoring against the primary (offset by
    1.0) would FAIL at this tolerance, so a wrong-curve regression cannot
    pass this test.
    """
    page = _open(tmp_path, playwright_browser, _against_context(tolerance=1e-6))
    verdict = page.evaluate(
        "() => recomputePassStates(TREE_VIEW)['/metrics/children/0']"
    )
    page.close()
    assert verdict is True, (
        "an against-leaf whose actual matches its soft_check exactly must "
        "PASS; FAIL means the scorer is still using the primary reference"
    )


def test_against_leaf_pill_flips_at_the_right_threshold(tmp_path, playwright_browser):
    """Live re-scoring, driven the way a user does it: change the
    tolerance and watch the pill flip — at the threshold implied by the
    SOFT_CHECK curve, not the primary.

    NRMSE against the soft_check is 0 (exact match) and stays passing at
    any tolerance > 0; NRMSE against the primary is
    offset / range = 1.0 / 1.5 ≈ 0.667. Tolerances either side of that
    number must therefore make NO difference — which is the sharpest
    statement of "the primary is not in play".
    """
    page = _open(tmp_path, playwright_browser, _against_context())
    results = page.evaluate("""
        () => {
            const path = '/metrics/children/0';
            const at = (tol) => {
                leafState[path].params.tolerance = tol;
                return recomputePassStates(TREE_VIEW)[path];
            };
            return {
                tight: at(1e-9),
                belowPrimaryError: at(0.5),
                abovePrimaryError: at(0.9),
                zero: at(0),
            };
        }
    """)
    page.close()
    # Exact match ⇒ NRMSE 0 < any positive tolerance.
    assert results["tight"] is True
    assert results["belowPrimaryError"] is True
    assert results["abovePrimaryError"] is True
    # ...and 0 < 0 is false: the pill flips exactly where the soft_check
    # curve says it should, which is at tolerance 0.
    assert results["zero"] is False


def test_scoring_against_the_primary_would_have_failed(tmp_path, playwright_browser):
    """The control: the same trajectories with the leaf pointed back at
    ``primary`` FAIL. Without this, the test above could pass for a
    fixture that simply cannot fail.
    """
    ctx = _against_context()
    ctx["tree_view"]["children"][0]["against"] = "primary"
    page = _open(tmp_path, playwright_browser, ctx)
    verdict = page.evaluate(
        "() => recomputePassStates(TREE_VIEW)['/metrics/children/0']"
    )
    page.close()
    assert verdict is False


def test_tube_envelope_is_drawn_on_the_named_baseline(tmp_path, playwright_browser):
    """D105's display limit: the tube polygon was drawn around the primary
    while the leaf was scored against the soft_check.
    """
    ctx = _against_context()
    leaf = ctx["tree_view"]["children"][0]
    leaf["metric"] = "tube"
    leaf["mode_effective"] = "tube"
    leaf["params"] = {"tube_rel": 0.1, "tube_width_mode": "rel"}
    leaf["mode_values"] = dict(leaf["params"])
    page = _open(tmp_path, playwright_browser, ctx)
    out = page.evaluate("""
        () => {
            const leaf = TREE_VIEW.children[0];
            const traj = (VARIABLES_BY_NAME['h'] || {}).trajectory || {};
            const contrib = MODE_PLOT_CONTRIBUTIONS['tube'](leaf, traj);
            const ys = contrib.traces[0].y;
            // Polygon is upper (forward) then lower (reversed): the first
            // point is upper[0] = base[0] * 1.1 for rel width 0.1.
            return {
                firstUpper: ys[0],
                softStart: (VARIABLES_BY_NAME['h'].overlays[0].values)[0],
                primaryStart: traj.ref_values[0],
                name: contrib.traces[0].name,
            };
        }
    """)
    page.close()
    # soft_check starts at 1.0 → upper 1.1; primary starts at 0.0 → 0.0.
    assert out["softStart"] == pytest.approx(1.0)
    assert out["primaryStart"] == pytest.approx(0.0)
    assert out["firstUpper"] == pytest.approx(1.1), (
        f"tube envelope drawn around the wrong curve: upper[0]="
        f"{out['firstUpper']} (soft_check implies 1.1, primary implies 0.0)"
    )
    assert out["name"] == "Tube · h [experiment]"


def test_missing_overlay_keeps_the_cli_badge(tmp_path, playwright_browser):
    """The half of the pin that remains: the named baseline carries no
    data for this variable, so there is nothing to score against — badge
    stays, and the CLI verdict is not overwritten by a live score.
    """
    ctx = _against_context(embed_overlay=False)
    page = _open(tmp_path, playwright_browser, ctx)
    badge = page.locator(
        '#nodes-full [data-path="/metrics/children/0"] .cli-authoritative'
    )
    n_badges = badge.count()
    verdict = page.evaluate(
        "() => recomputePassStates(TREE_VIEW)['/metrics/children/0']"
    )
    base = page.evaluate("""
        () => _leafBaselineCurve(TREE_VIEW.children[0],
                                 VARIABLES_BY_NAME['h'].trajectory)
    """)
    page.close()
    assert n_badges == 1
    assert base["embedded"] is False
    assert base["time"] == []
    # leaf.passed was seeded False; with no live scorer the CLI verdict stands.
    assert verdict is False


def test_added_against_leaf_is_live_when_its_overlay_is_embedded(
    tmp_path, playwright_browser
):
    """The browser's own add-leaf path stamps the same rule (JS mirror of
    the Python stamp).
    """
    page = _open(tmp_path, playwright_browser, _against_context())
    out = page.evaluate("""
        () => ({
            embedded: _leafIsCliAuthoritative('nrmse', 'h', 'experiment'),
            absent: _leafIsCliAuthoritative('nrmse', 'h', 'nosuchbaseline'),
            otherVariable: _leafIsCliAuthoritative('nrmse', 'v', 'experiment'),
            primary: _leafIsCliAuthoritative('nrmse', 'h', 'primary'),
            noScorer: _leafIsCliAuthoritative('scalar', 'h', 'experiment'),
        })
    """)
    page.close()
    assert out["embedded"] is False
    assert out["absent"] is True
    assert out["otherVariable"] is True
    assert out["primary"] is False
    assert out["noScorer"] is True


# ---------------------------------------------------------------------------
# Decimation disclosure for the overlay a leaf is scored against
# ---------------------------------------------------------------------------


def _decimated_overlay_context(*, overlay_full: int, embedded: int):
    """Primary UNDER the cap, soft_check overlay OVER it — the shape the
    trajectory-level ``full_samples`` stamp cannot see, since it only ever
    counted act/ref."""
    ctx = _against_context()
    ov = ctx["variables_by_name"]["h"]["overlays"][0]
    ov["full_samples"] = overlay_full
    ov["time"] = ov["time"][:embedded]
    ov["values"] = ov["values"][:embedded]
    return ctx


def test_banner_discloses_a_decimated_baseline(tmp_path, playwright_browser):
    """A soft_check that was decimated must be disclosed by name: an
    against-leaf live-scores on that curve, so "live scores are computed on
    decimated data" is as true of it as of the primary.
    """
    ctx = _decimated_overlay_context(overlay_full=5000, embedded=50)
    page = _open(tmp_path, playwright_browser, ctx)
    banner = page.locator("#decimation-banner")
    count = banner.count()
    text = banner.first.text_content() if count else ""
    page.close()
    assert count == 1, (
        "no decimation banner: an over-cap overlay under an under-cap "
        "primary was disclosed nowhere"
    )
    assert "decimated data" in text
    assert "experiment" in text, (
        f"the banner must name the decimated baseline being scored "
        f"against; got {text!r}"
    )
    assert "5000" in text, "sample totals must include the overlay's own"


def test_no_banner_when_the_overlay_is_full_resolution(tmp_path, playwright_browser):
    """Fail-path guard: an untouched overlay must not raise a banner."""
    ctx = _against_context()
    ov = ctx["variables_by_name"]["h"]["overlays"][0]
    ov["full_samples"] = len(ov["time"])
    page = _open(tmp_path, playwright_browser, ctx)
    count = page.locator("#decimation-banner").count()
    page.close()
    assert count == 0


def test_a_decimated_companion_is_not_named_as_a_scored_baseline(
    tmp_path, playwright_browser
):
    """Companions are unscorable, so a decimated one still counts toward
    the sample totals but must not be listed as a baseline scored against.
    """
    ctx = _decimated_overlay_context(overlay_full=5000, embedded=50)
    ctx["variables_by_name"]["h"]["overlays"][0]["role"] = "companion"
    page = _open(tmp_path, playwright_browser, ctx)
    text = page.locator("#decimation-banner").first.text_content()
    page.close()
    assert "decimated data" in text
    assert "Decimated baselines scored against" not in text


def test_dominant_frequency_pin_ignores_the_overlay(tmp_path, playwright_browser):
    """The DF pin keys on the ACTUAL signal, not the baseline.

    MODE_SCORERS['dominant-frequency'] FFTs ``traj.act_*`` only — the
    reference curve never enters the spectrum — which is why
    report_suite._mark_decimated_cli_authoritative pins on ``act`` alone and
    a decimated PRIMARY ref does not pin either. Mirroring that exactly, a
    decimated overlay is banner-disclosed and not pinned; the act-based pin
    already applies to against-leaves (see test_report_suite's
    test_decimation_pins_regardless_of_which_baseline_is_named).
    """
    ctx = _decimated_overlay_context(overlay_full=5000, embedded=50)
    page = _open(tmp_path, playwright_browser, ctx)
    out = page.evaluate("""
        () => {
            const traj = VARIABLES_BY_NAME['h'].trajectory;
            return {
                // The act signal — the only series the DF scorer reads.
                actDecimated: _trajectoryDecimated('h'),
                pinnedByOverlay: _leafIsCliAuthoritative(
                    'dominant-frequency', 'h', 'experiment'),
                pinnedForPrimary: _leafIsCliAuthoritative(
                    'dominant-frequency', 'h', 'primary'),
                scorerReadsRef: MODE_SCORERS['dominant-frequency']
                    .toString().includes('ref_values'),
                actLen: (traj.act_time || []).length,
            };
        }
    """)
    page.close()
    assert out["actDecimated"] is False, "fixture's act signal is under the cap"
    assert out["scorerReadsRef"] is False, (
        "the DF scorer now reads the reference — the pin rule below must be "
        "revisited, since overlay decimation would then change its verdict"
    )
    # Not pinned, exactly as a decimated PRIMARY ref would not pin it.
    assert out["pinnedByOverlay"] is False
    assert out["pinnedForPrimary"] is False
