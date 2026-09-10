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
  13–19 July. Twelve features, all computable at prediction time (`src/features.py`).
  Versioned artifacts under `data/models/{version}/` with a `LATEST` pointer; save with
  `python src\train_quantile.py --save`, load with `--load latest`. See D31–D35.
- **The test week (20–26 July) was opened once on 2026-09-10**, against a plan committed
  beforehand (D73). Result in D74: MAE **58.3s**, median 29.1s, interval coverage **80.0%**
  against a claimed 80.0%, on 217,290 unseen predictions, 32.1% better than persistence, and
  misses split 10.4% / 9.6% against 10 / 10 expected. Test beat validation (59.7s), so the
  calibration was genuine and the live 75.1% is distribution shift rather than an optimistic
  figure. **D25 is discharged and the week cannot be used again.** Raw output kept verbatim in
  `docs/test-week-2026-09-10.txt`.
- **Head-to-head vs the operator's own `ExpectedArrival`:** 80.1s vs 109.7s MAE across
  **9,077 comparisons over 2,654 distinct events** — a 27% improvement. This is the
  headline claim and every future change must not silently break it.

Current phase is **serving**: get the system running in AWS with a public URL.

### Where serving has got to

**Deployed and live:**

- Budget stack (`infra/foundation.yaml`, us-east-1) and poller stack
  (`infra/poller.yaml`, eu-west-1). Lambda poller running every 5 minutes.
- **Parallel run 2026-08-23 to 08-30** (D36, hard expiry). Forced-failure test 08-27.
  Compare with `python scripts\diff_parallel.py` after an `aws s3 sync`.
  **Interim read, 28 August, 83.0 covered hours: MEETS the bar.** Schema identical,
  event overlap 99.9% (15,040 both / 3 local-only / 13 lambda-only), volume deviation
  3.1% mean, 88.6% identical `Exparrival` over 259,643 shared events. This verified the
  script end to end after the window-intersection change. It is not the cutover call —
  D36 measures the full seven days, which needs both weekend days.
- API stack (`infra/api.yaml`, eu-west-1):
  The Function URL is the stack's `ApiUrl` output. It is deliberately not written here:
  this file is public and nothing throttles `/predict`. Fetch it with
  `aws cloudformation describe-stacks --stack-name rail-delay-api --region eu-west-1 --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text`.
  Try `/health`, `/docs`, `/predict?train=A220&station=THRLS`. Cold start ~3.2s,
  warm ~30ms. Predictions log to `s3://rail-delay-poller-kg/predictions/`.

**Also deployed:** generator stack function `rail-delay-api-generator` (D49) — a sampled
prediction producer, **schedule ENABLED since the 31 August cutover**, firing every five
minutes — and `infra/scorer.yaml` (D50), the nightly scorer, running at 06:15 UTC. Build
with `scripts\build_scorer.ps1`. Scores land in `s3://rail-delay-poller-kg/scores/date=*/`
as `summary.json` plus `rows.jsonl.gz`, and the rolling seven-day rollup as
`scores/accuracy.json` — the file the accuracy page will read, carrying `generated_at`,
the model version and per-group coverage. The scorer runs at 1024 MB after an
out-of-memory failure on 9 September (D65), which is also where the write-ordering
argument lives: the durable write happens before the derived one, so the crash cost a
stale page and no data.

**The site is live at https://dc9icf7494up8.cloudfront.net** (D66, D68, D70). Four pages
plus a 404, in the signal-box visual world: landing, board, accuracy, how it works. Tailwind
v4 compiled ahead of time to `site/app.css` (committed), TypeScript in `checkJs` mode as a
check rather than a compiler, no framework. The board opens each service into its full route;
routes for predicted trains come free with the prediction, the rest are fetched on click.

**Deploys are automatic.** Push to `main` and `.github/workflows/deploy.yml` compiles the
stylesheet, refuses a committed `app.css` that differs from its source, type checks, runs
every module self-check, checks the palette against WCAG, then syncs to S3 and invalidates
CloudFront. Credentials come from GitHub OIDC (`infra/github-oidc.yaml`), so **no AWS key
exists in the repo or in GitHub's secrets**. The trust condition matches the id-bearing
subject GitHub actually sends, which is not the form any guide shows: see D72 before
touching it.

The API is **not** in the pipeline. It still deploys by hand:
`scripts\build_api.ps1 -Version <v>` then `sam deploy --template-file infra/api.yaml`.

**Built and finished, off the serving path: the optimisation component** (D59–D63).
`src/buffer_lp.py` formulates timetable buffer allocation as a linear program — decision
variables, objective and constraints written out and handed to a solver, not a library
that optimises internally — solved over sampled scenarios (SAA) drawn from the archive,
with an L1 penalty shrinking toward the timetable's own allocation. Uniform weighting at
the best lambda: all five trains improve, three of five significant against a bootstrap
over days. The more interesting finding is that under terminus-only weighting there is
nothing to gain — the timetable is already tuned for terminal punctuality, so the
available gain is all at intermediate stops. Formulation and the linearisation argument:
`docs/optimization-formulation.pdf` and `docs/optimization-revision.pdf`; interview
questions in section O of `docs/explain-index.md`. This is a CV/interview asset, not part
of the deployed service, and nothing in serving depends on it.

**Serving model: `20260903T173007Z-5ebf03f`, promoted 2026-09-03 17:52:58 UTC** (D57).
Trained on journeys that pass `journey_consistent`, evaluated against the incumbent on
identical cleaned validation: MAE 60.6s → 59.7s; at the four Galway stations 1,056s →
413s and interval width 6,795s → 394s. The gate was amended with a failing candidate in
hand and D57 says so: "not worse" is now a paired-bootstrap interval, and a group can veto
only at n ≥ 1,100 (the size to detect a 5-point coverage drop at 80% power — derived from
power, not from the 424-row group that failed). Both validation figures are published, and
both belong to the previous champion: 76.1s across all journeys, 60.6s across the
consistent 96.1%. The serving model scores 59.7s on that same consistent set and has not
been evaluated on the all-journeys set. The previous champion
`20260813T221035Z-0c444e3` stays on disk; rollback is a deploy with the parameter changed
back.

**Published limitation, from D57:** the intervals do not cover disruptions. On real delays
over an hour (53 validation rows excluding the Galway stations; 16 Sligo-line predictions
on 2 Sep) both models cover **0%**. The champion's apparent 27.6% was Galway garbage. State
this on the accuracy page beside the coverage figure.

Everything the scope lock asked for is built, deployed and running. What is left, in the
order it is worth doing:

1. **Self-host the two webfonts.** Performance sits at 0.75 against a 0.9 target, and the
   render-blocking request to `fonts.googleapis.com` is the likely cause. It also removes a
   third party that currently sees every visitor's IP.
2. **Raise the Lambda concurrency limit, then reserve a few for the API** (D71). Counter-
   intuitive but correct: reserved concurrency is refused below an account limit of 100, so
   raising the limit is what makes a hard cap possible. Today a flood on the public endpoint
   could starve the poller and scorer of concurrency, which costs collected data rather than
   money.
3. **Make a failed prediction-log write visible.** `api.py` catches `LogWriteFailed` and
   returns 503, which Lambda counts as a success, so `Errors` stays at zero. D39 requires log
   trouble to be visible as API trouble and it still is not. Listed below as an open item and
   still true.
4. Split `requirements.txt`, which now mixes runtime with lint tooling.

**Cutover completed 2026-08-31** (D54). The parallel run met the D36 bar over 132.9 covered
hours: schema identical, **99.9% event overlap** (21,183 both / 5 local-only / 6
lambda-only), volume deviation 2.8%, 89.6% identical `Exparrival` over 372,688 shared
events. All eleven misses were single-cycle events visible for zero minutes, symmetric
between the two pollers, and none fell in the forced-failure test windows. The local poller
is stopped; the Lambda is the only collector and there is no fallback. The generator's
schedule is **ENABLED** and firing every five minutes.

The Lambda still writes to `parallel/lambda` and that name is now historical. Renaming it
would split the archive across two prefixes, so scoring any date spanning the change would
have to read both. The scorer's `PollerPrefix` therefore needed no change.

Open items that will not announce themselves:

- `harvest_codes.py --from-snapshots` now defaults to the poller's archive and refuses an
  archive whose newest snapshot is over 24h old, exiting 3 (D55). Both local archives are
  frozen since the cutover, so every future run trips the guard until snapshots come from
  somewhere live — which is the point: "0 new codes" from a dead folder is
  indistinguishable from a network with no new services. Not ported to S3 on purpose;
  nothing in the live path reads `codes.json` and live mode still works.
- `requirements.txt` now mixes runtime deps with lint tooling (cfn-lint pulled in sympy,
  networkx). Worth splitting the way `requirements-lambda.txt` already does.
- **CloudFormation cannot confirm an email subscription, so it reports success on a dead
  alarm channel.** This has now happened twice: the poller's topic on 27 August, and the
  scorer's, whose subscription CFN created at 2026-08-27 20:14 UTC and which AWS reaped
  unconfirmed **48 hours later** — sooner than the ~3 days AWS documents. Both were
  resubscribed by hand on 29 August; all three topics currently have one confirmed
  subscriber and nothing pending. CFN's stack state still points at the reaped ARN, which
  is harmless on a routine redeploy but **will create a duplicate subscription if
  `AlarmEmail` ever changes**, because that forces resource replacement. Nothing in the
  system checks that an alarm has a live subscriber; verify with
  `aws sns list-subscriptions-by-topic --topic-arn <arn>` rather than assuming a deploy
  that succeeded gave you working alerting.
- **The API's error alarm does not fire on a failed prediction-log write.** `api.py`
  catches `LogWriteFailed` and returns 503, which Lambda counts as a *successful*
  invocation, so the `Errors` metric stays at zero. D39 requires log trouble to be
  visible as API trouble, and it currently is not.
- **The generator's rules changed at 2026-09-03 17:25 UTC and again at 17:53** (D58):
  leads beyond four hours, inconsistent journeys, and vantages more than 30 minutes
  *early* are declined, and `vantage_auto` is logged. Scores before and after are not
  directly comparable; `decline_reasons` gains `lead_out_of_range`,
  `journey_inconsistent` and `vantage_delay_out_of_range`. Of 37 phantom-vantage rows on
  2 Sep, 36 would now be declined; the one that would not (A461, +1h44m at a plausible
  lead) is uncatchable by any label-only rule without also refusing real disruptions.
- **The coverage trigger has FIRED and the decision was to publish, not patch** (D64).
  Rolling 7-day to 2026-09-07: `intercity_cork_corridor` 66.7% (below 70% on 7 of 7 days),
  `commuter_kildare` 64.7% (6 of 7), `intercity_other` 56.7%. Overall is 75.4%, just above
  the global 75% clause, so it is the group trigger that fired. DART 77.6% and dublin_hubs
  79.0% are unaffected, and the two corridors that fired share track — a localised shift,
  not a model that has gone bad. No model change was made; the reasoning is in D64.
  **The accuracy page must show coverage per station group beside the nominal 80%**, never
  as a single blended figure, or it conceals that two corridors sit at 65%.
  Recovery is now itself a measurement: if those corridors return to ~79% unaided the shift
  was temporary; if they stay low past the deadline, per-group conformal recalibration is
  the right call with time to do it properly.
- **Coverage is published as two numbers, headline visitor-facing** (D53): 89.3%
  conditional on the train being in service, **37.8%** as a visitor meets it on a station
  board (42.3% of board entries are trains that have not departed). The offline "~56% of
  polls unanswerable" is retired as a published figure — different population, different
  period, and beside the generator's rate it reads as an improvement that never happened.
- A **degraded** cycle counts as local uptime in `diff_parallel.py` (`records > 0 and
  status != "failed"`), so a partial sweep is compared as if it were a full one. That was
  harmless for the 28 August DNS outage because an event is polled ~18 times across a
  90-minute lookahead, so nothing was uniquely visible in the lost two minutes. A longer
  outage could genuinely lose events, and they would count against the overlap bar. D36
  allows that — the bar is "every miss explained", not "no misses" — but the explanation
  has to come from the cycle records, so check `stations_failed` before blaming the Lambda.
- **`api.predict_row` asks for the journey under today's Dublin date.** A train that
  departed yesterday and is still running between 00:00 and 00:30 is fetched under the
  wrong `TrainDate`, so it declines as `not_in_service` or `no_upstream_report` instead of
  predicting. Quiet hours start at 00:30, so the window is half an hour a night. The
  scorer already reads two partitions for this reason (D50); the API and generator do
  not. Found 2026-09-03 by reading the code, not yet observed in scores.

## Who I am, and how to work with me

Final-year MAI student (Computer & Electronic Engineering, Trinity College Dublin).
Comfortable with ML theory, algorithms, probability, C, Python. **New to practical dev
tooling** — first project involving venv, git, Docker, or a cloud deploy.

**I am the constraint on this project, not the code.** If something is architecturally
elegant and I cannot explain it, it is worse than a simpler thing I can. The deliverable is
not a system that works; it is a system that works *and* that I can defend in an interview.
Optimise for the second — when they conflict, the simpler explicable option wins, and if
you build the clever one anyway you have made the project worse. Say so when you think a
trade-off is live rather than deciding it quietly.

**Lead with the plain-language version, then the technical one.** A good part of the output
here currently goes over my head, which makes it useless to me no matter how correct it is.
Every decision-log entry opens with a one-line "Why this matters" a non-specialist would
follow. Prose explanations state the idea in ordinary words first and the precise version
second. This is the same failure the training/serving skew was — reasoning that only helps
a reader who already understands it (D52) — pointed at a different reader.

- Explain setup and tooling concretely, exact commands, Windows / PowerShell.
- Do NOT dumb down ML, statistics, or systems design. Plain-language *first* is not
  plain-language *only*: the precise version still follows, because I have to defend the
  real thing, not a simplified story about it.
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

Raw data under `data/` is gitignored and never committed; the small derived
artifacts needed to build are the documented exception. See Conventions.

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
- **Trigger only on evidence, and on the product, not just the point estimate.** The
  baseline is the **first 30 days of live scored predictions after launch** — not the test
  week, which stays closed (D25). Two triggers, either sufficient:
  - rolling 7-day MAE rises above the live baseline and stays there for a week;
  - **rolling 7-day interval coverage falls below 75% overall, or below 70% on any
    station group with at least 200 scored events that week, and stays there for a
    week.** The intervals are the product of a quantile model. In the first three live
    days MAE was healthy at 80s while coverage on the Kildare and Cork corridors fell
    from 79% in July to 63–69% — a degradation the MAE trigger cannot see (D56). The
    remedy for a coverage trigger may be recalibration (widening the intervals) rather
    than a full retrain; the trigger decides that something is done, not what.
  - A **label-quality finding** — contaminated training labels discovered after the
    fact — is a third, non-scheduled trigger. It does not wait for a metric to move,
    because the metric may be computed against the same contamination (D56).
- **Champion/challenger gate.** A new model replaces the incumbent only if it beats it on
  a recent held-out week on MAE, is not worse on any station group's median, *and* is not
  worse on interval coverage for any station group. **Both models are scored on identical
  data**: if the incumbent is evaluated on the old examples and the challenger on cleaned
  ones, the gate measures the cleaning rather than the model.
  - **"Not worse" means the paired bootstrap 95% interval on the difference excludes
    zero in the wrong direction**, not that the point estimate is on the wrong side. A
    point comparison with no tolerance fails every retrain on noise (D57).
  - **A group can veto only at n ≥ 1,100 rows** — the size needed to detect a 5-point
    coverage drop from 80% at 80% power with a two-sided test (1,094; the paired
    bootstrap is more powerful, so this is the conservative side). Below that the group
    is reported beside the result, not enforced. Derived from power, not from any group
    that happened to fail.
  - **Why the trigger uses 200 events and the gate 1,100.** The trigger only starts an
    investigation, which is cheap, so it should fire early on thin evidence. The gate
    blocks a better model, which is expensive, so it needs evidence strong enough to be
    right about it. Same statistic, two very different costs of being wrong.

  Reason for the gate at all: without one, automated retraining is an automated way to
  degrade the system with nothing checking.
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
- Storage: gzipped raw on disk → Parquet → S3. **No database** — see decisions.md
  D40 for why, because "why no database?" is an interview question.
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
- **The site deploys itself from GitHub Actions using OIDC** (D68, D72). No long-lived
  access keys anywhere: not in the repo, not in GitHub's secrets. Stack and API deploys are
  still manual `sam deploy` runs from the laptop, using the `rail-delay-deploy` IAM user's
  keys in the local AWS CLI config.
- A budget alarm must exist before anything is deployed.
- **The API package needs three things a normal `pip install` will not give you**, all
  found the hard way and all encoded in `scripts/build_api.ps1`: two `--platform` tags
  (numpy past 2.2.6 ships only `manylinux_2_28`, lightgbm only `manylinux2014`, and
  either alone fails to resolve), `--python-version 3.13` because this machine runs 3.14,
  and a vendored `libgomp.so.1` because lightgbm links against OpenMP and the Lambda
  runtime does not ship it. Without the last one the import dies at `ctypes.LoadLibrary`.

**Measured cost, 2026-08-23.** One cycle writes 3 S3 objects totalling ~18 KB, so a month
of five-minute polling is ~20,500 PUTs and ~123 MB. That comes to about **$0.10/month**,
effectively all of it S3 PUT requests — Lambda, CloudWatch, SNS and Budgets all sit inside
their permanent free tiers. In INR that is roughly ₹12 including GST.

Two thresholds worth watching, because they move quietly: the deployment uses **8 of the
10 free CloudWatch custom metrics** and **5 of the 10 free alarms**. Past those it is
$0.30 per metric and $0.10 per alarm per month. The per-cycle object batching is what
keeps PUTs at 20,500 rather than 210,000; see the S3 write batching note above.

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

**Build them as plain HTML, CSS and vanilla JavaScript.** No React, no TypeScript, no
build step, no npm. Reason in decisions.md D41. An earlier version of this file deferred
React to "v2"; that line was removed in the July merge and the gap went unnoticed until
2026-08-25, so the choice is stated explicitly here rather than left to inference.

Binding constraints on the web layer, all decided in discussion and none negotiable by
inference:

- **The site gets its own bucket.** Never a public prefix on `rail-delay-poller-kg`, which
  holds every prediction, score and raw capture; all four public-access blocks stay on
  there. `accuracy.json` is **pushed out** to the site bucket by the scorer, not served
  from where it is written (`SiteBucket` in `infra/scorer.yaml`, one key, no wildcard).
- **This is the first deliberately public thing in the project, so the bucket policy gets
  reviewed twice and shown before it deploys.** Do not deploy a site bucket policy without
  putting it in front of me first.
- **The accuracy page must show `generated_at` and the model version**, both of which
  `accuracy.json` carries, so a stale page is visibly stale rather than silently so (D65
  is what that guards against).
- **Coverage goes on the page per station group, beside the nominal 80%**, never as a
  single blended figure (D64). A blended 75.4% is true and conceals two corridors at 65%.
- **State beside it that the intervals cover 0% of real delays over an hour** (D57), and
  that the scored predictions are scheduled samples from the generator, not user traffic.
- A landing page is allowed and is not a fourth feature page: one screen, no data.

## Conventions

- **`.gitignore` splits `data/` in two, and the split is a rule not a list of exceptions.**
  *Raw collected data is never committed* — raw XML, parsed Parquet, live poll output,
  snapshots. It is large and reproducible by re-fetching. *Small derived artifacts required
  to build or reproduce ARE committed* — currently `data/codes.json` (228 KB),
  `data/live/stations.json` (12 KB) and `data/models/` (~1.1 MB per version). The test of
  the rule is not reading it: **clone the repo to a temp directory and run both build
  scripts.** Until 2026-08-31 that failed, and nobody knew. Note `data/*` rather than
  `data/` in the file: git does not descend into an excluded directory, so a bare `data/`
  makes every negation silently inert.
- Other ignores: `.venv/`, `__pycache__/`, `.env`, `build/`, `src/model/`
- `.gitattributes`: `* text=auto eol=lf`
- Secrets in `.env`, never committed
- Commit small and often — the history is itself evidence of the work
- `requirements.txt` kept current via `pip freeze`
- **Comment the non-obvious only.** A comment that restates the next line is noise. Keep
  the ones naming a trap, a ceiling, or a rejected alternative. Reasoning belongs in
  `docs/decisions.md`, not repeated in the source.
- **Commit messages:** short imperative subject. A body only when there is a non-obvious
  reason worth recording, not by default. No co-author trailers.
- **Write down what was tested and what the evidence was, not just the conclusion.**
  Two claims in this file were wrong until tested against raw records.

## Review tooling

Installed and available. None of it needs to run before shipping.

| Need | Command |
|---|---|
| Correctness bugs | `/code-review` |
| Security | `/security-review` |
| Redundancy, over-engineering | `/ponytail-audit` (repo) or `/ponytail-review` (diff) |
| Harsh maintainability gate | `thermo-nuclear-code-quality-review` |
| Module structure, seams | `improve-codebase-architecture` |

`ponytail` is a persistent lazy-coding mode, active by default via a session hook. Its
three review skills all state that correctness, security and performance are **out of
scope**, so it never substitutes for `/code-review`.

Architecture refactors are cheap early and dangerous late: the headline claim rests on a
specific pipeline, and a restructure that quietly changes a feature or a join breaks it
with nothing catching it. Before publishing, run the reports to know the weak spots and
**act on nothing**.

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
