// @ts-check
/**
 * The live board. Calls /api, which CloudFront forwards to the prediction Lambda;
 * scripts/dev_site.py forwards it the same way locally, so there is no environment branch.
 */

import { el, flap, hhmm, need, notice } from "./site.js";

/** Thrown by `get` so the caller can tell a refusal from an outage. */
class ApiError extends Error {
  /** @param {string} message @param {number} status @param {number} retryAfter */
  constructor(message, status, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

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
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error; the status carries enough */
    }
    // Retry-After is the server saying when it will listen again. Believe it, and fall
    // back to something polite rather than retrying immediately if it is missing.
    const header = Number(res.headers.get("Retry-After"));
    throw new ApiError(detail, res.status, Number.isFinite(header) && header > 0 ? header : 20);
  }
  return res.json();
}

/* ── rendering ──────────────────────────────────────────────────────────── */

/**
 * One labelled time. Every time on the board says whose it is, because the first version
 * put the operator's number and RailCast's number in the same box and nothing distinguished
 * them, which made the whole board unreadable to anyone seeing it for the first time.
 * @param {string} label
 * @param {Node|string} value
 * @param {string} [tone]
 */
function timeCell(label, value, tone = "text-ink-2") {
  return el("div", { class: "text-right" }, [
    el("span", { class: "block text-[0.66rem] uppercase tracking-wider text-ink-3", text: label }),
    el("div", { class: `mt-0.5 ${tone}` }, [typeof value === "string" ? el("span", { text: value }) : value]),
  ]);
}

/**
 * How late the train was when it last reported, said in words rather than a signed number.
 * A raw "-4.5 min down" is a sentence nobody reads correctly at a glance.
 * @param {number|null|undefined} min
 */
function lateness(min) {
  if (min == null) return "";
  if (min <= -0.5) return `${Math.abs(min).toFixed(1)} min early`;
  if (min < 0.5) return "on time";
  return `${min.toFixed(1)} min late`;
}

/** @param {Entry} t */
function row(t) {
  const times = el("div", { class: "flex items-start justify-end gap-4 sm:gap-6" });

  times.append(timeCell("Timetable", hhmm(t.scheduled) || "–", "text-ink-3 text-[0.95rem]"));
  times.append(
    timeCell("Irish Rail", hhmm(t.operator_eta) || "–", "text-ink-2 text-[0.95rem]")
  );

  if (t.prediction) {
    const face = el("span", { class: "flap" });
    flap(face, hhmm(t.prediction.predicted));
    times.append(timeCell("RailCast", face, ""));
  } else {
    times.append(timeCell("RailCast", "–", "text-ink-3 text-[0.95rem]"));
  }

  const where = t.prediction?.vantage_name || t.prediction?.vantage_location;
  const seen =
    t.prediction && t.prediction.current_delay_min != null
      ? `${lateness(t.prediction.current_delay_min)} at ${where}`
      : "";

  const detail = el("p", { class: "mt-2 text-xs text-ink-3" });
  if (t.prediction) {
    const [lo, hi] = t.prediction.interval_80pct;
    detail.append(
      el("span", {
        class: "text-clear",
        text: `RailCast expects ${hhmm(lo)} to ${hhmm(hi)}, four times in five`,
      }),
      el("span", { text: seen ? ` · last reported ${seen}` : "" })
    );
  } else {
    detail.append(el("span", { text: t.explanation || "No prediction for this service." }));
  }

  return el("div", { class: "border-b border-rule py-3.5 last:border-b-0" }, [
    el("div", { class: "flex flex-wrap items-start justify-between gap-x-4 gap-y-2" }, [
      el("div", { class: "min-w-0" }, [
        el("span", { class: "font-bold tracking-wide", text: t.train }),
        el("span", {
          class: "ml-2 text-[0.94rem] text-ink-2",
          text: [t.origin, t.destination].filter(Boolean).join(" to "),
        }),
      ]),
      times,
    ]),
    detail,
  ]);
}

/** @param {string} code */
async function showBoard(code) {
  const status = need("status");
  const board = need("board");
  status.textContent = "Asking the model about each train. This takes a few seconds.";
  board.replaceChildren();
  // The long "how to read a row" panel gives way to the compact legend once there is a real
  // board to read; leaving both would push the answer below the fold.
  need("intro").hidden = true;

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
    showFailure(err, code);
  }
}

/** Whatever countdown is currently running, so a second failure does not stack timers. */
let stopCountdown = () => {};

/**
 * Say what happened, in the page, with a number attached. A board that simply stays empty
 * is the same failure the whole project is about: something went wrong and nothing said so.
 * @param {unknown} err
 * @param {string} code
 */
function showFailure(err, code) {
  const board = need("board");
  stopCountdown();

  if (err instanceof ApiError && err.status === 429) {
    stopCountdown = notice(board, {
      level: "caution",
      title: "Too many requests, so this one was refused",
      body:
        "Each board asks Irish Rail's free feed about every train on it, so this service " +
        "limits how often it will do that. The limit exists to be a good guest on someone " +
        "else's API rather than to keep you out.",
      countdown: err.retryAfter,
      onExpiry: () => void showBoard(code),
    });
    return;
  }

  const offline = !navigator.onLine;
  stopCountdown = notice(board, {
    level: "danger",
    title: offline ? "You appear to be offline" : "The prediction service did not answer",
    body: offline
      ? "The board needs a connection, because every prediction on it is made live rather than cached."
      : `${err instanceof Error ? err.message : String(err)}. Irish Rail's feed may be down, ` +
        "which happens, or the service may be starting up after a period of no traffic.",
    action: { label: "Try again", action: () => void showBoard(code) },
  });
}

/* ── start ──────────────────────────────────────────────────────────────── */

const select = /** @type {HTMLSelectElement} */ (need("station"));

// The station list is a cached static file. Loading a BOARD is not: it asks Irish Rail
// about every train on it, so it happens when a visitor asks for one and never merely
// because a page was opened. Someone who lands here and leaves costs the feed nothing.
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
    need("status").textContent = "Pick a station and press Show board.";
    need("intro").hidden = false;
  })
  .catch((err) => {
    need("status").textContent = "";
    showFailure(err, remembered());
  });

need("picker").addEventListener("submit", (ev) => {
  ev.preventDefault();
  remember(select.value);
  void showBoard(select.value);
});
