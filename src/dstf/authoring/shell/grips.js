// The Test page's tolerance grips (D147): the arrows just outside each side
// of the open criterion's tolerance are hidden at rest and shown while the
// pointer is near their checkpoint, event or peak, and through a drag. And
// the plot's pointer: every shape no editor moves is made inert.
//
// WHICH arrows show is decided by CLUSTERS. Each group — one checkpoint,
// event or peak: its ring and its arrows — has a hover area, a rectangle
// around its ring and its arrows' anchors, those of them that are ON the
// plot (`placed`): a group zoomed off it has none. Groups whose areas
// overlap show TOGETHER: they are one cluster, and with the pointer
// anywhere in a cluster's areas every arrow of every member is shown;
// outside every area, none. No two clusters' areas overlap, so the pointer
// is in at most one and there is nothing to choose between: no nearest
// group, nothing kept from where the pointer was. (ONE group was shown at
// a time before,
// chosen by the nearest anchor, then by the arrows' drawn tips too, then
// with an advantage for the shown one: at some spacings a facing arrow
// still could not be reached, the display flickered between two groups,
// and a press where a hidden arrow belonged moved the neighbour's ring.)
//
// WHERE a shown arrow is drawn is its figure's to say, but for two arrows of
// one cluster that would be drawn over each other: those are STAGGERED, each
// moved a step across its own axis (`stagger`), so that every arrow shown
// can be seen and pressed.
//
// The figure never changes with the hover: a drag names a shape by INDEX, and
// a figure changed mid-drag loses it. Hiding is one static rule of the shell's
// stylesheet (style.py) over the paths this module marks —
//   .js-plotly-plot .shapelayer path[data-grip]:not([data-grip-on])
// — `display: none`, because Plotly writes inline `opacity: 1; pointer-events:
// all` on every editable shape: hidden by `visibility` or `opacity` a grip is
// still grabbed. Where there is no hover (a touch screen) the same stylesheet
// shows every marked grip, and nothing here is needed. And an arrow whose
// edge is OFF the plot (`data-grip-off`) is not displayed at all, by the
// same stylesheet: one rule for an edge that is not on the plot — no arrow.
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
// card's `ui.plotly` element id — plot_card.py): its own clusters, its own
// pointer, its own latch.
//
// And what the pointer SAYS to the server beside Plotly's own events (D149),
// since this module already holds the press, the travel and the ranges:
//
// * a CLICK on the plot (`dstf_plot_click`) — where, in the axes of the
//   panel under the pointer, what those axes showed, and the modifier keys.
//   Plotly's own click event carries neither a position nor a key through
//   NiceGUI. The server decides what a click is: Ctrl+click adds a row, a
//   plain one sets an armed field;
// * a handle HELD (`dstf_grip_move`) — where its release would put it, a
//   few times a second, so the editor's fields follow the pointer. Plotly
//   says nothing during a shape drag;
// * Esc while a field is armed (`dstf_disarm`).
(() => {
  if (window.DstfGrips) return;

  // How far a group's hover area reaches outside its ring and its arrows'
  // anchors, px: an arrow is drawn 10 to 17 px outside its anchor
  // (figures/plotly.py `_GRIP_PATHS`), whole inside the area.
  const MARGIN = 25;
  // How near a shown arrow, or the ring its group stands around, the plot's
  // own hover label gives way, px.
  const HUSH = 20;
  // How far outside the plot area an anchor still counts as ON the plot,
  // px. A zoom lands an edge within a fraction of a pixel of the border it
  // was dragged to, on either side of it: that edge is the plot's own, and
  // its arrow — all in the margin — is the one the un-clip is for.
  const EDGE = 0.5;
  // Two arrows COLLIDE when what is painted of them shares more than this
  // on BOTH axes, px. An arrow is 12 px across and its 1 px outline stands
  // half a pixel outside it: two value arrows 12 px apart touch corner to
  // corner and share that one pixel of outline. At 0 that is a collision:
  // of thirty checkpoints 12 px apart every value arrow was moved, left
  // and right by turns, each ONTO its neighbour — 28 pairs colliding where
  // none had.
  const OVERLAP = 1;
  // How far a colliding arrow is moved ACROSS its own axis, px a step, and
  // the steps it may take, in the order it tries them. One step each way
  // parts a pair by 18 px, 5 px clear of each other; a third on the same
  // spot clears both at three. The stylesheet (style.py) turns the steps
  // into pixels: the same 9.
  const STEP = 9;
  const LEVELS = [-1, 1, -3, 3];
  // How far across its axis an arrow must already stand from those it
  // collides with to be moved THAT way first, px: a pair on a slope parts
  // the way it leans.
  const LEAN = 0.5;
  // How far the pointer must travel along an axis, press to release, for
  // the release to move a shape on it, px — Plotly's own threshold (its
  // `MINDRAG`): under it Plotly moves nothing on that axis.
  const MOVED = 8;
  // How far the pointer may travel, press to click, for the click to be a
  // click, px: a Ctrl+drag (Plotly's zoom box under a held key) ends in a
  // real click where it is let go.
  const CLICKED = 4;
  // The least time between two moves of a held handle sent to the server,
  // ms: at most 15 a second, each a preview the server computes.
  const PACE = 66;
  // Every move and every release is numbered, rising, page-wide: the
  // server drops a move numbered under a release it has heard (a preview
  // must not land after what the release staged). From the clock, so a
  // reloaded page's numbers are above the last page's.
  let seq = Date.now();

  const own = (map, name) =>
    map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;
  const dict = (v) => (v !== null && typeof v === "object" && !Array.isArray(v) ? v : null);

  // What the server says of the figure `gd` holds — its pointer map — or
  // null for a figure that says nothing: no `meta`, another's, a `dstf`
  // that is no dict. Then nothing is marked and every shape is left as
  // Plotly draws it. A map is read key by key, each with a default: a
  // figure's `meta` crosses Plotly, where an empty dict need not survive,
  // and a `draggable` iterated as it came threw in the redraw's observer
  // when it was no list — after which nothing was marked at all.
  const said = (gd) => {
    const meta = dict(gd.layout && gd.layout.meta);
    const info = meta && dict(own(meta, "dstf"));
    if (!info) return null;
    return {
      grips: dict(info.grips) || {},
      targets: dict(info.targets) || {},
      draggable: new Set(Array.isArray(info.draggable) ? info.draggable : []),
    };
  };
  // What a map says of the grip shape named `name` — a dict naming its
  // group — or null; and of the ring named `name`, its group or null.
  const gripOf = (info, name) => {
    const grip = info ? dict(own(info.grips, name)) : null;
    return grip && typeof grip.group === "string" ? grip : null;
  };
  const ringOf = (info, name) => {
    const group = info ? own(info.targets, name) : null;
    return typeof group === "string" ? group : null;
  };

  const instances = new Map();

  // Where the pointer is on the PAGE — viewport px, or null once it has
  // left the window — kept once, for every plot: a plot hears the moves
  // over itself alone, and one wheeled under a pointer that had never
  // moved over it had no pointer to test (`scrolled`).
  let pointer = null;
  const tracked = (e) => {
    pointer = [e.clientX, e.clientY];
  };
  document.addEventListener("mousemove", tracked, true);
  document.documentElement.addEventListener("mouseleave", () => {
    pointer = null;
  });

  // Esc while a field is armed disarms it. The armed field is the
  // server's to know — its ⌖ says so in `aria-pressed`, which the server
  // renders — so this only asks; Esc with nothing armed is everyone
  // else's.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.querySelector('.dstf-arm[aria-pressed="true"]')) {
      emitEvent("dstf_disarm");
    }
  }, true);

  function create(gd, id) {
    const st = {
      gd, clusters: [], on: null, latched: false, xy: null, ranges: {}, press: null,
      travel: null, failed: false, frame: 0, held: null, ended: null,
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

    // Whether the grip path `el` is of the shown cluster.
    const shown = (el) => !!st.on && st.on.groups.has(el.getAttribute("data-grip"));

    // Where the shape `s` — a ring or an arrow, sized in pixels about its
    // anchor — is anchored, in pixels of the graph div, as the figure is
    // drawn NOW (`_fullLayout`: the axes' ranges of this very redraw, a
    // pan's every step too), or null where it is on no axis of the figure.
    // An arrow anchored to the PAPER in y (an event's or a peak's window
    // spans its panel) is drawn at a height of the paper, `y`, and stands
    // for no value there: it is `paper`, and it reaches from the panel's
    // `top` to its `bottom`.
    //
    // And whether that anchor is OFF the plot: outside the plot area by
    // more than `allowed` px on either axis. For an arrow that is EDGE px
    // (the default), on the axis it moves on (its edge is past the end of
    // the axis) or the other (its checkpoint is); a ring is allowed its
    // radius (`areas`).
    const placed = (s, fl, allowed = EDGE) => {
      const xa = fl["xaxis" + (s.xref || "x").slice(1)];
      if (!xa) return null;
      const paper = s.yref === "paper";
      const ya = fl["yaxis" + (paper ? xa.anchor : s.yref || "y").slice(1)];
      if (!ya) return null;
      const within = (p, axis) => p >= -allowed && p <= axis._length + allowed;
      const px = xa.d2p(s.xanchor);
      const py = paper ? null : ya.d2p(s.yanchor);
      const size = fl._size;
      return {
        x: xa._offset + px,
        y: paper ? size.t + size.h * (1 - s.yanchor) : ya._offset + py,
        paper,
        top: ya._offset,
        bottom: ya._offset + ya._length,
        off: !(within(px, xa) && (paper || within(py, ya))),
      };
    };

    // The points of the path the figure sizes shape `s` by, in pixels
    // about its anchor (positive y is UP) — an arrow's corners, a ring's
    // arcs with their control points.
    const points = (s) => {
      const n = (typeof s.path === "string" && s.path.match(/-?\d*\.?\d+/g)) || [];
      const out = [];
      for (let k = 0; k + 1 < n.length; k += 2) out.push([+n[k], +n[k + 1]]);
      return out;
    };

    // What is PAINTED of the arrow `s` anchored at `at` (`placed`), as a
    // box in pixels of the graph div: its path about that anchor, and the
    // half of its outline's width that stands outside it. Or null for a
    // path that says no point.
    const painted = (s, at) => {
      const half = ((dict(s.line) || {}).width || 0) / 2;
      const box = { l: Infinity, r: -Infinity, t: Infinity, b: -Infinity };
      for (const [dx, dy] of points(s)) {
        box.l = Math.min(box.l, at.x + dx - half);
        box.r = Math.max(box.r, at.x + dx + half);
        box.t = Math.min(box.t, at.y - dy - half);
        box.b = Math.max(box.b, at.y - dy + half);
      }
      return box.l <= box.r ? box : null;
    };

    // How far the ring `s` reaches from its anchor, px: its radius.
    const radius = (s) => Math.max(0, ...points(s).flat().map(Math.abs));

    // Whether the LINE `s` — a bound an editor moves — is off the plot
    // whole: both its ends past the same end of an axis, by more than EDGE
    // px. Plotly clips what it draws of a line and not the wide helper
    // that takes its drag: a scalar's bound zoomed 3 or 12 px below the
    // plot was still what the pointer met in the bottom margin, with the
    // `move` cursor, and a drag from there staged a tolerance.
    const gone = (s, fl) => {
      if (s.type !== "line" || !fl) return false;
      const past = (key, v0, v1) => {
        const ref = s[key + "ref"] || key;
        const axis = /^[xy]\d*$/.test(ref) ? fl[key + "axis" + ref.slice(1)] : null;
        if (!axis) return false;
        const p = [axis.d2p(v0), axis.d2p(v1)];
        return Math.max(...p) < -EDGE || Math.min(...p) > axis._length + EDGE;
      };
      return past("x", s.x0, s.x1) || past("y", s.y0, s.y1);
    };

    // Plotly replaces every shape path on a react or a newPlot: the marks go
    // with them, and are put back from what the figure says of its shapes.
    // A shape is three elements of one `data-index` — its group, its path
    // and, for a line, the wide helper that takes its drag: all three inert
    // — a shape no editor moves, and a line one does that is off the plot
    // (`gone`).
    //
    // The mark on a grip's path is also what takes Plotly's clip off it:
    // an arrow stands 10 to 17 px OUTSIDE its edge, in the margin when that
    // edge is the plot's, and the stylesheet's rules over `data-grip`
    // (style.py) un-clip the path and its group. Nothing here touches a
    // `clip-path`.
    //
    // An arrow whose anchor is OFF the plot (`placed`) is marked
    // `data-grip-off`, and the stylesheet does not display it, shown group
    // or not. Un-clipped like the rest, the arrows of a checkpoint zoomed
    // just off the plot were painted in the margin, about a ring that was
    // not drawn, and took a press there; left Plotly's clip and no more,
    // an arrow drawn INWARD from an edge 2 px off the panel (the
    // spectrum's lower one, next to 0 Hz) was painted whole inside it,
    // where the pointer could not come, and one beside a border its
    // checkpoint was just past left a sliver on the border. The mark is
    // this pass's, like the others: of the figure as this redraw draws it.
    //
    // Returns the arrows that are ON the plot, each with what is painted
    // of it (`painted`), for `stagger`.
    const mark = () => {
      const shapes = (gd.layout && gd.layout.shapes) || [];
      const info = said(gd);
      const fl = gd._fullLayout;
      const arrows = [];
      for (const el of gd.querySelectorAll(".shapelayer [data-index]")) {
        const shape = shapes[+el.getAttribute("data-index")] || {};
        el.toggleAttribute(
          "data-inert",
          !!info && (!info.draggable.has(shape.name) || gone(shape, fl)),
        );
        if (el.tagName !== "path") continue;
        const grip = gripOf(info, shape.name);
        if (grip) {
          const at = fl ? placed(shape, fl) : null;
          el.setAttribute("data-grip", grip.group);
          el.toggleAttribute("data-grip-on", shown(el));
          el.toggleAttribute("data-grip-off", !!at && at.off);
          el.removeAttribute("data-grip-shift");
          tell(el, grip);
          const box = at && !at.off ? painted(shape, at) : null;
          if (box) {
            arrows.push({
              el, group: grip.group, axis: grip.axis,
              index: +el.getAttribute("data-index"), x: at.x, y: at.y, ...box,
            });
          }
        } else if (el.hasAttribute("data-grip")) {
          el.removeAttribute("data-grip");
          el.removeAttribute("data-grip-on");
          el.removeAttribute("data-grip-off");
          el.removeAttribute("data-grip-shift");
          untell(el);
        }
      }
      return arrows;
    };

    // The marks, taken off: every shape as Plotly draws it, clipped to
    // the plot area.
    const unmark = () => {
      for (const el of gd.querySelectorAll("[data-grip]")) {
        el.removeAttribute("data-grip");
        el.removeAttribute("data-grip-on");
        el.removeAttribute("data-grip-off");
        el.removeAttribute("data-grip-shift");
        untell(el);
      }
      for (const el of gd.querySelectorAll("[data-inert]")) {
        el.removeAttribute("data-inert");
      }
      gd.removeAttribute("data-grip-near");
    };

    // A pass over the plot that throws must not throw where it runs — in
    // the observer of every redraw, in the page's own mouse events — and
    // again on every redraw and every move after: it is said ONCE on the
    // console, and every shape is left as Plotly draws it, none inert and
    // none hidden, until a pass goes through.
    const guarded = (pass) => (...args) => {
      try {
        return pass(...args);
      } catch (err) {
        st.on = null;
        st.clusters = [];
        try {
          unmark();
        } catch (_) {
          // What could not be read cannot be unmarked either.
        }
        if (!st.failed) {
          st.failed = true;
          console.error(
            `DstfGrips: plot ${gd.id}: its shapes could not be marked and are ` +
              `left as Plotly draws them — ${err}`,
          );
        }
        return undefined;
      }
    };

    const show = (cluster) => {
      if (cluster === st.on) return;
      st.on = cluster;
      for (const el of gd.querySelectorAll(".shapelayer path[data-grip]")) {
        el.toggleAttribute("data-grip-on", shown(el));
      }
    };

    // Each group's hover area, in pixels of the graph div: the bounding box
    // of the ring it stands around and of its grips' anchors, grown by
    // MARGIN on every side — with the rings, for the hover label (`hush`).
    // A grip anchored to the PAPER in y (an event's or a peak's window
    // spans its panel) has no y of its own: it reaches over the panel's
    // whole height. A ring whose group has no grip has no area.
    //
    // Of the members that are ON the plot (`placed`) alone: an arrow whose
    // anchor is off it is not displayed (`mark`). A RING is on the plot
    // while any of it is drawn — its centre within its own radius of the
    // plot area: Plotly clips it to the area, and what is left of it has
    // the `move` cursor and drags; by its centre alone, a ring 3 px past
    // the border showed 4 px, dragged, and showed no arrow. A group
    // partly on the plot has the area of what is on it — and shows the
    // arrows whose own edges are; one that is off it whole — its ring and
    // every arrow — has none, and nothing shows for it wherever the
    // pointer is.
    const areas = () => {
      const fl = gd._fullLayout;
      const out = new Map();
      if (!fl) return out;
      const info = said(gd);
      const gripped = new Set();
      const rings = [];
      // Group `g`'s area, grown to hold x from t to b.
      const reach = (g, x, t, b) => {
        const a = out.get(g) || { l: x, r: x, t, b, rings: [] };
        a.l = Math.min(a.l, x);
        a.r = Math.max(a.r, x);
        a.t = Math.min(a.t, t);
        a.b = Math.max(a.b, b);
        out.set(g, a);
        return a;
      };
      for (const s of (gd.layout && gd.layout.shapes) || []) {
        if (s.visible === false) continue;
        const grip = gripOf(info, s.name);
        const g = grip ? grip.group : ringOf(info, s.name);
        if (g === null) continue;
        const at = placed(s, fl, grip ? EDGE : radius(s));
        if (!at) continue;
        if (grip) gripped.add(g);
        if (at.off) continue;
        if (!grip) {
          if (!at.paper) rings.push([g, at.x, at.y]);
        } else if (at.paper) {
          reach(g, at.x, at.top, at.bottom);
        } else {
          reach(g, at.x, at.y, at.y);
        }
      }
      for (const [g, x, y] of rings) {
        if (gripped.has(g)) reach(g, x, y, y).rings.push([x, y]);
      }
      // To the hundredth of a pixel, the precision Plotly places a shape
      // at (its `l2p`): two areas that touch on the screen touch here too,
      // whatever an axis' offset adds in the last bit of a sum.
      const px = (v) => Math.round(v * 100) / 100;
      for (const a of out.values()) {
        a.l = px(a.l - MARGIN);
        a.r = px(a.r + MARGIN);
        a.t = px(a.t - MARGIN);
        a.b = px(a.b + MARGIN);
      }
      return out;
    };

    // The clusters: groups whose areas overlap — touching counts — are of
    // one, and so on through every group between (three in a row, each
    // overlapping the next, are one). A cluster's own area is the UNION of
    // its members' rectangles, never their bounding box: an L of two shows
    // nothing from its empty corner. In PIXELS of the figure as it is
    // drawn, so they are found again wherever the shapes are drawn again
    // (`remark`) — a zoom parts a cluster, a dragged anchor joins two —
    // and never on a move of the pointer. `hover` is `areas()`'s.
    const clustered = (hover) => {
      const meet = (p, q) => p.l <= q.r && q.l <= p.r && p.t <= q.b && q.t <= p.b;
      let clusters = [];
      for (const [g, area] of hover) {
        const joined = { groups: new Set([g]), areas: [area], rings: [...area.rings] };
        const apart = [];
        // Every cluster the new area overlaps becomes one with it.
        for (const c of clusters) {
          if (!c.areas.some((other) => meet(other, area))) {
            apart.push(c);
            continue;
          }
          for (const member of c.groups) joined.groups.add(member);
          joined.areas.push(...c.areas);
          joined.rings.push(...c.rings);
        }
        clusters = [...apart, joined];
      }
      return clusters;
    };

    // STAGGER. A cluster shows all its members' arrows at once, and two of
    // them can be drawn on one spot: with the facing edges of two
    // neighbours 20 to 34 px apart at one height — two event windows
    // always are at one — the `after` arrow of the first and the `before`
    // of the second were one triangle on the screen, the later-drawn took
    // the press, and a press on what looked like the first's arrow moved
    // the second's side. The rule:
    //
    // 1. Two arrows COLLIDE when both are on the plot, are of DIFFERENT
    //    groups of ONE cluster, and what is painted of them (`painted`)
    //    shares more than OVERLAP px on both axes. (Arrows of two clusters
    //    are never shown together, and never meet: by 4 each is inside its
    //    own group's area, and two clusters' areas do not overlap.)
    // 2. An arrow that collides is moved ACROSS its own axis — a time or a
    //    frequency arrow up or down, a value arrow left or right — by
    //    whole steps of STEP px. In order along their axis (then as
    //    drawn), each takes the first of LEVELS — one step up (left), one
    //    down (right), three up, three down — at which it clears every
    //    arrow already placed: those that collide with nothing, where the
    //    figure draws them, and those moved before it. One that already
    //    stands LEAN px or more below (right of) the arrows it collides
    //    with tries down (right) first: a pair parts the way it leans.
    // 3. Found here, after every redraw, with the marks and the clusters
    //    (`remark`), and never on a move of the pointer: what is drawn
    //    with the pointer near is what was there before it came.
    // 4. A moved arrow stays REACHABLE: as drawn it lies inside its own
    //    group's hover area, so the pointer on its way from the ring to
    //    it never leaves the area and hides it. A level that would put any
    //    of it outside is not one it may take: three steps is 33.5 px from
    //    the arrow's own line and an area reaches 25 px past its group's
    //    anchors, so a group that is flat across the arrow's axis — a
    //    checkpoint whose value tolerance is under 8.5 px, for a time
    //    arrow — has one step each way and no more.
    // 5. An arrow that clears at no level it may take is left at the first
    //    it tried, still colliding: the third arrow on one spot where its
    //    group is flat, the fifth on one spot anywhere.
    //
    // The move is an ATTRIBUTE of the path, `data-grip-shift`, the steps
    // as a number; the stylesheet (style.py) makes it a `transform`. So
    // the path Plotly drags is the one it drew: held, it follows the
    // pointer by the pointer's own travel, and a release says its edge
    // where the pointer took it, never the step it is drawn aside by.
    // `arrows` is `mark()`'s, `hover` `areas()`'s.
    const stagger = (arrows, hover) => {
      // To the hundredth of a pixel, as an area's edge is (`areas`).
      const shared = (lo, hi) => Math.round((hi - lo) * 100) / 100;
      const collide = (p, q) =>
        shared(Math.max(p.l, q.l), Math.min(p.r, q.r)) > OVERLAP &&
        shared(Math.max(p.t, q.t), Math.min(p.b, q.b)) > OVERLAP;
      const across = (a) => (a.axis === "x" ? a.y : a.x);
      const along = (a) => (a.axis === "x" ? a.x : a.y);
      // Arrow `a` as it is painted `k` steps across its axis.
      const moved = (a, k) =>
        a.axis === "x"
          ? { ...a, t: a.t + k * STEP, b: a.b + k * STEP }
          : { ...a, l: a.l + k * STEP, r: a.r + k * STEP };
      const inside = (box, area) =>
        box.l >= area.l && box.r <= area.r && box.t >= area.t && box.b <= area.b;
      for (const cluster of st.clusters) {
        const members = arrows.filter((a) => cluster.groups.has(a.group));
        // Per colliding arrow, those it collides with.
        const rivals = new Map();
        const rival = (a, other) => rivals.set(a, [...(rivals.get(a) || []), other]);
        members.forEach((p, i) => {
          for (const q of members.slice(i + 1)) {
            if (p.group === q.group || !collide(p, q)) continue;
            rival(p, q);
            rival(q, p);
          }
        });
        const set = members.filter((a) => !rivals.has(a));
        const moving = [...rivals.keys()];
        moving.sort((p, q) => along(p) - along(q) || p.index - q.index);
        for (const a of moving) {
          const others = rivals.get(a);
          const lean = across(a) - others.reduce((sum, o) => sum + across(o), 0) / others.length;
          const levels = lean >= LEAN ? LEVELS.map((k) => -k) : LEVELS;
          const area = hover.get(a.group);
          const clears = (k) => {
            const there = moved(a, k);
            return inside(there, area) && !set.some((o) => collide(o, there));
          };
          const level = levels.find(clears);
          const taken = level === undefined ? levels[0] : level;
          set.push(moved(a, taken));
          a.el.setAttribute("data-grip-shift", String(taken));
        }
      }
    };

    // The cluster whose area holds the pointer (viewport px), or null: at
    // most one does.
    const under = (cx, cy) => {
      const box = gd.getBoundingClientRect();
      const x = cx - box.left;
      const y = cy - box.top;
      const holds = (a) => x >= a.l && x <= a.r && y >= a.t && y <= a.b;
      return st.clusters.find((c) => c.areas.some(holds)) || null;
    };

    // How far the pointer (viewport px) is from the nearest SHOWN arrow's
    // box — 0 on it, Infinity with none shown. The box is the path's own,
    // as drawn: whichever way its arrow points, and wherever it is
    // staggered to. An arrow off the plot is of a shown group and is not
    // displayed (style.py): it has no box, and is passed over.
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

    // Within HUSH px of a shown arrow, or of a ring a shown group stands
    // around, the plot's own hover label is not shown (`data-grip-near`,
    // style.py): the unified label and its spike line were drawn over
    // them. Only there: hidden for as long as arrows were shown, the curve
    // could not be read anywhere in an event's window — the panel's whole
    // height — while its criterion was open. An ATTRIBUTE of the graph
    // div, not a class: a server update of the element's classes wipes one
    // the page set, and keeps an attribute.
    const hush = (xy) => {
      let near = false;
      if (xy && st.on) {
        near = arrow(xy[0], xy[1]) <= HUSH;
        if (!near) {
          const box = gd.getBoundingClientRect();
          const x = xy[0] - box.left;
          const y = xy[1] - box.top;
          near = st.on.rings.some(([px, py]) => Math.hypot(x - px, y - py) <= HUSH);
        }
      }
      gd.toggleAttribute("data-grip-near", near);
    };

    // Where the pointer is now — viewport px, or null off this plot: the
    // cluster it is in shows. Held, nothing changes.
    const here = (xy) => {
      st.xy = xy;
      if (st.latched) return;
      show(xy ? under(xy[0], xy[1]) : null);
      hush(xy);
    };

    const move = guarded((e) => here([e.clientX, e.clientY]));
    const leave = guarded(() => here(null));
    // Once a drag starts the graph div gets one `mouseleave` and no
    // `mousemove` (Plotly's drag cover takes the pointer): what is shown is
    // held from the press to the release.
    const down = guarded((e) => {
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
      // The handle this press holds, if it is on one (`follow`): the
      // shape an editor moves, where the figure has it NOW — Plotly moves
      // the drawn path under the pointer and writes the figure only at
      // the release. Not under Ctrl: Plotly takes that press as a
      // right-click.
      st.held = st.ended = null;
      const el = e.target instanceof Element ? e.target.closest(".shapelayer [data-index]") : null;
      if (el && e.button === 0 && !e.ctrlKey && !el.hasAttribute("data-inert")) {
        const index = +el.getAttribute("data-index");
        const s = ((gd.layout && gd.layout.shapes) || [])[index];
        const info = said(gd);
        if (s && info && info.draggable.has(s.name)) {
          st.held = { index, drag: ++seq, last: 0, pending: false, to: null };
        }
      }
    });
    const up = guarded((e) => {
      if (!st.latched) return;
      st.latched = false;
      // How far the pointer went, per axis (`released`): heard here, in
      // the capture phase, before Plotly ends its drag and relayouts.
      st.travel = st.press && {
        x: Math.abs(e.clientX - st.press[0]),
        y: Math.abs(e.clientY - st.press[1]),
      };
      // The held handle is let go: no move of it is sent from here on,
      // and the release is numbered past every one that was (`released`).
      // Under MOVED px on both axes Plotly moves nothing and says nothing:
      // the drag ends here, and the server is told so.
      if (st.held) {
        st.ended = { index: st.held.index, seq: ++seq };
        st.held = null;
        if (st.travel && st.travel.x < MOVED && st.travel.y < MOVED) {
          emitEvent("dstf_grip_move", { plot: id, seq: st.ended.seq, end: true });
        }
      }
      const box = gd.getBoundingClientRect();
      const over =
        e.clientX >= box.left && e.clientX <= box.right &&
        e.clientY >= box.top && e.clientY <= box.bottom;
      here(over ? [e.clientX, e.clientY] : null);
    });
    // A release outside the window never arrives.
    const blur = guarded(() => {
      st.latched = false;
      if (st.held) {
        st.held = null;
        emitEvent("dstf_grip_move", { plot: id, seq: ++seq, end: true });
      }
      here(null);
    });

    // What the axes of shape `s` SHOWED at the press, as spans: what the
    // server snaps a placed value by (`comparison/snap.py`). An axis the
    // shape is not on in data — an arrow anchored to the paper — has none.
    const visible = (s) => {
      const span = (letter, ref) => {
        const range = /^[xy]\d*$/.test(ref) ? st.ranges[letter + "axis" + ref.slice(1)] : null;
        return range ? Math.abs(range[1] - range[0]) : null;
      };
      return { x: span("x", s.xref || "x"), y: span("y", s.yref || "y") };
    };

    // Where the release of the held handle would put it, as the relayout
    // that release would send: the figure's position at the press plus
    // the pointer's travel, an axis the pointer went under MOVED px along
    // not moved (Plotly's own rule, and `released`'s) — measured in
    // Chromium to be the release's own number, bit for bit — and held to
    // the range of the press, as `released` holds a release. A bound line
    // moves in y, both its ends; a ring or an arrow by its anchor.
    const would = (held) => {
      const s = ((gd.layout && gd.layout.shapes) || [])[held.index];
      const fl = gd._fullLayout;
      if (!s || !fl || !st.press || !held.to) return null;
      const out = {};
      const put = (letter, key, from) => {
        const ref = s[letter + "ref"] || letter;
        if (!/^[xy]\d*$/.test(ref) || typeof from !== "number") return;
        const axis = fl[letter + "axis" + ref.slice(1)];
        const range = st.ranges[letter + "axis" + ref.slice(1)];
        if (!axis || !range) return;
        const travel = held.to[letter === "x" ? 0 : 1] - st.press[letter === "x" ? 0 : 1];
        let value = null;
        if (Math.abs(travel) >= MOVED) {
          value = axis.p2d(axis.d2p(from) + travel);
          value = Math.min(Math.max(value, Math.min(...range)), Math.max(...range));
        }
        out[`shapes[${held.index}].${key}`] = value;
      };
      if (s.type === "line") {
        put("y", "y0", s.y0);
        put("y", "y1", s.y1);
      } else {
        put("x", "xanchor", s.xanchor);
        put("y", "yanchor", s.yanchor);
      }
      out[`shapes[${held.index}].visible`] = visible(s);
      out[`shapes[${held.index}].drag`] = held.drag;
      return out;
    };

    // A handle is HELD and the pointer moved: its would-be position goes
    // to the server, once a frame at the most and PACE ms apart, the last
    // one always sent. Heard on the document, in the capture phase:
    // Plotly's drag cover takes every move from the plot itself.
    const send = guarded((held) => {
      held.pending = false;
      if (st.held !== held) return; // let go meanwhile
      const wait = PACE - (performance.now() - held.last);
      if (wait > 0) {
        held.pending = true;
        setTimeout(() => requestAnimationFrame(() => send(held)), wait);
        return;
      }
      const update = would(held);
      if (!update) return;
      held.last = performance.now();
      emitEvent("dstf_grip_move", { plot: id, seq: ++seq, update });
    });
    const follow = (e) => {
      const held = st.held;
      if (!held) return;
      held.to = [e.clientX, e.clientY];
      if (held.pending) return;
      held.pending = true;
      requestAnimationFrame(() => send(held));
    };

    // The panel under the pointer (viewport px) and the pointer in its
    // axes — a panel is a pair of axes that overlays no other: the time
    // plot's, the spectrum's — with what each axis shows; null outside
    // every panel's plot area.
    const spot = (cx, cy) => {
      const fl = gd._fullLayout;
      if (!fl || !fl._plots) return null;
      const box = gd.getBoundingClientRect();
      for (const plot of Object.values(fl._plots)) {
        const xa = plot.xaxis;
        const ya = plot.yaxis;
        if (!xa || !ya || xa.overlaying || ya.overlaying) continue;
        const px = cx - box.left - xa._offset;
        const py = cy - box.top - ya._offset;
        if (px < 0 || px > xa._length || py < 0 || py > ya._length) continue;
        return {
          xaxis: xa._id, yaxis: ya._id, x: xa.p2d(px), y: ya.p2d(py),
          xrange: xa.range.slice(), yrange: ya.range.slice(),
        };
      }
      return null;
    };

    // A CLICK on the plot, said to the server with where it is. Not a
    // double-click (it resets the axes: `detail` counts the clicks); not
    // the click that ends a drag — a Ctrl+drag draws Plotly's zoom box
    // and ends in a real click where it is let go; not one on a shape an
    // editor moves (a ring, an arrow, a bound: a press there is a drag's),
    // on the toolbar or on the legend; not one outside every panel. Heard
    // in the capture phase: a plain click here is one Plotly makes itself
    // after its own handling, and it is still this plot's.
    const clicked = guarded((e) => {
      if (e.detail >= 2 || !(e.target instanceof Element)) return;
      if (st.press && Math.hypot(e.clientX - st.press[0], e.clientY - st.press[1]) > CLICKED) {
        return;
      }
      if (e.target.closest(".modebar-container, .legend")) return;
      const shape = e.target.closest(".shapelayer [data-index]");
      if (shape && !shape.hasAttribute("data-inert")) return;
      const at = spot(e.clientX, e.clientY);
      if (!at) return;
      emitEvent("dstf_plot_click", {
        plot: id, ...at,
        ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, meta: e.metaKey,
      });
    });
    // A scroll moves the plot under a pointer at rest, and no mouse event
    // says so: the arrows of a checkpoint wheeled out from under the
    // pointer stayed shown until it moved, and one wheeled under it showed
    // none. The pointer is where it was on the screen (the page's
    // `pointer`) and the plot is not: the same test as a move, against
    // the plot where it is now — if the pointer is over this plot at all,
    // by what a press there would land on. Once per frame, however many
    // scroll events one brings.
    const rehere = guarded(() => {
      const under = pointer && document.elementFromPoint(pointer[0], pointer[1]);
      here(under && gd.contains(under) ? pointer : null);
    });
    const scrolled = () => {
      if (st.frame) return;
      st.frame = requestAnimationFrame(() => {
        st.frame = 0;
        rehere();
      });
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
    // And an axis the pointer barely moved along is not moved. Under
    // MOVED px of travel along an axis Plotly zeroes that travel, moves
    // the shape on the other axis alone and re-snaps the position it did
    // not move: a value side's arrow dragged sideways came back 0.0800462
    // for 0.08, a ring dragged sideways wrote its value 0.5 as
    // 0.500009668842, a bound line 1.0 as 1.0000266 — each staged. For
    // EVERY shape an editor moves, a release says no position (null) on
    // an axis the pointer went under MOVED px along, press to release:
    // the editor reads no move there — under MOVED px on both, no move at
    // all — and the redraw that answers every release puts the shape
    // back.
    const released = (update) => {
      const shapes = (gd.layout && gd.layout.shapes) || [];
      const info = said(gd);
      const travel = st.travel;
      st.travel = null;
      const ended = st.ended;
      st.ended = null;
      const out = { ...update };
      if (!info) return out;
      for (const [key, value] of Object.entries(update)) {
        const m = /^shapes\[(\d+)\]\.([xy])(anchor|0|1)$/.exec(key);
        if (!m || typeof value !== "number") continue;
        const s = shapes[+m[1]];
        if (!s || !info.draggable.has(s.name)) continue;
        // What its axes showed, for the server's snap, and the release's
        // number: past every move of the drag it ends.
        out[`shapes[${m[1]}].visible`] = visible(s);
        out[`shapes[${m[1]}].seq`] = ended ? ended.seq : ++seq;
        if (travel && travel[m[2]] < MOVED) {
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
    // A release that cannot be read goes on as it came.
    st.released = (update) => guarded(released)(update) || update;

    gd.addEventListener("mousemove", move);
    gd.addEventListener("mouseleave", leave);
    gd.addEventListener("click", clicked, true);
    document.addEventListener("mousemove", follow, true);
    document.addEventListener("mousedown", down, true);
    document.addEventListener("mouseup", up, true);
    window.addEventListener("blur", blur);
    // On the window, as it is captured: a scroll does not bubble, and
    // whichever ancestor of the plot scrolls is heard on the way down.
    window.addEventListener("scroll", scrolled, { capture: true, passive: true });

    // The marks, the clusters, the colliding arrows' moves, and which
    // cluster the pointer is in: after every redraw — the one after a
    // release too, shown if the pointer is still there. Whatever moves a
    // shape's pixels draws it again: a zoom, a pan, a reset, a resize of
    // the window, a trace hidden from the legend, the server's own update
    // are each a relayout or a replot, and Plotly draws every shape anew
    // in each (`shapes.draw`).
    const remark = guarded(() => {
      const arrows = mark();
      const hover = areas();
      st.clusters = clustered(hover);
      stagger(arrows, hover);
      here(st.xy);
    });

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
            remark();
            return;
          }
        }
      }
    });
    observer.observe(gd, { childList: true, subtree: true });

    st.destroy = () => {
      gd.removeEventListener("mousemove", move);
      gd.removeEventListener("mouseleave", leave);
      gd.removeEventListener("click", clicked, true);
      document.removeEventListener("mousemove", follow, true);
      document.removeEventListener("mousedown", down, true);
      st.held = null;
      document.removeEventListener("mouseup", up, true);
      window.removeEventListener("blur", blur);
      window.removeEventListener("scroll", scrolled, true);
      cancelAnimationFrame(st.frame);
      observer.disconnect();
      unmark();
    };
    remark();
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
      instances.set(id, create(gd, id));
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
