# CLAUDE.md: working rules for rail-delay

Instructions for anyone, human or agent, changing this repository.

**This file holds rules, not status.** What the system currently does and what it currently
measures live in [README.md](README.md) and [docs/decisions.md](docs/decisions.md). Both this
file and the README used to carry status and both went stale, in one case claiming a sealed
test week had never been opened after it had been. One place per fact.

## What this is

A live Irish Rail delay predictor with **prediction intervals**, not point estimates. Given a
train currently running that has already reported at an upstream stop, predict how late it
will arrive at a station further along its route, as a range. Horizon is minutes to about an
hour. **One model for the whole network**, not one per train.

It does not forecast future-dated services and it does not plan journeys. The features are
derived from upstream reported delays, so no upstream report means no prediction. That limit
belongs on the site, because a visitor expecting next-week forecasts will think the system is
broken.

**Scope lock.** This is a CV and portfolio artifact, finished and deployed before the MAI
started in mid-September 2026. It is not thesis work. The MAI thesis on stochastic train delay
prediction is separate. Work that does not serve "working deployed service" is out of scope:
flag it as thesis-later and move on. Scope creep is the main way this project fails.

Success criterion: beat Irish Rail's own `ExpectedArrival` on well-covered lines, and be
explicit about the lines where the data cannot support a claim. **An honest loss is a better
result than an unverifiable win.**

## How to work on this project

**Explicability beats elegance.** The deliverable is not a system that works. It is a system
that works and that its author can defend under questioning. When those conflict the simpler
explicable option wins, and building the clever one anyway makes the project worse. Say when a
trade-off is live rather than deciding it quietly.

**Plain language first, then the precise version.** Every decision-log entry opens with a
one-line "Why this matters" that a non-specialist can follow. Prose states the idea in ordinary
words first and the exact version second. Plain-language-first is not plain-language-only: the
precise version still follows, because the real thing has to be defensible, not a simplified
story about it. Do not dumb down the statistics, the modelling or the systems design.

**Do not silently make design decisions.** Schema, features, model choice and evaluation design
belong to the author. Lay out options and trade-offs. Boilerplate that would otherwise be
googled, just write.

**Challenge weak assumptions.** No flattery, no filler.

**Explain tooling concretely**, with exact commands in their Windows and PowerShell form.

## Leakage rules, which protect the headline claim

- **Log every prediction at request time**, with timestamp, train, station, predicted quantiles
  and model version. The accuracy page is only trustworthy if the prediction provably predates
  the outcome.
- **Never regenerate historical predictions.** Recomputing what the model "would have said"
  uses today's model against a known outcome. That is leakage, and code that does it is a bug
  rather than an optimisation.
- **Never feed `ExpectedArrival` into the model.** It is the baseline being compared against.
- The nightly scorer joins yesterday's logged predictions to realised arrivals. It reads
  outcomes and never writes or recomputes predictions. This is enforced by IAM: the API may
  write the prediction prefix and not read it, the scorer may read it and not write it.

## Feature design, the load-bearing rule

**Features describe the situation, not the identity.**

Train code is not a feature. A model that learned "A218 runs two minutes down" has nothing to
say about a service launched next March, which arrives as an unknown category, and the same
problem hits any renumbered service. Use time of day, day of week, route, stops remaining,
distance to go, delay accumulated upstream today. All of those exist for a train that launched
yesterday. Get this right and new services work automatically. Get it wrong and the model fails
silently on exactly the trains people most want to ask about.

Reasoning and the rejected candidates: [docs/feature-ideas.md](docs/feature-ideas.md).
`src/features.py` is the single definition, imported by training and serving alike.

## Data rules

- **Label quality is governed by the `AutoArrival` field, not by which line a station sits on.**
  Read [docs/label-quality.md](docs/label-quality.md) and decisions D20 to D23 before writing
  anything that filters on label-quality grounds. The line-keyword approach was tried, appeared
  to confirm the official documentation, and was rejected: the apparent line effect was
  Simpson's paradox from composition. **D23 stands, flag and keep.** No record is dropped at
  ingestion; exclusion is an evaluation-time decision, reported both ways.
- ~31% of movement records never receive actual times. Handle explicitly.
- All feed times are quantised to 6-second intervals.
- The fields are `Arrival` and `ScheduledArrival`. There is no `ArrivalTime` or
  `ScheduledArrivalTime`.
- Full field reference and every known data-quality issue:
  [docs/data-dictionary.md](docs/data-dictionary.md). **Read it before writing parsing code.**

### Collecting more

- **Politeness.** No documented rate limit, and the feed is offered as-is with no support.
  Throttle to 1 to 2 requests per second, back off exponentially, honour 429 and 503, run bulk
  jobs overnight. **If throttled, slow down rather than retrying harder.**
- **Archive raw responses before parsing.** Gzipped, one file per train and date. If parsing
  logic changes, reprocess from raw. Parse-and-discard loses data permanently.
- **A missed collection run is recoverable**, because `TrainDate` is honoured historically.
  This does not apply to `ExpectedArrival`, which exists only live and cannot be backfilled.
- **More data is not automatically better.** 2020 is COVID-era with different infrastructure.
  Decide any training window empirically rather than assuming.

## Retraining policy

- **No scheduled retraining.** Retraining is a change to a working system and changes carry
  risk. One extra day on top of months of data moves the model almost not at all, so a nightly
  job is risk with no benefit.
- **Trigger on evidence, and on the product rather than just the point estimate.** Either of
  these is sufficient:
  - rolling 7-day MAE rises above the live baseline and stays there for a week;
  - **rolling 7-day interval coverage falls below 75% overall, or below 70% on any station
    group with at least 200 scored events that week, and stays there for a week.** The
    intervals are the product of a quantile model. In the first three live days MAE was healthy
    at 80s while coverage on two corridors fell from 79% to 63–69%, a degradation the MAE
    trigger cannot see (D56).
  - A **label-quality finding** is a third, non-scheduled trigger. It does not wait for a
    metric to move, because the metric may be computed against the same contamination.
  - The trigger decides that something is done, not what. Recalibration may be the answer
    rather than a full retrain.
- **Champion/challenger gate.** A new model replaces the incumbent only if it beats it on a
  recent held-out week on MAE, is not worse on any station group's median, and is not worse on
  interval coverage for any station group. **Both models are scored on identical data**: score
  the incumbent on old examples and the challenger on cleaned ones and the gate measures the
  cleaning rather than the model.
  - **"Not worse" means the paired bootstrap 95% interval on the difference excludes zero in
    the wrong direction.** A point comparison with no tolerance fails every retrain on noise.
  - **A group can veto only at n ≥ 1,100 rows**, the size needed to detect a 5-point coverage
    drop from 80% at 80% power. Below that the group is reported beside the result, not
    enforced. Derived from power, not from any group that happened to fail.
  - **Why the trigger uses 200 events and the gate 1,100.** The trigger starts an investigation,
    which is cheap, so it should fire early on thin evidence. The gate blocks a better model,
    which is expensive, so it needs evidence strong enough to be right. Same statistic, two very
    different costs of being wrong.
  - Without a gate, automated retraining is an automated way to degrade the system with nothing
    checking.
- If the trigger never fires, that is a finding rather than a gap. Publish it.

## Reporting rules for the accuracy page

- Head-to-head numbers only on **matched events**, where both the model and the operator
  produced a prediction for the same train, station and moment.
- **Always publish coverage alongside accuracy.** "27% better" without the answer rate is the
  misleading version.
- **Coverage goes per station group, beside the nominal 80%**, never as a single blended
  figure. A blended number conceals corridors in the sixties.
- **State that the intervals cover 0% of real delays over an hour** (D57), and that the scored
  predictions are scheduled samples from the generator rather than user traffic.
- The accuracy page must show `generated_at` and the model version, so a stale page is visibly
  stale rather than silently so.
- Report weak-coverage lines separately, never blended into the aggregate.
- Aggregate three ways: rolling 7-day, daily with sample size shown, and cumulative since
  launch.
- **Publish where the model loses.** The known caveats go on the page, not buried in the README.

## Stack and infrastructure rules

Python and `requests` for ingestion. Gzipped raw to Parquet to S3, with **no database**, for
the reasons in D40, because "why no database?" is an interview question. LightGBM with quantile
loss. FastAPI. AWS, serverless by default.

- **Never provision** a NAT Gateway, an Application Load Balancer, or a 24/7 RDS instance.
  These bill hourly regardless of traffic and would exhaust the credits for no benefit at this
  scale.
- A budget alarm must exist before anything is deployed.
- **The site deploys itself from GitHub Actions over OIDC.** No long-lived AWS key exists in
  the repo or in GitHub's secrets. The trust condition matches the id-bearing subject GitHub
  actually sends, which is not the form any guide shows: **read D72 before touching it.**
- The API still deploys by hand: `scripts\build_api.ps1 -Version <v>` then `sam deploy
  --template-file infra/api.yaml`.
- **The public endpoint is capped at 5 concurrent executions** (D78). Raising the account limit
  to 1000 was the precondition, because AWS refuses a reservation leaving under 100 unreserved.
  **Do not remove the reservation without replacing it with something equivalent.** The raise
  and the cap only make sense together, and the raise alone is worse than neither.
- **The API package needs three things a normal `pip install` will not give you**, all found
  the hard way and all encoded in `scripts/build_api.ps1`: two `--platform` tags, because numpy
  past 2.2.6 ships only `manylinux_2_28` and lightgbm only `manylinux2014` and either alone
  fails to resolve; `--python-version 3.13`, because the build machine runs 3.14; and a vendored
  `libgomp.so.1`, because lightgbm links against OpenMP and the Lambda runtime does not ship it.
  Without the last one the import dies at `ctypes.LoadLibrary`.
- Two free-tier thresholds move quietly and both have already been crossed: custom metrics and
  alarms are past or near the free ten. **Count them rather than trusting any prose**, with
  `aws cloudwatch list-metrics --namespace RailDelay` and `aws cloudwatch describe-alarms`.
  That is exactly how the previous numbers in this file went stale.

### Web layer

Three pages plus a landing page: predictions, accuracy, how it works. **Out of scope:** map
view, route planning, user accounts, saved stations, notifications, mobile app, dark mode
toggle. Each costs a week and adds nothing an interviewer will ask about.

- **The site has its own bucket.** Never a public prefix on the data bucket, which holds every
  prediction, score and raw capture. All four public-access blocks stay on both buckets, and
  CloudFront reads the site bucket through Origin Access Control. `accuracy.json` is pushed out
  to the site bucket by the scorer rather than served from where it is written.
- **The bucket policy gets reviewed before it deploys.** It is the only deliberately public
  grant in the project.
- Built with Tailwind v4 compiled ahead of time, TypeScript in `checkJs` mode as a check rather
  than a compiler, and no framework. **This amends D41**, which originally said no build step
  and no npm; the amendment and its reasoning are in D66. The compiled `site/app.css` is
  committed and CI refuses a copy that differs from its source.

## Conventions

- **`.gitignore` splits `data/` in two, and the split is a rule rather than a list of
  exceptions.** Raw collected data is never committed, because it is large and re-fetchable.
  Small derived artifacts required to build are committed: `data/codes.json`,
  `data/live/stations.json`, `data/models/`. **The test of the rule is not reading it: clone to
  a temp directory and run both build scripts.** Until 2026-08-31 that failed and nobody knew.
  Note `data/*` rather than `data/`, because git does not descend into an excluded directory,
  which makes every negation silently inert.
- `private/` is gitignored. Interview prep and product notes live there, out of the public repo.
- Secrets in `.env`, never committed.
- **Four requirements files, narrowest last.** `requirements-dev.txt` (a laptop) →
  `requirements.txt` (run and train) → `requirements-api.txt` and `requirements-lambda.txt`
  (what each Lambda packages). Pins are exact everywhere, because numpy, scipy and lightgbm
  decide the numbers a retrain produces. **Add a new package to the right file by hand rather
  than pasting `pip freeze` over the top**: a freeze cannot tell a linter's dependency from the
  project's, which is how the old single file ended up carrying a computer algebra system (D77).
- **Comment the non-obvious only.** A comment that restates the next line is noise. Keep the
  ones naming a trap, a ceiling or a rejected alternative. **Reasoning belongs in
  `docs/decisions.md`**, not repeated in the source.
- **Commit messages:** short imperative subject. A body only when there is a non-obvious reason
  worth recording.
- **Write down what was tested and what the evidence was, not just the conclusion.** Several
  claims in this file were wrong until tested against raw records.
- Commit small and often. The history is itself evidence of the work.

## Reliability principle

Do not aim for perfect uptime, aim for recovery. Every scheduled job should ask "what am I
missing?" and fetch that, rather than assuming the previous run succeeded. Schedulers fail,
laptops sleep, networks drop.

## Traps that will not announce themselves

- **CloudFormation cannot confirm an email subscription, so it reports success on a dead alarm
  channel.** This has happened twice. AWS reaped one unconfirmed subscription after 48 hours,
  sooner than the roughly 3 days it documents. The stack state then points at a reaped ARN,
  which is harmless on a routine redeploy but **will create a duplicate subscription if the
  alarm address ever changes**, because that forces resource replacement. Nothing in the system
  checks that an alarm has a live subscriber. Verify with `aws sns
  list-subscriptions-by-topic --topic-arn <arn>` rather than assuming a successful deploy gave
  you working alerting.
- **`api.predict_row` asks for the journey under today's Dublin date.** A train that departed
  yesterday and is still running between 00:00 and 00:30 is fetched under the wrong `TrainDate`
  and declines instead of predicting. Quiet hours start at 00:30, so the window is half an hour
  a night. The scorer already reads two partitions for this reason; the API and generator do
  not. Found by reading the code, not yet observed in scores.
- **`harvest_codes.py --from-snapshots` refuses an archive whose newest snapshot is over 24h
  old**, exiting 3. Every local archive is frozen since the cutover, so every future run trips
  the guard. That is the point: "0 new codes" from a dead folder is indistinguishable from a
  network with no new services.
- **A degraded poll cycle counts as uptime in `diff_parallel.py`**, so a partial sweep is
  compared as if it were a full one. Check `stations_failed` in the cycle records before
  blaming a collector for a missing event.
- **The generator's decline rules changed twice on 2026-09-03** (D58). Scores before and after
  are not directly comparable.
- **The sealed test week has been opened** (D74) and cannot be used again. Any future held-out
  evaluation needs new data.

## Review tooling

Installed and available. None of it needs to run before shipping.

| Need | Command |
|---|---|
| Correctness bugs | `/code-review` |
| Security | `/security-review` |
| Redundancy, over-engineering | `/ponytail-audit` (repo) or `/ponytail-review` (diff) |
| Harsh maintainability gate | `thermo-nuclear-code-quality-review` |
| Module structure, seams | `improve-codebase-architecture` |

The ponytail review skills all state that correctness, security and performance are out of
scope, so they never substitute for `/code-review`.

Architecture refactors are cheap early and dangerous late. The headline claim rests on a
specific pipeline, and a restructure that quietly changes a feature or a join breaks it with
nothing catching it. **Run the reports to know the weak spots and act on nothing.**

## Not yet

Docker, Kubernetes, Terraform, message queues, GTFS-R, weather data. Premature infrastructure
is procrastination.

## How to treat this file

These are decisions made in discussion, mostly without the repo in view. **If the code or the
data contradicts something here, stop and say so** rather than working around it silently. A
rule here that turns out to be wrong is more dangerous than no rule, because it looks
authoritative. Three rules in earlier versions of this file were wrong and were followed for
days before being caught, and the build-step rule above was a fourth: it said no npm while CI
was already running `npm ci`.
