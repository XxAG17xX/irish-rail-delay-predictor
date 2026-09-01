# irish-rail-delay-predictor

Predicts how late an Irish Rail train will arrive at a stop further along its route, as a
**range** rather than a single number.

> It is 16:30. A220 left Heuston at 16:00 for Cork and has been 1–2 minutes down at
> Kildare, Portarlington and Portlaoise. Asked about Thurles, the service answers:
> expected 17:07–17:14, most likely 17:09, 80% confidence.

One model for the whole network. The target is to beat Irish Rail's own `ExpectedArrival`
on well-covered lines, and to be explicit about the lines where the data cannot support a
claim.

Data comes from the [Irish Rail Realtime API](http://api.irishrail.ie/realtime/) — no key,
no registration, no rate limit documented and no support offered.

## Status

**Running unattended on AWS.** Collection, model and serving are done and deployed. The
three web pages are the remaining work.

- [x] API feasibility probes, field reference, known data-quality issues
- [x] Historical availability established — `TrainDate` is honoured back to at least 2007
- [x] `harvest_codes.py` / `backfill.py` — resumable, throttled collection
- [x] `parse_raw.py` — raw XML → Parquet
- [x] Label quality resolved — it follows `AutoArrival`, not the line list
- [x] `build_examples.py`, `baseline.py`, `train_quantile.py` — LightGBM quantile model
- [x] Head-to-head against the operator's own live estimates
- [x] FastAPI service on Lambda, prediction logging, nightly scorer
- [x] Cutover to the Lambda poller after a seven-day parallel run
- [ ] Three web pages: predictions, accuracy, how it works

**Dataset.** 28,706 gzipped responses, 1,087 train codes, 34 dates (25 Jun – 2 Aug 2026),
504,810 stop-level records. Collected once, not repeated — re-fetchable by date if needed.

**Model.** LightGBM quantile regression at the 10th, 50th and 90th percentiles. Twelve
features, all computable at prediction time. Trained 27 Jun – 12 Jul, validated 13–19 Jul.
The test week (20–26 Jul) has never been opened and stays closed until the end — every
number quoted anywhere is validation or live, never test.

**Against the operator.** 80.1s MAE against `ExpectedArrival` at 109.7s — a 27%
improvement over 9,077 matched comparisons covering 2,654 distinct events. Replicated on
live data on 2026-08-31: 92.3s against 124.5s, **25.9%** over 1,079 matched events, from
predictions logged before the outcomes existed.

**Honest limits, kept next to the number.**

- It answers only for a train that has already reported at an upstream stop. Measured
  live: **91.8%** of sampled in-service trains, but **38.0%** of what a visitor meets on a
  station board, because a board also lists trains that have not departed.
- The 80% interval measured **79.0%** live overall, but degrades with horizon — 78.1% at
  0–5 minutes down to **74.5%** beyond an hour. Never quote one coverage number.
- On documented weak-coverage lines the median is worse than the operator's. Reported as
  a loss, not omitted.

## Architecture

```
EventBridge ──> poller Lambda ──> S3   raw station boards + operator ETAs, every 5 min
EventBridge ──> generator ─────> S3   sampled predictions, so the scoreboard has input
                    │
                 api.py (FastAPI + Mangum, Lambda Function URL)
                    │
                    └────────────> S3   prediction log, written BEFORE the outcome exists
EventBridge ──> scorer Lambda ──> S3   nightly: joins yesterday's predictions to arrivals
```

Four Lambdas, two buckets, three CloudFormation stacks, eight CloudWatch alarms. No
database — see [decisions.md](docs/decisions.md) D40. No EC2, no RDS, no VPC, no queues.

The API is live behind a Lambda Function URL. The URL is not published here yet: every
`/predict` call makes an upstream request to Irish Rail, and there is no throttle in front
of it.

## Layout

```
src/
  harvest_codes.py   poll getCurrentTrainsXML, accumulate train codes
  backfill.py        download raw getTrainMovementsXML per (date, code)
  parse_raw.py       raw XML -> Parquet, partitioned by date
  build_examples.py  training examples at fixed horizons
  baseline.py        persistence and zero baselines
  train_quantile.py  LightGBM quantile model, versioned artifacts
  features.py        THE feature definition, imported by training and serving alike
  feedtime.py        feed time/date parsing, delay rule, lead-time bands
  poll_live.py       the poll cycle, shared by the local poller and the Lambda
  sinks.py           where a cycle's output goes: local disk, S3, or memory
  lambda_poll.py     one poll cycle as a Lambda invocation
  api.py             the prediction service
  generate.py        scheduled sampled predictions, so the scorer has input
  score.py           nightly scorer: predictions joined to realised arrivals
  prediction_log.py  fail-closed prediction logging
  hostlock.py        one collector per host
scripts/     read-only probes, surveys and the build scripts
infra/       CloudFormation/SAM templates
docs/        data dictionary, decision log, label quality, explain index
data/        raw collected data is gitignored; small build artifacts are committed
```

`data/` splits in two and the split is a rule, not a list of exceptions. Raw XML, Parquet
and poll output are never committed. The artifacts a build needs — `codes.json`,
`live/stations.json`, `models/` — are. The test of the rule is not reading it: clone to a
temp directory and run the build scripts.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requests` for ingestion, `pyarrow` for the Parquet stage, `lightgbm` and `numpy` for the
model, and `tzdata` because Windows ships no system timezone database and the poller
evaluates its schedule in `Europe/Dublin`.

## Running

Collection. Harvest across a full service day (~05:30 to after midnight), on a weekday and
again on a weekend — the timetables differ.

```powershell
python src\harvest_codes.py
python src\backfill.py --start 2026-06-25 --end 2026-07-24 --dry-run
python src\backfill.py --start 2026-06-25 --end 2026-07-24
```

Parse, build examples, baseline, train. All idempotent; `--save` persists an artifact.

```powershell
python src\parse_raw.py
python src\build_examples.py
python src\baseline.py
python src\train_quantile.py --save
```

Evaluate against the operator, and check the join before trusting it.

```powershell
python scripts\validate_join.py
python scripts\compare_to_operator.py
```

Serve locally, or score a past day.

```powershell
uvicorn api:app --app-dir src
python src\score.py --date 2026-08-31 --dry-run
```

The collectors take an exclusive lock (`src/hostlock.py`) and refuse to run concurrently:
the 2 req/s budget is per host, not per script, so running two would silently double it.

## Deploying

Each stack builds its own package, then deploys with SAM. The API's version argument is
required and is not defaulted — the artifact baked into the package must be the version
the stack names.

```powershell
powershell scripts\build_lambda.ps1
sam deploy --region eu-west-1 --template-file infra/poller.yaml

powershell scripts\build_api.ps1 -Version 20260813T221035Z-0c444e3
sam deploy --region eu-west-1 --template-file infra/api.yaml

powershell scripts\build_scorer.ps1
sam deploy --region eu-west-1 --template-file infra/scorer.yaml
```

A CloudFormation stack that creates an email SNS subscription reports success before
anyone confirms it, and AWS discards an unconfirmed subscription. Check afterwards rather
than assuming:

```powershell
aws sns list-subscriptions-by-topic --topic-arn <arn> --region eu-west-1
```

## Techniques

The ingestion scripts are deliberately defensive, because the expensive thing here is not
compute — it is elapsed download time against someone else's server. Every technique below
exists to stop a specific failure. Full reasoning and the rejected alternatives are in
[docs/decisions.md](docs/decisions.md).

### Being a good client

**Request pacing (fixed-interval rate limiting).** One timestamp says when the next
request is allowed; the code sleeps until then. Deliberately *not* a token bucket, because
a bucket saves up credit while idle and then fires a burst. Prevents hitting an
undocumented rate limit and prevents being rude to a free service.

**Adaptive rate control (AIMD — additive increase, multiplicative decrease).** A 429 or
503 doubles the gap between requests; a long run of successes shrinks it back a little at
a time. Same shape as TCP congestion control. Prevents both failure modes: hammering a
server that has asked for less, and staying permanently slow after one transient blip.

**Server-directed backoff.** If the response carries a `Retry-After` header, that value is
used instead of our own guess. Prevents guessing when the server has already told us.

**Connection reuse (HTTP keep-alive).** One `requests.Session` for the whole run instead
of a new connection per request. Prevents thousands of redundant TCP and TLS handshakes.

### Handling failure

**Error taxonomy.** Failures are sorted into three kinds and treated differently: a
network timeout (retry, don't slow down — the server never complained), a 429/503 (slow
down and retry patiently), a 404 (never retry, it will never work). Prevents the single
worst bug in naive retry code, which is retrying a rate-limit response at the same rate
and calling it "handling errors".

**Exponential backoff with full jitter.** Each retry waits a random time up to a doubling
cap, rather than a fixed doubling. Prevents synchronised retry spikes — the thundering
herd — if this ever runs from more than one place.

**Retry budgets.** Each failure kind has a maximum attempt count. Prevents an infinite
loop against a permanently broken item.

**Dead-letter log.** A pair that exhausts its retries is appended to a JSONL failure log
and the run carries on; `--retry-failures` replays that log later. Prevents one bad train
code on hour three of a four-hour run costing the remaining hour, and prevents losing the
list of what failed when the process is killed.

### Not corrupting the archive

**Atomic writes (write-then-rename).** Every file is written to a `.tmp` name and moved
into place with `os.replace()`. Prevents a Ctrl-C mid-write leaving a half-written file
that the resume check then treats as complete — corruption that stays silent until the
parser hits it weeks later.

**Idempotent resume (checkpoint-restart).** Work already on disk is skipped by a plain
file-existence check, so rerunning the same command costs nothing and interrupting is
free. Combined with atomic writes, "the file exists" reliably means "the file is
complete". Prevents re-downloading hours of data to recover from one interruption.

**Response validation before persisting.** Before archiving, the raw bytes are checked for
an XML declaration and the expected element name — no parsing, just a substring check.
ASMX services return HTTP 200 with an HTML error page often enough to matter. Prevents
archiving thousands of error pages under `.xml.gz` names and discovering it at parse time.

**Path sanitisation (allowlist).** Train codes become filenames, so anything that is not
alphanumeric, `-` or `_` is dropped and reported. Prevents a corrupted or hand-edited code
list writing outside the target directory.

**Monotonic clock for all durations.** Timing uses `time.monotonic()`, never the wall
clock. Prevents an NTP correction, a DST change, or a laptop waking from sleep from making
an elapsed time negative or skipping the throttle mid-run.

**Locale-independent date formatting.** The API's `25 jul 2026` format is built from an
explicit month table rather than `strftime("%b")`. Prevents a non-English system locale
silently turning every request into an empty result that looks exactly like "no trains ran
that day".

### Staying honest about the data

**Read-only by construction.** The survey script has no write path at all. Prevents the
one artefact that costs hours to reproduce from being damaged by a tool meant to look at
it.

**Cheap metadata reads.** File sizes come from the gzip trailer's stored length rather
than decompressing. Prevents the survey's cost growing with the archive when it only needs
a number.

**Seeded sampling.** The sample of files to parse is drawn with a fixed RNG seed, so two
runs are comparable. Prevents mistaking a different random draw for a real change in the
data.

**Domain-aware comparison.** When checking whether an actual arrival differs from the
scheduled one, records where the scheduled time is `00:00:00` are excluded — at an origin,
that means "structurally absent", not "missing". Prevents inflating the headline
label-quality number with records that were never comparable.

**Verifiable heuristics.** The empty-response threshold is a parameter, and the script
prints every distinct file size it classified as empty so the threshold can be checked
against reality. Prevents a silently wrong constant.

### Surviving interruption

**Incremental checkpointing.** The harvester rewrites its state file after every poll, so
an interrupted run loses at most one poll rather than a day.

**Idempotent merge.** Codes are merged as a set union with first-seen and last-seen dates,
so re-running or overlapping runs cannot double-count or corrupt the accumulated list.

**Quarantine on corrupt input.** An unreadable state file is moved aside with a timestamp
and the run continues from empty, rather than crashing or deleting. Prevents one bad file
ending a multi-hour harvest, and prevents destroying evidence of why it went bad.

**Responsive sleep.** Long waits are slept in one-second slices. Prevents Ctrl-C appearing
to hang for up to five minutes.

**Fixed-rate scheduling.** The next poll is scheduled relative to when the last one
*started*, not when it finished. Prevents the polling interval drifting later and later
across a long day.

**Dry-run mode.** `--dry-run` reports exactly what would be fetched and exits. Prevents
committing to a multi-hour job with the wrong arguments.

## Documentation

The decision log is the primary record; code comments point at it rather than repeating it.

- [CLAUDE.md](CLAUDE.md) — project scope, working agreement, what is deliberately out of scope
- [docs/decisions.md](docs/decisions.md) — 55 entries: what was chosen, what was rejected, why
- [docs/data-dictionary.md](docs/data-dictionary.md) — every field, provenance-tagged `[DOC]` / `[VERIFIED]` / `[INFERRED]` / `[UNKNOWN]`
- [docs/label-quality.md](docs/label-quality.md) — the echo problem, written to be read cold
- [docs/feature-ideas.md](docs/feature-ideas.md) — candidate model inputs and the rule that admits them
- [docs/explain-index.md](docs/explain-index.md) — questions this project should be able to answer, no answers given

### One theme worth reading for

Seven failures in this project shared a shape: none raised an error, and every one
produced output that looked like a correct result. Arrival times identical to the
schedule. 420 successful fetches that were a captive portal. An alarm topic with no
subscribers. A model with a 22-minute average error and a 48-second median. A harvester
reporting "0 new codes" from a folder nothing had written to. A count taken from 400 of
2,088 files and reported as complete. A `.gitignore` fix that was inert while the file sat
visibly in the repo.

Each was caught the same way: taking a number and asking what it should have been.
Section N of [explain-index.md](docs/explain-index.md) collects them.
