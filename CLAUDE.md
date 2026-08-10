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

## Status

The offline phase is complete. Do not redo it.

- **Backfill:** 28,706 gzipped XML files, 1,087 train codes, 34 dates. Done once, not
  repeated.
- **Model:** LightGBM quantile regression, trained on 27 June – 12 July, validated
  13–19 July. Trained locally. **There is no persisted model artifact** — training runs
  in-process and saves nothing. Model persistence is unbuilt work, not a completed step.
- **The test week (20–26 July) has never been opened** and stays closed until the end,
  per decisions.md D25. Every number quoted anywhere is validation, not test.
- **Head-to-head vs the operator's own `ExpectedArrival`:** 80.1s vs 109.7s MAE across
  **9,077 comparisons over 2,654 distinct events** — a 27% improvement. This is the
  headline claim and every future change must not silently break it.

Current phase is **serving**: get the system running in AWS with a public URL.

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

## What the product answers

For a train **currently in service** that has already reported at an upstream stop: how
late will it be on arrival at a given station. Horizon is minutes to about an hour.

It does not forecast future-dated services. The features are derived from upstream
reported delays, so no upstream report means no prediction — this is why **~56% of polls**
in the operator comparison were unanswerable. (That figure is a share of polls, not of
events; a single event is polled ~18 times as the train approaches.) State this limit on
the site; a visitor expecting next-week forecasts will think the system is broken.

## Data source

Irish Rail Realtime API. No key, no registration. Full field reference and all known
data-quality issues are in `docs/data-dictionary.md` — **read that file before writing
any parsing code.**

Four endpoints are in use. Ignore the rest.

| Endpoint | Role |
|---|---|
| `getTrainMovementsXML?TrainId=&TrainDate=` | labels + features. This is the project. |
| `getCurrentTrainsXML` | discovering which train codes exist |
| `getStationDataByCodeXML_WithNumMins` | captures operator ETA for the baseline |
| `getAllStationsXML` | station list for the poller; cached, rarely refetched |

## Historical data — verified 2026-07-25

**`TrainDate` is honoured. Real history is served, back to 2007.**

Verified by browser: 2007 returns data, 2006 does not, 2027 does not (so it is not
generating from a timetable). Confirmed genuine by comparing dates — all recorded times
differ, past journeys are complete while today's is mid-flight, and the 2020 response has
41 records against 2026's 44 with a different scheduled arrival at Cork, i.e. the
timetable and infrastructure of that era.

**Consequence: a missed collection run is recoverable — just re-fetch those dates later.**
This does not apply to `ExpectedArrival`, which exists only live and cannot be backfilled.

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

## Leakage rules — these protect the headline claim

- **Log every prediction at request time**, with a timestamp, train, station, the
  predicted quantiles, and the model version. Reason: the accuracy page is only
  trustworthy if the prediction provably predates the outcome.
- **Never regenerate historical predictions.** Recomputing what the model "would have
  said" uses today's model against a known outcome, which is leakage. Any code that does
  this is a bug, not an optimisation.
- **Never feed `ExpectedArrival` into the model.** It is the baseline being compared
  against; using it as an input makes the comparison meaningless.
- The nightly scorer joins yesterday's logged predictions to realised arrivals. It reads
  outcomes; it never writes or recomputes predictions.

## Retraining policy

- **No scheduled retraining.** Reason: retraining is a change to a working system, and
  changes carry risk. One extra day on top of months of data moves the model almost not at
  all, so a nightly job is risk with no benefit.
- **Trigger only on evidence.** The baseline is the **first 30 days of live scored
  predictions after launch** — not the test week, which stays closed (D25). Retrain when
  rolling 7-day MAE rises above that live baseline and stays there for a week.
- **Champion/challenger gate.** A new model replaces the incumbent only if it beats it on
  a recent held-out week on MAE *and* is not worse on any individual line's median. Reason:
  without a gate, automated retraining is an automated way to degrade the system with
  nothing checking.
- If the trigger never fires, that is a finding, not a gap. Publish it: "trained on
  July 2026 data, unchanged since, no sustained degradation."

## Reporting rules for the accuracy page

- Head-to-head numbers only on **matched events** — where both the model and the operator
  produced a prediction for the same train, station and moment.
- **Always publish coverage alongside accuracy.** Reporting "27% better" without "answers
  ~44% of queries" is the misleading version.
- **Report weak-coverage lines separately, never blended into the aggregate.** The reason
  is label quality, not line identity — see Data rules below.
- Aggregate three ways: rolling 7-day (headline), daily with sample size shown (trend),
  and cumulative since launch (the CV number).
- Publish where the model loses. The two known caveats — worse median on weak-coverage
  lines, and the coverage gap — go on the page, not buried in the README.

## Data rules

- **~31% of movement records never receive actual times** (348,837 of 504,810 carry an
  arrival, so 30.9% do not; excluding structural and future nulls, 28.5% are genuinely
  unreported). Handle explicitly.
- **Label quality is governed by `AutoArrival`, not by which line a station sits on.**
  Read `docs/label-quality.md` and decisions.md D20–D23 before writing anything that
  filters or drops records on label-quality grounds.
  The line-keyword approach — treating `Arrival == ScheduledArrival` on the ~10 documented
  weak-coverage lines as missing — **was tried, appeared to confirm the documentation, and
  was rejected.** The apparent line effect was Simpson's paradox from composition; within
  machine-captured records the flagged lines echo *less* than unflagged ones, and three
  quarters of suspect records sit on lines the documentation never flagged.
  **D23 stands: flag and keep.** No record is dropped at ingestion; exclusion is an
  evaluation-time decision, reported both ways.
- **All feed times are quantised to 6-second intervals** (zero violations across 614,041
  non-null delays).
- The fields are `Arrival` and `ScheduledArrival`. There is no `ArrivalTime` or
  `ScheduledArrivalTime`.

## Stack

- Ingestion: Python + `requests`
- Storage: gzipped raw on disk → Parquet → Postgres
- Model: LightGBM, quantile loss, for prediction intervals
- Serving: FastAPI
- Deploy: AWS. **No scheduled retraining** — see the retraining policy above.

## AWS and cost rules

- Serverless by default: S3 for raw snapshots and the model artifact, EventBridge for
  scheduling, CloudFront + S3 for the frontend, CloudWatch for logs, Lambda for the API.
- **Open question, not a decision: whether the poller runs on Lambda.** The current poller
  is a long-running loop with in-process state, a heartbeat host lock, and quiet-hours
  logic. None of that survives a lift-and-shift. Assess before committing to it.
- **Never provision** a NAT Gateway (~€33/mo), an Application Load Balancer (~€18/mo), or
  a 24/7 RDS instance. Reason: these bill hourly regardless of traffic and will exhaust
  the credits for no benefit at this scale.
- GitHub Actions authenticates to AWS via OIDC. No long-lived access keys in the repo or
  in Actions secrets.
- A budget alarm must exist before anything is deployed.

**Note, not a rule — S3 write batching.** If the poller is rewritten for AWS anyway,
prefer one object per poll cycle over one per station: 30 objects every 5 minutes is
~259k PUTs/month for no gain. This does not justify rewriting the current poller on its
own; it writes one gzip per station per cycle today and that is fine on local disk.

## Scope lock — web layer

Exactly three pages:

1. Predictions — pick a station, see next trains with delay ranges
2. Accuracy — the scoreboard, split by line, with the coverage caveat visible
3. How it works — architecture, data provenance, method, limitations

**Explicitly out of scope:** map view, route planning, user accounts, saved stations,
notifications, mobile app, dark mode toggle. Reason: each costs a week and adds nothing an
interviewer will ask about. The deadline is real.

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

Docker, Kubernetes, Terraform, message queues, GTFS-R, weather data. Premature
infrastructure is procrastination.

## How to treat this file

These are decisions made in discussion, mostly without the repo in view. If what you find
in the code or the data contradicts something here, **stop and say so** rather than working
around it silently. A rule in this file that turns out to be wrong is more dangerous than
no rule, because it looks authoritative. Two rules in earlier versions of this file were
wrong and were followed for days before being caught — and the flagged-lines rule in the
July update block was a third, caught only because it was checked against the repo before
being merged.
