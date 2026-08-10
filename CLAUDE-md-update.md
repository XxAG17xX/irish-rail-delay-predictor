# CLAUDE.md update block — merge into existing file

Instructions for merging: the sections below are new decisions made after the
25 July version of `CLAUDE.md` was written. Reconcile them against what is
already in the file. Where this block contradicts the existing file, this block
wins — but say what you changed and why, do not silently overwrite.

---

## Status

The offline phase is complete. Do not redo it.

- Backfill: 26,532 gzipped XML files, 882 train codes, 30 days. Done once, not repeated.
- Model: LightGBM quantile regression, trained on 27 June – 12 July, validated
  13–19 July, tested 20–26 July. Trained locally; the artifact is uploaded to S3.
- Head-to-head vs the operator's own `ExpectedArrival`: 80.1s vs 109.7s MAE
  across 9,077 matched events — a 27% improvement. This is the headline claim
  and every future change must not silently break it.

Current phase is **serving**: get the system running in AWS with a public URL.

## What the product answers

For a train **currently in service** that has already reported at an upstream
stop: how late will it be on arrival at a given station. Horizon is minutes to
about an hour.

It does not forecast future-dated services. The features are derived from
upstream reported delays, so no upstream report means no prediction — this is
why ~56% of matched polling events were unanswerable. State this limit on the
site; a visitor expecting next-week forecasts will think the system is broken.

## Leakage rules — these protect the headline claim

- **Log every prediction at request time**, with a timestamp, train, station,
  the predicted quantiles, and the model version. Reason: the accuracy page is
  only trustworthy if the prediction provably predates the outcome.
- **Never regenerate historical predictions.** Recomputing what the model
  "would have said" uses today's model against a known outcome, which is
  leakage. Any code that does this is a bug, not an optimisation.
- **Never feed `ExpectedArrival` into the model.** It is the baseline being
  compared against; using it as an input makes the comparison meaningless.
- The nightly scorer joins yesterday's logged predictions to realised arrivals.
  It reads outcomes; it never writes or recomputes predictions.

## Retraining policy

- **No scheduled retraining.** Reason: retraining is a change to a working
  system, and changes carry risk. One extra day on top of months of data moves
  the model almost not at all, so a nightly job is risk with no benefit.
- Trigger only on evidence: rolling 7-day MAE rising above the test-week
  baseline, sustained for a week.
- **Champion/challenger gate.** A new model replaces the incumbent only if it
  beats it on a recent held-out week on MAE *and* is not worse on any
  individual line's median. Reason: without a gate, automated retraining is an
  automated way to degrade the system with nothing checking.
- If the trigger never fires, that is a finding, not a gap. Publish it:
  "trained on July 2026 data, unchanged since, no sustained degradation."

## Reporting rules for the accuracy page

- Head-to-head numbers only on **matched events** — where both the model and
  the operator produced a prediction for the same train, station and moment.
- **Always publish coverage alongside accuracy.** Reporting "27% better"
  without "answers 44% of queries" is the misleading version.
- **Never blend flagged lines into the aggregate.** On those lines the ground
  truth is fabricated, so both sides of the comparison are meaningless there.
  Report them separately and say why.
- Aggregate three ways: rolling 7-day (headline), daily with sample size shown
  (trend), and cumulative since launch (the CV number).
- Publish where the model loses. The two known caveats — worse median on
  weak-coverage lines, and the coverage gap — go on the page, not buried in
  the README.

## Data rules

Unchanged from the data dictionary, restated because they are load-bearing:

- ~40% of movement records never receive actual times. Handle explicitly.
- On the ~10 flagged weak-coverage lines, `ArrivalTime == ScheduledArrivalTime`
  is missing data, not a zero delay. Treat as missing.
- All feed times are quantised to 6-second intervals (zero violations across
  614,041 non-null delays).

## AWS and cost rules

- Serverless by default: Lambda for the poller and the API, S3 for raw
  snapshots and the model artifact, EventBridge for scheduling, CloudFront +
  S3 for the frontend, CloudWatch for logs.
- **Never provision** a NAT Gateway (~€33/mo), an Application Load Balancer
  (~€18/mo), or a 24/7 RDS instance. Reason: these bill hourly regardless of
  traffic and will exhaust the credits for no benefit at this scale.
- Batch S3 writes — one object per poll cycle, not one per station. Reason:
  30 objects every 2 minutes is ~650k PUTs/month for no gain.
- GitHub Actions authenticates to AWS via OIDC. No long-lived access keys in
  the repo or in Actions secrets.
- A budget alarm must exist before anything is deployed.

## Scope lock — web layer

Exactly three pages:

1. Predictions — pick a station, see next trains with delay ranges
2. Accuracy — the scoreboard, split by line, with the coverage caveat visible
3. How it works — architecture, data provenance, method, limitations

**Explicitly out of scope:** map view, route planning, user accounts, saved
stations, notifications, mobile app, dark mode toggle. Reason: each costs a
week and adds nothing an interviewer will ask about. The deadline is real.

## How to treat this file

These are decisions made in discussion, mostly without the repo in view. If
what you find in the code or the data contradicts something here, **stop and
say so** rather than working around it silently. A rule in this file that turns
out to be wrong is more dangerous than no rule, because it looks authoritative.
Two rules in earlier versions of this file were wrong and were followed for
days before being caught.
