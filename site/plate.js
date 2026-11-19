// @ts-check
/**
 * The network plate: Ireland's railway drawn from network.json, with every running train at
 * the place it last reported (D87). The feed's coordinates are report locations, not
 * measured positions, so a train sits exactly on its last station, pointed toward its next
 * stop, and nothing moves between snapshots.
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
 * @typedef {{train: string, status: string, direction: string, lat: number|null, lon: number|null, message: string[]}} FleetTrain
 * @typedef {{code: string, name: string, scheduled: string, arrived: string|null, expected: string|null, here: boolean}} PatternStop
 * @typedef {{train: string, station_code: string, predicted: string, interval_80pct: [string, string], vantage_location: string, predicted_at: string}} LivePrediction
 * @typedef {{generated_at: string, model_version: string|null, quiet_hours: boolean, fleet: FleetTrain[], journeys: Record<string, PatternStop[]>, predictions: LivePrediction[]}} Live
 */

/** Places named on the plate, by the feed's own station names. */
const LABELS = ["Dublin Connolly", "Cork", "Galway", "Limerick", "Belfast", "Sligo", "Waterford",
  "Westport", "Tralee", "Rosslare Europort", "Athlone", "Ballina", "Dundalk", "Ennis", "Mallow"];
const SHORT = /** @type {Record<string, string>} */ ({ "Dublin Connolly": "Dublin", "Rosslare Europort": "Rosslare" });

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
 * Draw the plate into `host`. Returns what was drawn, so the page can state it.
 * @param {HTMLElement} host
 * @param {Network} net
 * @param {Live|null} live
 * @param {{onTrain?: (info: TrainInfo|null, el: Element|null) => void}} [opts]
 *
 * @typedef {{train: string, route: string, report: string, asOf: string, target?: {name: string, code: string, lo: string, hi: string, operator: string|null}, href: string}} TrainInfo
 */
export function drawPlate(host, net, live, opts = {}) {
  const placed = net.stations.filter((st) => st.x != null && st.y != null);
  const byCode = new Map(placed.map((st) => [st.code, st]));
  const linked = new Set(net.links.flatMap((l) => [l.a, l.b]));
  const byName = new Map(placed.filter((st) => linked.has(st.code)).map((st) => [st.name.toLowerCase(), st]));

  const xs = net.coast.flat().map((p) => p[0] ?? 0);
  const ys = net.coast.flat().map((p) => p[1] ?? 0);
  const pad = 12000;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  // Kilometres from the plate's top-left corner. Raw ITM metres run to ~960,000, and at that
  // magnitude Chromium mis-rasterises small shapes inside a 3D-transformed ancestor: the
  // trains and labels vanished from the tilted plate while the long lines survived.
  /** @param {number} x @param {number} y */
  const K = (x, y) => [(x - minX) / 1000, (maxY - y) / 1000];
  /** @param {Station} st */
  const P = (st) => K(/** @type {number} */ (st.x), /** @type {number} */ (st.y));

  const svg = s("svg", {
    class: "plate", viewBox: `0 0 ${(maxX - minX) / 1000} ${(maxY - minY) / 1000}`,
    role: "img", "aria-labelledby": "plate-title",
  });
  svg.append(s("title", { id: "plate-title" }, "Ireland's railway network, with each running train at the place it last reported"));

  const land = s("g", { class: "plate-land" });
  for (const ring of net.coast) {
    land.append(s("path", { d: "M" + ring.map((p) => K(p[0] ?? 0, p[1] ?? 0).map((v) => v.toFixed(2)).join(" ")).join("L") + "Z" }));
  }
  svg.append(land);

  // A service seen once is a diversion, not a line: one run of A523 round the Lavistown
  // loop would otherwise draw a triangle at Kilkenny.
  const lines = s("g", { class: "plate-lines" });
  for (const l of net.links) {
    const a = byCode.get(l.a);
    const b = byCode.get(l.b);
    if (!a || !b || l.services < 2) continue;
    const [x1, y1] = P(a);
    const [x2, y2] = P(b);
    lines.append(s("line", { x1: x1 ?? 0, y1: y1 ?? 0, x2: x2 ?? 0, y2: y2 ?? 0, class: "draw", pathLength: 1 }));
  }
  svg.append(lines);

  // One dot per place: several codes share a name and a position (Kildare-line platforms).
  const dots = s("g", { class: "plate-stations" });
  const seen = new Set();
  for (const st of placed) {
    if (!linked.has(st.code)) continue;
    const key = st.name.toLowerCase() + Math.round((st.x ?? 0) / 300) + ":" + Math.round((st.y ?? 0) / 300);
    if (seen.has(key)) continue;
    seen.add(key);
    const [cx, cy] = P(st);
    dots.append(s("circle", { cx: cx ?? 0, cy: cy ?? 0, r: 1.3 }));
  }
  svg.append(dots);

  const labels = s("g", { class: "plate-labels", "aria-hidden": "true" });
  for (const name of LABELS) {
    const st = byName.get(name.toLowerCase());
    if (!st) continue;
    const [lx, ly] = P(st);
    labels.append(s("text", { x: (lx ?? 0) + 4.2, y: (ly ?? 0) + 2.4 }, SHORT[name] || name));
  }
  svg.append(labels);

  const result = { running: 0, drawn: 0, unplaced: 0, asOf: live ? hhmmOf(live.generated_at) : "" };
  const trains = s("g", { class: "plate-trains" });
  const running = (live?.fleet || []).filter((t) => t.status === "R");
  result.running = running.length;
  const preds = new Map((live?.predictions || []).map((p) => [p.train, p]));

  for (const t of running) {
    const rep = lastReport(t.message);
    if (t.lat == null || t.lon == null || !rep) {
      result.unplaced++;
      continue;
    }
    const at = nearest(placed, t.lat, t.lon);
    if (!at) {
      result.unplaced++;
      continue;
    }
    const next = byName.get(rep.next.toLowerCase());
    const [x0, y0] = P(at);
    const [nx, ny] = next ? P(next) : [x0, y0];
    const angle = (Math.atan2((ny ?? 0) - (y0 ?? 0), (nx ?? 0) - (x0 ?? 0)) * 180) / Math.PI;

    const route = (t.message[0] || "").replace(/^\d\d:\d\d - /, "").replace(/ \(.*\)$/, "");
    /** @type {TrainInfo} */
    const info = {
      train: t.train, route,
      report: `${rep.kind} ${rep.at}, next stop ${rep.next}`,
      asOf: result.asOf,
      href: `predictions.html?station=${encodeURIComponent(next?.code || at.code)}`,
    };

    const p = preds.get(t.train);
    const pattern = live?.journeys?.[t.train] || [];
    const target = p ? pattern.find((x) => x.code === p.station_code) : undefined;
    const g = s("a", {
      href: info.href, class: "plate-train",
      transform: `translate(${(x0 ?? 0).toFixed(2)} ${(y0 ?? 0).toFixed(2)}) rotate(${angle.toFixed(1)})`,
      "aria-label": `${t.train}, ${route}. ${info.report}, as reported ${result.asOf}.`,
    });
    if (p && target) {
      const [lo = "", hi = ""] = p.interval_80pct;
      info.target = { name: target.name, code: target.code, lo: lo.slice(0, 5), hi: hi.slice(0, 5), operator: target.expected };
      info.href = `predictions.html?station=${encodeURIComponent(target.code)}`;
      g.setAttribute("href", info.href);
      // Fan width is the range in minutes, so a wide fan is an unsure answer.
      const width = Math.max(1, mins(hi) - mins(lo));
      const half = (Math.min(34, 5 + width * 2.2) * Math.PI) / 180;
      const R = 17;
      g.append(s("path", {
        class: "plate-fan",
        d: `M0 0L${R * Math.cos(half)} ${-R * Math.sin(half)}A${R} ${R} 0 0 1 ${R * Math.cos(half)} ${R * Math.sin(half)}Z`,
      }));
    }
    // A small train seen from above: body, window band, and a darker cab at the leading end.
    g.append(s("rect", { class: "tr-body", x: -6.5, y: -2.4, width: 13, height: 4.8, rx: 2.4 }));
    g.append(s("rect", { class: "tr-band", x: -4.5, y: -0.9, width: 8, height: 1.8, rx: 0.9 }));
    g.append(s("path", { class: "tr-cab", d: "M4.3 -2.4h-.2v4.8h.2a2.4 2.4 0 0 0 0 -4.8z" }));
    if (opts.onTrain) {
      const show = () => opts.onTrain?.(info, g);
      const hide = () => opts.onTrain?.(null, null);
      g.addEventListener("pointerenter", show);
      g.addEventListener("focus", show);
      g.addEventListener("pointerleave", hide);
      g.addEventListener("blur", hide);
    }
    trains.append(g);
    result.drawn++;
  }
  svg.append(trains);
  host.replaceChildren(svg);
  return result;
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
