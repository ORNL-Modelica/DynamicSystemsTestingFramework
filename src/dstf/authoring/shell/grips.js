// The Test page's tolerance grips (D147): the arrows just outside each side
// of the open criterion's tolerance are hidden at rest and shown while the
// pointer is near their checkpoint, event or peak, and through a drag. And
// the plot's pointer: every shape no editor moves is made inert.
//
// The figure never changes with the hover: a drag names a shape by INDEX, and
// a figure changed mid-drag loses it. Hiding is one static rule of the shell's
// stylesheet (style.py) over the paths this module marks —
//   .js-plotly-plot .shapelayer path[data-grip]:not([data-grip-on])
// — `display: none`, because Plotly writes inline `opacity: 1; pointer-events:
// all` on every editable shape: hidden by `visibility` or `opacity` a grip is
// still grabbed. Where there is no hover (a touch screen) the same stylesheet
// shows every marked grip, and nothing here is needed.
//
// WHICH shape is a grip, of which group, and which shapes may be dragged at
// all is the SERVER's to say: it rides in the figure itself
// (`layout.meta.dstf`, figures/plotly.py `POINTER`), so it is read off the
// very figure whose paths are being marked — the two cannot be out of step —
// and nothing here knows a metric's id pattern. A shape the server does not
// name draggable gets `data-inert` (`pointer-events: none`, style.py): a
// press on a box, a window or a result line is the plot's own.
//
// One instance per plot card (`DstfGrips.install(id)` / `.destroy(id)`, the
// card's `ui.plotly` element id — plot_card.py): its own shown group, its own
// pointer, its own latch.
(() => {
  if (window.DstfGrips) return;

  // How far outside a group's anchors the pointer still shows it, px.
  const MARGIN = 25;
  // How much nearer another group must be before it takes over from the
  // shown one, px: two arrows of neighbours can stand on one spot, and
  // without it the one reached first flickered away under the pointer.
  const HYSTERESIS = 6;

  // What the server says of the figure `gd` holds, or null for a figure
  // that says nothing (then nothing is marked).
  const said = (gd) => ((gd.layout && gd.layout.meta) || {}).dstf || null;
  const own = (map, name) =>
    map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;

  const instances = new Map();

  function create(gd) {
    const st = { gd, on: null, latched: false, xy: null };

    // The group of the grip shape named `name`, or null.
    const groupOf = (name) => {
      const info = said(gd);
      const grip = info && own(info.grips, name);
      return grip ? grip.group : null;
    };

    // Plotly replaces every shape path on a react or a newPlot: the marks go
    // with them, and are put back from what the figure says of its shapes.
    // A shape is three elements of one `data-index` — its group, its path
    // and, for a line, the wide helper that takes its drag: all three inert.
    const mark = () => {
      const shapes = (gd.layout && gd.layout.shapes) || [];
      const info = said(gd);
      const free = info && new Set(info.draggable || []);
      for (const el of gd.querySelectorAll(".shapelayer [data-index]")) {
        const name = (shapes[+el.getAttribute("data-index")] || {}).name;
        el.toggleAttribute("data-inert", !!free && !free.has(name));
        if (el.tagName !== "path") continue;
        const g = groupOf(name);
        if (g) {
          el.setAttribute("data-grip", g);
          el.toggleAttribute("data-grip-on", g === st.on);
        } else if (el.hasAttribute("data-grip")) {
          el.removeAttribute("data-grip");
          el.removeAttribute("data-grip-on");
        }
      }
    };

    // While a group is shown the plot's own hover label is not
    // (`data-grip-shown`, style.py): the unified label and its spike line
    // were drawn over the arrows. An ATTRIBUTE of the graph div, not a
    // class: a server update of the element's classes wipes one the page
    // set, and keeps an attribute.
    const show = (g) => {
      if (g === st.on) return;
      st.on = g;
      gd.toggleAttribute("data-grip-shown", g !== null);
      for (const el of gd.querySelectorAll(".shapelayer path[data-grip]")) {
        el.toggleAttribute("data-grip-on", el.getAttribute("data-grip") === g);
      }
    };

    // Each group's reveal region, in pixels of the graph div: the bounding
    // box of its grips' anchors — and its ANCHOR POINTS, those and the ring
    // they stand around. A grip anchored to the PAPER in y (an event's or a
    // peak's window spans its panel) has no y of its own: its region is the
    // panel's whole height, and what the pointer is near is the window's
    // x-range at the pointer's own height.
    const regions = () => {
      const fl = gd._fullLayout;
      const out = new Map();
      if (!fl) return out;
      const info = said(gd);
      const rings = [];
      for (const s of (gd.layout && gd.layout.shapes) || []) {
        if (s.visible === false) continue;
        const g = groupOf(s.name);
        const ring = g ? null : info && own(info.targets, s.name);
        if (!g && !ring) continue;
        const xa = fl["xaxis" + (s.xref || "x").slice(1)];
        if (!xa) continue;
        const paper = s.yref === "paper";
        const ya = fl["yaxis" + (paper ? xa.anchor : s.yref || "y").slice(1)];
        if (!ya) continue;
        const x = xa._offset + xa.d2p(s.xanchor);
        const y = paper ? null : ya._offset + ya.d2p(s.yanchor);
        if (ring) {
          rings.push([ring, x, y]);
          continue;
        }
        const t = paper ? ya._offset : y;
        const b = paper ? ya._offset + ya._length : y;
        const r = out.get(g) || { l: x, r: x, t, b, paper: false, points: [] };
        r.l = Math.min(r.l, x);
        r.r = Math.max(r.r, x);
        r.t = Math.min(r.t, t);
        r.b = Math.max(r.b, b);
        r.paper = r.paper || paper;
        if (!paper) r.points.push([x, y]);
        out.set(g, r);
      }
      for (const [g, x, y] of rings) {
        const r = out.get(g);
        if (r && y !== null) r.points.push([x, y]);
      }
      return out;
    };

    // How far the pointer is from a group: from the nearest of its anchor
    // points, or — a window — from the nearest point of its x-range.
    const distance = (r, x, y) => {
      if (r.paper) {
        const dx = x < r.l ? r.l - x : x > r.r ? x - r.r : 0;
        const dy = y < r.t ? r.t - y : y > r.b ? y - r.b : 0;
        return Math.hypot(dx, dy);
      }
      return Math.min(...r.points.map(([px, py]) => Math.hypot(x - px, y - py)));
    };

    // The group the pointer is near, or null: of the groups whose region
    // holds the pointer, the one whose nearest anchor is closest. The shown
    // group stays unless another is at least HYSTERESIS px nearer — sticky
    // for as long as the pointer was in its region, a group kept its arrows
    // while the pointer stood on a NEIGHBOUR's ring, 15 px away, and where
    // that neighbour's own arrow belongs was plain plot.
    const pick = (cx, cy) => {
      const box = gd.getBoundingClientRect();
      const x = cx - box.left;
      const y = cy - box.top;
      const inside = (r) =>
        x >= r.l - MARGIN && x <= r.r + MARGIN && y >= r.t - MARGIN && y <= r.b + MARGIN;
      let best = null;
      let nearest = Infinity;
      let shown = Infinity;
      for (const [g, r] of regions()) {
        if (!inside(r)) continue;
        const d = distance(r, x, y);
        if (g === st.on) shown = d;
        if (d < nearest) {
          nearest = d;
          best = g;
        }
      }
      return shown - nearest < HYSTERESIS ? st.on : best;
    };

    const move = (e) => {
      st.xy = [e.clientX, e.clientY];
      if (!st.latched) show(pick(e.clientX, e.clientY));
    };
    const leave = () => {
      st.xy = null;
      if (!st.latched) show(null);
    };
    // Once a drag starts the graph div gets one `mouseleave` and no
    // `mousemove` (Plotly's drag cover takes the pointer): what is shown is
    // held from the press to the release.
    const down = (e) => {
      if (gd.contains(e.target)) st.latched = true;
    };
    const up = (e) => {
      if (!st.latched) return;
      st.latched = false;
      const box = gd.getBoundingClientRect();
      const over =
        e.clientX >= box.left && e.clientX <= box.right &&
        e.clientY >= box.top && e.clientY <= box.bottom;
      st.xy = over ? [e.clientX, e.clientY] : null;
      show(over ? pick(e.clientX, e.clientY) : null);
    };
    // A release outside the window never arrives.
    const blur = () => {
      st.latched = false;
      st.xy = null;
      show(null);
    };

    gd.addEventListener("mousemove", move);
    gd.addEventListener("mouseleave", leave);
    document.addEventListener("mousedown", down, true);
    document.addEventListener("mouseup", up, true);
    window.addEventListener("blur", blur);

    // Before paint, so no frame shows an unmarked grip. A `plotly_afterplot`
    // listener would be purged by the next newPlot.
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (
            node.nodeType === 1 &&
            (node.hasAttribute("data-index") ||
              node.classList.contains("shapelayer") ||
              node.querySelector(".shapelayer"))
          ) {
            mark();
            // The redraw after a release: shown if the pointer is still near.
            if (!st.latched) show(st.xy ? pick(st.xy[0], st.xy[1]) : null);
            return;
          }
        }
      }
    });
    observer.observe(gd, { childList: true, subtree: true });

    st.destroy = () => {
      gd.removeEventListener("mousemove", move);
      gd.removeEventListener("mouseleave", leave);
      document.removeEventListener("mousedown", down, true);
      document.removeEventListener("mouseup", up, true);
      window.removeEventListener("blur", blur);
      observer.disconnect();
      for (const el of gd.querySelectorAll("[data-grip]")) {
        el.removeAttribute("data-grip");
        el.removeAttribute("data-grip-on");
      }
      for (const el of gd.querySelectorAll("[data-inert]")) {
        el.removeAttribute("data-inert");
      }
      gd.removeAttribute("data-grip-shown");
    };
    mark();
    return st;
  }

  window.DstfGrips = {
    instances,
    // The element may not be in the page yet: NiceGUI's update that creates
    // it and this call travel separately.
    install(id, tries = 300) {
      const gd = document.getElementById("c" + id);
      if (!gd) {
        if (tries > 0) requestAnimationFrame(() => this.install(id, tries - 1));
        return;
      }
      this.destroy(id);
      instances.set(id, create(gd));
    },
    destroy(id) {
      const st = instances.get(id);
      if (st) st.destroy();
      instances.delete(id);
    },
  };
})();
