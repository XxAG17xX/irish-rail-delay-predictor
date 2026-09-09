// @ts-check
/**
 * The live board. Calls /api, which CloudFront forwards to the prediction Lambda;
 * scripts/dev_site.py forwards it the same way locally, so there is no environment branch.
 */

import { el, flap, hhmm, need } from "./site.js";

const API = "/api";
const LIMIT = 6;
const REMEMBER = "railcast.station";
const DEFAULT_STATION = "KDARE"; // a through station: a terminus board is nearly all departures

/**
 * @typedef {object} Prediction
 * @property {string} predicted
 * @property {[string, string]} interval_80pct
 * @property {number|null} current_delay_min
 * @property {string|null} vantage_name
 * @property {string|null} vantage_location
 * @property {string} confidence
 * @property {string|null} station_group
 */

/**
 * @typedef {object} Entry
 * @property {string} train
 * @property {string} origin
 * @property {string} destination
 * @property {number|null} due_in_min
 * @property {string} scheduled
 * @property {string} operator_eta
 * @property {string} scope
 * @property {Prediction|null} prediction
 * @property {string|null} reason
 * @property {string|null} explanation
 */

/**
 * @typedef {object} Board
 * @property {string} station
 * @property {string} station_name
 * @property {string} generated_at
 * @property {string} model_version
 * @property {number} board_minutes
 * @property {Entry[]} trains
 */

/** localStorage throws outright in some privacy modes, so both directions are guarded. */
function remembered() {
  try {
    return localStorage.getItem(REMEMBER) || DEFAULT_STATION;
  } catch {
    return DEFAULT_STATION;
  }
}

/** @param {string} code */
function remember(code) {
  try {
    localStorage.setItem(REMEMBER, code);
  } catch {
    /* nothing to do: a remembered station is a convenience, not state the page needs */
  }
}

/**
 * @param {string} path
 * @returns {Promise<any>}
 */
async function get(path) {
  const res = await fetch(API + path, { cache: "no-store" });
  if (!res.ok) {
    let detail = String(res.status);
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error; the status carries enough */
    }
    throw new Error(detail);
  }
  return res.json();
}

/* ── rendering ──────────────────────────────────────────────────────────── */

/** @param {Entry} t */
function row(t) {
  const when = el("div", { class: "text-right" });

  if (t.scheduled) {
    when.append(
      el("span", {
        class: "mr-2 text-[0.82rem] text-ink-3" + (t.prediction ? " line-through" : ""),
        text: hhmm(t.scheduled),
      })
    );
  }

  const face = el("span", { class: "flap" });
  when.append(face);
  flap(face, hhmm(t.prediction ? t.prediction.predicted : t.operator_eta || t.scheduled));

  if (t.prediction) {
    const [lo, hi] = t.prediction.interval_80pct;
    when.append(
      el("span", {
        class: "mt-1 block text-xs text-clear",
        text: `range ${hhmm(lo)} to ${hhmm(hi)}`,
      })
    );
  } else {
    when.append(
      el("span", {
        class: "mt-1 block text-xs text-ink-3",
        text: t.reason === "not_yet_departed" ? "no prediction, nothing reported yet" : "no prediction",
      })
    );
  }

  const seen = t.prediction && t.prediction.current_delay_min != null
    ? `${t.prediction.current_delay_min.toFixed(1)} min down at ${t.prediction.vantage_name || t.prediction.vantage_location}`
    : t.explanation || "";

  return el(
    "div",
    { class: "grid grid-cols-[3.4rem_1fr_auto] items-center gap-3 border-b border-rule px-1 py-3 last:border-b-0" },
    [
      el("span", { class: "font-bold tracking-wide", text: t.train }),
      el("span", { class: "text-[0.94rem] text-ink-2" }, [
        el("span", { text: t.destination || "–" }),
        el("small", {
          class: "mt-0.5 block text-xs text-ink-3",
          text: [t.origin ? `from ${t.origin}` : "", seen].filter(Boolean).join(" · "),
        }),
      ]),
      when,
    ]
  );
}

/** @param {string} code */
async function showBoard(code) {
  const status = need("status");
  const board = need("board");
  status.textContent = "Asking the model about each train. This takes a few seconds.";
  board.replaceChildren();

  try {
    /** @type {Board} */
    const b = await get(`/board?station=${encodeURIComponent(code)}&limit=${LIMIT}`);
    const predicted = b.trains.filter((t) => t.prediction).length;
    const waiting = b.trains.filter((t) => t.reason === "not_yet_departed").length;

    status.replaceChildren(
      el("b", { text: b.station_name }),
      el("span", {
        text: ` · ${b.trains.length} due in the next ${b.board_minutes} minutes` +
          ` · ${predicted} with a range` +
          (waiting ? `, ${waiting} not departed yet` : "") +
          ` · as of ${hhmm(b.generated_at.slice(11))}`,
      })
    );

    if (b.trains.length === 0) {
      board.append(
        el("p", { class: "text-ink-3", text: "Nothing due here in the next ninety minutes." })
      );
    } else {
      const panel = el("div", { class: "panel px-4" }, b.trains.map(row));
      board.append(panel);
    }
    need("explainer").hidden = false;
  } catch (err) {
    status.textContent = "";
    board.replaceChildren(
      el("p", {
        class: "text-caution",
        text: `Could not load the board: ${err instanceof Error ? err.message : String(err)}`,
      })
    );
  }
}

/* ── start ──────────────────────────────────────────────────────────────── */

const select = /** @type {HTMLSelectElement} */ (need("station"));

get("/stations")
  .then((data) => {
    /** @type {{code: string, name: string, polled: boolean}[]} */
    const stations = data.stations;
    const watched = el("optgroup", { label: "Watched stations (scored against the operator)" });
    const rest = el("optgroup", { label: "Everywhere else" });
    for (const s of stations) {
      (s.polled ? watched : rest).append(el("option", { value: s.code, text: s.name }));
    }
    select.replaceChildren(watched, rest);
    select.value = remembered();
    if (!select.value && stations[0]) select.value = stations[0].code;
    return showBoard(select.value);
  })
  .catch((err) => {
    need("status").replaceChildren(
      el("span", {
        class: "text-caution",
        text: `The prediction service is not reachable (${err instanceof Error ? err.message : String(err)}).`,
      })
    );
  });

need("picker").addEventListener("submit", (ev) => {
  ev.preventDefault();
  remember(select.value);
  void showBoard(select.value);
});
