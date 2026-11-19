// @ts-check
/**
 * The network plate: Ireland's railway drawn from network.json (and the real track from
 * tracks.json when it is present), with every running train at the place it last reported
 * (D87). The feed's coordinates are report locations, not measured positions, so a train sits
 * exactly on its last station, pointed toward its next stop, and nothing moves between
 * snapshots.
 *
 * The same drawing serves the tilted overview and the flat explorer: `view()` changes the
 * window onto the map, and marks, labels and strokes are kept a constant size on screen.
 */

const NS = "http://www.w3.org/2000/svg";

/**
 * @param {string} tag
 * @param {Record<string, string|number>} [attrs]
 * @param {string} [text]
 */
function s(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text != null) node.textContent = text;
  return node;
}

/**
 * @typedef {{code: string, name: string, lat: number|null, lon: number|null, x: number|null, y: number|null}} Station
 * @typedef {{a: string, b: string, services: number}} Link
 * @typedef {{stations: Station[], links: Link[], coast: number[][][]}} Network
 * @typedef {{links: Record<string, number[][]>, stations?: Record<string, {x: number, y: number}>}} Tracks
 * @typedef {{train: string, status: string, direction: string, lat: number|null, lon: number|null, message: string[]}} FleetTrain
 * @typedef {{code: string, name: string, scheduled: string, arrived: string|null, expected: string|null, here: boolean}} PatternStop
 * @typedef {{train: string, station_code: string, predicted: string, interval_80pct: [string, string], vantage_location: string, predicted_at: string}} LivePrediction
 * @typedef {{generated_at: string, model_version: string|null, quiet_hours: boolean, fleet: FleetTrain[], journeys: Record<string, PatternStop[]>, predictions: LivePrediction[]}} Live
 * @typedef {{train: string, route: string, report: string, asOf: string, target?: {name: string, code: string, lo: string, hi: string, operator: string|null}, href: string}} TrainInfo
 */

/** Places always named, by the feed's own station names, in order of precedence. */
const MAJOR = ["Dublin Connolly", "Cork", "Galway", "Limerick", "Belfast", "Sligo", "Waterford",
  "Westport", "Tralee", "Rosslare Europort", "Athlone", "Ballina", "Dundalk", "Ennis", "Mallow"];
const SHORT = /** @type {Record<string, string>} */ ({ "Dublin Connolly": "Dublin", "Rosslare Europort": "Rosslare" });

/** Sizes on screen, in CSS pixels, whatever the zoom. */
const PX = { label: 12, dot: 2.6, train: 24, fan: 30 };

/** "HH:MM:SS" to minutes. @param {string} t */
const mins = (t) => {
  const [h = 0, m = 0, sec = 0] = t.split(":").map(Number);
  return h * 60 + m + sec / 60;
};
/** @param {string} iso */
export const hhmmOf = (iso) => (iso.match(/T(\d\d:\d\d)/) || [])[1] || "";

/**
 * The last report in a fleet message: "Departed Thurles next stop Limerick Junction".
 * @param {string[]} message
 */
export function lastReport(message) {
  const line = message[message.length - 1] || "";
  const m = line.match(/^(Departed|Arrived) (.+?) next stop (.+)$/);
  return m ? { kind: m[1] || "", at: m[2] || "", next: m[3] || "" } : null;
}

/**
 * Draw the plate into `host`.
 * @param {HTMLElement} host
 * @param {Network} net
 * @param {Live|null} live
 * @param {Tracks|null} tracks
 * @param {{onTrain?: (info: TrainInfo|null, el: Element|null) => void, irish?: Record<string, {ga: string}>}} [opts]
 */
export function drawPlate(host, net, live, tracks, opts = {}) {
  const irish = opts.irish || {};
  const extra = tracks?.stations || {};
  // tracks.json places the codes the feed leaves without a position and corrects the few
  // it puts far from the station (Belfast by 1.9 km). lat/lon stay the feed's, so a train
  // reported at the feed's coordinates still finds its station.
  const stations = net.stations.map((st) =>
    extra[st.code] ? { ...st, x: extra[st.code]?.x ?? null, y: extra[st.code]?.y ?? null } : st);
  const placed = stations.filter((st) => st.x != null && st.y != null);
  const byCode = new Map(placed.map((st) => [st.code, st]));
  const drawn = net.links.filter((l) => l.services >= 2 && byCode.has(l.a) && byCode.has(l.b));
  const linked = new Set(drawn.flatMap((l) => [l.a, l.b]));
  const byName = new Map(placed.filter((st) => linked.has(st.code)).map((st) => [st.name.toLowerCase(), st]));

  const xs = net.coast.flat().map((p) => p[0] ?? 0);
  const ys = net.coast.flat().map((p) => p[1] ?? 0);
  const pad = 12000;
  const minX = Math.min(...xs) - pad;
  const maxY = Math.max(...ys) + pad;
  const W = (Math.max(...xs) + pad - minX) / 1000;
  const H = (maxY - (Math.min(...ys) - pad)) / 1000;
  // Kilometres from the plate's top-left corner. Raw ITM metres run to ~960,000, and at that
  // magnitude Chromium mis-rasterises small shapes inside a 3D-transformed ancestor.
  /** @param {number} x @param {number} y @returns {[number, number]} */
  const K = (x, y) => [(x - minX) / 1000, (maxY - y) / 1000];
  /** @param {Station} st */
  const P = (st) => K(/** @type {number} */ (st.x), /** @type {number} */ (st.y));
  /** @param {number[][]} pts */
  const d = (pts) => "M" + pts.map((p) => K(p[0] ?? 0, p[1] ?? 0).map((v) => v.toFixed(3)).join(" ")).join("L");

  const svg = s("svg", { class: "plate", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-labelledby": "plate-title" });
  svg.append(s("title", { id: "plate-title" }, "Ireland's railway network, with each running train at the place it last reported"));

  const land = s("g", { class: "plate-land" });
  for (const ring of net.coast) land.append(s("path", { d: d(ring) + "Z" }));
  svg.append(land);

  // A link seen on one service only is a diversion, not a line: one run of A523 round the
  // Lavistown loop would otherwise draw a triangle at Kilkenny.
  const lines = s("g", { class: "plate-lines" });
  for (const l of drawn) {
    const a = /** @type {Station} */ (byCode.get(l.a));
    const b = /** @type {Station} */ (byCode.get(l.b));
    const path = tracks?.links[`${l.a}|${l.b}`] || [[a.x ?? 0, a.y ?? 0], [b.x ?? 0, b.y ?? 0]];
    lines.append(s("path", { d: d(path), class: "draw", pathLength: 1 }));
  }
  svg.append(lines);

  // One mark per place: several codes share a name and a position (Kildare-line platforms).
  const seen = new Set();
  const places = placed.filter((st) => {
    if (!linked.has(st.code)) return false;
    const key = st.name.toLowerCase() + Math.round((st.x ?? 0) / 300) + ":" + Math.round((st.y ?? 0) / 300);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const degree = new Map();
  for (const l of drawn) for (const c of [l.a, l.b]) degree.set(c, (degree.get(c) || 0) + l.services);
  // Precedence for naming when zoomed: the major places first, then by traffic.
  const ranked = [...places].sort((p, q) => {
    const mp = MAJOR.indexOf(p.name);
    const mq = MAJOR.indexOf(q.name);
    if (mp !== mq) return (mp < 0 ? 99 : mp) - (mq < 0 ? 99 : mq);
    return (degree.get(q.code) || 0) - (degree.get(p.code) || 0);
  });

  const dots = s("g", { class: "plate-stations" });
  const dotEls = places.map((st) => {
    const [cx, cy] = P(st);
    const c = s("circle", { cx: cx.toFixed(3), cy: cy.toFixed(3), r: 1.3 });
    dots.append(c);
    return c;
  });
  svg.append(dots);
  const labels = s("g", { class: "plate-labels", "aria-hidden": "true" });
  svg.append(labels);

  const result = { running: 0, drawn: 0, unplaced: 0, asOf: live ? hhmmOf(live.generated_at) : "" };
  const trains = s("g", { class: "plate-trains" });
  const running = (live?.fleet || []).filter((t) => t.status === "R");
  result.running = running.length;
  const preds = new Map((live?.predictions || []).map((p) => [p.train, p]));
  /** @type {{g: Element, x: number, y: number, angle: number}[]} */
  const trainEls = [];

  for (const t of running) {
    const rep = lastReport(t.message);
    const at = t.lat != null && t.lon != null ? nearest(placed, t.lat, t.lon) : null;
    if (!rep || !at) {
      result.unplaced++;
      continue;
    }
    const next = byName.get(rep.next.toLowerCase());
    const [x0, y0] = P(at);
    const [nx, ny] = next ? P(next) : [x0, y0];
    const angle = (Math.atan2(ny - y0, nx - x0) * 180) / Math.PI;
    const route = (t.message[0] || "").replace(/^\d\d:\d\d - /, "").replace(/ \(.*\)$/, "");
    /** @type {TrainInfo} */
    const info = {
      train: t.train, route, report: `${rep.kind} ${rep.at}, next stop ${rep.next}`, asOf: result.asOf,
      href: `predictions.html?station=${encodeURIComponent(next?.code || at.code)}`,
    };
    const g = s("a", { href: info.href, class: "plate-train",
      "aria-label": `${t.train}, ${route}. ${info.report}, as reported ${result.asOf}.` });
    const p = preds.get(t.train);
    const target = p ? (live?.journeys?.[t.train] || []).find((x) => x.code === p.station_code) : undefined;
    if (p && target) {
      const [lo = "", hi = ""] = p.interval_80pct;
      info.target = { name: target.name, code: target.code, lo: lo.slice(0, 5), hi: hi.slice(0, 5), operator: target.expected };
      info.href = `predictions.html?station=${encodeURIComponent(target.code)}`;
      g.setAttribute("href", info.href);
      // Fan width is the range in minutes, so a wide fan is an unsure answer.
      const half = (Math.min(34, 5 + Math.max(1, mins(hi) - mins(lo)) * 2.2) * Math.PI) / 180;
      const R = (PX.fan / PX.train) * 13;
      g.append(s("path", { class: "plate-fan",
        d: `M0 0L${R * Math.cos(half)} ${-R * Math.sin(half)}A${R} ${R} 0 0 1 ${R * Math.cos(half)} ${R * Math.sin(half)}Z` }));
    }
    // A small train seen from above, 13 units long: body, window band, a darker leading cab.
    g.append(s("rect", { class: "tr-body", x: -6.5, y: -2.4, width: 13, height: 4.8, rx: 2.4 }));
    g.append(s("rect", { class: "tr-band", x: -4.5, y: -0.9, width: 8, height: 1.8, rx: 0.9 }));
    g.append(s("path", { class: "tr-cab", d: "M4.3 -2.4h-.2v4.8h.2a2.4 2.4 0 0 0 0 -4.8z" }));
    if (opts.onTrain) {
      g.addEventListener("pointerenter", () => opts.onTrain?.(info, g));
      g.addEventListener("focus", () => opts.onTrain?.(info, g));
      g.addEventListener("pointerleave", () => opts.onTrain?.(null, null));
      g.addEventListener("blur", () => opts.onTrain?.(null, null));
    }
    trains.append(g);
    trainEls.push({ g, x: x0, y: y0, angle });
    result.drawn++;
  }
  svg.append(trains);
  host.replaceChildren(svg);

  /** The current window onto the map, in plate kilometres. */
  let vb = { x: 0, y: 0, w: W, h: H };

  /** CSS pixels per plate kilometre as the SVG is laid out now (it scales to fit, "meet"). */
  function ratio() {
    const w = svg.clientWidth || svg.getBoundingClientRect().width;
    const h = svg.clientHeight || (w * H) / W;
    return Math.min(w / vb.w, h / vb.h) || 2;
  }

  /**
   * Keep marks a constant size on screen, and name as many places as fit without overlap,
   * the major places first.
   * @param {boolean} all name every place that fits, not only the major ones
   */
  function redraw(all) {
    const k = ratio();
    svg.style.setProperty("--u", String(1 / k));
    for (const c of dotEls) c.setAttribute("r", (PX.dot / k).toFixed(4));
    // Trains scale with the plate until it is wide enough for their full size: on a phone a
    // 24 px train per report would bury Dublin, where most of the network's trains are.
    const plateW = svg.clientWidth || svg.getBoundingClientRect().width || 600;
    const sc = Math.min(PX.train, Math.max(12, plateW / 30)) / 13 / k;
    for (const t of trainEls) {
      t.g.setAttribute("transform",
        `translate(${t.x.toFixed(3)} ${t.y.toFixed(3)}) rotate(${t.angle.toFixed(1)}) scale(${sc.toFixed(5)})`);
    }
    /** @type {number[][]} */
    const taken = [];
    labels.replaceChildren();
    for (const st of all ? ranked : ranked.filter((p) => MAJOR.includes(p.name))) {
      const [x, y] = P(st);
      if (x < vb.x || x > vb.x + vb.w || y < vb.y || y > vb.y + vb.h) continue;
      const name = all ? st.name : SHORT[st.name] || st.name;
      const ga = all && irish[st.code]?.ga !== st.name ? irish[st.code]?.ga || "" : "";
      const sx = (x - vb.x) * k + PX.dot + 4;
      const sy = (y - vb.y) * k - PX.label * 0.6;
      const ex = sx + (name.length + (ga ? ga.length + 2 : 0)) * PX.label * 0.56 + 6;
      const ey = sy + PX.label * 1.25;
      if (taken.some(([x0 = 0, y0 = 0, x1 = 0, y1 = 0]) => sx < x1 && ex > x0 && sy < y1 && ey > y0)) continue;
      taken.push([sx, sy, ex, ey]);
      const label = s("text", {
        x: (x + (PX.dot + 4) / k).toFixed(3), y: (y + (PX.label * 0.36) / k).toFixed(3),
        "font-size": (PX.label / k).toFixed(4), "stroke-width": (3.5 / k).toFixed(4),
      }, name);
      if (ga) label.append(s("tspan", { class: "ga", "xml:lang": "ga", dx: (PX.label * 0.4 / k).toFixed(4) }, ga));
      labels.append(label);
    }
  }

  return {
    result, W, H, svg,
    /** Set the window onto the map, in plate kilometres, and redraw what depends on scale.
     * @param {{x: number, y: number, w: number, h: number}} next @param {boolean} [all] */
    view(next, all = false) {
      vb = next;
      svg.setAttribute("viewBox", `${vb.x.toFixed(3)} ${vb.y.toFixed(3)} ${vb.w.toFixed(3)} ${vb.h.toFixed(3)}`);
      redraw(all);
    },
    redraw,
  };
}

/**
 * The station a reported position sits on. 90% of positions are exactly a station's
 * coordinates; anything over 300 m from every station is not drawn.
 * @param {Station[]} stations @param {number} lat @param {number} lon
 */
function nearest(stations, lat, lon) {
  let best = null;
  let bestKm = 0.3;
  for (const st of stations) {
    if (st.lat == null || st.lon == null) continue;
    const km = Math.hypot((lat - st.lat) * 111.2, (lon - st.lon) * 111.2 * Math.cos((lat * Math.PI) / 180));
    if (km < bestKm) {
      bestKm = km;
      best = st;
    }
  }
  return best;
}
