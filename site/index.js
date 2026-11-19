// @ts-check
/** Landing page: the network plate, the live headline, and the horizon control. */
import { need } from "./site.js";
import { drawPlate, hhmmOf } from "./plate.js";

/* ── the plate and the headline ───────────────────────────────────────────── */

/** A snapshot older than this is not shown as live: four missed cycles. */
const STALE_MIN = 20;

/** @param {string} url */
async function json(url) {
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

/**
 * The live headline: a prediction whose station also has Irish Rail's estimate, so the two
 * can be set side by side, for a train 10 to 45 minutes out, the moment someone is deciding
 * whether to hurry. One about twenty minutes ahead is preferred, and a range that rounds to a
 * single minute is skipped, because "14:24 to 14:24" says nothing. Without one, the page
 * keeps the logged example it was served with.
 * @param {import("./plate.js").Live} live
 */
function headline(live) {
  const asked = hhmmOf(live.generated_at);
  const now = minsOf(asked);
  const options = live.predictions
    .map((p) => {
      const pattern = live.journeys[p.train] || [];
      const target = pattern.find((x) => x.code === p.station_code);
      const [lo = "", hi = ""] = p.interval_80pct;
      let ahead = minsOf(lo.slice(0, 5)) - now;
      if (ahead < -720) ahead += 1440;
      return { p, target, lo: lo.slice(0, 5), hi: hi.slice(0, 5), ahead, dest: pattern[pattern.length - 1]?.name || "" };
    })
    .filter((o) => o.target?.expected && o.ahead >= 10 && o.ahead <= 45 && o.lo !== o.hi)
    .sort((a, b) => Math.abs(a.ahead - 20) - Math.abs(b.ahead - 20));
  const pick = options[0];
  if (!pick || !pick.target) return;
  need("h-op").textContent = pick.target.expected || "";
  need("h-lo").textContent = pick.lo;
  need("h-hi").textContent = pick.hi;
  need("h-v1").textContent = "says";
  need("h-v2").textContent = "says";
  need("h-sub").textContent =
    `${pick.p.train} to ${pick.dest}, arriving ${pick.target.name}. Live, as of ${asked}.`;
}

/** "HH:MM" to minutes. @param {string} t */
function minsOf(t) {
  const [h = 0, m = 0] = t.split(":").map(Number);
  return h * 60 + m;
}

const stage = need("stage");
const card = need("card");

/** @param {import("./plate.js").TrainInfo|null} info @param {Element|null} el */
function showCard(info, el) {
  if (!info || !el) {
    card.hidden = true;
    return;
  }
  const lines = [`${info.train} · ${info.route}`, `${info.report}`, `As reported ${info.asOf}`];
  if (info.target) {
    lines.push(`${info.target.name}: ${info.target.lo} to ${info.target.hi}` +
      (info.target.operator ? ` · Irish Rail ${info.target.operator}` : ""));
  }
  card.replaceChildren(...lines.map((t, i) => {
    const p = document.createElement("p");
    p.textContent = t;
    if (i === 0) p.className = "card-title";
    if (i === 3) p.className = "card-range";
    return p;
  }));
  const box = el.getBoundingClientRect();
  const host = stage.getBoundingClientRect();
  card.hidden = false;
  const x = Math.min(box.left - host.left + box.width / 2, host.width - card.offsetWidth / 2 - 12);
  card.style.left = Math.max(card.offsetWidth / 2 + 12, x) + "px";
  card.style.top = box.top - host.top - 12 + "px";
}

/** @type {ReturnType<typeof drawPlate>|null} */
let plate = null;
/** The 3D trains, once loaded. @type {{render: () => void, dispose: () => void}|null} */
let trains3d = null;

/**
 * Stand the trains up in 3D, after the page is idle and only where it pays: a wide screen
 * (on a phone the trains are 12 px and the 135 KB of Three.js buys nothing) and live trains to
 * draw. Any failure leaves the 2D trains, which are always there underneath.
 */
function standTrainsUp() {
  if (!plate || !matchMedia("(min-width: 52rem)").matches || !plate.trains().length) return;
  const go = () => {
    import("./trains3d.js")
      .then(({ mountTrains }) => {
        if (!plate) return;
        trains3d = mountTrains(stage, need("plate"), plate);
        if (!trains3d) return;
        trains3d.render();
        stage.classList.add("has-3d");
      })
      .catch(() => {});
  };
  const idle = /** @type {Window & {requestIdleCallback?: (cb: () => void, o?: {timeout: number}) => void}} */ (window);
  if (idle.requestIdleCallback) idle.requestIdleCallback(go, { timeout: 2500 });
  else setTimeout(go, 1200);
}
let exploring = false;
let vb = { x: 0, y: 0, w: 1, h: 1 };
const hero = need("hero");

/** Keep the window on the island and between the whole map and 1/40 of it. @param {typeof vb} next */
function setView(next) {
  if (!plate) return;
  const { W, H } = plate;
  const w = Math.min(Math.max(next.w, W / 40), W);
  const h = (w * H) / W;
  const cx = Math.min(Math.max(next.x + next.w / 2, 0), W);
  const cy = Math.min(Math.max(next.y + (next.w * H) / W / 2, 0), H);
  vb = { x: cx - w / 2, y: cy - h / 2, w, h };
  plate.view(vb, exploring);
}

/** A point on screen in plate kilometres. @param {number} sx @param {number} sy */
function toMap(sx, sy) {
  const r = (plate?.svg || stage).getBoundingClientRect();
  const k = Math.min(r.width / vb.w, r.height / vb.h);
  return {
    x: vb.x + (sx - r.left - (r.width - vb.w * k) / 2) / k,
    y: vb.y + (sy - r.top - (r.height - vb.h * k) / 2) / k,
    k,
  };
}

/** Zoom by `f` keeping the screen point (sx, sy) still. @param {number} f @param {number} sx @param {number} sy */
function zoomAt(f, sx, sy) {
  const m = toMap(sx, sy);
  setView({ x: m.x - (m.x - vb.x) / f, y: m.y - (m.y - vb.y) / f, w: vb.w / f, h: vb.h / f });
}
function zoomCentre(/** @type {number} */ f) {
  const r = stage.getBoundingClientRect();
  zoomAt(f, r.left + r.width / 2, r.top + r.height / 2);
}

/** Unfold the plate flat and hand it to the visitor, or fold it back. @param {boolean} on */
function explore(on) {
  if (!plate || on === exploring) return;
  const flip = () => {
    exploring = on;
    hero.classList.toggle("exploring", on);
    document.documentElement.classList.toggle("map-open", on);
    need("tools").hidden = !on;
    need("help").hidden = !on;
    card.hidden = true;
    if (plate) setView({ x: 0, y: 0, w: plate.W, h: plate.H });
    if (!on) requestAnimationFrame(() => trains3d?.render());
  };
  const doc = /** @type {Document & {startViewTransition?: (cb: () => void) => unknown}} */ (document);
  if (doc.startViewTransition) doc.startViewTransition(flip);
  else flip();
  if (on) stage.focus({ preventScroll: true });
  else need("explore").focus({ preventScroll: true });
}

need("explore").addEventListener("click", () => explore(true));
need("zin").addEventListener("click", () => zoomCentre(1.6));
need("zout").addEventListener("click", () => zoomCentre(1 / 1.6));
need("zreset").addEventListener("click", () => plate && setView({ x: 0, y: 0, w: plate.W, h: plate.H }));
need("zclose").addEventListener("click", () => explore(false));

stage.addEventListener("click", (ev) => {
  if (!exploring && !(/** @type {Element} */ (ev.target)).closest(".plate-train")) explore(true);
});
stage.addEventListener("wheel", (ev) => {
  if (!exploring) return;
  ev.preventDefault();
  zoomAt(Math.exp(-ev.deltaY * 0.0015), ev.clientX, ev.clientY);
}, { passive: false });
stage.addEventListener("keydown", (ev) => {
  if (!exploring) return;
  const step = 80 / toMap(0, 0).k;
  const keys = /** @type {Record<string, () => void>} */ ({
    Escape: () => explore(false),
    "+": () => zoomCentre(1.6), "=": () => zoomCentre(1.6), "-": () => zoomCentre(1 / 1.6),
    "0": () => plate && setView({ x: 0, y: 0, w: plate.W, h: plate.H }),
    ArrowLeft: () => setView({ ...vb, x: vb.x - step }), ArrowRight: () => setView({ ...vb, x: vb.x + step }),
    ArrowUp: () => setView({ ...vb, y: vb.y - step }), ArrowDown: () => setView({ ...vb, y: vb.y + step }),
  });
  const act = keys[ev.key];
  if (act) {
    ev.preventDefault();
    act();
  }
});

// Drag to pan with one pointer, pinch to zoom with two. A drag that moved is not a click,
// so releasing over a train does not open its board.
/** @type {Map<number, {x: number, y: number}>} */
const pointers = new Map();
let moved = 0;
stage.addEventListener("pointerdown", (ev) => {
  if (!exploring || (/** @type {Element} */ (ev.target)).closest(".map-tools")) return;
  pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  stage.setPointerCapture(ev.pointerId);
  moved = 0;
});
stage.addEventListener("pointermove", (ev) => {
  const was = pointers.get(ev.pointerId);
  if (!was) return;
  const now = { x: ev.clientX, y: ev.clientY };
  if (pointers.size === 1) {
    const k = toMap(0, 0).k;
    moved += Math.abs(now.x - was.x) + Math.abs(now.y - was.y);
    setView({ ...vb, x: vb.x - (now.x - was.x) / k, y: vb.y - (now.y - was.y) / k });
  } else if (pointers.size === 2) {
    const other = [...pointers.entries()].find(([id]) => id !== ev.pointerId)?.[1];
    if (other) {
      const before = Math.hypot(was.x - other.x, was.y - other.y);
      const after = Math.hypot(now.x - other.x, now.y - other.y);
      moved += 10;
      if (before > 0) zoomAt(after / before, (now.x + other.x) / 2, (now.y + other.y) / 2);
    }
  }
  pointers.set(ev.pointerId, now);
});
for (const type of ["pointerup", "pointercancel"]) {
  stage.addEventListener(type, (ev) => pointers.delete(/** @type {PointerEvent} */ (ev).pointerId));
}
stage.addEventListener("click", (ev) => {
  if (exploring && moved > 6) {
    ev.preventDefault();
    ev.stopPropagation();
  }
}, true);
addEventListener("resize", () => {
  if (!plate) return;
  plate.view(vb, exploring);
  if (!exploring) trains3d?.render();
});

Promise.all([json("network.json"), json("live.json").catch(() => null), json("tracks.json").catch(() => null),
  json("stations_ga.json").catch(() => null)])
  .then(([net, live, tracks, names]) => {
    const age = live ? (Date.now() - Date.parse(live.generated_at)) / 60000 : Infinity;
    const fresh = live && !live.quiet_hours && age < STALE_MIN;
    const shown = fresh ? live : null;
    plate = drawPlate(need("plate"), net, shown, tracks, { onTrain: showCard, irish: names?.stations });
    setView({ x: 0, y: 0, w: plate.W, h: plate.H });
    need("credit").textContent = (tracks ? "Track \u00a9 OpenStreetMap contributors (ODbL). " : "") +
      "Coast: Natural Earth. Stations: Irish Rail." + (names ? " Irish names: Logainm.ie (CC BY 4.0)." : "");
    const drawn = plate.result;
    if (!fresh || drawn.running === 0) {
      document.documentElement.dataset.service = "night";
      need("stamp").textContent = live?.quiet_hours
        ? "No trains running overnight. Trains appear here as they report in the morning."
        : "No live positions right now. Trains appear here as they report.";
      return;
    }
    headline(shown);
    standTrainsUp();
    need("stamp").textContent =
      `${drawn.drawn} of ${drawn.running} running trains drawn where they last reported, as of ${drawn.asOf}.` +
      (drawn.unplaced ? ` ${drawn.unplaced} last reported from a point without coordinates.` : "");
  })
  .catch(() => {
    need("stamp").textContent = "The network map could not be loaded.";
  });

/* ── the horizon control ──────────────────────────────────────────────────── */

/**
 * Measured over the seven days to 2026-09-08, one row per lead band, from that day's
 * accuracy.json (rolling.by_lead_band); inlined so the page needs no fetch. `mae`, `op` and
 * `better` are the matched set: RailCast and the operator on the same events. `cov` and `n`
 * cover every scored prediction in the band.
 * @type {{name: string, mae: number, op: number, better: number, matched: number, cov: number, n: number}[]}
 */
const BANDS = [
  { name: "the next 5 minutes", mae: 56.8, op: 79.2, better: 28.2, matched: 6078, cov: 75.7, n: 27304 },
  { name: "5 to 15 minutes", mae: 77.1, op: 109.2, better: 29.4, matched: 5574, cov: 75.9, n: 24465 },
  { name: "15 to 30 minutes", mae: 98.3, op: 131.4, better: 25.2, matched: 4778, cov: 76.6, n: 19571 },
  { name: "30 to 60 minutes", mae: 117.9, op: 148.7, better: 20.7, matched: 3219, cov: 73.4, n: 12856 },
  { name: "an hour or more", mae: 147.7, op: 193.0, better: 23.5, matched: 1133, cov: 65.7, n: 4340 },
];
const MAX_ERR = 200;
const lead = /** @type {HTMLInputElement} */ (need("lead"));

function showBand() {
  const b = BANDS[Number(lead.value)];
  if (!b) return;
  need("leadOut").textContent = b.name;
  for (const id of ["rMae", "rMae2"]) need(id).textContent = b.mae.toFixed(1) + "s";
  for (const id of ["rOp", "rOp2"]) need(id).textContent = b.op.toFixed(1) + "s";
  need("rBet").textContent = b.better.toFixed(1) + "%";
  need("rCov").textContent = b.cov.toFixed(1) + "%";
  need("rN").textContent = b.n.toLocaleString("en-IE");
  need("rMatched").textContent = b.matched.toLocaleString("en-IE");
  need("barMe").style.width = (b.mae / MAX_ERR) * 100 + "%";
  need("barOp").style.width = (b.op / MAX_ERR) * 100 + "%";
  need("rCov").classList.toggle("short", b.cov < 70);
  lead.setAttribute("aria-valuetext", b.name);
}
lead.addEventListener("input", showBand);
showBand();
