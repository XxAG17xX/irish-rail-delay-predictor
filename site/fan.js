// @ts-check
/**
 * The time-distance fan: the signature chart wherever a prediction appears.
 *
 * A train graph in the manner of a railway planner's working timetable: time runs across,
 * the train's stops run down. The timetable is a dashed line, the train's reported path is
 * solid, and from its last report a fan opens to the station asked about, spanning the 80%
 * range. Between the report and that station the fan is geometry, not prediction: the model
 * answers only at the target, so only the target carries figures.
 */

const NS = "http://www.w3.org/2000/svg";
let drawn = 0;

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

/** "HH:MM" or "HH:MM:SS" to minutes after midnight, or null. @param {string|null|undefined} t */
function minutes(t) {
  if (!t) return null;
  const [h = 0, m = 0, sec = 0] = t.split(":").map(Number);
  return h * 60 + m + sec / 60;
}

/** Minutes back to "HH:MM", wrapping past midnight. @param {number} m */
export function clock(m) {
  const r = Math.round(m);
  const w = ((r % 1440) + 1440) % 1440;
  return String(Math.floor(w / 60)).padStart(2, "0") + ":" + String(w % 60).padStart(2, "0");
}

/**
 * @typedef {object} FanStop
 * @property {string} code
 * @property {string} name
 * @property {string} scheduled
 * @property {string|null} arrived
 * @property {boolean} here
 *
 * @typedef {object} FanInput
 * @property {string} train
 * @property {string} destination
 * @property {string} operator_eta
 * @property {FanStop[]} journey
 * @property {{predicted: string, interval_80pct: [string, string], vantage_location: string|null, vantage_name: string|null}} prediction
 */

/**
 * Draw the fan for one train into `host`, replacing what is there.
 * @param {HTMLElement} host
 * @param {FanInput} entry
 * @param {{now: string, km?: (code: string) => number|undefined, animate?: boolean}} opts
 *   `now` is the board's generation time. `km` places stops by distance along the line
 *   when station positions are known; without it stops are evenly spaced.
 * @returns {"exact"|"earlier"|false} "earlier" when the last report came from a timing
 *   point the route does not list, so the fan starts from the last listed stop that reported;
 *   false when there is nothing honest to draw
 */
export function drawFan(host, entry, opts) {
  const p = entry.prediction;
  const stops = entry.journey || [];
  const ti = stops.findIndex((x) => x.here);
  let vi = stops.findIndex((x) => x.code === p.vantage_location);
  const exact = vi >= 0;
  if (!exact) {
    // Timing points are left out of the route, so a report from one has no row to start from.
    for (let i = ti - 1; i >= 0; i--) if (stops[i]?.arrived) { vi = i; break; }
  }
  const vantage = stops[vi];
  const target = stops[ti];
  if (vi < 0 || !vantage || !target || ti <= vi || !vantage.arrived) return false;

  const first = Math.max(0, vi - 1);
  const shown = stops.slice(first, ti + 1);
  const base = minutes(shown[0]?.scheduled) ?? minutes(shown[0]?.arrived) ?? 0;
  /** Unwrap a clock time onto this journey's own timeline. @param {string|null|undefined} t */
  const t = (t) => {
    const m = minutes(t);
    if (m == null) return null;
    return m < base - 720 ? m + 1440 : m;
  };

  const [loT, hiT] = [t(p.interval_80pct[0]), t(p.interval_80pct[1])];
  const medT = t(p.predicted);
  const opT = t(entry.operator_eta);
  const nowT = t(opts.now);
  const apexT = t(vantage.arrived);
  if (loT == null || hiT == null || medT == null || apexT == null) return false;

  // The window is the decision, not the journey: from shortly before the last report to
  // just past the range. Earlier path is clipped at the left edge rather than squeezing
  // the fan into a sliver.
  const t0 = Math.floor((Math.min(apexT, nowT ?? apexT) - 15) / 5) * 5;
  const t1 = Math.ceil((Math.max(hiT, opT ?? hiT, nowT ?? hiT) + 4) / 5) * 5;

  const W = Math.max(320, host.clientWidth || 640);
  const narrow = W < 520;
  // On a phone the station names sit above their rows, so the plot keeps the full width.
  // Elsewhere the gutter fits the longest name shown, at about 6.6 px a character.
  const longest = Math.max(...shown.map((x) => x.name.length));
  const left = narrow ? 10 : Math.min(Math.max(longest * 6.6 + 20, 96), 220);
  const right = 18;
  const top = narrow ? 74 : 56;
  const rowGap = narrow ? 44 : 46;
  const plotH = Math.max(rowGap * (shown.length - 1), 150);
  const H = top + plotH + 40;

  // Distance along the line when it is known, otherwise even spacing.
  const along = shown.map((x, i) => (opts.km ? opts.km(x.code) : undefined) ?? i);
  const a0 = along[0] ?? 0;
  const a1 = along[along.length - 1] ?? 1;
  /** @param {number} i */
  const y = (i) => top + (((along[i] ?? i) - a0) / (a1 - a0 || 1)) * plotH;
  /** @param {number} m */
  const x = (m) => left + ((m - t0) / (t1 - t0)) * (W - left - right);

  const yT = y(shown.length - 1);
  const yV = y(vi - first);

  const svg = s("svg", {
    viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "fan", role: "img",
    "aria-label": `${entry.train} to ${entry.destination}: last reported at ` +
      `${vantage.name} at ${clock(apexT)}. RailCast expects ${target.name} between ` +
      `${clock(loT)} and ${clock(hiT)}, four times in five.` +
      (opT != null ? ` Irish Rail's estimate is ${clock(opT)}.` : ""),
  });

  // Time grid: a hairline every five minutes, labelled every ten (every five when short).
  const perMin = (W - left - right) / (t1 - t0);
  const step = [5, 10, 15, 20, 30, 60].find((m) => m * perMin >= 46) ?? 60;
  const grid = s("g", { class: "fan-grid", "aria-hidden": "true" });
  for (let m = t0; m <= t1; m += 5) {
    grid.append(s("line", { x1: x(m), x2: x(m), y1: top - 8, y2: top + plotH + 6, class: m % step ? "minor" : "" }));
    if (m % step === 0 && x(m) > 18 && x(m) < W - 18) grid.append(s("text", { x: x(m), y: top - (narrow ? 30 : 14), "text-anchor": "middle" }, clock(m)));
  }
  // Station names go on top of the drawing, haloed, so the path never hides one.
  const names = s("g", { class: "fan-grid", "aria-hidden": "true" });
  shown.forEach((stop, i) => {
    grid.append(s("line", { x1: left - 6, x2: W - right, y1: y(i), y2: y(i), class: "row" }));
    names.append(s("text", narrow
      ? { x: left, y: y(i) - 6, class: stop.here ? "here" : "" }
      : { x: left - 12, y: y(i) + 4, "text-anchor": "end", class: stop.here ? "here" : "" },
    stop.name));
  });
  svg.append(grid);

  const clipId = `fan-clip-${++drawn}`;
  const clip = s("clipPath", { id: clipId });
  clip.append(s("rect", { x: left, y: 0, width: W - left - right, height: H }));
  svg.append(clip);
  const plot = s("g", { "clip-path": `url(#${clipId})` });
  svg.append(plot);

  // The timetable, dashed: where the train should be.
  /** @param {(stop: FanStop) => string|null} field */
  const points = (field) => shown.flatMap((stop, i) => {
    const m = t(field(stop));
    return m == null ? [] : [{ x: x(m), y: y(i), i }];
  });
  const plan = points((stop) => stop.scheduled);
  plot.append(s("polyline", {
    class: "fan-plan", points: plan.map((q) => `${q.x},${q.y}`).join(" "),
  }));

  // The reported path, solid: where the train was, up to its last report.
  const done = points((stop) => stop.arrived).filter((q) => q.i <= vi - first);
  const path = s("polyline", {
    class: "fan-path", pathLength: 1,
    points: done.map((q) => `${q.x},${q.y}`).join(" "),
  });
  plot.append(path);

  // The fan, from the last report to the range at the target.
  const ax = x(apexT);
  const fan = s("g", { class: "fan-body" });
  fan.append(s("polygon", { class: "fan-band", points: `${ax},${yV} ${x(loT)},${yT} ${x(hiT)},${yT}` }));
  fan.append(s("line", { class: "fan-edge", x1: ax, y1: yV, x2: x(loT), y2: yT }));
  fan.append(s("line", { class: "fan-edge", x1: ax, y1: yV, x2: x(hiT), y2: yT }));
  fan.append(s("line", { class: "fan-median", x1: ax, y1: yV, x2: x(medT), y2: yT }));
  fan.append(s("line", { class: "fan-span", x1: x(loT), x2: x(hiT), y1: yT, y2: yT }));
  plot.append(fan);

  for (const q of done) plot.append(s("circle", { class: "fan-report", cx: q.x, cy: q.y, r: 3.5 }));
  svg.append(s("circle", { class: "fan-apex", cx: ax, cy: yV, r: 5 }));
  svg.append(names);

  // Figures at the target only: the range above the row, Irish Rail's single time below.
  const figures = s("g", { class: "fan-figures" });
  const lx = x(loT);
  const hx = x(hiT);
  const close = hx - lx < 84;
  figures.append(s("text", { x: lx, y: yT - 10, "text-anchor": close ? "end" : "middle", class: "range" }, clock(loT)));
  figures.append(s("text", { x: hx, y: yT - 10, "text-anchor": close ? "start" : "middle", class: "range" }, clock(hiT)));
  if (opT != null) {
    const ox = x(opT);
    const tx = Math.min(Math.max(ox, left + 48), W - right - 48);
    figures.append(s("path", { class: "fan-operator", d: `M${ox} ${yT + 4}v12` }));
    figures.append(s("text", { x: tx, y: yT + 30, "text-anchor": "middle", class: "operator" }, `Irish Rail ${clock(opT)}`));
  }
  svg.append(figures);

  if (nowT != null && nowT >= t0 && nowT <= t1) {
    const nx = x(nowT);
    svg.append(s("line", { class: "fan-now", x1: nx, x2: nx, y1: top - (narrow ? 46 : 30), y2: top + plotH + 6 }));
    svg.append(s("text", { class: "fan-now-label", x: nx + 5, y: top - (narrow ? 50 : 34), "text-anchor": "start" }, `now ${clock(nowT)}`));
  }

  host.replaceChildren(svg);

  // Lines draw, then the fan unfolds from the report. Nothing moves after that.
  if (opts.animate !== false && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    path.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }],
      { duration: 650, easing: "cubic-bezier(.5,0,.2,1)", fill: "backwards" });
    fan.style.transformOrigin = `${ax}px ${yV}px`;
    fan.animate([{ transform: "scale(0.02)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
      { duration: 700, delay: 450, easing: "cubic-bezier(.16,1,.3,1)", fill: "backwards" });
    figures.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, delay: 950, fill: "backwards" });
  }
  return exact ? "exact" : "earlier";
}
