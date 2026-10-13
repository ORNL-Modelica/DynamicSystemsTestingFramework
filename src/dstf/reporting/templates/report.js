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
// One figure per PLOT SECTION, in the template's own order — the page is
// laid out per plot, not per variable, and a plot may draw several series.
const PLOT_FIGURES = MT_REPORT.PLOT_FIGURES || [];
// Each plot's series names, in order (the browser tests read it).
const PLOT_SERIES = MT_REPORT.PLOT_SERIES || [];
const DIAG_FIGURES = MT_REPORT.DIAG_FIGURES || [];

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
  dstfWireSecondaryAxes(el);
}

function renderAllPlots() {
  PLOT_FIGURES.forEach((fig, idx) => {
    paintFigure(document.getElementById(`plot-${idx}`), fig);
  });
  DIAG_FIGURES.forEach((fig, idx) => {
    paintFigure(document.getElementById(`diag-plot-${idx}`), fig);
  });
}

// ---- Overlay picker ----
// Each .overlay-picker div declares which Plotly chart it drives via its
// data-plot-id attribute (plot-N), so one handler serves every plot.
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

// ---- Error overlays (signed / abs on the error axis, NRMSE on its own; axes.js shows the axis) ----
// The three error series ride into the figure as legend-only traces on
// yaxis3 / yaxis4; the dropdown only decides which one is visible.
function wireErrorOverlays() {
  document.querySelectorAll('.error-overlay-select').forEach(sel => {
    sel.addEventListener('change', (e) => {
      // `data-plot`, not `data-vidx`: the select belongs to a PLOT. On this
      // page `data-vidx` means a variable's index (var-status), and on a
      // plot of one subject the two only happen to coincide.
      const plotIdx = parseInt(e.target.dataset.plot);
      if (isNaN(plotIdx)) return;
      setErrorOverlay(plotIdx, e.target.value);
    });
  });
}

function setErrorOverlay(plotIdx, mode) {
  const el = document.getElementById(`plot-${plotIdx}`);
  if (!el || !el.data) return;
  const idx = [];
  const vis = [];
  el.data.forEach((tr, i) => {
    if (!tr.meta || tr.meta.kind !== 'error') return;
    idx.push(i);
    vis.push(mode !== 'none' && tr.meta.id === `error/${mode}` ? true : 'legendonly');
  });
  if (idx.length) Plotly.restyle(el, { visible: vis }, idx);
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  renderAllPlots();
  wireOverlayPickers();
  wireErrorOverlays();
});
