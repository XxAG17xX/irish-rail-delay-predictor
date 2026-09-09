/* Renders accuracy.json. The scorer writes that file nightly; this page never computes
   a number of its own, so anything shown here is reproducible from the scored rows. */

const GROUP_NAMES = {
  dart: "DART",
  dublin_hubs: "Dublin hubs",
  commuter_kildare: "Kildare commuter",
  commuter_maynooth: "Maynooth commuter",
  intercity_cork_corridor: "Cork corridor",
  intercity_other: "Other intercity",
  weak_coverage: "Weak-coverage lines",
  "(unpolled)": "Stations off the board sample",
};

const STATE_NAMES = {
  scored: "Scored against a real arrival",
  no_actual_arrival: "No arrival was ever recorded",
  echo_suspect: "Arrival identical to the timetable (echo_suspect)",
  journey_inconsistent: "Journey records internally inconsistent",
  declined: "Declined — outside what the model will answer",
};

// Under about a hundred matched events a day is noise, not a trend: the first two days
// carry 1 and 13. They stay in the table with their sample size and out of the chart.
const CHART_MIN_N = 100;

const NOMINAL = 80;

const fmtInt = (n) => (n == null ? "—" : n.toLocaleString("en-IE"));
const fmtSec = (n) => (n == null ? "—" : Math.round(n) + "s");
const fmtPct = (n, d = 1) => (n == null ? "—" : n.toFixed(d) + "%");
const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const kid of kids) n.appendChild(kid);
  return n;
};

function shortDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IE", { day: "numeric", month: "short" });
}

function figure(parent, value, label, note) {
  const f = el("div", { class: "figure" }, [
    el("span", { class: "n", text: value }),
    el("span", { class: "label", text: label }),
  ]);
  if (note) f.appendChild(el("span", { class: "note", text: note }));
  parent.appendChild(f);
}

/* ---- charts: hand-drawn SVG, because two line charts do not justify a library ---- */

const SVG_NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs) => {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

function lineChart(host, { labels, series, yMin, yMax, yTicks, yFmt, rule }) {
  const W = 600, H = 210, L = 44, R = 8, T = 12, B = 28;
  const x = (i) => L + (i * (W - L - R)) / Math.max(1, labels.length - 1);
  const y = (v) => T + ((yMax - v) * (H - T - B)) / (yMax - yMin);

  const svg = svgEl("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": host.dataset.alt || "chart",
  });

  for (const t of yTicks) {
    svg.appendChild(svgEl("line", { class: "grid", x1: L, x2: W - R, y1: y(t), y2: y(t) }));
    const lab = svgEl("text", { class: "axis", x: L - 6, y: y(t) + 3, "text-anchor": "end" });
    lab.textContent = yFmt(t);
    svg.appendChild(lab);
  }

  if (rule != null) {
    svg.appendChild(svgEl("line", { class: "nominal", x1: L, x2: W - R, y1: y(rule), y2: y(rule) }));
  }

  for (const s of series) {
    const d = s.values
      .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
      .filter(Boolean)
      .join(" L ");
    svg.appendChild(svgEl("path", { class: "line" + (s.alt ? " alt" : ""), d: "M " + d }));
    if (!s.alt) {
      s.values.forEach((v, i) => {
        if (v != null) svg.appendChild(svgEl("circle", { class: "dot", cx: x(i), cy: y(v), r: 2.5 }));
      });
    }
  }

  // Every label overlaps on a narrow screen, so only the ends and a middle one are drawn.
  const show = new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1]);
  labels.forEach((lab, i) => {
    if (!show.has(i)) return;
    const t = svgEl("text", {
      class: "axis", x: x(i), y: H - 8,
      "text-anchor": i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle",
    });
    t.textContent = lab;
    svg.appendChild(t);
  });

  host.replaceChildren(svg);
}

/* ---- render ---- */

function render(a) {
  const r = a.rolling;

  const generated = new Date(a.generated_at);
  document.getElementById("freshness").innerHTML =
    "Last scored " +
    generated.toLocaleString("en-IE", { dateStyle: "medium", timeStyle: "short" }) +
    " · window " + shortDate(a.window_dates[0]) + " – " +
    shortDate(a.window_dates[a.window_dates.length - 1]) +
    " · model <code>" + Object.keys(a.model_versions).slice(-1)[0] + "</code>";

  const head = document.getElementById("headline");
  figure(head, fmtPct(r.head_to_head.improvement_pct, 1), "better than the operator",
         "on " + fmtInt(r.head_to_head.matched_events) + " matched events");
  figure(head, fmtSec(r.head_to_head.model_mae_sec), "average error",
         "operator: " + fmtSec(r.head_to_head.operator_mae_sec));
  figure(head, fmtSec(r.accuracy.medae_sec), "median error",
         "half of all answers are closer than this");
  figure(head, fmtPct(r.interval_coverage_pct, 1), "of arrivals inside the range",
         "the range claims " + r.interval_coverage_nominal_pct + "%");

  document.getElementById("window-note").textContent =
    fmtInt(r.accuracy.n) + " predictions scored over " + a.window_days +
    " days. Average error is the mean absolute difference between the predicted arrival " +
    "and the real one; the median is lower because a small number of large misses pull " +
    "the mean up.";

  // Daily table and the two charts.
  const daily = a.daily;
  const tbody = document.querySelector("#daily tbody");
  tbody.replaceChildren(...daily.slice().reverse().map((d) => {
    const thin = d.matched_events < CHART_MIN_N;
    const tr = el("tr", {}, [
      el("td", { text: shortDate(d.date) + (thin ? " *" : "") }),
      el("td", { text: fmtInt(d.scored_n) }),
      el("td", { text: fmtInt(d.matched_events) }),
      el("td", { text: fmtSec(d.model_mae_sec) }),
      el("td", { text: fmtSec(d.operator_mae_sec) }),
      el("td", {
        class: d.improvement_pct >= 0 ? "good" : "bad",
        text: (d.improvement_pct >= 0 ? "+" : "") + fmtPct(d.improvement_pct, 1),
      }),
      el("td", { text: fmtPct(d.interval_coverage_pct, 1) }),
    ]);
    return tr;
  }));

  const solid = daily.filter((d) => d.matched_events >= CHART_MIN_N);
  const labels = solid.map((d) => shortDate(d.date));

  const maes = solid.flatMap((d) => [d.model_mae_sec, d.operator_mae_sec]);
  const top = Math.ceil(Math.max(...maes) / 20) * 20 + 20;
  lineChart(document.getElementById("chart-mae"), {
    labels,
    series: [
      { values: solid.map((d) => d.operator_mae_sec), alt: true },
      { values: solid.map((d) => d.model_mae_sec) },
    ],
    yMin: 0, yMax: top,
    yTicks: [0, top / 2, top].map((v) => Math.round(v)),
    yFmt: (v) => v + "s",
  });

  lineChart(document.getElementById("chart-cov"), {
    labels,
    series: [{ values: solid.map((d) => d.interval_coverage_pct) }],
    yMin: 50, yMax: 90, yTicks: [50, 60, 70, 80, 90],
    yFmt: (v) => v + "%",
    rule: NOMINAL,
  });

  // By line. Sorted by sample size so the thin groups sit at the bottom where they belong.
  const groups = Object.entries(r.by_station_group)
    .sort((p, q) => q[1].accuracy.n - p[1].accuracy.n);
  document.querySelector("#groups tbody").replaceChildren(...groups.map(([key, g]) => {
    const cov = g.interval_coverage_pct;
    const bar = el("div", { class: "bar" });
    bar.appendChild(el("i", { class: cov < 70 ? "low" : "", style: `width:${cov}%` }));
    bar.appendChild(el("u", { style: `left:${NOMINAL}%` }));
    const h2h = g.head_to_head;
    const tr = el("tr", { class: cov < 70 ? "flag" : "" }, [
      el("td", { text: GROUP_NAMES[key] || key }),
      el("td", { text: fmtInt(g.accuracy.n) }),
      el("td", { text: fmtSec(g.accuracy.mae_sec) }),
      el("td", {
        class: h2h ? (h2h.improvement_pct >= 0 ? "good" : "bad") : "",
        text: h2h ? (h2h.improvement_pct >= 0 ? "+" : "") + fmtPct(h2h.improvement_pct, 1) : "no board",
      }),
      el("td", { text: fmtPct(cov, 1) }),
    ]);
    tr.appendChild(el("td", { style: "width:40%" }, [bar]));
    return tr;
  }));

  // By lead band, in time order rather than the alphabetical order JSON keys arrive in.
  const bandOrder = ["0-5 min", "5-15 min", "15-30 min", "30-60 min", "60+ min"];
  document.querySelector("#bands tbody").replaceChildren(
    ...bandOrder.filter((b) => r.by_lead_band[b]).map((b) => {
      const g = r.by_lead_band[b];
      return el("tr", {}, [
        el("td", { text: b.replace("-", "–") + " ahead" }),
        el("td", { text: fmtInt(g.accuracy.n) }),
        el("td", { text: fmtSec(g.accuracy.mae_sec) }),
        el("td", { text: fmtSec(g.head_to_head.operator_mae_sec) }),
        el("td", {
          class: g.head_to_head.improvement_pct >= 0 ? "good" : "bad",
          text: (g.head_to_head.improvement_pct >= 0 ? "+" : "") +
                fmtPct(g.head_to_head.improvement_pct, 1),
        }),
        el("td", { text: fmtPct(g.interval_coverage_pct, 1) }),
      ]);
    })
  );

  // Coverage: asked-and-answered, plus what happened to everything that was scored.
  const cov = document.getElementById("coverage");
  figure(cov, fmtPct(r.coverage.in_service_pct, 1), "of questions answered",
         "given the train is actually in service");
  figure(cov, fmtInt(r.coverage.answered), "predictions made", "in seven days");
  figure(cov, fmtInt(r.coverage.declined), "declined",
         "no upstream report, or outside the envelope");

  const states = Object.entries(r.score_states).sort((p, q) => q[1] - p[1]);
  const total = states.reduce((s, [, n]) => s + n, 0);
  document.querySelector("#states tbody").replaceChildren(...states.map(([k, n]) =>
    el("tr", {}, [
      el("td", { text: STATE_NAMES[k] || k }),
      el("td", { text: fmtInt(n) }),
      el("td", { text: fmtPct((100 * n) / total, 1) }),
    ])
  ));

  const c = a.cumulative;
  const cum = document.getElementById("cumulative");
  figure(cum, fmtPct(c.head_to_head.improvement_pct, 1), "better than the operator",
         "over " + c.days + " days of live scoring");
  figure(cum, fmtInt(c.accuracy.n), "predictions scored",
         fmtInt(c.head_to_head.matched_events) + " of them matched");
  figure(cum, fmtSec(c.accuracy.mae_sec), "average error",
         "operator: " + fmtSec(c.head_to_head.operator_mae_sec));
  figure(cum, fmtPct(c.interval_coverage.interval_coverage_pct, 1), "inside the range",
         "against " + NOMINAL + "% claimed");

  document.getElementById("models").textContent =
    "Predictions in this total come from " + Object.keys(a.model_versions).length +
    " model versions: " +
    Object.entries(a.model_versions)
      .map(([v, n]) => v + " (" + fmtInt(n) + ")")
      .join(", ") +
    ". A version change is a deploy, never a retroactive recomputation — nothing here is " +
    "regenerated after the fact.";

  document.getElementById("content").hidden = false;
}

fetch("accuracy.json", { cache: "no-store" })
  .then((res) => {
    if (!res.ok) throw new Error(res.status);
    return res.json();
  })
  .then(render)
  .catch((err) => {
    document.getElementById("freshness").hidden = true;
    const box = document.getElementById("fail");
    box.hidden = false;
    box.appendChild(el("span", { text: " (" + err.message + ")" }));
  });
