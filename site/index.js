// @ts-check
/** Landing page: the hero run, the scroll-driven method walkthrough, and the lead control. */

import { need, reducedMotion, watchSteps } from "./site.js";

/* ── the hero train runs once, to the position of its own prediction ────── */

const train = need("train");
const HERO_Q50_X = 715;
if (reducedMotion) {
  train.setAttribute("transform", `translate(${HERO_Q50_X},0)`);
} else {
  train.animate(
    [{ transform: "translate(300px,0)" }, { transform: `translate(${HERO_Q50_X}px,0)` }],
    { duration: 2600, delay: 700, easing: "cubic-bezier(.33,1,.68,1)", fill: "both" }
  );
}

/* ── the walkthrough diagram ────────────────────────────────────────────── */

const sd = {
  done: need("sd-done"),
  band: need("sd-band"),
  eL: need("sd-eL"),
  eR: need("sd-eR"),
  cap: need("sd-cap"),
  train: need("sd-train"),
  stops: /** @type {SVGCircleElement[]} */ ([...document.querySelectorAll("#sd-stops circle")]),
};

/**
 * @typedef {object} Frame
 * @property {number} done   how far along the journey has been travelled
 * @property {number} reported how many stops have reported
 * @property {[number, number]|null} band the 80% range, or null when there is none yet
 * @property {number|null} train where the train sits, or null when it has not departed
 * @property {string} cap
 */

const X = [30, 110, 190, 290, 430];

/** @type {Frame[]} */
const FRAMES = [
  { done: X[0] ?? 30, reported: 0, band: null, train: null, cap: "nothing reported yet" },
  { done: X[2] ?? 190, reported: 3, band: null, train: X[2] ?? 190, cap: "3 stops reported" },
  { done: X[2] ?? 190, reported: 3, band: [396, 424], train: X[2] ?? 190, cap: "80% range" },
  { done: X[1] ?? 110, reported: 2, band: [352, 430], train: X[1] ?? 110, cap: "asked earlier, wider range" },
  { done: X[2] ?? 190, reported: 3, band: [396, 424], train: X[2] ?? 190, cap: "landed inside" },
];

/** @param {number} i */
function paint(i) {
  const f = FRAMES[i];
  if (!f) return;

  sd.done.setAttribute("x2", String(f.done));
  sd.stops.forEach((c, n) => c.classList.toggle("rep", n < f.reported));
  sd.cap.textContent = f.cap;
  sd.cap.classList.toggle("hot", i === 4);

  if (f.band) {
    const [lo, hi] = f.band;
    sd.band.setAttribute("x1", String(lo));
    sd.band.setAttribute("x2", String(hi));
    sd.eL.setAttribute("x1", String(lo));
    sd.eL.setAttribute("x2", String(lo));
    sd.eR.setAttribute("x1", String(hi));
    sd.eR.setAttribute("x2", String(hi));
    sd.eL.setAttribute("opacity", "0.55");
    sd.eR.setAttribute("opacity", "0.55");
    sd.band.style.opacity = "0.16";
  } else {
    sd.band.style.opacity = "0";
    sd.eL.setAttribute("opacity", "0");
    sd.eR.setAttribute("opacity", "0");
  }

  if (f.train == null) {
    sd.train.setAttribute("opacity", "0");
  } else {
    sd.train.setAttribute("opacity", "1");
    sd.train.setAttribute("transform", `translate(${f.train},0)`);
  }
}

paint(0);
watchSteps(document.querySelectorAll(".step"), (i) => paint(i));

/* ── the lead control ───────────────────────────────────────────────────── */

/**
 * Measured over the seven days to 2026-09-08, one row per lead band. These are the same
 * figures accuracy.json carries under rolling.by_lead_band; they are inlined here so the
 * landing page needs no fetch, and they change only when the rollup is regenerated.
 * @type {{name: string, mae: number, op: number, better: number, cov: number, n: number}[]}
 */
const BANDS = [
  { name: "the next 5 minutes", mae: 59.7, op: 79.2, better: 28.2, cov: 75.7, n: 27304 },
  { name: "5 to 15 minutes", mae: 82.9, op: 109.2, better: 29.4, cov: 75.9, n: 24465 },
  { name: "15 to 30 minutes", mae: 111.1, op: 131.4, better: 25.2, cov: 76.6, n: 19571 },
  { name: "30 to 60 minutes", mae: 140.9, op: 148.7, better: 20.7, cov: 73.4, n: 12856 },
  { name: "an hour or more", mae: 194.9, op: 193.0, better: 23.5, cov: 65.7, n: 4340 },
];

const lead = /** @type {HTMLInputElement} */ (need("lead"));
const band = need("band");
const edgeL = need("edgeL");
const edgeR = need("edgeR");

function showBand() {
  const b = BANDS[Number(lead.value)];
  if (!b) return;

  need("leadOut").textContent = b.name;
  need("rMae").textContent = b.mae.toFixed(1) + "s";
  need("rOp").textContent = b.op.toFixed(1) + "s";
  need("rBet").textContent = "+" + b.better.toFixed(1) + "%";
  need("rCov").textContent = b.cov.toFixed(1) + "%";
  need("rN").textContent = b.n.toLocaleString("en-IE");

  const low = b.cov < 70;
  need("rCov").classList.toggle("text-caution", low);

  // The range on the hero diagram is scaled from the measured error for that band, so
  // dragging the control shows the widening rather than asserting it.
  const half = 9 + b.mae * 0.42;
  const centre = HERO_Q50_X;
  for (const el of [band, edgeL, edgeR]) el.classList.toggle("warn", low);
  band.setAttribute("x1", String(centre - half));
  band.setAttribute("x2", String(centre + half));
  edgeL.setAttribute("x1", String(centre - half));
  edgeL.setAttribute("x2", String(centre - half));
  edgeR.setAttribute("x1", String(centre + half));
  edgeR.setAttribute("x2", String(centre + half));
  need("rangeT").textContent = "the range widens with the horizon";
}

lead.addEventListener("input", showBand);
showBand();
