// @ts-check
/**
 * Renders accuracy.json, which the nightly scorer publishes. This page computes no number of
 * its own, so everything shown here is reproducible from the scored rows on S3.
 */

import { el, int, need, pct, secs } from "./site.js";

const NOMINAL = 80;
/** Under about a hundred matched events a day is noise. Those days stay in the table. */
const CHART_MIN_N = 100;

/** @type {Record<string, string>} */
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

/** @type {Record<string, string>} */
const STATE_NAMES = {
  scored: "Scored against a real arrival",
  no_actual_arrival: "No arrival recorded by the operator",
  echo_suspect: "Arrival identical to the timetable",
  journey_inconsistent: "Journey records internally inconsistent",
  declined: "Declined, outside what the model will answer",
};

const BAND_ORDER = ["0-5 min", "5-15 min", "15-30 min", "30-60 min", "60+ min"];

/** @param {string} iso */
function shortDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-IE", {
    day: "numeric",
    month: "short",
  });
}

/* ── charts: hand drawn, because two line charts do not justify a library ── */

const SVG = "http://www.w3.org/2000/svg";

/**
 * @param {string} tag
 * @param {Record<string, string|number>} attrs
 */
function s(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

/**
 * @param {HTMLElement} host
 * @param {object} opts
 * @param {string[]} opts.labels
 * @param {{values: (number|null)[], alt?: boolean}[]} opts.series
 * @param {number} opts.yMin
 * @param {number} opts.yMax
 * @param {number[]} opts.yTicks
 * @param {(v: number) => string} opts.yFmt
 * @param {number} [opts.rule] a horizontal reference line, used for the claimed 80%
 * @param {string} opts.label accessible description
 */
function lineChart(host, opts) {
  const W = 620, H = 200, L = 42, R = 8, T = 12, B = 26;
  /** @param {number} i */
  const x = (i) => L + (i * (W - L - R)) / Math.max(1, opts.labels.length - 1);
  /** @param {number} v */
  const y = (v) => T + ((opts.yMax - v) * (H - T - B)) / (opts.yMax - opts.yMin);

  const svg = s("svg", {
    class: "diagram",
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": opts.label,
  });

  for (const t of opts.yTicks) {
    svg.append(s("line", { class: "stroke-rule", "stroke-width": 1, x1: L, x2: W - R, y1: y(t), y2: y(t) }));
    const lab = s("text", { class: "lbl", x: L - 6, y: y(t) + 3, "text-anchor": "end" });
    lab.textContent = opts.yFmt(t);
    svg.append(lab);
  }

  if (opts.rule != null) {
    svg.append(
      s("line", {
        class: "stroke-caution",
        "stroke-width": 1,
        "stroke-dasharray": "3 3",
        x1: L, x2: W - R, y1: y(opts.rule), y2: y(opts.rule),
      })
    );
  }

  for (const series of opts.series) {
    const points = series.values
      .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
      .filter((p) => p !== null);
    if (points.length === 0) continue;
    svg.append(
      s("path", {
        class: series.alt ? "stroke-ink-3" : "stroke-clear",
        fill: "none",
        "stroke-width": 2,
        "stroke-dasharray": series.alt ? "4 3" : "none",
        d: "M " + points.join(" L "),
      })
    );
    if (!series.alt) {
      series.values.forEach((v, i) => {
        if (v != null) svg.append(s("circle", { class: "fill-clear", cx: x(i), cy: y(v), r: 2.5 }));
      });
    }
  }

  // Every label overlaps on a narrow screen, so only the ends and the middle are drawn.
  const last = opts.labels.length - 1;
  for (const i of new Set([0, Math.floor(last / 2), last])) {
    const label = opts.labels[i];
    if (label == null) continue;
    const t = s("text", {
      class: "lbl",
      x: x(i),
      y: H - 8,
      "text-anchor": i === 0 ? "start" : i === last ? "end" : "middle",
    });
    t.textContent = label;
    svg.append(t);
  }

  host.replaceChildren(svg);
}

/* ── render ─────────────────────────────────────────────────────────────── */

/** @param {any} a the parsed accuracy.json */
function render(a) {
  const r = a.rolling;
  const versions = Object.keys(a.model_versions);
  const serving = versions[versions.length - 1] ?? "unknown";

  const generated = new Date(a.generated_at);
  need("freshness").replaceChildren(
    el("span", {
      text:
        "Last scored " +
        generated.toLocaleString("en-IE", { dateStyle: "medium", timeStyle: "short" }) +
        " · window " +
        shortDate(a.window_dates[0]) +
        " to " +
        shortDate(a.window_dates[a.window_dates.length - 1]) +
        " · model ",
    }),
    el("code", { class: "text-ink-2", text: serving })
  );

  need("headline").replaceChildren(
    el("span", { text: "Over the seven days to " }),
    el("b", { text: shortDate(a.window_dates[a.window_dates.length - 1]) }),
    el("span", { text: ", RailCast was " }),
    el("b", { class: "text-clear", text: pct(r.head_to_head.improvement_pct) }),
    el("span", { text: " closer than the operator's own estimate on " }),
    el("b", { text: int(r.head_to_head.matched_events) }),
    el("span", {
      text:
        " matched predictions, " +
        `${secs(r.head_to_head.model_mae_sec)} of average error against ${secs(r.head_to_head.operator_mae_sec)}. ` +
        "The arrival landed inside the range ",
    }),
    el("b", { class: r.interval_coverage_pct < NOMINAL ? "text-caution" : "text-clear",
              text: pct(r.interval_coverage_pct) }),
    el("span", {
      text: ` of the time, against the ${r.interval_coverage_nominal_pct}% it claims, across ` +
        `${int(r.accuracy.n)} scored predictions.`,
    })
  );

  /* by line */
  const groups = Object.entries(r.by_station_group).sort(
    (p, q) => /** @type {any} */ (q[1]).accuracy.n - /** @type {any} */ (p[1]).accuracy.n
  );
  need("groups").replaceChildren(
    ...groups.map(([key, raw]) => {
      const g = /** @type {any} */ (raw);
      const cov = g.interval_coverage_pct;
      const low = cov < 70;
      // Set through the CSSOM rather than as a style attribute: a style attribute written
      // from script is subject to the content security policy, and avoiding it here is
      // what lets style-src stay strict with no 'unsafe-inline'.
      const fill = el("i", { class: low ? "warn" : "" });
      fill.style.width = `${cov}%`;
      const mark = el("u");
      mark.style.left = `${NOMINAL}%`;
      const track = el("div", { class: "sec" }, [fill, mark]);
      // The drawn track is the widest column and the least essential: the same figure sits
      // beside it as a percentage. Hiding it below md keeps the line name, which is the
      // column a reader needs most, from being the one that scrolls off the screen.
      return el("tr", {}, [
        el("td", { text: GROUP_NAMES[key] ?? key }),
        el("td", { text: int(g.accuracy.n) }),
        el("td", { text: secs(g.accuracy.mae_sec) }),
        el("td", {
          class: g.head_to_head ? (g.head_to_head.improvement_pct >= 0 ? "over" : "under") : "",
          text: g.head_to_head
            ? (g.head_to_head.improvement_pct >= 0 ? "+" : "") + pct(g.head_to_head.improvement_pct)
            : "no board",
        }),
        el("td", { class: low ? "under" : "", text: pct(cov) }),
        el("td", { class: "hidden md:table-cell w-[34%]" }, [track]),
      ]);
    })
  );

  /* day by day */
  /** @type {any[]} */
  const daily = a.daily;
  need("daily").replaceChildren(
    ...[...daily].reverse().map((d) =>
      el("tr", {}, [
        el("td", { text: shortDate(d.date) + (d.matched_events < CHART_MIN_N ? " †" : "") }),
        el("td", { text: int(d.scored_n) }),
        el("td", { text: int(d.matched_events) }),
        el("td", { text: secs(d.model_mae_sec) }),
        el("td", { text: secs(d.operator_mae_sec) }),
        el("td", {
          class: d.improvement_pct >= 0 ? "over" : "under",
          text: (d.improvement_pct >= 0 ? "+" : "") + pct(d.improvement_pct),
        }),
        el("td", { text: pct(d.interval_coverage_pct) }),
      ])
    )
  );

  const solid = daily.filter((d) => d.matched_events >= CHART_MIN_N);
  const labels = solid.map((d) => shortDate(d.date));
  const worst = Math.max(...solid.flatMap((d) => [d.model_mae_sec, d.operator_mae_sec]));
  const top = Math.ceil(worst / 20) * 20 + 20;

  lineChart(/** @type {HTMLElement} */ (need("chart-mae")), {
    labels,
    series: [
      { values: solid.map((d) => d.operator_mae_sec), alt: true },
      { values: solid.map((d) => d.model_mae_sec) },
    ],
    yMin: 0,
    yMax: top,
    yTicks: [0, Math.round(top / 2), top],
    yFmt: (v) => v + "s",
    label: "Daily average error, RailCast against the operator's own estimate.",
  });

  lineChart(/** @type {HTMLElement} */ (need("chart-cov")), {
    labels,
    series: [{ values: solid.map((d) => d.interval_coverage_pct) }],
    yMin: 50,
    yMax: 90,
    yTicks: [50, 60, 70, 80, 90],
    yFmt: (v) => v + "%",
    rule: NOMINAL,
    label: "Daily interval coverage against the 80% the range claims.",
  });

  /* by horizon */
  need("bands").replaceChildren(
    ...BAND_ORDER.filter((b) => r.by_lead_band[b]).map((b) => {
      const g = r.by_lead_band[b];
      return el("tr", {}, [
        el("td", { text: b.replace("-", " to ").replace("+", " or more") + " ahead" }),
        el("td", { text: int(g.accuracy.n) }),
        el("td", { text: secs(g.accuracy.mae_sec) }),
        el("td", { text: secs(g.head_to_head.operator_mae_sec) }),
        el("td", {
          class: g.head_to_head.improvement_pct >= 0 ? "over" : "under",
          text: (g.head_to_head.improvement_pct >= 0 ? "+" : "") + pct(g.head_to_head.improvement_pct),
        }),
        el("td", { class: g.interval_coverage_pct < 70 ? "under" : "", text: pct(g.interval_coverage_pct) }),
      ]);
    })
  );

  /* coverage */
  need("coverage").replaceChildren(
    el("span", { text: "Given a train actually in service, the model answered " }),
    el("b", { class: "text-clear", text: pct(r.coverage.in_service_pct) }),
    el("span", {
      text:
        ` of the time over the week: ${int(r.coverage.answered)} predictions made and ` +
        `${int(r.coverage.declined)} declined for want of an upstream report or for falling ` +
        "outside what the model will answer.",
    })
  );

  /** @type {[string, number][]} */
  const states = Object.entries(r.score_states).map(([k, v]) => [k, Number(v)]);
  states.sort((p, q) => q[1] - p[1]);
  const total = states.reduce((sum, [, n]) => sum + n, 0);
  need("states").replaceChildren(
    ...states.map(([k, n]) =>
      el("tr", {}, [
        el("td", { text: STATE_NAMES[k] ?? k }),
        el("td", { text: int(n) }),
        el("td", { text: pct((100 * n) / total) }),
      ])
    )
  );

  /* since launch */
  const c = a.cumulative;
  need("cumulative").replaceChildren(
    el("span", { text: "Across " }),
    el("b", { text: String(c.days) }),
    el("span", { text: " days of live scoring: " }),
    el("b", { text: int(c.accuracy.n) }),
    el("span", { text: " predictions scored, " }),
    el("b", { text: int(c.head_to_head.matched_events) }),
    el("span", { text: " of them matched against the operator, and " }),
    el("b", { class: "text-clear", text: pct(c.head_to_head.improvement_pct) }),
    el("span", {
      text:
        ` closer overall. Coverage across the whole period is ${pct(c.interval_coverage.interval_coverage_pct)} ` +
        `against the ${NOMINAL}% claimed.`,
    })
  );

  need("models").textContent =
    `Predictions in that total come from ${versions.length} model ` +
    `${versions.length === 1 ? "version" : "versions"}: ` +
    Object.entries(a.model_versions)
      .map(([v, n]) => `${v} (${int(Number(n))})`)
      .join(", ") +
    ". A version change is a deploy, never a retroactive recomputation.";

  need("content").hidden = false;
}

fetch("accuracy.json", { cache: "no-store" })
  .then((res) => {
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  })
  .then(render)
  .catch((err) => {
    need("freshness").hidden = true;
    const box = need("fail");
    box.hidden = false;
    box.append(el("span", { text: ` (${err instanceof Error ? err.message : String(err)})` }));
  });
