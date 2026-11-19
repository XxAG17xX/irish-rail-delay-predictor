// @ts-check
/**
 * The live board. Calls /api, which CloudFront forwards to the prediction Lambda;
 * scripts/dev_site.py forwards it the same way locally, so there is no environment branch.
 */

import { el, flap, hhmm, need, notice, reducedMotion } from "./site.js";
import { drawFan } from "./fan.js";

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
 * @property {string} origin_time
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
 * @property {boolean} [passes] a timing point the train passes, kept because it last reported there
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

/**
 * The station code in the page's own URL, so a board can be linked to and shared.
 * Upper-cased because station codes are, and validated against the real list before it
 * reaches the feed.
 * @returns {string} the code asked for, or "" when the URL names none
 */
function askedFor() {
  try {
    return (new URL(location.href).searchParams.get("station") || "").trim().toUpperCase();
  } catch {
    return "";
  }
}

/**
 * The station typed into the header's search box (`?find=`), matched by name: an exact
 * match first, then the only name that starts with what was typed. Anything ambiguous
 * matches nothing, so the board never guesses which station was meant.
 * @param {{code: string, name: string}[]} stations
 * @returns {string} a station code, or ""
 */
function foundIn(stations) {
  let q = "";
  try {
    q = (new URL(location.href).searchParams.get("find") || "").trim().toLowerCase();
  } catch {
    return "";
  }
  if (!q) return "";
  const exact = stations.filter((s) => s.name.toLowerCase() === q);
  const start = stations.filter((s) => s.name.toLowerCase().startsWith(q));
  const hit = exact.length === 1 ? exact[0] : start.length === 1 ? start[0] : undefined;
  return hit ? hit.code : "";
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
function timeCell(label, value, tone = "") {
  return el("div", { class: "br-cell" }, [
    el("span", { class: "br-label", text: label }),
    typeof value === "string" ? el("span", { class: `br-val ${tone}`, text: value }) : value,
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
  const where = t.origin || "its first stop";
  switch (t.reason) {
    case "not_yet_departed":
      // Origin time, never `scheduled`: that one is this service's time at the station
      // being looked at, which for an arrival is the end of the journey, not the start.
      return t.origin_time
        ? `Leaves ${where} at ${hhmm(t.origin_time)}, so it has not started yet.`
        : `Has not left ${where} yet.`;
    case "no_upstream_report":
      return "On its way, but it has not reported at a stop yet.";
    case "not_asked":
      return "Further down the board than this page asks the model about.";
    case "already_arrived":
      return "Already arrived here.";
    case "no_scheduled_arrival":
      return "This is where the train starts, so there is no arrival to predict.";
    case "not_in_service":
      return "Not running today, according to the feed.";
    case "journey_inconsistent":
      return "This service's reported times are out of order, so they are not trusted.";
    case "lead_out_of_range":
      return "Too far ahead for the model to answer.";
    case "vantage_delay_out_of_range":
      return "Its last reported time looks wrong, so it is not used.";
    case "station_not_on_route":
      return "The timetable and the route disagree for this service.";
    case "upstream_unavailable":
      return "Irish Rail did not answer for this service.";
    default:
      return "No prediction for this service.";
  }
}

/**
 * @param {Entry} t
 * @param {string} stationCode the board's station, so the route can mark where you are
 * @param {string} now the board's read time, "HH:MM", where the fan draws its now line
 */
function row(t, stationCode, now) {
  const times = el("div", { class: "br-times" });
  times.append(timeCell("Timetable", hhmm(t.scheduled) || "–", "dim"));
  times.append(timeCell("Irish Rail", hhmm(t.operator_eta) || "–"));
  if (t.prediction) {
    const face = el("span", { class: "flap" });
    flap(face, hhmm(t.prediction.predicted));
    times.append(timeCell("RailCast", face));
  } else {
    times.append(timeCell("RailCast", "–", "dim"));
  }

  // Destination first, because that is what a passenger reads a board by, then what this
  // station is to the service, then where it came from.
  const heading = el("div", { class: "br-head" }, [el("span", { class: "br-train", text: t.train })]);
  if (t.kind) heading.append(el("span", { class: "br-kind" + (t.kind === "DART" ? " dart" : ""), text: t.kind === "DART" ? "DART" : "Train" }));
  heading.append(el("span", { class: "br-dest", text: t.destination || "–" }));
  heading.append(el("span", {
    class: "br-calls",
    text: [t.calls_as, t.origin ? `from ${t.origin}` : ""].filter(Boolean).join(" · "),
  }));

  const detail = el("p", { class: "br-detail" });
  if (t.prediction) {
    const [lo, hi] = t.prediction.interval_80pct;
    const where = t.prediction.vantage_name || `timing point ${t.prediction.vantage_location}`;
    const seen =
      t.prediction.current_delay_min != null ? `${lateness(t.prediction.current_delay_min)} at ${where}` : "";
    detail.append(
      el("span", { class: "br-range", text: `${hhmm(lo)} to ${hhmm(hi)}` }),
      el("span", { text: seen ? ` · last reported ${seen}` : "" })
    );
  } else {
    detail.append(el("span", { text: noPredictionReason(t) }));
  }

  return el("div", { class: "board-row" }, [heading, times, detail, ...journeyToggle(t, stationCode, now)]);
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
    cell.append(el("span", { class: "dim", text: s.scheduled || "–" }));
  }
  return cell;
}

/**
 * The disclosure and the route it opens. Built as a button plus a labelled region rather
 * than a clickable div so it works from the keyboard and announces its state.
 * @param {Entry} t
 * @param {string} stationCode
 * @param {string} now
 */
function journeyToggle(t, stationCode, now) {
  let stops = t.journey ?? [];
  let loaded = stops.length > 0;
  const id = `journey-${t.train}`;

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

  const closedLabel = () => (loaded ? `Full route, ${stops.length} stops` : "Show full route");
  const label = el("span", { text: closedLabel() });
  const button = /** @type {HTMLButtonElement} */ (
    el("button", {
      class: "jrny-toggle",
      type: "button",
      "aria-expanded": "false",
      "aria-controls": id,
    })
  );
  button.append(chevron, label);

  const list = el("ol", { class: "jrny" });
  const footnote = el("p", { class: "br-note" });
  // The fan, for a train with a prediction: drawn each time the route opens, so it unfolds
  // in front of the visitor rather than while hidden.
  const fanHost = el("div", { class: "fan-host" });
  const inner = el("div", {}, [fanHost, list, footnote]);
  const fan = () => {
    if (!t.prediction || !stops.length) return;
    const drawn = drawFan(fanHost, { ...t, journey: stops, prediction: t.prediction }, { now });
    if (drawn === "earlier") {
      fanHost.append(el("p", { class: "br-note", text:
        "The last report came from a timing point the route does not list, so the fan starts from the last listed stop that reported." }));
    }
  };

  function draw() {
    list.replaceChildren(
      ...stops.map((s, i) => {
        const item = el("li", {
          class: [s.arrived ? "done" : "", s.here ? "here" : "", s.passes ? "passes" : ""].filter(Boolean).join(" "),
        });
        item.style.setProperty("--i", String(i));
        item.append(
          el("span", { class: "pip" }, [el("i")]),
          el("span", { class: "stop-name", text: s.name + (s.here ? " · you are here" : s.passes ? " · passed, last report" : "") }),
          stopTime(s)
        );
        return item;
      })
    );
    footnote.textContent = stops.some((s) => s.arrived)
      ? "Times already recorded show how far off the timetable they were, in minutes. Later stops show the timetable."
      : "This service has not reported anywhere yet, so every time here is the timetable.";
  }

  /**
   * Say something in the panel while there is no route in it yet.
   * @param {string} text
   * @param {string} [tone]
   */
  function placeholder(text, tone = "") {
    list.replaceChildren();
    footnote.className = `br-note ${tone}`;
    footnote.textContent = text;
  }

  if (loaded) draw();
  const wrap = el("div", {
    class: "jrny-wrap",
    id,
    role: "region",
    "aria-label": `Route of ${t.train}`,
    "data-open": "false",
  });
  wrap.append(inner);

  button.addEventListener("click", async () => {
    const open = wrap.dataset.open === "true";
    wrap.dataset.open = String(!open);
    button.setAttribute("aria-expanded", String(!open));
    label.textContent = open ? closedLabel() : "Hide route";

    if (open || loaded) {
      if (!open) fan();
      slide(wrap, !open);
      return;
    }

    // Opening a route that was never downloaded. The panel opens straight away with a
    // line saying what is happening, then resizes when the route lands: showing nothing
    // for a second reads as a button that did not work.
    placeholder("Fetching the route from Irish Rail.");
    slide(wrap, true);
    button.disabled = true;
    try {
      const data = await get(
        `/journey?train=${encodeURIComponent(t.train)}&station=${encodeURIComponent(stationCode)}`
      );
      stops = data.journey ?? [];
      loaded = true;
      if (stops.length) {
        footnote.className = "br-note";
        draw();
        fan();
      } else {
        placeholder("Irish Rail has no route for this service today.");
      }
    } catch (err) {
      const wait = err instanceof ApiError && err.status === 429 ? ` Try again in ${err.retryAfter}s.` : "";
      placeholder(
        `Could not load the route: ${err instanceof Error ? err.message : String(err)}.${wait}`,
        "warn"
      );
    } finally {
      button.disabled = false;
      // Re-measure: the panel was sized around the placeholder, not the route.
      if (wrap.dataset.open === "true") slide(wrap, true);
    }
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
          (waiting ? `; ${waiting} ${waiting === 1 ? "has" : "have"} not started their journey yet` : "") +
          `. Read at ${hhmm(b.generated_at.slice(11))}.`,
      })
    );

    if (b.trains.length === 0) {
      board.append(el("p", { class: "text-fg-3", text: "Nothing due here in the next ninety minutes." }));
    } else {
      const now = hhmm(b.generated_at.slice(11));
      const head = el("div", { class: "board-head" }, [
        el("h2", { class: "board-name", text: b.station_name }),
        el("span", { class: "board-time", text: `Read at ${now}` }),
      ]);
      board.append(el("div", { class: "board" }, [head, ...b.trains.map((t) => row(t, b.station, now))]));
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
    const codes = new Set(stations.map((s) => s.code));
    const asked = askedFor() || foundIn(stations);
    const linked = codes.has(asked);
    select.value = linked ? asked : remembered();
    if (!select.value && stations[0]) select.value = stations[0].code;
    need("status").textContent = "Pick a station and press Show board.";
    need("intro").hidden = false;
    // A ?station= link is somebody asking for that board, so it loads without a second
    // click. It is the one case where opening a page costs Irish Rail requests, and it is
    // still a deliberate ask rather than a drive-by. A code that is not on the list is
    // ignored rather than handed to the feed.
    if (linked) {
      remember(asked);
      try {
        const url = new URL(location.href);
        url.searchParams.delete("find");
        url.searchParams.set("station", asked);
        history.replaceState(null, "", url);
      } catch {
        /* the board still loads; only the shareable address is lost */
      }
      void showBoard(asked);
    } else if (new URL(location.href).searchParams.get("find")) {
      need("status").textContent = "No single station matches that name. Pick one from the list.";
    }
  })
  .catch((err) => {
    need("status").textContent = "";
    showFailure(err, remembered());
  });

need("picker").addEventListener("submit", (ev) => {
  ev.preventDefault();
  remember(select.value);
  // The address bar becomes a link to this board. replaceState rather than pushState: the
  // back button should leave the page, not walk back through every station tried.
  try {
    const url = new URL(location.href);
    url.searchParams.set("station", select.value);
    history.replaceState(null, "", url);
  } catch {
    /* an unwritable history is a lost convenience, not a broken board */
  }
  void showBoard(select.value);
});
