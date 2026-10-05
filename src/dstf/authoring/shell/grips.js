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
  // The middle of an arrow as it is drawn, px from its anchor along its
  // axis: a triangle from 10 to 17 px out (figures/plotly.py `_GRIP_PATHS`;
  // `test_the_pages_tip_is_the_middle_of_the_drawn_arrow` holds the two
  // together).
  const TIP = 13.5;
  // How far outside a shown arrow's box the pointer is still on it, px.
  const ON_ARROW = 3;
  // How near a shown arrow, or the ring its group stands around, the plot's
  // own hover label gives way, px.
  const HUSH = 20;
  // How far the pointer must travel along an arrow's own axis, press to
  // release, for the release to move it, px.
  const OWN_AXIS = 3;

  // What the server says of the figure `gd` holds, or null for a figure
  // that says nothing (then nothing is marked).
  const said = (gd) => ((gd.layout && gd.layout.meta) || {}).dstf || null;
  const own = (map, name) =>
    map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;

  const instances = new Map();

  function create(gd) {
    const st = { gd, on: null, latched: false, xy: null, ranges: {}, press: null, travel: null };

    // What the server says of the grip shape named `name`, or null; and
    // its group.
    const gripOf = (name) => {
      const info = said(gd);
      return info ? own(info.grips, name) : null;
    };
    const groupOf = (name) => {
      const grip = gripOf(name);
      return grip ? grip.group : null;
    };

    // An arrow says what it sets — a tooltip, the SVG `title` child — in
    // the server's words, and carries the axis it moves on for the
    // stylesheet's cursor.
    const SVG = "http://www.w3.org/2000/svg";
    const tell = (el, grip) => {
      el.setAttribute("data-grip-axis", grip.axis);
      let title = el.querySelector("title");
      if (!title) title = el.appendChild(document.createElementNS(SVG, "title"));
      if (title.textContent !== grip.label) title.textContent = grip.label || "";
    };
    const untell = (el) => {
      el.removeAttribute("data-grip-axis");
      for (const title of el.querySelectorAll("title")) title.remove();
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
        const grip = gripOf(name);
        if (grip) {
          el.setAttribute("data-grip", grip.group);
          el.toggleAttribute("data-grip-on", grip.group === st.on);
          tell(el, grip);
        } else if (el.hasAttribute("data-grip")) {
          el.removeAttribute("data-grip");
          el.removeAttribute("data-grip-on");
          untell(el);
        }
      }
    };

    const show = (g) => {
      if (g === st.on) return;
      st.on = g;
      for (const el of gd.querySelectorAll(".shapelayer path[data-grip]")) {
        el.toggleAttribute("data-grip-on", el.getAttribute("data-grip") === g);
      }
    };

    // Each group's reveal region, in pixels of the graph div: the bounding
    // box of its grips' anchors — and its POINTS, the places a person aims
    // at: the ring its grips stand around, each grip's anchor and each
    // arrow's drawn TIP, `TIP` px from that anchor the way the server says
    // the arrow is drawn (`outward`, along its `axis`). A grip anchored to
    // the PAPER in y (an event's or a peak's window spans its panel) has no
    // y of its own: its region is the panel's whole height, and its anchor
    // and its tip are at the pointer's own height (`columns`).
    const regions = () => {
      const fl = gd._fullLayout;
      const out = new Map();
      if (!fl) return out;
      const info = said(gd);
      const rings = [];
      for (const s of (gd.layout && gd.layout.shapes) || []) {
        if (s.visible === false) continue;
        const grip = gripOf(s.name);
        const g = grip ? grip.group : null;
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
        const r = out.get(g) || { l: x, r: x, t, b, points: [], columns: [], rings: [] };
        r.l = Math.min(r.l, x);
        r.r = Math.max(r.r, x);
        r.t = Math.min(r.t, t);
        r.b = Math.max(r.b, b);
        // A shape's pixel path has y UP: outward on the value axis is up
        // the screen.
        const way = TIP * (Math.sign(grip.outward) || 0);
        const dx = grip.axis === "x" ? way : 0;
        const dy = grip.axis === "y" ? -way : 0;
        if (paper) r.columns.push(x, x + dx);
        else r.points.push([x, y], [x + dx, y + dy]);
        out.set(g, r);
      }
      for (const [g, x, y] of rings) {
        const r = out.get(g);
        if (!r || y === null) continue;
        r.points.push([x, y]);
        r.rings.push([x, y]);
      }
      return out;
    };

    // How far the pointer is from a group: from the nearest of its points.
    const distance = (r, x, y) => {
      const level = Math.min(Math.max(y, r.t), r.b);
      return Math.min(
        ...r.points.map(([px, py]) => Math.hypot(x - px, y - py)),
        ...r.columns.map((px) => Math.hypot(x - px, y - level)),
      );
    };

    // How far the pointer (viewport px) is from the nearest SHOWN arrow's
    // box — 0 on it, Infinity with none shown. The box is the path's own,
    // as drawn: whichever way its arrow points.
    const arrow = (cx, cy) => {
      let nearest = Infinity;
      for (const el of gd.querySelectorAll(".shapelayer path[data-grip-on]")) {
        const b = el.getBoundingClientRect();
        if (!b.width && !b.height) continue;
        const dx = cx < b.left ? b.left - cx : cx > b.right ? cx - b.right : 0;
        const dy = cy < b.top ? b.top - cy : cy > b.bottom ? cy - b.bottom : 0;
        nearest = Math.min(nearest, Math.hypot(dx, dy));
      }
      return nearest;
    };

    // The group the pointer is near, or null. On a shown arrow, the group
    // it is of stays: an arrow stands 10 to 17 px OUTSIDE its anchor, on a
    // close neighbour's ring or inside its window, and shown by the nearest
    // ANCHOR it went from under the pointer that walked onto it. Else, of
    // the groups whose region holds the pointer, the one whose nearest
    // point is closest — of equals, the first in the figure's order. The
    // shown group stays unless another is at least HYSTERESIS px nearer.
    const pick = (cx, cy) => {
      if (st.on !== null && arrow(cx, cy) <= ON_ARROW) return st.on;
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

    // Within HUSH px of a shown arrow, or of the ring the shown group
    // stands around, the plot's own hover label is not shown
    // (`data-grip-near`, style.py): the unified label and its spike line
    // were drawn over them. Only there: hidden for as long as a group was
    // shown, the curve could not be read anywhere in an event's window —
    // the panel's whole height — while its criterion was open. An
    // ATTRIBUTE of the graph div, not a class: a server update of the
    // element's classes wipes one the page set, and keeps an attribute.
    const hush = (xy) => {
      let near = false;
      if (xy && st.on !== null) {
        near = arrow(xy[0], xy[1]) <= HUSH;
        const r = near ? null : regions().get(st.on);
        if (r) {
          const box = gd.getBoundingClientRect();
          const x = xy[0] - box.left;
          const y = xy[1] - box.top;
          near = r.rings.some(([px, py]) => Math.hypot(x - px, y - py) <= HUSH);
        }
      }
      gd.toggleAttribute("data-grip-near", near);
    };

    // Where the pointer is now — viewport px, or null off this plot: the
    // group it is near shows. Held, nothing changes.
    const here = (xy) => {
      st.xy = xy;
      if (st.latched) return;
      show(xy ? pick(xy[0], xy[1]) : null);
      hush(xy);
    };

    const move = (e) => here([e.clientX, e.clientY]);
    const leave = () => here(null);
    // Once a drag starts the graph div gets one `mouseleave` and no
    // `mousemove` (Plotly's drag cover takes the pointer): what is shown is
    // held from the press to the release.
    const down = (e) => {
      if (!gd.contains(e.target)) return;
      st.latched = true;
      st.press = [e.clientX, e.clientY];
      st.travel = null;
      // The ranges the drag is made in (`released`): the release's own
      // relayout autoranges the axes over the dragged shape before anyone
      // hears of it.
      st.ranges = {};
      for (const [key, axis] of Object.entries(gd._fullLayout || {})) {
        if (/^[xy]axis\d*$/.test(key) && axis && axis.range) {
          st.ranges[key] = axis.range.slice();
        }
      }
    };
    const up = (e) => {
      if (!st.latched) return;
      st.latched = false;
      // How far the pointer went, per axis (`released`): heard here, in
      // the capture phase, before Plotly ends its drag and relayouts.
      st.travel = st.press && {
        x: Math.abs(e.clientX - st.press[0]),
        y: Math.abs(e.clientY - st.press[1]),
      };
      const box = gd.getBoundingClientRect();
      const over =
        e.clientX >= box.left && e.clientX <= box.right &&
        e.clientY >= box.top && e.clientY <= box.bottom;
      here(over ? [e.clientX, e.clientY] : null);
    };
    // A release outside the window never arrives.
    const blur = () => {
      st.latched = false;
      here(null);
    };

    // What a release sends of the shapes it moved: `update` is a relayout's
    // (`shapes[N].xanchor`, `shapes[N].y0`, ...), read against the figure
    // this plot holds; a copy is returned.
    //
    // A shape let go OUTSIDE the plot area — on the page above it, off the
    // spectrum's left end — was released where the axis would be out
    // there, and that number was written (an arrow's side at 2.5 on an
    // axis ending at 1.3; a ring's time at 1.289 on a run ending at 1.0).
    // Every position of a shape an editor moves — an arrow's anchor, a
    // ring's, a bound line's ends — is held to the range that was VISIBLE
    // through the drag, the press's (`down`): it lands on the plot's
    // edge, where it was last drawn. A position on the paper, and the
    // pixel offsets of a shape sized in pixels, are no data to hold.
    //
    // And an arrow moves on ONE axis. Dragged across it — a value side's
    // arrow, sideways — Plotly moves the shape and re-snaps the anchor it
    // did not move: 0.08 came back 0.0800462, and was staged. With under
    // OWN_AXIS px of travel along its own axis the release says no
    // position on that axis (null): the editor reads no move, and the
    // redraw that answers every release puts the arrow back.
    st.released = (update) => {
      const shapes = (gd.layout && gd.layout.shapes) || [];
      const info = said(gd);
      const free = new Set((info && info.draggable) || []);
      const travel = st.travel;
      st.travel = null;
      const out = { ...update };
      for (const [key, value] of Object.entries(update)) {
        const m = /^shapes\[(\d+)\]\.([xy])(anchor|0|1)$/.exec(key);
        if (!m || typeof value !== "number") continue;
        const s = shapes[+m[1]];
        if (!s || !free.has(s.name)) continue;
        const grip = gripOf(s.name);
        if (grip && grip.axis === m[2] && travel && travel[m[2]] < OWN_AXIS) {
          out[key] = null;
          continue;
        }
        if (m[3] !== "anchor" && s[m[2] + "sizemode"] === "pixel") continue;
        const ref = m[2] === "x" ? s.xref || "x" : s.yref || "y";
        const range = /^[xy]\d*$/.test(ref) ? st.ranges[m[2] + "axis" + ref.slice(1)] : null;
        if (!range) continue;
        out[key] = Math.min(Math.max(value, Math.min(...range)), Math.max(...range));
      }
      return out;
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
            here(st.xy);
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
        untell(el);
      }
      for (const el of gd.querySelectorAll("[data-inert]")) {
        el.removeAttribute("data-inert");
      }
      gd.removeAttribute("data-grip-near");
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
    // A relayout of plot `id` on its way to the server (plot_card.py):
    // what that plot's instance says of a release.
    released(id, update) {
      const st = instances.get(id);
      return st && update && typeof update === "object" ? st.released(update) : update;
    },
  };
})();
