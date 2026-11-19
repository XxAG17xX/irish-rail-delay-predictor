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
 * The live headline: the soonest prediction in the snapshot whose station also has Irish
 * Rail's estimate, so the two can be set side by side. Without one, the page keeps the
 * logged example it was served with.
 * @param {import("./plate.js").Live} live
 */
function headline(live) {
  const asked = hhmmOf(live.generated_at);
  const options = live.predictions
    .map((p) => {
      const pattern = live.journeys[p.train] || [];
      const target = pattern.find((x) => x.code === p.station_code);
      return { p, target, dest: pattern[pattern.length - 1]?.name || "" };
    })
    .filter((o) => o.target && o.target.expected)
    .sort((a, b) => (a.p.interval_80pct[0] < b.p.interval_80pct[0] ? -1 : 1));
  const pick = options[0];
  if (!pick || !pick.target) return;
  const [lo = "", hi = ""] = pick.p.interval_80pct;
  need("h-op").textContent = pick.target.expected || "";
  need("h-lo").textContent = lo.slice(0, 5);
  need("h-hi").textContent = hi.slice(0, 5);
  need("h-v1").textContent = "says";
  need("h-v2").textContent = "says";
  need("h-sub").textContent =
    `${pick.p.train} to ${pick.dest}, arriving ${pick.target.name}. Live, as of ${asked}.`;
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

Promise.all([json("network.json"), json("live.json").catch(() => null)])
  .then(([net, live]) => {
    const age = live ? (Date.now() - Date.parse(live.generated_at)) / 60000 : Infinity;
    const fresh = live && !live.quiet_hours && age < STALE_MIN;
    const shown = fresh ? live : null;
    const drawn = drawPlate(need("plate"), net, shown, { onTrain: showCard });
    if (!fresh || drawn.running === 0) {
      document.documentElement.dataset.service = "night";
      need("stamp").textContent = live?.quiet_hours
        ? "No trains running overnight. Trains appear here as they report in the morning."
        : "No live positions right now. Trains appear here as they report.";
      return;
    }
    headline(shown);
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
