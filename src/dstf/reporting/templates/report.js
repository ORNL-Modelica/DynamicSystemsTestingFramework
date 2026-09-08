// ---- interactive.js (report.js) ----
// The report page's ONE script. It paints the figure dicts Python built
// and wires the two view controls that need JavaScript at all — the
// overlay picker and the error-overlay dropdown, both of which only
// toggle the visibility of traces that are already in the figure.
//
// It computes nothing. No verdict, no bound, no box, no spectrum, no
// event: every number on the page is either inside a figure dict or was
// rendered into the markup by Jinja. Anything that needs to be computed
// belongs in `dstf.figures` (drawing) or the authoring tool (editing).

// ---- Embedded data ----
// interactive.html inlines the figure dicts into window.MT_REPORT (one
// <script> block), then loads this file via <script src>. Keeps the JS
// pure-static and the template responsible for data marshalling.
const MT_REPORT = (typeof window !== 'undefined' && window.MT_REPORT) || {};
const FIGURES = MT_REPORT.FIGURES || {};
const DIAG_FIGURES = MT_REPORT.DIAG_FIGURES || [];
const NB_FIGURES = MT_REPORT.NB_FIGURES || [];
// review 2026-07-06 finding 76h: the template emits the ordered key list
// explicitly — JS Object.keys() reorders integer-like keys numerically,
// which desyncs plot element IDs from the Jinja loop.index0 numbering.
const VARIABLE_ORDER = MT_REPORT.VARIABLE_ORDER || Object.keys(FIGURES);

// ---- Plotly rendering ----
const PLOT_CFG = {
  responsive: true,
  displaylogo: false,
  scrollZoom: true,
};

function paintFigure(el, fig) {
  if (!el || !fig) return;
  Plotly.newPlot(el, fig.data, fig.layout, PLOT_CFG);
  // The marker the browser tests read: this plot is the Python figure
  // dict, verbatim, and nothing rebuilt it.
  el._mt_python = true;
}

function renderAllPlots() {
  VARIABLE_ORDER.forEach((varname, idx) => {
    paintFigure(document.getElementById(`plot-${idx}`), FIGURES[varname]);
  });
  DIAG_FIGURES.forEach((fig, idx) => {
    paintFigure(document.getElementById(`diag-plot-${idx}`), fig);
  });
  NB_FIGURES.forEach((fig, idx) => {
    paintFigure(document.getElementById(`nb-plot-${idx}`), fig);
  });
}

// ---- Overlay picker ----
// Each .overlay-picker div declares which Plotly chart it drives via its
// data-plot-id attribute, so the single handler works for both the
// comparison plots (plot-N) and the no-baseline plots (nb-plot-N).
function overlayTraceName(role, name) {
  return `Overlay: ${role}/${name}`;
}

function wireOverlayPickers() {
  document.querySelectorAll('.overlay-picker .overlay-toggle').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const picker = e.target.closest('.overlay-picker');
      if (!picker) return;
      const plotId = picker.dataset.plotId;
      if (!plotId) return;
      const name = e.target.dataset.ovName;
      const role = e.target.dataset.ovRole;
      setOverlayVisible(plotId, role, name, e.target.checked);
    });
  });
}

function setOverlayVisible(plotId, role, name, visible) {
  const el = document.getElementById(plotId);
  if (!el || !el.data) return;
  const traceName = overlayTraceName(role, name);
  for (let i = 0; i < el.data.length; i++) {
    if (el.data[i].name === traceName) {
      Plotly.restyle(el, { visible: visible ? true : 'legendonly' }, [i]);
      return;
    }
  }
}

// ---- Error overlays (signed / abs / NRMSE, right y-axis) ------------
// The three error series ride into the figure as legend-only traces on
// yaxis3; the dropdown only decides which one is visible.
function wireErrorOverlays() {
  document.querySelectorAll('.error-overlay-select').forEach(sel => {
    sel.addEventListener('change', (e) => {
      const vidx = parseInt(e.target.dataset.vidx);
      if (isNaN(vidx)) return;
      setErrorOverlay(vidx, e.target.value);
    });
  });
}

function setErrorOverlay(vidx, mode) {
  const el = document.getElementById(`plot-${vidx}`);
  if (!el || !el.data) return;
  const idx = [];
  const vis = [];
  el.data.forEach((tr, i) => {
    if (!tr.meta || tr.meta.kind !== 'error') return;
    idx.push(i);
    vis.push(mode !== 'none' && tr.meta.id === `error/${mode}` ? true : 'legendonly');
  });
  if (idx.length) Plotly.restyle(el, { visible: vis }, idx);
  // The axis is laid out hidden (see dstf.figures.plotly); show it only
  // while an error series is visible.
  if (el.layout && el.layout.yaxis3) {
    Plotly.relayout(el, { 'yaxis3.visible': mode !== 'none' });
  }
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  renderAllPlots();
  wireOverlayPickers();
  wireErrorOverlays();
});
