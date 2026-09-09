// @ts-check
/**
 * The live board. Calls /api, which CloudFront forwards to the prediction Lambda;
 * scripts/dev_site.py forwards it the same way locally, so there is no environment branch.
 */

import { el, flap, hhmm, need, notice, reducedMotion } from "./site.js";

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
 * @property {string} kind
 * @property {string} direction
 * @property {string} calls_as
 * @property {string} operator_status
 * @property {Stop[]} [journey]
 * @property {Prediction|null} prediction
 * @property {string|null} reason
 * @property {string|null} explanation
 */

/**
 * @typedef {object} Stop
 * @property {string} code
 * @property {string} name
 * @property {string} scheduled
 * @property {string|null} arrived
 * @property {number|null} delay_min
 * @property {boolean} here
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

/**
 * Why a service has no prediction, in the visitor's terms. The API's own `explanation` is
 * written for the log; this is written for someone standing on a platform.
 * @param {Entry} t
 */
function noPredictionReason(t) {
  if (t.reason === "not_yet_departed" || t.operator_status === "No Information") {
    return `Starts at ${t.origin || "its origin"} at ${hhmm(t.scheduled) || "a later time"}. ` +
      "Nothing to predict from until it is moving.";
  }
  if (t.reason === "no_upstream_report") return "Moving, but it has not reported at a stop yet.";
  if (t.reason === "already_arrived") return "Already arrived.";
  if (t.reason === "station_not_on_route") return "Timetable and route disagree for this service.";
  return t.explanation || "No prediction for this service.";
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

  // Destination first, because that is what a passenger reads a board by, then what this
  // station is to the service, then where it came from.
  const heading = el("div", { class: "flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1" }, [
    el("span", { class: "font-bold tracking-wide", text: t.train }),
  ]);
  if (t.kind) {
    heading.append(
      el("span", {
        class:
          "rounded-sm border px-1.5 py-px text-[0.68rem] uppercase tracking-wider " +
          (t.kind === "DART" ? "border-clear/40 text-clear" : "border-rule text-ink-3"),
        text: t.kind === "DART" ? "DART" : "Train",
      })
    );
  }
  heading.append(
    el("span", { class: "text-[0.94rem] text-ink", text: `to ${t.destination || "–"}` })
  );
  heading.append(
    el("span", {
      class: "text-[0.88rem] text-ink-3",
      text: [t.calls_as, t.origin ? `from ${t.origin}` : ""].filter(Boolean).join(" · "),
    })
  );

  const detail = el("p", { class: "mt-2 text-xs text-ink-3" });
  if (t.prediction) {
    const [lo, hi] = t.prediction.interval_80pct;
    const where = t.prediction.vantage_name || t.prediction.vantage_location;
    const seen =
      t.prediction.current_delay_min != null ? `${lateness(t.prediction.current_delay_min)} at ${where}` : "";
    detail.append(
      el("span", { class: "text-clear", text: `Expected between ${hhmm(lo)} and ${hhmm(hi)}` }),
      el("span", { text: seen ? ` · last reported ${seen}` : "" })
    );
  } else {
    detail.append(el("span", { text: noPredictionReason(t) }));
  }

  const parts = [
    el("div", { class: "flex flex-wrap items-start justify-between gap-x-4 gap-y-2" }, [
      heading,
      times,
    ]),
    detail,
  ];
  if (t.journey && t.journey.length) parts.push(...journeyToggle(t));

  return el("div", { class: "border-b border-rule py-3.5 last:border-b-0" }, parts);
}

/**
 * A stop's time, and how far off the timetable it was if it has already happened.
 * @param {Stop} s
 */
function stopTime(s) {
  const cell = el("span", { class: "when" });
  if (s.arrived) {
    cell.append(el("span", { text: s.arrived }));
    if (s.delay_min != null && Math.abs(s.delay_min) >= 0.5) {
      cell.append(
        el("span", {
          class: "ml-2 " + (s.delay_min > 0 ? "late" : "early"),
          text: (s.delay_min > 0 ? "+" : "") + s.delay_min.toFixed(1),
        })
      );
    }
  } else {
    cell.append(el("span", { class: "text-ink-3", text: s.scheduled || "–" }));
  }
  return cell;
}

/**
 * The disclosure and the route it opens. Built as a button plus a labelled region rather
 * than a clickable div so it works from the keyboard and announces its state.
 * @param {Entry} t
 */
function journeyToggle(t) {
  const stops = t.journey ?? [];
  const id = `journey-${t.train}`;
  const done = stops.filter((s) => s.arrived).length;

  const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  chevron.setAttribute("viewBox", "0 0 12 12");
  chevron.setAttribute("aria-hidden", "true");
  const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
  arrow.setAttribute("d", "M4 2l4 4-4 4");
  arrow.setAttribute("fill", "none");
  arrow.setAttribute("stroke", "currentColor");
  arrow.setAttribute("stroke-width", "1.8");
  arrow.setAttribute("stroke-linecap", "round");
  arrow.setAttribute("stroke-linejoin", "round");
  chevron.append(arrow);

  const label = el("span", { text: `Full route, ${stops.length} stops` });
  const button = el("button", {
    class: "jrny-toggle",
    type: "button",
    "aria-expanded": "false",
    "aria-controls": id,
  });
  button.append(chevron, label);

  const list = el("ol", { class: "jrny" });
  stops.forEach((s, i) => {
    const item = el("li", {
      class: [s.arrived ? "done" : "", s.here ? "here" : ""].filter(Boolean).join(" "),
    });
    item.style.setProperty("--i", String(i));
    item.append(
      el("span", { text: s.name + (s.here ? " · you are here" : "") }),
      stopTime(s)
    );
    list.append(item);
  });
  // The green overlay stops at the last station that has actually reported.
  list.style.setProperty(
    "--travelled",
    stops.length > 1 ? `${(Math.max(0, done - 1) / (stops.length - 1)) * 100}%` : "0%"
  );

  const inner = el("div", {}, [
    list,
    el("p", {
      class: "text-xs text-ink-3",
      text:
        "Times already recorded are shown with how far off the timetable they were, in " +
        "minutes. Later stops show the timetable.",
    }),
  ]);
  const wrap = el("div", {
    class: "jrny-wrap",
    id,
    role: "region",
    "aria-label": `Route of ${t.train}`,
    "data-open": "false",
  });
  wrap.append(inner);

  button.addEventListener("click", () => {
    const open = wrap.dataset.open === "true";
    wrap.dataset.open = String(!open);
    button.setAttribute("aria-expanded", String(!open));
    label.textContent = open ? `Full route, ${stops.length} stops` : "Hide route";
    slide(wrap, !open);
  });

  return [button, wrap];
}

/**
 * Open or close a panel, setting the final state first and animating over the top.
 *
 * The obvious version transitions `height` in CSS and waits for `transitionend` to release
 * it back to `auto`. That leaves the panel stuck at zero whenever the animation does not
 * run: a throttled background tab, a browser that drops the frame, anything. Here the
 * element is already in its finished state before the animation starts, so the animation
 * is decoration and its absence costs nothing.
 *
 * @param {HTMLElement} panel
 * @param {boolean} open
 */
function slide(panel, open) {
  // A half-finished animation from a previous toggle would otherwise keep holding the
  // height it was mid-way through, which reads as a panel that refuses to open.
  for (const running of panel.getAnimations()) running.cancel();

  const from = panel.getBoundingClientRect().height;
  panel.style.height = open ? "auto" : "0px";
  if (reducedMotion || typeof panel.animate !== "function") return;

  const to = panel.getBoundingClientRect().height;
  if (Math.abs(to - from) < 1) return;

  panel.animate(
    [{ height: `${from}px` }, { height: `${to}px` }],
    { duration: 380, easing: "cubic-bezier(.16, 1, .3, 1)" }
  );
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
        text:
          `: ${b.trains.length} ${b.trains.length === 1 ? "train stops" : "trains stop"} here in ` +
          `the next ${b.board_minutes} minutes. ${predicted} of them ` +
          `${predicted === 1 ? "has" : "have"} a RailCast range` +
          (waiting ? `; ${waiting} have not started their journey yet` : "") +
          `. Read at ${hhmm(b.generated_at.slice(11))}.`,
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
