# CLAUDE.md — rail-delay

## What this is

A live Irish Rail delay predictor with **prediction intervals** (not point estimates).

**Scope lock:** this is a CV/portfolio artifact, to be finished and deployed before the
MAI starts mid-September 2026. It is NOT thesis work. The MAI thesis (stochastic train
delay prediction, supervised by Bidisha Ghosh) starts September and is separate. If a
task does not serve "working deployed service by mid-September", it is out of scope —
flag it as thesis-later and move on. Scope creep is the main way this project fails.

### What it does, concretely

Given a train currently running, predict how late it will arrive at a stop further along
its route, as a **range** rather than a single number.

Example: it is 16:30. A220 left Heuston at 16:00 for Cork, and has been 1–2 minutes down
at Kildare, Portarlington and Portlaoise. User asks about Thurles. Service answers:
"expected 16:07–16:12, most likely 16:09, 80% confidence."

Output shape:

```json
{
  "train": "A220", "station": "Thurles",
  "scheduled": "17:06:30",
  "operator_eta": "17:08:30",
  "predicted": "17:09:40",
  "interval_80pct": ["17:07:10", "17:14:20"],
  "current_delay_min": 2.1,
  "confidence": "good coverage on this line"
}
```

**One model for the whole network**, not one per train. Success criterion: beat Irish
Rail's own `ExpectedArrival` on well-covered lines, and be honest in the writeup about
lines where the data cannot support a claim. An honest loss is a better result than an
unverifiable win.

## Who I am, and how to work with me

Final-year MAI student (Computer & Electronic Engineering, Trinity College Dublin).
Comfortable with ML theory, algorithms, probability, C, Python. **New to practical dev
tooling** — first project involving venv, git, Docker, or a cloud deploy.

- Explain setup and tooling concretely, exact commands, Windows / PowerShell.
- Do NOT dumb down ML, statistics, or systems design.
- **Do not silently make design decisions.** Schema, features, model choice, evaluation
  design are mine. Lay out options and trade-offs; let me choose. Boilerplate I would
  otherwise Google, just write.
- Challenge weak assumptions. No flattery, no filler.
- I must be able to defend every design decision in an interview. If I cannot explain it,
  it should not be in the repo.

## Data source

Irish Rail Realtime API. No key, no registration. Full field reference and all known
data-quality issues are in `docs/data-dictionary.md` — **read that file before writing
any parsing code.**

Three endpoints are in scope. Ignore the other seven.

| Endpoint | Role |
|---|---|
| `getTrainMovementsXML?TrainId=&TrainDate=` | labels + features. This is the project. |
| `getCurrentTrainsXML` | discovering which train codes exist |
| `getStationDataByCodeXML_WithNumMins` | later — captures operator ETA for the baseline |

## Historical data — verified 2026-07-25

**`TrainDate` is honoured. Real history is served, back to 2007.**

Verified by browser: 2007 returns data, 2006 does not, 2027 does not (so it is not
generating from a timetable). Confirmed genuine by comparing dates — all recorded times
differ, past journeys are complete while today's is mid-flight, and the 2020 response has
41 records against 2026's 44 with a different scheduled arrival at Cork, i.e. the
timetable and infrastructure of that era.

**Consequence: backfill, not live polling, is the priority.** An earlier plan assumed no
history and treated continuous collection as urgent. That was wrong. A missed collection
run is now recoverable — just re-fetch those dates later.

The live poller is still needed eventually, for two reasons: the deployed service needs
current train positions to predict from, and `ExpectedArrival` (the operator baseline) is
only available live and cannot be backfilled.

## Backfill strategy

**The one hard problem: enumerating train codes.** No endpoint lists "all trains that ran
on date X". `getCurrentTrainsXML` only shows trains currently moving, or starting within
10 minutes.

Approach: harvest codes from repeated `getCurrentTrainsXML` calls (every 5 min across a
full service day, ~05:30–00:30, gives ~500–600 codes), then replay that list against past
dates. Codes are stable day to day. Empty responses for days a service did not run are
expected and harmless. Harvest on both a weekday and a weekend — timetables differ.

Discontinued services are missing from a harvested list. That is fine — we predict for
trains that run now.

Rejected: brute-forcing the code space (26,000 combinations/day, rude and pointless).
Later improvement: check whether GTFS static trip short names map to train codes, which
would give an authoritative per-day list.

**Politeness:** no documented rate limit, and Irish Rail states the service is provided
as-is with no support. Throttle to 1–2 requests/second, exponential backoff, honour 429
and 503, run bulk jobs overnight. If throttled, slow down — do not retry harder.

**Volume:** ~600 trains/day. 30 days ≈ 18k requests ≈ 2.5 hours. 1 year ≈ 220k ≈ 30 hours.
15 years is ~19 days of downloading and is not happening.

**Order of work: 30 days first.** Build parse → features → baseline → model → serve end to
end on that thin slice. The pipeline will change repeatedly; do not re-download a year's
data each time it does. Widen only once the chain works.

**More data is not automatically better.** 2020 is COVID-era with different infrastructure.
Decide the training window empirically — train on 3, 6, 12, 24 months and compare — rather
than assuming.

## Storage

**Archive raw responses before parsing.** Gzipped, one file per (train, date), under
`data/raw/{date}/{code}.xml.gz`. If parsing logic changes later, reprocess from raw.
Parse-and-discard loses data permanently.

Parsed records → Parquet for analysis, Postgres for serving.

`data/` is gitignored and never committed.

## Feature design — the load-bearing rule

**Features must describe the situation, not the identity.**

Do not use train code as a feature. A service launched next March arrives as an unknown
category, and a model that learned "A218 runs 2 minutes down" has nothing to say about it.
Same problem for renumbered services.

Use instead: time of day, day of week, line/route, stops remaining, distance to go, delay
accumulated upstream today, weather, holiday flag. All of these exist for a train that
launched yesterday.

Get this right and new services work automatically. Get it wrong and the model silently
fails on exactly the trains people most want to ask about.

Candidate features and reasoning: `docs/feature-ideas.md`.

Expected dominant feature: delay at previous stop. Delay is strongly autocorrelated within
a journey. Seasonality and holiday effects are third-order polish.

## Stack (planned — do not build ahead of the current stage)

- Ingestion: Python + `requests`
- Storage: gzipped raw on disk → Parquet → Postgres
- Model: LightGBM, quantile loss, for prediction intervals
- Serving: FastAPI
- Deploy: AWS, with a scheduled retraining job
- v2 only: React + TypeScript frontend, Terraform, monitoring

## Current state

- [x] Feasibility probe — all endpoints verified (`scripts/probe_irishrail.py`)
- [x] venv, VS Code, git, GitHub repo set up
- [x] Historical availability established (2007–present)
- [x] `docs/data-dictionary.md` written
- [ ] **NEXT: `harvest_codes.py` then `backfill.py` — 30 days of raw XML on disk**
- [ ] Parser: raw XML → Parquet
- [ ] Label-quality check: measure `Arrival == ScheduledArrival` exact-match rates per line
- [ ] Baseline: naive schedule, then operator ETA
- [ ] LightGBM quantile model + evaluation harness
- [ ] FastAPI service
- [ ] AWS deploy + retraining schedule

## Conventions

- `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `data/`
- `.gitattributes`: `* text=auto eol=lf`
- Secrets in `.env`, never committed
- Commit small and often — the history is itself evidence of the work
- `requirements.txt` kept current via `pip freeze`
- **Write down what was tested and what the evidence was, not just the conclusion.**
  Two claims in this file were wrong until tested against raw records.

## Reliability principle

Do not aim for perfect uptime — aim for recovery. Every scheduled job should ask "what am
I missing?" and fetch that, rather than assuming the previous run succeeded. Schedulers
fail, laptops sleep, networks drop.

## Do not, yet

Docker, Kubernetes, Terraform, message queues, a frontend, AWS, GTFS-R, weather data.
Get 30 days parsed and a baseline number first. Premature infrastructure is
procrastination.
