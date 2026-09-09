/* The live board. Calls /api, which CloudFront forwards to the prediction Lambda;
   scripts/dev_site.py does the same forwarding locally so there is no environment branch. */

const API = "/api";
const LIMIT = 6;
const REMEMBER = "railcast.station";
const DEFAULT_STATION = "KDARE"; // a through-station: a terminus board is nearly all departures

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v);
  }
  for (const kid of kids) n.appendChild(kid);
  return n;
};

const hhmm = (t) => (t || "").slice(0, 5);

function remembered() {
  try {
    return localStorage.getItem(REMEMBER) || DEFAULT_STATION;
  } catch {
    return DEFAULT_STATION; // private browsing throws on access, not just on write
  }
}

function remember(code) {
  try {
    localStorage.setItem(REMEMBER, code);
  } catch {}
}

async function get(path) {
  const res = await fetch(API + path, { cache: "no-store" });
  if (!res.ok) {
    let detail = res.status;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---- rendering ---- */

function lateness(pred) {
  // The interval in minutes late, which is what the range is actually about. The clock
  // times are the answer; this is the shape of it.
  const s = pred.pred_q50_sec, lo = pred.pred_q10_sec, hi = pred.pred_q90_sec;
  if (s == null) return "";
  const m = (v) => (v / 60 >= 0 ? "+" : "") + (v / 60).toFixed(1);
  return `${m(s)} min late, ${m(lo)} to ${m(hi)}`;
}

function trainCard(t) {
  const head = el("div", { class: "train-head" }, [
    el("span", { class: "code", text: t.train }),
    el("span", { class: "route", text: `${t.origin} → ${t.destination}` }),
    el("span", { class: "due", text: t.due_in_min == null ? "" : `due ${t.due_in_min} min` }),
  ]);

  const times = el("div", { class: "train-times" }, [
    el("span", { html: `<b>${hhmm(t.scheduled) || "—"}</b> scheduled` }),
    el("span", { html: `<b>${hhmm(t.operator_eta) || "—"}</b> Irish Rail` }),
  ]);

  const card = el("div", { class: "train" }, [head, times]);

  if (t.prediction) {
    const p = t.prediction;
    times.appendChild(el("span", {
      class: "mine",
      html: `<b>${hhmm(p.predicted)}</b> RailCast`,
    }));
    card.appendChild(el("div", { class: "pred" }, [
      el("span", { class: "band",
        text: `80% range ${hhmm(p.interval_80pct[0])} – ${hhmm(p.interval_80pct[1])}` }),
      el("span", { class: "sub", text: lateness(p) }),
    ]));
    const seen = p.current_delay_min == null ? "" :
      `${p.current_delay_min.toFixed(1)} min down at ${p.vantage_name || p.vantage_location}`;
    card.appendChild(el("p", {
      class: "sub why",
      text: [seen, p.confidence].filter(Boolean).join(" · "),
    }));
  } else {
    card.appendChild(el("p", {
      class: "sub why declined",
      text: t.explanation || t.reason || "No prediction.",
    }));
  }
  return card;
}

async function showBoard(code) {
  $("status").textContent = "Asking the model about each train — this takes a few seconds…";
  $("board").replaceChildren();
  try {
    const b = await get(`/board?station=${encodeURIComponent(code)}&limit=${LIMIT}`);
    const predicted = b.trains.filter((t) => t.prediction).length;
    const waiting = b.trains.filter((t) => t.reason === "not_yet_departed").length;

    $("status").innerHTML =
      `<b>${b.station_name}</b> · ${b.trains.length} due in the next ${b.board_minutes} ` +
      `minutes · ${predicted} with a prediction, ${waiting} not departed yet · as of ` +
      `${hhmm(b.generated_at.slice(11))} · model <code>${b.model_version}</code>`;

    if (!b.trains.length) {
      $("board").appendChild(el("p", { class: "loading",
        text: "Nothing due here in the next ninety minutes." }));
    } else {
      $("board").replaceChildren(...b.trains.map(trainCard));
    }
    $("explainer").hidden = false;
  } catch (e) {
    $("status").textContent = "";
    $("board").replaceChildren(el("p", { class: "error",
      text: `Could not load the board: ${e.message}` }));
  }
}

/* ---- start ---- */

get("/stations")
  .then(({ stations }) => {
    const sel = $("station");
    const watched = el("optgroup", { label: "Watched stations (scored against the operator)" });
    const rest = el("optgroup", { label: "Everywhere else" });
    for (const s of stations) {
      const o = el("option", { value: s.code, text: s.name });
      (s.polled ? watched : rest).appendChild(o);
    }
    sel.replaceChildren(watched, rest);
    sel.value = remembered();
    if (!sel.value) sel.value = stations[0].code;
    return showBoard(sel.value);
  })
  .catch((e) => {
    $("status").innerHTML =
      `<span class="error">The prediction service is not reachable (${e.message}).</span>`;
  });

$("picker").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const code = $("station").value;
  remember(code);
  showBoard(code);
});
