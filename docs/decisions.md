# Decision log

One entry per significant design choice: what was decided, what else was on the table,
why it lost, and when. The point is to be able to defend every choice later without
re-deriving the reasoning.

Append new entries at the bottom. Do not edit an old entry to reflect a changed mind —
add a new entry that supersedes it, and note the supersession in both. Correcting a wrong
*number* is different and is done in place, with a dated correction note (see D49).

**Every entry opens with a one-line "Why this matters" that a non-specialist would follow,
before any technical detail.** The reason is in CLAUDE.md: the person who has to defend
this project in an interview is the constraint on it, not the code. An entry that only
makes sense to a reader who already understands it has failed at the one job it has.
D49-D53 carry these; earlier entries do not yet, and retrofitting them is a pending pass.

Entries D1–D10 were written on 2026-07-25 and cover decisions made up to that point, a
few of which were settled slightly earlier in the same week. Dates from D11 on are the
date the decision was actually made.

## Index

Grouped by area, newest thinking usually last within each group. A gap in a group is
worth noticing: three entries (D46-D48) were written weeks late because the reasoning
sat in code comments and nobody looked for the hole.

**Collection and politeness** 
- [D1](#d1--archive-raw-responses-before-parsing) Archive raw responses before parsing
- [D2](#d2--archive-the-live-getcurrenttrainsxml-snapshots-too) Archive the live `getCurrentTrainsXML` snapshots too
- [D3](#d3--save-empty-responses-plus-a-per-day-manifest) Save empty responses, plus a per-day manifest
- [D5](#d5--date-major-iteration-oldest-first) Date-major iteration, oldest first
- [D6](#d6--serial-pacer-not-a-token-bucket) Serial pacer, not a token bucket
- [D7](#d7--adaptive-pacing-aimd-not-a-fixed-rate) Adaptive pacing (AIMD), not a fixed rate
- [D8](#d8--three-retry-classes-handled-differently) Three retry classes, handled differently
- [D9](#d9--byte-level-body-guard-before-writing) Byte-level body guard before writing
- [D11](#d11--fetch-every-code-every-date-do-not-skip-on-the-weekday-set) Fetch every code × every date; do not skip on the weekday set
- [D12](#d12--monotonic-clock-for-every-interval-and-duration) Monotonic clock for every interval and duration
- [D13](#d13--full-jitter-on-backoff-not-plain-exponential) Full jitter on backoff, not plain exponential
- [D14](#d14--failures-go-to-an-append-only-log-not-an-exception) Failures go to an append-only log, not an exception
- [D15](#d15--train-codes-are-allowlist-sanitised-before-becoming-filenames) Train codes are allowlist-sanitised before becoming filenames
- [D16](#d16--dates-for-the-api-are-built-from-an-explicit-month-table) Dates for the API are built from an explicit month table
- [D29](#d29--poll-live-covers-30-stratified-stations-not-all-171) poll_live covers 30 stratified stations, not all 171
- [D30](#d30--an-exclusive-host-lock-because-a-docstring-is-not-a-control) An exclusive host lock, because a docstring is not a control

**Storage, parsing and correctness** 
- [D4](#d4--atomic-writes-for-every-file) Atomic writes for every file
- [D10](#d10--record-day-of-week-per-harvested-code) Record day-of-week per harvested code
- [D17](#d17--analysis-lives-in-scripts-and-is-strictly-read-only) Analysis lives in `scripts/` and is strictly read-only
- [D18](#d18--read-the-gzip-isize-trailer-instead-of-decompressing) Read the gzip ISIZE trailer instead of decompressing
- [D19](#d19--seeded-sampling-and-structural-nulls-excluded-from-comparison) Seeded sampling, and structural nulls excluded from comparison
- [D22](#d22--times-are-quantised-to-6-seconds-spike-ratios-must-account-for-it) Times are quantised to 6 seconds; spike ratios must account for it
- [D37](#d37--cfn-lint-validates-shape-not-service-rules-check-quotas-and-units-before-deploying) cfn-lint validates shape, not service rules; check quotas and units before deploying
- [D40](#d40--no-database-s3-and-parquet-instead) No database. S3 and Parquet instead
- [D44](#d44--packaging-lightgbm-for-lambda-three-problems-a-normal-pip-install-hides) Packaging lightgbm for Lambda: three problems a normal pip install hides

**Data quality and the echo problem** 
- [D20](#d20--echo-detection-by-line-name-tried-appeared-to-work-abandoned) Echo detection by line name: tried, appeared to work, abandoned
- [D21](#d21--autoarrival-is-the-echo-signal) `AutoArrival` is the echo signal
- [D23](#d23--flag-non-auto-records-do-not-drop-them) Flag non-auto records, do not drop them
- [D56](#d56--a-third-class-of-bad-label-machine-captured-arrivals-attributed-to-the-wrong-train) A third class of bad label: machine-captured arrivals attributed to the wrong train
- [D48](#d48--line-keywords-are-matched-on-word-boundaries-not-substrings) Line keywords are matched on word boundaries, not substrings

**Modelling and evaluation** 
- [D24](#d24--training-examples-at-fixed-horizons-of-1-3-5-and-10-observed-stops) Training examples at fixed horizons of 1, 3, 5 and 10 observed stops
- [D25](#d25--three-way-temporal-split-the-test-week-opens-once) Three-way temporal split; the test week opens once
- [D26](#d26--persistence-not-zero-is-the-baseline-to-beat) Persistence, not zero, is the baseline to beat
- [D27](#d27--quantile-outputs-are-sorted-before-use) Quantile outputs are sorted before use
- [D28](#d28--interval-coverage-degrades-with-horizon-never-quote-one-number) Interval coverage degrades with horizon; never quote one number
- [D31](#d31--a-model-artifact-bundles-boosters-vocabularies-and-the-feature-list) A model artifact bundles boosters, vocabularies and the feature list
- [D32](#d32--version-id-is-a-utc-timestamp-plus-the-git-commit) Version id is a UTC timestamp plus the git commit
- [D33](#d33--an-explicit-latest-pointer-not-newest-by-name) An explicit LATEST pointer, not newest-by-name
- [D34](#d34--saving-is-opt-in-via-save) Saving is opt-in via `--save`
- [D35](#d35--one-feature-definition-in-src-features-py) One feature definition, in `src/features.py`
- [D45](#d45--shared-logic-moves-to-a-module-the-moment-a-second-caller-appears) Shared logic moves to a module the moment a second caller appears
- [D46](#d46--how-the-head-to-head-against-the-operator-is-kept-fair) How the head-to-head against the operator is kept fair

**Serving** 
- [D39](#d39--prediction-log-schema-write-once-storage-and-fail-closed-serving) Prediction log: schema, write-once storage, and fail-closed serving
- [D41](#d41--plain-html-css-and-vanilla-javascript-for-the-three-pages) Plain HTML, CSS and vanilla JavaScript for the three pages
- [D42](#d42--predictions-page-loads-per-train-on-demand-no-precomputation-yet) Predictions page loads per train on demand; no precomputation yet
- [D43](#d43--the-api-s-deployment-shape-fastapi-behind-mangum-function-url-baked-artifact) The API's deployment shape: FastAPI behind Mangum, Function URL, baked artifact
- [D49](#d49--the-prediction-log-is-filled-by-a-scheduled-sampler-not-by-traffic) The prediction log is filled by a scheduled sampler, not by traffic
- [D50](#d50--the-scorer-reuses-the-offline-methodology-rather-than-approximating-it) The scorer reuses the offline methodology rather than approximating it
- [D51](#d51--three-custom-cloudwatch-metrics-for-the-generator-not-eight) Three custom CloudWatch metrics for the generator, not eight
- [D52](#d52--delay-is-anchored-to-the-stops-own-schedule-everywhere) Delay is anchored to the stop's own schedule, everywhere
- [D53](#d53--two-coverage-numbers-and-the-visitor-facing-one-is-the-headline) Two coverage numbers, and the visitor-facing one is the headline
- [D57](#d57--the-retrain-on-consistent-journeys-what-it-fixed-what-it-revealed-and-a-gate-that-cannot-pass) The retrain on consistent journeys: what it fixed, what it revealed, and a gate that cannot pass
- [D58](#d58--the-generator-refuses-out-of-envelope-questions-from-1725-utc-on-3-september) The generator refuses out-of-envelope questions, from 17:25 UTC on 3 September

**Cutover and verification** 
- [D36](#d36--the-lambda-parallel-run-is-a-time-boxed-exception-to-d30-and-it-expires) The Lambda parallel run is a time-boxed exception to D30, and it expires
- [D38](#d38--two-structural-artefacts-in-the-parallel-run-diff-and-how-to-tell-them-from-real-disagreement) Two structural artefacts in the parallel-run diff, and how to tell them from real disagreement
- [D47](#d47--a-100-join-rate-is-necessary-and-nearly-meaningless-usability-is-the-number) A 100% join rate is necessary and nearly meaningless; usability is the number
- [D54](#d54--cutover-the-lambda-poller-is-the-only-poller) Cutover: the Lambda poller is the only poller
- [D55](#d55--harvest_codes-gets-a-staleness-guard-not-a-port-to-s3) harvest_codes gets a staleness guard, not a port to S3

---

## D1 — Archive raw responses before parsing

**Decision.** Every API response is gzipped to disk untouched. Parsing is a separate,
later stage that reads from that archive.

**Alternatives.** Parse on download and store only extracted records (Parquet or
Postgres rows), skipping the raw layer entirely.

**Why rejected.** Parse-and-discard loses data permanently. The parser will change
repeatedly — new fields, changed null handling, quarantine rules for anomalies — and each
change would mean re-downloading the range at 2 req/s. Storage is cheap; 30 days of raw
XML gzips to well under a gigabyte. Re-downloading a year is 30 hours.

**Date.** 2026-07-25

---

## D2 — Archive the live `getCurrentTrainsXML` snapshots too

**Decision.** `harvest_codes.py` gzips each poll body to `data/raw/current/{UTC}.xml.gz`
in addition to extracting train codes from it.

**Alternatives.** Extract codes and discard the body — the script's only stated job is
building the code list.

**Why rejected.** This endpoint has no history. `PublicMessage` delay text and live train
positions exist only in the instant they are polled and can never be backfilled, unlike
`getTrainMovementsXML`. A few KB per poll (~300 polls/day) is a negligible price for data
that is otherwise unrecoverable, and it follows the same rule as D1.

**Date.** 2026-07-25

---

## D3 — Save empty responses, plus a per-day manifest

**Decision.** `backfill.py` writes every 200-OK response to disk including the ~209-byte
empty envelope a train returns for a date it did not run, and appends one line per fetch
to `data/raw/{date}/_manifest.jsonl` recording code, HTTP status, byte count and elapsed
time.

**Alternatives.** (a) Save everything with no manifest. (b) Detect empties by byte length
and record them in a separate `known_empty.json` rather than writing a file.

**Why rejected.** (b) creates a second source of resume state that can drift out of sync
with the filesystem — the exact class of bug that costs a re-download to diagnose. (a) is
correct but leaves no way to survey coverage without opening thousands of XML files; the
manifest answers "how many trains actually ran on this date" from one pass over a text
file. Verified empirically: `P345` on 2026-07-23 returns a valid self-closing
`ArrayOfObjTrainMovements` at 209 bytes.

**Date.** 2026-07-25

---

## D4 — Atomic writes for every file

**Decision.** All output — gzipped responses, `codes.json` — is written to a `.tmp`
sibling and moved into place with `os.replace()`.

**Alternatives.** Write directly to the destination path.

**Why rejected.** Resume in `backfill.py` is a plain `Path.exists()` check. Without
atomicity, a Ctrl-C or power loss mid-write leaves a truncated file that the skip check
then treats as complete. The corruption is silent and surfaces at parse time, weeks
later, with no way to tell which files are affected. `os.replace()` makes "the file
exists" imply "the file is complete".

**Date.** 2026-07-25

---

## D5 — Date-major iteration, oldest first

**Decision.** `backfill.py` loops over dates on the outside and codes on the inside, so
each day completes before the next begins.

**Alternatives.** (a) Code-major — all dates for one train, then the next train.
(b) Date-major but newest date first.

**Why rejected.** An interrupted or abandoned run should leave whole usable days rather
than a uniformly sparse range; partial days are useless for per-day aggregates and for
the label-quality analysis. Code-major gives complete journey histories per service,
which is only useful for eyeballing one train's variance — a smaller need. Newest-first
was a genuine contender given that recent data is more relevant, but plain chronological
order keeps the on-disk range contiguous from a fixed start and is easier to reason about
when extending backwards later.

**Date.** 2026-07-25

---

## D6 — Serial pacer, not a token bucket

**Decision.** One monotonic `next_allowed` timestamp, measured from request start:
sleep until it, then set it to `now + interval`. One thread, one request in flight.

**Alternatives.** A token bucket, the usual off-the-shelf rate limiter.

**Why rejected.** A bucket with capacity greater than 1 accumulates credit while idle and
then discharges it as a burst — precisely the behaviour to avoid against a service with
no published rate limit and no support contract. A bucket of capacity 1 is just this
pacer with more machinery. Measuring the interval from request *start* also means a slow
response costs no additional sleep, so the API's own latency self-limits us.

**Date.** 2026-07-25

---

## D7 — Adaptive pacing (AIMD), not a fixed rate

**Decision.** The interval is mutable state. A 429/503 doubles it, capped at 30 s;
50 consecutive successes shave 0.05 s off it, floored at the configured base.

**Alternatives.** (a) A constant interval, ignoring load signals. (b) Back off on
throttling and never recover.

**Why rejected.** (a) ignores the only feedback the server gives us. (b) is worse than it
sounds: one transient 503 early in a run permanently converts a 2.5-hour job into an
overnight one. Recovering instantly instead would re-trip whatever throttle fired. The
multiplicative-decrease / additive-increase shape is TCP congestion control, chosen
because it is the well-understood answer to exactly this trade-off.

**Date.** 2026-07-25

---

## D8 — Three retry classes, handled differently

**Decision.** Failures are classified and treated separately. *Transport* (timeouts,
connection resets): exponential backoff with full jitter, 5 attempts, global pace
unchanged. *Throttled* (429, 502, 503, 504): widen the global pace, honour `Retry-After`,
retry patiently up to 8 times. *Permanent* (other 4xx): no retries at all.

**Alternatives.** A single retry loop with one backoff policy for anything that isn't a
success.

**Why rejected.** The three cases carry opposite information. A timeout means the network
flaked and the server never complained, so slowing every subsequent request would be an
unwarranted tax on the whole run. A 429 means the server is explicitly asking for less,
so retrying at the same cadence — the natural behaviour of a single unified loop — is
retrying harder, which is what the project's politeness rule forbids. A 404 will never
succeed, so any retry at all is pure noise at the server.

**Date.** 2026-07-25

---

## D9 — Byte-level body guard before writing

**Decision.** Before archiving, check the first 4 KB: strip any UTF-8 BOM, require
`<?xml` at the start, and require `objtrainmovements` as a case-insensitive substring.
No decode, no `ElementTree`, no parsing.

**Alternatives.** (a) Trust HTTP 200 and archive whatever arrives. (b) Parse the XML
properly to validate it.

**Why rejected.** ASMX services return 200 with an HTML error page often enough that (a)
means archiving thousands of error pages under `.xml.gz` names, discovered only at parse
time. (b) violates the raw-bytes-only rule and would make the downloader depend on a
schema that is still being characterised. The substring check matches both the root
element `ArrayOfObjTrainMovements` and the child `objTrainMovements`, and was verified
against a real empty response, which passes.

**Date.** 2026-07-25

---

## D10 — Record day-of-week per harvested code

**Decision.** `data/codes.json` stores `first_seen`, `last_seen` and the set of weekday
names each code has been observed on.

**Alternatives.** (a) `first_seen` and `last_seen` only. (b) Also store an observation
count per code.

**Why rejected.** Weekday and weekend timetables differ, so a code list harvested on one
kind of day is systematically incomplete — the weekday set makes that visible directly
instead of leaving it to be rediscovered from backfilled data later, and lets `backfill.py`
eventually skip fetches that are known-futile. The observation count in (b) is a proxy for
journey duration, not a clean signal, and would invite over-reading.

**Date.** 2026-07-25

---

## D11 — Fetch every code × every date; do not skip on the weekday set

**Decision.** `backfill.py` requests every (date, code) pair in the range and archives the
empty responses that come back for services that did not run. The `days_of_week` field
from D10 is recorded but not used to skip fetches.

**Alternatives.** Skip a code on a date whose weekday it has never been observed running
on — roughly 600 requests saved per weekend day, a meaningful fraction of a 30-day run.

**Why rejected (for now).** The costs are wildly asymmetric. A wasted request costs half a
second and a 209-byte file. A wrongly skipped one costs a training example that is gone
silently and permanently: the gap is indistinguishable from "that train genuinely did not
run", so no later pass can detect it, and D3's manifest would record the absence as a
legitimate empty. The weekday set is only as good as the harvest behind it, and a code
list gathered on a single Saturday would classify every Mon–Fri commuter service as
weekend-only and drop the busiest part of the network.

**Trigger to revisit.** Once `codes.json` reflects several full service days spanning both
weekdays and weekends, and the backfilled data confirms that codes marked weekday-only do
in fact return empties on weekends. Until that check has been run against real data, the
optimisation is trading a certain small saving for an undetectable loss.

**Date.** 2026-07-25

---

## D12 — Monotonic clock for every interval and duration

**Decision.** All pacing, backoff, ETA and elapsed-time arithmetic uses
`time.monotonic()`. Wall-clock time (`datetime.now()`) is used only for timestamps
written into files, never for measuring a gap.

**Alternatives.** Use wall-clock time throughout, which is what most examples do.

**Why rejected.** The wall clock can jump — NTP correction, a DST transition, a laptop
resuming from sleep. A backfill run is hours long and will cross at least one of those.
A backwards jump makes an elapsed time negative and can make the pacer sleep for what it
computes as hours; a forward jump silently skips the throttle. The monotonic clock only
ever moves forward, which is the only property the pacing logic actually needs.

**Date.** 2026-07-25

---

## D13 — Full jitter on backoff, not plain exponential

**Decision.** Backoff sleeps a random duration in `[0, 2^(n-1) * base)` rather than
exactly `2^(n-1) * base`.

**Alternatives.** Plain exponential backoff, or "equal jitter" (half fixed, half random).

**Why rejected.** Plain exponential means every client that failed at the same moment
retries at the same moment, so the retry itself arrives as a spike — the thundering-herd
problem. We are currently one client, so this buys little today, but it costs one function
call and it stops the retry pattern from being a synchronised burst if the script is ever
run from two machines or alongside anything else. Full jitter is the variant AWS measured
as best for total completion time.

**Date.** 2026-07-25

---

## D14 — Failures go to an append-only log, not an exception

**Decision.** A pair that exhausts its retries is written as one JSON line to
`data/logs/backfill_failures.jsonl` and the run continues. `--retry-failures` replays that
log, rotating the old file aside first.

**Alternatives.** (a) Raise and stop the run on the first unrecoverable failure.
(b) Keep failures in memory and print a summary at the end.

**Why rejected.** (a) means one bad train code on hour three of a four-hour run costs the
remaining hour. (b) loses the list entirely if the process is killed, which is the case
most likely to produce failures in the first place. This is the dead-letter-queue pattern:
the bad item leaves the main path immediately, is recorded durably with enough context to
retry, and gets processed separately. Rotating the log on retry stops a second retry pass
re-walking items that have since succeeded.

**Date.** 2026-07-25

---

## D15 — Train codes are allowlist-sanitised before becoming filenames

**Decision.** `load_codes()` keeps only codes that are alphanumeric plus `-` and `_`, and
reports how many it dropped.

**Alternatives.** Trust the codes file, since we wrote it ourselves.

**Why rejected.** The code goes straight into a write path. A value containing `..` or a
path separator — from a corrupted file, a hand-edit, or a future feed change — would write
outside the target directory. This is standard path-traversal defence, and the argument
"we control the input" is exactly the assumption that stops being true later. The
allowlist form is deliberate: enumerate what is permitted rather than trying to enumerate
what is dangerous.

**Date.** 2026-07-25

---

## D16 — Dates for the API are built from an explicit month table

**Decision.** `api_date()` formats `25 jul 2026` from a hardcoded tuple of month
abbreviations rather than `strftime("%d %b %Y")`.

**Alternatives.** Use `strftime`, as the original probe scripts do.

**Why rejected.** `%b` is locale-dependent. On a machine with a non-English locale it
emits `juil` or `Juli`, the API returns nothing, and every file in the run is a 209-byte
empty envelope — a failure that looks exactly like "those trains did not run" and would
not be caught by any check we have. A nine-element tuple removes the dependency entirely.

**Date.** 2026-07-25

---

## D17 — Analysis lives in `scripts/` and is strictly read-only

**Decision.** `src/` holds the pipeline that writes data. `scripts/` holds one-off probes
and surveys that only read. `inspect_raw.py` opens files for reading and has no write path
at all.

**Alternatives.** One directory for everything, or let the inspector cache its results
next to the data.

**Why rejected.** The archive is the one artefact that is expensive to reproduce — hours
of throttled downloading. Anything that surveys it should be incapable of damaging it, and
that is easiest to guarantee structurally rather than by care. A cache would also become a
second source of truth that can go stale against the files it describes.

**Date.** 2026-07-25

---

## D18 — Read the gzip ISIZE trailer instead of decompressing

**Decision.** `inspect_raw.py` gets each file's uncompressed size from the last four bytes
of the gzip member rather than inflating it.

**Alternatives.** Decompress every file to measure it.

**Why rejected.** The size columns are needed for every file, but parsing is only needed
for the sample. Inflating 18k files to count bytes is minutes of pointless I/O that grows
with the archive. The trailer stores the uncompressed length exactly, for single-member
files under 4 GB, which ours are. Trade-off accepted: it would be wrong for multi-member
gzip, which nothing in this project produces.

**Date.** 2026-07-25

---

## D19 — Seeded sampling, and structural nulls excluded from comparison

**Decision.** `inspect_raw.py` samples files with a seeded RNG, and when comparing
`Arrival` against `ScheduledArrival` it skips records where the scheduled value is
`00:00:00`.

**Alternatives.** (a) Unseeded random sampling. (b) Take the first N files.
(c) Compare every record with a populated arrival.

**Why rejected.** (a) makes two runs incomparable, so you cannot tell a real change in the
data from a different draw. (b) biases toward whatever sorts first, which here is
alphabetical by train code and therefore by service type. (c) is a correctness bug rather
than a preference: `00:00:00` at an origin means the arrival is structurally absent, not
missing, so every such record would be counted as a difference and inflate the headline
number. See data-dictionary.md section 3.

**Date.** 2026-07-25

---

## D20 — Echo detection by line name: tried, appeared to work, abandoned

**Decision.** Detect schedule echoes by flagging locations whose name matches one of the
ten lines documented as weakly covered, then compare exact-match rates against everywhere
else. **Superseded by D21.** Recorded because the sequence matters more than the answer.

**What happened.** The test ran and looked like clean confirmation: flagged lines matched
the schedule exactly 21.23% of the time (3,439 comparable records) against 2.73%
everywhere else (316,541). Eightfold gap, in the direction the documentation predicted.
The obvious next step was to widen the keyword list to cover the rest of the ten lines and
start distrusting those records.

**Why it was abandoned.** Splitting the same data by `AutoArrival` reversed the result.
Within machine-captured records, flagged lines echo *less* than unflagged — 0.99% against
2.38%. The aggregate gap was Simpson's paradox: flagged lines carry 46.99% non-auto
records against 1.54% elsewhere, and 97.53% of their exact matches sit in that one cell.
Give them the network's normal auto/non-auto mix and their rate falls to 1.65%, better
than average.

Two further failures, once the right comparison existed. **It misses most of the
problem** — 75.14% of non-auto records are on lines the documentation never flagged.
**It condemns good data** — 1,823 machine-captured records on flagged lines that echo
below the network average. Name matching was also only ever a proxy: the documentation
lists *lines*, the data has *locations*, and intermediate stops like Carrigtwohill on the
Cobh line never matched at all.

**The lesson worth keeping.** The first result agreed with the documentation, which is
exactly why it went unchallenged for a whole analysis cycle. Confirmation is not
verification. What broke it was not a better idea but a per-record signal that allowed the
aggregate to be decomposed.

**Date.** 2026-07-28

---

## D21 — `AutoArrival` is the echo signal

**Decision.** Schedule-echo risk is identified per record via `AutoArrival`, not by line,
location, or name. The keyword list is retained only as a documentation cross-reference.

**Alternatives.** The line-keyword approach of D20; a per-location echo rate learned from
the data; no echo handling at all.

**Why rejected.** `AutoArrival` separates the data far more sharply than any geographic
proxy: 2.37% exact on machine-captured records (313,479) against 29.43% on non-auto
(6,501). It is present on every record carrying an arrival, needs no name matching, and
works on the 41 locations whose `LocationFullName` is empty. A learned per-location rate
would be circular — it would use the statistic we are trying to explain as its own
explanation — and could not distinguish an echoing location from a genuinely punctual one.

**Date.** 2026-07-28

---

## D22 — Times are quantised to 6 seconds; spike ratios must account for it

**Decision.** Any density or histogram analysis of arrival delay divides by the number of
*reachable* 6-second buckets, not by the number of seconds in the window.

**What was measured.** `Arrival` seconds, `ScheduledArrival` seconds, and the resulting
delay are **100.00%** divisible by 6 across all 319,980 comparable records — every one,
no exceptions. Only ten distinct second-values exist within a minute.

**Why it matters.** A first pass looked for a spike at exactly zero delay against the
surrounding ±60 seconds and found ratios of 6.0–9.5 at all twenty of the busiest
locations, including the best-covered DART stations. That looked like network-wide
echoing. It was an artefact: five of every six second-values in the window are
structurally empty, so dividing by 120 rather than 20 understates the baseline sixfold.
Corrected, the same locations sit at **0.99–1.58** — no meaningful excess. Booterstown at
1.58 and OSSRY at 1.53 are mildly elevated and worth a look; nothing else is.

**Open, marked [INFERRED] not [VERIFIED].** Six seconds is one tenth of a minute, so the
source system plausibly stores decimal minutes and converts on output. That explains the
observation but is not established by it. The quantisation is fact; the reason is a guess.

**Second-order note.** Quantisation also raises the coincidence floor — with ten possible
second-values instead of sixty, exact matches by luck are commoner than to-the-second
timing would suggest. This is part of why 2.37% of trusted records still match exactly.

**Date.** 2026-07-28

---

## D23 — Flag non-auto records, do not drop them

**Decision.** `AutoArrival` is carried through the pipeline as a column. No record is
excluded at ingestion. Whether to exclude non-auto records is an evaluation-time decision,
and results get reported both ways.

**Alternatives.** (a) Drop every `AutoArrival=0` record during parsing. (b) Drop only the
exact matches within the non-auto group. (c) Ignore the field.

**Why rejected.** (a) destroys far more than it fixes: 70.57% of non-auto records are not
exact matches and are almost certainly genuine observations that happened to be entered by
hand. Removing 1,913 echoes would cost roughly 4,588 real labels. Worse, it falls hardest
where coverage is already thinnest — non-auto is 46.99% of arrivals on the flagged lines,
so the cut would come close to erasing Cork, Tralee and Westport from the dataset, which
are exactly the places an honest answer is most wanted. (b) is more targeted but bakes an
unproven assumption into the data itself: an exact match is suspicious, not proven fake,
and the 2.37% coincidence floor means some of them are real. (c) ignores the strongest
signal available.

Consistent with the quarantine-not-delete policy already set for anomalous records in
data-dictionary.md 5.3. Deletion at ingestion is irreversible; a column is not.

**Date.** 2026-07-28

---

## D24 — Training examples at fixed horizons of 1, 3, 5 and 10 observed stops

**Decision.** Each journey yields one example per (vantage stop × horizon in {1,3,5,10}
observed stops ahead). Horizons index *observed* stops, not route positions, and the true
route distance plus the scheduled seconds from vantage to target are carried as columns.
961,606 examples from 18,477 journeys.

**Alternatives.** (a) All (vantage, target) pairs — 3,337,012 examples. (b) A fixed number
of randomly sampled pairs per journey. (c) Next observed stop only — 295,648.

**Why rejected.** (a) is the tempting one and the trap. The median journey has 18 observed
stops, so all-pairs yields 187 examples per journey that share a single underlying delay
realisation — a train five minutes down all afternoon appears 187 times. The effective
sample size stays near 18,000, so any standard error computed on 3.3M is fiction. It also
silently reweights the data toward long runs: a 35-stop intercity produces 595 examples
against a commuter hop's 3, which is precisely the DART/intercity asymmetry recorded in
feature-ideas.md. (c) fits a horizon-1 model that would have to extrapolate to the
multi-stop questions the service exists to answer. (b) is statistically the cleanest but is
an unconventional design that would need defending, and it gives up clean per-horizon
evaluation.

Fixed horizons bound the multiplier to ~4 per vantage point and make MAE reportable at
each horizon separately, which turned out to matter — see the baseline result below.

**Why observed stops rather than route positions.** Indexing by `LocationOrder + h` would
require that exact position to have reported, so the example set would be drawn only from
well-covered stretches — conditioning the training data on the coverage problem the
project exists to handle. `horizon_route_stops` preserves the true distance so nothing is
hidden.

**Date.** 2026-07-28

---

## D25 — Three-way temporal split; the test week opens once

**Decision.**

```
train        2026-06-27 .. 2026-07-12   16 dates   500,243 examples   52%
validation   2026-07-13 .. 2026-07-19    7 dates   230,966 examples   24%
test         2026-07-20 .. 2026-07-26    7 dates   230,397 examples   24%
```

All tuning and model selection go against validation. The test week is read once, at the
end. `baseline.py` defaults to validation and prints a warning when `--split test` is
passed, so opening it is deliberate rather than incidental.

2026-06-25 and 06-26 are excluded — 34 journeys each from the original 36-code test slice
against ~880 on a normal weekday.

**Alternatives.** (a) Random split. (b) Two-way train/test with one held-out week.
(c) A three-date test window, maximising training data.

**Why rejected.** (a) is disqualified outright: examples from the same journey would land
on both sides of the split, so the model would be scored on journeys it had partly seen.
Even splitting by journey rather than by row would leak the day's weather and disruptions
across the boundary. (b) leaves nowhere to tune — every hyperparameter choice checked
against the held-out week contaminates it, and the final number is then optimistic by an
unknown amount. (c) is not a representative week: Fri/Sat/Sun would leave the headline
dominated by weekend service, and Sundays run 332 journeys against 880 on a weekday.

**Why both held-out windows are whole calendar weeks.** Each contains every day of the
week exactly once. A window that is not week-aligned differs from the training data in
service mix as well as in date, and the two effects cannot be separated afterwards. The
two windows came out within 0.3% of each other in size, which is a useful check that they
are comparable.

**Date.** 2026-07-28

---

## D26 — Persistence, not zero, is the baseline to beat

**Decision.** The naive predictor a model must beat is **persistence** — the delay
observed at the vantage stop carries forward unchanged. The zero-delay predictor is
reported alongside as context, not as the bar.

**Measured on validation**, AutoArrival=1 at both ends, 226,370 examples:

| Predictor | MAE | median AE | bias |
|---|---|---|---|
| zero | 184.9 s | 90 s | −140.3 s |
| persistence | **103.3 s** | **48 s** | −32.8 s |

**Why it matters.** Beating zero only proves the timetable is not a perfect predictor,
which is not in doubt. Persistence already removes 44.1% of the error, so a model that
beats zero but not persistence has earned nothing.

**Two findings that shape what the model has to do.**

Persistence decays with horizon exactly as autocorrelation predicts — it beats zero by
63.5% at one stop ahead but only 23.1% at ten, and by only 3.5% beyond an hour of
scheduled travel, where its median error is actually *worse* than predicting on-time
(156 s against 144 s). **The model's value is concentrated at long horizons.** Short-range
prediction is close to solved by persistence at 45.1 s MAE.

Both predictors are biased negative — persistence by −32.8 s — meaning delay systematically
*grows* along a journey. That is a learnable pattern, not noise, and a model that does
nothing but add a horizon-dependent constant to persistence should already beat it.

**Correction 2026-07-28:** the negative bias is largely *expected*, not a defect. A q0.50
model on a right-skewed delay distribution will show a negative mean signed error even
when perfectly calibrated, because the median sits below the mean. The trained model's
coverage numbers (D28) confirm the quantiles are honest. The claim that a constant offset
would beat persistence was too strong.

**Date.** 2026-07-28

---

## D27 — Quantile outputs are sorted before use

**Decision.** The three quantile predictions are sorted per row so that
q0.10 <= q0.50 <= q0.90 always holds.

**Alternatives.** (a) Leave them as fitted. (b) Fit a single multi-quantile model that
enforces monotonicity structurally.

**Why rejected.** (a) is indefensible in a service that quotes intervals: an interval
whose lower bound exceeds its upper bound is not a weaker answer, it is a broken one.
(b) is the principled fix and worth revisiting, but it changes the model rather than the
post-processing and is not a step to take before the untuned baseline is understood.

Sorting is the standard remedy (quantile rearrangement, Chernozhukov et al.) and is free:
sorting a set of quantile estimates weakly reduces the pinball loss of every one of them,
so it cannot make calibration worse. Overall coverage moved 80.0% -> 80.2%.

**The original diagnostic understated the problem by about seventy-fold.** The first run
reported 33 crossings, but that counted only the extreme case q0.10 > q0.90. Counting all
non-monotonic rows — including q0.50 falling outside its own interval — gives **2,355 of
226,370 rows, 1.040%**. Roughly one prediction in a hundred was internally inconsistent,
not one in seven thousand. Worth remembering as a lesson about what a check actually
measures.

**Date.** 2026-07-28

---

## D28 — Interval coverage degrades with horizon; never quote one number

**Decision.** Coverage is reported **per horizon**, never as a single figure. Any
user-facing or writeup claim of "80% confidence" must be qualified by horizon, or the
interval must be widened until the claim holds at the horizon being served.

**Measured** on validation, AutoArrival=1 both ends, untuned model, after sorting:

| Subset | Coverage | Median width |
|---|---|---|
| overall | 80.2% | 154 s |
| horizon 1 | 80.6% | 67 s |
| horizon 3 | 80.0% | 141 s |
| horizon 5 | 80.2% | 208 s |
| horizon 10 | 79.3% | 285 s |
| 0–5 min | 80.9% | 56 s |
| 5–15 min | 80.1% | 139 s |
| 15–30 min | 80.1% | 249 s |
| 30–60 min | 78.8% | 349 s |
| **60+ min** | **75.0%** | 468 s |

Misses split 9.6% below q0.10 and 10.2% above q0.90 — balanced, so the problem is interval
*width*, not skew.

**Why this is a limitation and not a curiosity.** The headline 80.2% is an average over a
query mix dominated by short horizons — 60+ minute queries are 4,210 of 226,370 examples,
1.9%. Quoting it uniformly would mean telling a passenger asking about a stop an hour away
that the range holds 80% of the time when it actually holds 75%. One arrival in four falls
outside a range advertised as containing four in five. That is the specific failure mode
CLAUDE.md's success criterion warns about: being honest about where the data cannot
support a claim matters more than the headline number.

It is also exactly where the service is most useful. Nobody needs a prediction interval
for a train two minutes away.

**Not yet addressed.** The model is untuned and this is the first fit. Candidate remedies:
horizon-conditional calibration, a monotone multi-quantile model (D27), or simply
declining to serve long-horizon intervals until coverage holds. Decide after tuning, not
before.

**Date.** 2026-07-28

---

## D29 — poll_live covers 30 stratified stations, not all 171

**Decision.** `poll_live.py` polls a stratified subset of 30 stations defined in
`config/poll_stations.toml`, in seven groups: Dublin hubs (4), DART (5), Maynooth commuter
(2), Kildare commuter (3), Cork-corridor intercity (5), other intercity (3), and
documented weak-coverage lines (8). Every captured record carries its `station_group`.
`--all-stations` restores the full sweep.

**Alternatives.** (a) All 171 stations. (b) A random or top-by-volume subset of 30.

**Why rejected — and the reason is measurement, not politeness.** The claim this project
is aiming at is "we beat the operator's `ExpectedArrival` on well-covered lines, and here
is where we cannot". That is a claim *per kind of line*, so it needs comparison events on
each kind. A random 30 would be overwhelmingly Dublin commuter stops — that is simply where
most stations are — and would leave the weak-coverage lines with too few events to support
any statement at all. The one place an honest answer matters most is the place uniform
sampling thins out first. Top-by-volume is worse still: it selects for exactly the
well-covered stations and would guarantee a flattering result.

Each group earns its place against a specific question. DART is the best-covered part of
the network and therefore the hardest case in which to beat the operator. The Cork
corridor supplies the 60+ minute horizons where our interval coverage falls to 75% (D28).
Maynooth separates "suburban" from "electrified" as explanations for any accuracy
difference. Kildare shares track between commuter and intercity services, which is where
inter-train delay propagation should be visible. Galway is in there because the coverage
analysis found it at 38.8% exact-match without any mention in data-dictionary 5.1.

The politeness gain is real but secondary: 31 requests per cycle against 172, roughly
7,000 requests a day instead of 39,000.

**Cost accepted.** Journeys are only observed where they touch these 30 stations, so
per-journey trajectories are sparser than a full sweep would give. That is the right trade
while the question is "how do we compare against the operator by line type" rather than
"reconstruct every journey".

**Date.** 2026-07-28

---

## D30 — An exclusive host lock, because a docstring is not a control

**Decision.** `harvest_codes.py`, `backfill.py` and `poll_live.py` each take an exclusive
lock (`src/hostlock.py`) before making requests, and refuse to start if another holds it.
`--force-lock` overrides.

**Alternatives.** (a) Keep documenting the rule in each docstring, as before. (b) A shared
cross-process rate limiter that lets all three run at a combined 2 req/s.

**Why rejected.** (a) had been the approach since the first script, and every one of them
carried the warning. A warning nobody reads at 2am is not a control, and the failure is
silent — nothing in either script's output would reveal that the host was being hit at
4 req/s. (b) is the more capable design and would let a backfill run alongside the live
poller, but it needs shared state updated on every request rather than once a cycle, and
the throughput it buys is not currently needed.

**Staleness by heartbeat, not by PID liveness.** Checking whether a process still exists is
awkward across platforms and unsound anyway, since PIDs get reused. The holder instead
touches the lock file each cycle, and a lock with no heartbeat for 15 minutes is treated as
abandoned and taken over with a printed notice. Fifteen minutes is comfortably longer than
any poll cycle or backfill progress interval, so a live holder is never mistaken for a dead
one.

Verified: with a lock held, `harvest_codes.py --once` exits 2 with a message naming the
holder. `backfill.py` acquires only after the resume check, so a run with nothing to fetch
never contends for it.

**Date.** 2026-07-28

---

## D31 — A model artifact bundles boosters, vocabularies and the feature list

**Decision.** One artifact directory holds the three quantile boosters *and* the
categorical vocabularies *and* the ordered feature list, in LightGBM's native text format,
with a `manifest.json` carrying a sha256 per file. Loading verifies every checksum and
refuses a mismatch.

**Alternatives.** (a) Save only the boosters and rebuild vocabularies from the training
data at load time. (b) Pickle the whole Python object graph.

**Why rejected.** (a) is the dangerous one, and it fails silently. Categorical features are
encoded as integers via vocabularies built from whatever training rows were present at fit
time. Rebuild them later from a different slice — one more day of data, one fewer station —
and `CNLLY` maps to a different integer than the model was trained on. The model loads. It
predicts. It returns plausible-looking seconds. Every number is wrong and nothing raises.
No test that only checks "does it run" would catch it. (b) breaks across library versions
and makes the artifact executable code, which is a poor thing to fetch from S3 later.

Native text is stable, diffable and version-independent. The checksums exist because a
truncated download is otherwise indistinguishable from a valid model.

**Verification is part of the decision.** After saving, the artifact is immediately
reloaded and its predictions compared bit-for-bit against the run that produced it. That
check is what would catch the vocabulary bug this bundling prevents, so it runs every time
rather than being a test someone remembers to write.

**Date.** 2026-08-13

---

## D32 — Version id is a UTC timestamp plus the git commit

**Decision.** `20260813T220924Z-eb39350`, with a `-dirty` suffix when the working tree has
uncommitted changes.

**Alternatives.** (a) Timestamp alone. (b) A content hash of the artifact. (c) Sequential
integers.

**Why rejected.** CLAUDE.md's leakage rules require the model version on every logged
prediction. A timestamp alone (a) tells you *when* but not *what code* — you would have to
cross-reference the git history by date to find out what produced a prediction, and that
mapping is ambiguous if you trained twice in a day. (b) is reproducible and deduplicating
but opaque: you cannot tell which of two versions is newer, which matters when reading a
prediction log. (c) needs a registry and races if anything ever trains concurrently.

The `-dirty` suffix is the useful part in practice: it makes "this model came from code
that was never committed" visible in the artifact name rather than something you discover
later while trying to reproduce a number.

**Date.** 2026-08-13

---

## D33 — An explicit LATEST pointer, not newest-by-name

**Decision.** `data/models/LATEST` contains the version string. It is written last and
atomically, after the artifact directory is in place.

**Alternatives.** Sort the directory names and take the newest — timestamps sort correctly,
so this needs no extra state.

**Why rejected.** Rolling back would mean deleting or renaming a good artifact, which
destroys the thing versioning exists to keep. With a pointer, a rollback is a one-line
edit and both versions survive. It also makes "which model is serving" an explicit,
auditable fact rather than an emergent property of filenames.

Ordering matters: because LATEST is written after the directory, a save that fails partway
leaves the pointer naming the previous good version rather than a half-written one.

**Amended 2026-08-25 (see D40).** The rationale above claimed LATEST makes "which model is
serving" an explicit auditable fact. That is no longer true and should not be relied on.
The serving version is pinned as a CloudFormation parameter, so LATEST now answers only
"what did I last train". Two different questions with two different answers: LATEST for
offline work (`--load latest`), the pinned parameter for production.

**Date.** 2026-08-13

---

## D34 — Saving is opt-in via `--save`

**Decision.** `train_quantile.py` writes an artifact only when asked. Default behaviour is
unchanged.

**Alternatives.** Always save; or save unless `--no-save`.

**Why rejected.** Most runs are exploratory — changing a feature, checking a breakdown,
re-reading a number. Persisting each one fills `data/models/` with near-identical artifacts
nobody will ever load, and makes LATEST churn for reasons that have nothing to do with
serving. A model you intend to log predictions against is a deliberate choice, so the flag
matches the intent. Opt-out (`--no-save`) has the same clutter problem, since the opt-out
is exactly what you forget when iterating.

**Date.** 2026-08-13

---

## D35 — One feature definition, in `src/features.py`

**Decision.** `NUMERIC`, `CATEGORICAL` and `FEATURES` are defined once and imported by both
`train_quantile.py` and `compare_to_operator.py`. Membership is decided by a single rule:
**every feature must be computable at prediction time.**

**What went wrong.** The two files each kept their own copy. `compare_to_operator.py`
excluded `horizon_observed_stops` because it counts stops that *did* report — knowable only
after a journey finishes, never at the moment of a live request. `train_quantile.py` still
included it. So the saved artifact carried thirteen features while the honest comparison
used twelve, and **the persisted model could not have served a live request.** Nothing
detected this. It surfaced only because a human read both files.

**Why one definition rather than two coordinated ones.** Two lists that must agree, kept in
separate files, will diverge again — that is what just happened. Collapsing to the serving
set costs 0.28% of gain (the measured importance of `horizon_observed_stops`), and
`horizon_route_stops` carries the same information in a form that is actually knowable.
Paying 0.28% to make a whole class of bug impossible is a good trade.

**What is kept but not used as input.** `horizon_observed_stops` remains a column in the
examples Parquet and is loaded under `REPORTING` for the per-horizon evaluation breakdown,
which is an offline question. `features.EXCLUDED` records each exclusion with its reason
next to the code, so the rationale does not live only in a commit message.

**Cost accepted.** Validation MAE moved 75.9s to 76.1s and coverage 80.15% to 80.23%. The
head-to-head against the operator is unchanged at 80.1s vs 109.7s, because that comparison
already used the twelve-feature set.

**Date.** 2026-08-13

---

## D36 — The Lambda parallel run is a time-boxed exception to D30, and it expires

**Decision.** During cutover the Lambda poller and the local poller run **at the same
time**, both polling api.irishrail.ie. This is a deliberate, named exception to D30, which
exists to stop two collectors ever running together. It runs for **seven days** and then
ends, whether or not cutover happens.

**Dates.** Started **2026-08-23**, first two Lambda cycles landing in S3 at 14:03 and
14:08 UTC. **Hard stop 2026-08-30, no extension.**

**Forced-failure test: run 2026-08-26/27. Both alarms verified end to end.**

| | Test A: function-errors | Test B: not-running |
|---|---|---|
| Method | POLL_BUCKET set to a nonexistent bucket | EventBridge rule disabled |
| Broke at | 18:24 UTC 26 Aug | 23:39 UTC 26 Aug |
| Alarm raised | 18:29:43 UTC | 00:19:09 UTC 27 Aug |
| Email received | yes | yes |
| Reverted | 18:31 UTC | 00:14 UTC |
| Returned to OK | 18:34:43 UTC | 00:20:09 UTC |
| Recovery confirmed | cycles at 18:43, 18:48, 18:53 | invocation at 00:15 |

**The test found a real defect, which is the point of running it.** The poller's SNS topic
had **zero subscribers**: the subscription the stack created on 23 August expired because
an unconfirmed email subscription is discarded after three days and the link was never
clicked. Every alarm for three days would have shown ALARM in the console and notified
nobody. Both topics are now confirmed. Check subscriptions after any stack that creates
one, because nothing else surfaces this.

**Both tests cost no comparison data.** Test A ran while local was collecting, so the
parallel run lost only the Lambda side for nine minutes. Test B ran entirely inside quiet
hours (00:30-05:30 Dublin), when neither poller collects, so the 35 minutes were free.

**A design choice paid off here.** The not-running alarm watches `Invocations`, which keep
ticking through quiet hours because the skip lives in the function rather than in the
schedule (D29 rationale). Had quiet hours been a cron expression instead, invocations
would stop nightly and the liveness alarm would have had nothing to watch — or would have
fired every night.

**Known gap, not tested.** The API's `api-errors` alarm cannot see a failed prediction-log
write. `api.py` catches `LogWriteFailed` and returns 503, which Lambda counts as a
successful invocation, so `Errors` stays zero. D39 requires log trouble to be visible as
API trouble and it currently is not. Needs a metric or a deliberate unhandled raise.

**What "ends" means.** At the stop date exactly one of two things happens:

- the diff met the bar, so the **local poller stops** and the Lambda takes over; or
- the diff did not meet the bar, so the **Lambda stops** and the local poller carries on
  alone while the problem is fixed.

There is no third option where both keep running. The exception expires on a **date**, not
on success. That is the whole point of writing it down: a temporary doubling of load on
somebody else's free service is exactly the kind of thing that becomes permanent by
nobody deciding anything.

**Why the exception is acceptable at all.** Each poller is 31 requests per five-minute
cycle, ~0.10 req/s. Both together are ~0.20 req/s against a 2 req/s budget — a tenth of
what the politeness rule allows. D30's lock exists to prevent a bulk backfill running
alongside a poller (4+ req/s), not this. The `hostlock` is per-host and cannot see across
machines anyway, so the control has to be a written decision with an end date rather than
code.

**The bar for cutover.** Measured over the full seven days, which covers every weekday and
both weekend days since the timetables differ:

- record **schema identical** — exact match, no tolerance
- **≥99.5% overlap** on the set of `(date, train, station)` events, every miss explained
- per-station-per-hour record volume within a few percent
- for cycle pairs landing within ~30s of each other, `Exparrival` agrees on shared events

Byte-level record equality is *not* the bar and expecting it would be wrong: the two
pollers hit the API at different instants and the API's answer changes between them.

**Running the comparison.** Daily, once there is a full day of overlap:

```
aws s3 sync s3://rail-delay-poller-kg/parallel/lambda/ data/live/lambda/ --region eu-west-1
python scripts\diff_parallel.py
```

The script scopes everything to windows where local was demonstrably running, because the
laptop gets shut down and a gap is not a miss. Coverage hours print above every
percentage for that reason. Run it once on day 1 as a smoke test of the tooling itself: a
broken sync or a crashing script is worth finding on day 1, not on day 7 while trying to
make the cutover call.

**Stopping and restarting local.** Ctrl+C before shutting the laptop down; that runs the
cleanup and releases the host lock. A hard shutdown does not, so the lock survives with a
recent heartbeat and `hostlock` refuses to start a new poller for 15 minutes. Either wait,
or use `--force-lock`. The lock file is the liveness check too: no
`data/.irishrail-api.lock` means local is not running, and it does not restart itself
after a reboot. Automating that is not worth it for a seven-day window.

**A forced-failure test runs inside the week, not after it — 2026-08-27, day 5 of 7.**

Why day 5 and not day 1: you need to know what working looks like before you break it,
or you cannot tell a real alarm from a misconfigured one. Why not day 6 or 7: the point
is to discover an alarm that *does not* fire, and that needs days left to fix and
re-test. An alarm that has never fired is not a verified alarm — the failure mode being
guarded against is finding out the "poller is dead" alarm never worked on the day the
poller actually died, having already lost unrecoverable `ExpectedArrival` data.

Three failures, three alarms, roughly 45 minutes. Revert each and confirm the alarm
returns to OK before starting the next; an alarm that fires but never clears is its own
bug.

| Break | How | Expect | Delay |
|---|---|---|---|
| S3 permission | point the role's `s3:PutObject` resource at a bogus prefix, redeploy | `function-errors` | ~5 min |
| Timeout | set `Timeout: 3`, redeploy | `function-errors`, maybe `partial-cycle` | ~5 min |
| Schedule | `aws events disable-rule --name rail-delay-poller-tick --region eu-west-1` | `not-running` | **30 min** |

The third is the important one and the slowest: `not-running` needs six consecutive empty
five-minute periods. It is also the only alarm that catches the silent failure, since a
poller that stops being invoked produces no error anywhere.

**Comparison method.** The local poller is the **control and is not modified**: it keeps
writing to local disk exactly as it does today. The Lambda's S3 prefix is synced down and
diffed against those files. Giving the local poller an S3 sink too would be symmetric, but
a bug in the shared sink would then affect both sides identically and stay invisible.

**Related breakage to clear before the local poller stops.**
`harvest_codes.py --from-snapshots` reads a local directory of archived
`getCurrentTrainsXML` responses. Once the local poller stops, that directory stops growing
and the script reports "0 new codes" — indistinguishable from a genuinely unchanged
network. Fix before cutover day: a staleness guard that complains when the newest snapshot
is more than ~3 days old, plus an `aws s3 sync` step in the cutover runbook. A native S3
reader is not needed; harvesting is rare and manual.

**Date.** 2026-08-16

---

## D37 — cfn-lint validates shape, not service rules; check quotas and units before deploying

**Decision.** Before deploying any CloudFormation template, check the values that a
service API validates at create time rather than at template-parse time: currency units,
account quotas, globally-unique names, and region-restricted services. A clean cfn-lint
run says the template is well-formed, not that AWS will accept it.

**What prompted it.** Two failures of exactly this shape, one caught the expensive way
and one caught before it cost anything.

`infra/foundation.yaml` deployed and failed on:

```
Unable to create/update budget - EUR is not in the supported unit set: [USD]
```

The budget was denominated in EUR because CLAUDE.md quotes euro figures for the cost
rules. AWS Budgets does not convert; it accepts only the account's billing currency. The
template had `AllowedValues: [EUR, USD, GBP]` sitting next to a comment saying "must
match the account's billing currency" — a list advertising three options when the API
accepts one. Fixed by defaulting to USD and deleting the AllowedValues, since a list of
three that only ever accepts one is a trap rather than documentation.

Note the account is AISPL (Amazon Web Services India Private Limited): invoices are in
INR, but Budgets still works in USD because AWS prices in USD. The budget unit follows
the pricing currency, not the invoice currency.

`infra/poller.yaml` had not been deployed yet, and would have failed on:

```
ReservedConcurrentExecutions: 1
```

AWS refuses any reservation leaving the account below **100** unreserved concurrent
executions. `aws lambda get-account-settings` reports this account's total limit as
**10**, so no reservation is possible at all. Removed rather than worked around: overlap
was already prevented structurally, because the 240s timeout sits below the 300s schedule
with retries at 0, so an invocation cannot still be running when its successor fires. The
reservation was defence in depth, not the mechanism.

**Why this is a rule and not a pair of bugfixes.** Both passed cfn-lint. Both would pass
`aws cloudformation validate-template`. Neither is a typo. The class is: **CloudFormation
validates structure; the underlying service validates business rules, and only at create
time.** A failed initial create is also auto-deleted now, so the stack vanishes and
`describe-stacks` reports "does not exist" — the evidence has to be recovered from
`list-stacks --stack-status-filter DELETE_COMPLETE` and then `describe-stack-events`
against the deleted stack's ARN.

**The pre-deploy checks, concretely.**

- Currency or unit fields: confirm against the account, not against the docs.
- Anything with a quota: `aws lambda get-account-settings`, `aws service-quotas
  get-service-quota`. New accounts get far lower limits than the published defaults.
- S3 bucket names are globally unique across every AWS account. `aws s3api head-bucket`
  returns 404 if free, 403 if taken by someone else.
- Region-restricted services: Budgets requires its SNS topic in us-east-1, which is why
  the foundation stack is a separate stack in a separate region.

**Date.** 2026-08-23

---

## D38 — Two structural artefacts in the parallel-run diff, and how to tell them from real disagreement

**Decision.** `scripts/diff_parallel.py` reports its own sampling skew and schedule phase
offset alongside every result, and refuses to give a pass or fail verdict below two
covered hours. Both artefacts below look exactly like the pollers disagreeing, and neither
is.

**Artefact 1: window-edge sampling skew.** Comparison windows are built from local cycle
timestamps, because local uptime is what has gaps. So the window edges *are* local cycles,
and local necessarily has a cycle at each boundary while the Lambda's fall just outside.
Measured on the first real window: **4 local cycles against 3 Lambda cycles**, a 25%
sampling difference. That alone produced 8 local-only events, 0 lambda-only, and a 21.8%
volume deviation.

**The tell is the direction.** Sampling skew is one-sided: whichever poller sampled more
finds extra events. Real disagreement scatters both ways. A `lambda only` column that
stops being zero is worth investigating; a `local only` column on its own, with a matching
skew percentage, is not. Over a full day the skew is one cycle in ~144 and the effect
disappears.

**Artefact 2: fixed phase offset.** The two schedules are independent and never drift into
step. Measured at **exactly 85 seconds**, on every single cycle: local fires at :21:50,
the Lambda at :23:15. Two consequences.

First, the original 30-second pairing threshold for the value-agreement check matched
nothing at all, so the check silently measured zero for its whole existence and reported
a bare "-". A check that cannot fire is worse than no check, because it reads as a pass.
Threshold now defaults to half the poll interval, and the observed median offset is
printed so it cannot go stale silently.

Second, `ExpectedArrival` is *supposed* to change across 85 seconds — that is the operator
revising its estimate as the train approaches, which is the entire signal this project
captures. Measured agreement is **89.2% across 743 shared events**, and the missing 11% is
mostly legitimate revision rather than error. Do not read that number as an error rate.

**Why a provisional floor rather than a verdict.** The first run printed "DOES NOT MEET
the D36 bar" off twelve minutes of data, where every number was edge-dominated. That would
have sent someone chasing a defect that does not exist. Below two covered hours the script
now says PROVISIONAL and explains why, which is the honest answer.

**Found by running the tool on day 1 rather than day 7.** Neither artefact appears in a
synthetic test, because both come from the interaction of two real schedules. Both would
have been actively misleading on the day the cutover decision was due.

**Date.** 2026-08-23

---

## D39 — Prediction log: schema, write-once storage, and fail-closed serving

**Decision.** Every served prediction is written to
`s3://<bucket>/predictions/date={train_date}/{stamp}-{request_id}.jsonl` before the
response is returned, and the same JSON line goes to stdout. A prediction that cannot be
logged is not served.

**The row stores outputs, not inputs.** It carries `pred_q10_sec`, `pred_q50_sec` and
`pred_q90_sec` plus `model_version`, so scoring never re-runs the model. If the scorer
could re-derive a prediction it would be running today's model against a known outcome,
which is the regeneration CLAUDE.md forbids. Vantage and horizon fields are logged too,
because D28 requires accuracy reported per horizon rather than as one blended number.

`operator_eta` is logged despite never being a model input. The accuracy page compares
against it on matched events, and reconstructing it afterwards would be its own form of
regeneration. `scheduled_arrival` is logged even though the movements record has it, so a
row is self-contained and a timetable change between prediction and scoring is detectable
rather than silently corrupting the delay.

**Partitioned by service date, not prediction date.** The scorer joins on
`(train_date, train_code, station_code)` — the same triple the parallel-run diff uses — so
scoring one day reads exactly one prefix.

**Enforcement is IAM, not discipline.** The API role gets `PutObject` on `predictions/*`
and nothing else. The scorer role gets `GetObject` there and `PutObject` only on
`scores/*`. "The scorer reads outcomes, it never writes or recomputes predictions" stops
being a rule someone must remember and becomes something the credentials refuse to do.

**Honest limit, to be stated on the accuracy page.** Write-only IAM plus bucket versioning
makes the log tamper-*evident*: casual or accidental modification is prevented, and an
overwrite leaves the original recoverable. It is not tamper-*proof* — an account admin can
still rewrite history. Object Lock would close that, but it can only be enabled at bucket
creation and the bucket already exists; a migration was not worth it three weeks from the
deadline. Better to state the limit than imply a guarantee that is not there.

**Alternatives rejected.** CloudWatch Logs alone: free and AWS-timestamped, but the
nightly scorer would need Logs Insights queries or an export task, which is more moving
parts in the job that has to run unattended. S3 alone: fine, but one print statement buys
a second copy whose timestamp is assigned by AWS at ingestion rather than by our own
clock, which is independent corroboration of the ordering claim. Both, with S3 written
first, so a CloudWatch line exists if and only if the prediction was logged and served.

**Fail closed, with one retry.** Serving an unlogged prediction would bias the accuracy
page, because unlogged predictions are not missing at random — they cluster during
infrastructure trouble, which is exactly when behaviour is unusual. One retry with a short
backoff absorbs a transient blip; a persistent failure raises and the request errors.
Availability of a portfolio site is worth less than the integrity of its headline claim.

**Date.** 2026-08-25

---

## D40 — No database. S3 and Parquet instead

**Decision.** There is no relational database. Raw responses are gzipped on S3, parsed
records are Parquet, and the API reads what it needs at request time. CLAUDE.md's Stack
line said "Parquet → Postgres" until 2026-08-25; nothing ever implemented it, and this
records why that drift was the right outcome rather than an oversight to fix.

**Why not Postgres.** Three reasons, in order of weight.

*Cost rules forbid it.* A 24/7 RDS instance bills hourly whether queried or not, roughly
€15 a month at the smallest usable size, against a measured total spend of about $0.10.
CLAUDE.md's AWS rules name RDS explicitly alongside NAT Gateways and load balancers as
things never to provision. A database would be over 99% of the bill for a service that
gets a handful of requests a day.

*The data is file-shaped and read-mostly.* It arrives as whole objects on a five-minute
cadence, is never updated in place, and is read in date-partitioned slices: "all movements
for 2026-08-01", "yesterday's predictions". That is exactly what object storage plus
partitioned Parquet does well. Writing it into rows first would be work that buys nothing.

*There are no relational queries.* Nothing joins across entities at request time. The API
answers one train at one station from one live upstream call. The scorer joins predictions
to outcomes, but both sides are date-partitioned files read in bulk once a night, which is
a batch job rather than a query workload.

**What is given up.** No ad-hoc SQL, no indexes, no transactions, no concurrent writers.
None of those are needed here, but the first would be genuinely convenient for exploring
the data, and DuckDB over the Parquet files covers that without a server.

**When this stops being right.** If the accuracy page ever needs per-request aggregation
over months of scores rather than a precomputed summary, or if anything needs to update a
record in place, revisit. Neither is on the path to mid-September.

**Date.** 2026-08-25

---

## D41 — Plain HTML, CSS and vanilla JavaScript for the three pages

**Decision.** No React, no TypeScript, no bundler, no npm. Three static files served from
S3 behind CloudFront, calling the API with `fetch`.

**How this came up is itself the point.** An earlier CLAUDE.md deferred React to "v2
only". That line was removed in the July merge, correctly, because the frontend moved into
scope. But nothing replaced it, so the framework question was left unanswered for six
weeks, and on 2026-08-25 I asserted from memory that React was still deferred — citing a
line that no longer existed. The rule is now written down rather than inferred.

**Alternatives.** (a) React, with Vite. (b) A lighter framework such as Svelte or Alpine.
(c) Server-rendered HTML from FastAPI.

**Why rejected.** (a) buys component state management and a build pipeline. Three pages
with no shared client state and no interactivity beyond a dropdown and a fetch have
nothing for it to manage, and it adds npm, a bundler, a `node_modules`, and a build step
between editing a file and seeing the change. (b) is lighter but still a dependency and
still something to explain. (c) couples the pages to the API Lambda, so a page change
means redeploying the prediction service, and it forfeits CloudFront caching of static
assets that CLAUDE.md's AWS rules already assume.

Vanilla also keeps the deployment honest: `aws s3 sync` of three files, no build artefacts
to reconcile with source, and nothing that can drift between what is in git and what is
served.

**The real constraint is the deadline.** Three weeks, with the parallel run, the scorer
and the API deploy still outstanding. A framework is a week of learning for a portfolio
piece whose value is the data work behind it, not the widgets in front.

**When this stops being right.** If the pages ever need shared client-side state, routing,
or more than a few hundred lines of JavaScript, the vanilla version will start hurting.
That is a v2 problem and there is no v2 before mid-September.

**Date.** 2026-08-25

---

## D42 — Predictions page loads per train on demand; no precomputation yet

**Decision.** The Predictions page shows a station board with Irish Rail's own
`ExpectedArrival` for each train immediately, and fetches our prediction per train on
demand. Nothing is precomputed. Revisit after the parallel run closes on 2026-08-30.

**The problem.** `/predict` costs one upstream `getTrainMovementsXML` call per train. A
station page listing ten trains would be eleven upstream calls, about 5.5 seconds at the
2 req/s politeness limit. That is too slow for a page load and rude to a free service.

**The obvious fix, and why not yet.** The poller already fetches all 30 station boards
every five minutes. It could compute and store a prediction for every train at every
polled station in the same cycle, turning the page into a single S3 read.

That change would land in `poll_live.py`, which is **the control for the parallel run
until 30 August**. Changing the control mid-experiment invalidates the comparison it
exists to produce: any divergence afterwards could be the port or could be the change, and
there would be no way to tell. Same reasoning as moving cycle metadata into `LocalSink`
*before* the run started rather than during it.

**What the deferral costs.** The page shows operator ETAs instantly and our predictions
arrive progressively as each request returns. That is honest rather than degraded: the
operator answers for every train while we answer for roughly 44% of them, and a page that
fills in unevenly makes the coverage gap visible instead of hiding it behind a spinner.

**What to weigh at cutover.** Precomputing generates predictions nobody asked for, which
inflates the prediction log and makes the coverage denominator mean something different:
"what the model could answer" rather than "what users asked for". D39 logs requests, and
that distinction should be settled before precomputation lands, not after.

**Date.** 2026-08-25

---

## D43 — The API's deployment shape: FastAPI behind Mangum, Function URL, baked artifact

**Decision.** One FastAPI app in `src/api.py`, served as a Lambda through Mangum, exposed
by a Lambda Function URL, with the model artifact baked into the deployment package and
the serving version pinned as a CloudFormation parameter.

**FastAPI plus Mangum, not a bare handler.** Three endpoints do not need a web framework,
and a bare handler would be a smaller package and a faster cold start. The deciding reason
was not on the original list of trade-offs: **the same app runs under `uvicorn` locally**,
so every path — all four decline reasons, the version guard, the log failure — was tested
before anything was deployed. Same principle as the lazy S3 client in `lambda_poll.py` and
the injectable time budget in `poll_cycle`: if a thing can only be exercised in
production, it will be debugged in production. FastAPI's generated `/docs` is a secondary
benefit, and it is the honest answer to "did you build a FastAPI service".

**Function URL, not API Gateway.** One public read-only endpoint. API Gateway adds
$1/million requests and a second thing to configure, for throttling, auth and usage plans
that nothing here uses. CORS is `*`, which is correct for a public read-only API and
avoids pinning the frontend's CloudFront domain into the API stack.

**Baked artifact, not fetched from S3.** With a pinned version there is nothing to fetch
that a redeploy does not already carry. Baking removes the cold-start download, the
cache-invalidation logic, the `s3:GetObject` permission and the failure mode where S3 is
unreachable and the API cannot start. Cost is ~1MB in a package already carrying numpy and
scipy.

**Baking plus pinning puts the version in two places**, so `load_model` refuses to start
unless the baked manifest matches `SERVING_MODEL_VERSION`, and `build_api.ps1` takes the
version as a required argument rather than defaulting to LATEST, so a mismatch cannot
originate in the build either. Verified: passing a wrong version raises rather than loading.

**1024 MB, not 128.** The poller runs at 128 because it is I/O bound and sleeps. The API is
dominated by importing numpy, scipy and lightgbm, and Lambda scales CPU with memory. At
this traffic both settings sit inside the free tier, so the smaller one would buy nothing
but a slower first request. Measured: 3.2s cold, 30ms warm.

**Separate stack from the poller**, so the two roles cannot leak into each other. The API
may write `predictions/` and nothing else; the poller may write its own prefix and nothing
else.

**Date.** 2026-08-25

---

## D44 — Packaging lightgbm for Lambda: three problems a normal pip install hides

**Recorded because each cost real time to diagnose and none is discoverable from an error
message.** All three are encoded in `scripts/build_api.ps1`, but a build script is where
you look once you already know to look there.

**1. The dependencies disagree about manylinux tags.** numpy past 2.2.6 publishes only
`manylinux_2_28` wheels; lightgbm 4.7.0 publishes only `manylinux2014`. Either
`--platform` alone fails to resolve the requirements file. pip accepts the flag more than
once, and Amazon Linux 2023 satisfies both, so passing both tags is the fix.

**2. Python version skew reports as a missing package.** This machine runs 3.14, the Lambda
runtime is 3.13. Without `--python-version 3.13`, pip looks for cp314 wheels and says
`Could not find a version that satisfies the requirement numpy==2.5.1 (from versions:
none)`. "from versions: none" reads as "this package does not exist for this platform",
which sent the first diagnosis toward architecture rather than interpreter version. Adding
the flag changed the message to a real version list, which is what made the manylinux tag
problem visible.

**3. lightgbm needs OpenMP and Lambda does not ship it.** `lib_lightgbm.so` links against
`libgomp.so.1`. The Lambda Python image has no OpenMP and the lightgbm wheel does not
bundle it, so the import dies at `ctypes.LoadLibrary` with `libgomp.so.1: cannot open
shared object file`. This one only appears **after a successful build and deploy**, as a
502 from a function that looked fine.

Fixed by extracting `libgomp.so.1` from a scikit-learn manylinux wheel, which bundles a
matching aarch64 build, into `build/api/lib/`. Lambda's default `LD_LIBRARY_PATH` already
contains `/var/task/lib`, so no environment variable is needed. Borrowed from that wheel
rather than adding scikit-learn itself, which would be ~40MB for one shared object.

**The general lesson.** `pip install` succeeding on the build machine says nothing about
whether the package runs on the target. The build script now verifies the baked artifact
before shipping, but only a deploy proves the native libraries load. Budget for that.

**Date.** 2026-08-25

---

## D45 — Shared logic moves to a module the moment a second caller appears

**Decision.** `featurise()` moved from `scripts/compare_to_operator.py` into
`src/features.py`; the feed's time and date parsing moved into `src/feedtime.py`. Both
moves happened when the API became a second caller, not later.

**Why this is a standing rule and not two edits.** D35 records what happened when two
copies of the feature list were maintained separately: they diverged, the trained artifact
carried a feature the comparison excluded, the saved model could not have served a live
request, and nothing detected it. A human reading both files caught it.

Building the API would have created that situation twice more. `featurise` is the function
that must agree between offline evaluation and live serving, or the measured accuracy
describes a different model from the one answering requests. The time parsing had already
been written three times over — `to_seconds` in `parse_raw.py`, `hms` in
`compare_to_operator.py`, `iso_train_date` in `prediction_log.py` — and the API needed a
fourth.

**Verified rather than assumed.** After moving `featurise`, `compare_to_operator.py` was
re-run and produced identical numbers: 80.1s against 109.7s, 27.0%, 9,077 comparisons.
A refactor of the function that produces the headline claim is not something to take on
faith.

**Known incomplete.** `parse_raw.py` and `compare_to_operator.py` still carry their own
time helpers. Both work and both have their own checks, so converging them is a follow-up
rather than something to do to working code three weeks from a deadline. New code imports
from `feedtime`.

**Date.** 2026-08-25

---

## D46 — How the head-to-head against the operator is kept fair

**This entry should have been written on 2026-07-28 when the comparison was built.** It
was not: the reasoning went into `scripts/compare_to_operator.py`'s docstring and stayed
there. It is the methodology behind the project's headline claim, so it belongs here.

**The claim.** Model 80.1s MAE against Irish Rail's `ExpectedArrival` at 109.7s, a 27%
improvement, over 9,077 comparisons on 2,654 distinct events (2026-08-01/02).

**Four ways the comparison could have been unfair, each handled explicitly.**

**1. Temporal leakage.** The operator issued its ETA at an instant. Our model must use only
what was knowable then. So for a poll at time P, the vantage is the last stop whose
**actual arrival clock time** was before P — not the last stop in the journey, and not the
last stop with a recorded arrival. A stop that reported at 10:20 is invisible to a
prediction made at 10:15 even though it sits earlier in the route with a perfectly good
delay against it. Polls taken after the train already arrived are dropped: a board keeps
listing a train past arrival, and at that point `Exparrival` is not a prediction.

**2. A feature that cannot exist at prediction time.** `horizon_observed_stops` counts
stops that *did* report, knowable only once the journey finished. Including it would have
measured a model that could not be deployed. Excluded, at a cost of 0.28% of gain. This is
what later forced the 12-feature retrain and D35.

**3. Output granularity.** `Exparrival` is minute-precision; actual arrivals are 6-second
(D22). Scoring a to-the-second prediction against a to-the-minute one hands us up to 30
seconds of free accuracy on every event. Both variants are reported and **the
minute-rounded one is the headline**: 80.1s rather than the flattering 77.9s. Giving away
2.2 seconds to make the comparison honest is the right trade for a claim that has to
survive scrutiny.

**4. Correlated repeats.** Each event is polled ~18 times as the train approaches.
Treating those as 18 independent comparisons would inflate every count and narrow every
interval. One comparison is kept per (event, lead-time band), the last poll in that band
being the most informed prediction the operator made at that range.

**What the comparison cannot say.** 29,118 polls (~56%) were dropped because the train had
not reported anywhere yet. The operator answers those; we cannot. So "27% better" is
conditional on us having anything to say at all, and that limit belongs beside the number
everywhere it appears.

**Where it loses.** Weak-coverage lines: MAE looks 2.3% better but the median is worse
(183s against 159s) and the model loses 58.7% of head-to-head comparisons on n=104.
Reported as a loss.

**Date.** 2026-08-25 (recording work done 2026-07-28)

---

## D47 — A 100% join rate is necessary and nearly meaningless; usability is the number

**Also written late.** The reasoning lived in `scripts/validate_join.py` and in one
conversation.

**Decision.** `validate_join.py` reports match rate *and* usability, and its verdict line
says explicitly that the match rate should not be read as readiness.

**Why the match rate is close to guaranteed.** Live station boards and
`getTrainMovementsXML` are two views of the same timetable. If the board says a train
calls at Connolly, the movements record lists Connolly. The join was only ever at risk
from *formatting* mismatches — train-code whitespace, station-code vocabulary, date
format — and all three were ruled out before running it. Result: 4,179 of 4,179 events,
100.00%, every station group.

**The number that gates the comparison.** How many matched events have **both** a real
operator ETA and a real recorded arrival. A matched event with a null `Arrival` cannot be
scored against anything.

| group | events | usable | with AutoArrival=1 |
|---|---|---|---|
| dart | 1,212 | 1,068 (88.1%) | 1,053 |
| dublin_hubs | 1,195 | 961 (80.4%) | 848 |
| **weak_coverage** | **683** | **180 (26.4%)** | **74** |
| TOTAL | 4,179 | 3,103 (74.3%) | 2,818 |

Weak-coverage lines join perfectly and are still unusable: the operator issues an ETA for
398 of 683 events but an arrival is recorded for only 180, and with trustworthy labels it
is 74. A weekend of data yields 74 comparable events on the lines where an honest answer
matters most, which is not enough to claim anything in either direction.

**Events, not records.** Distinct `(date, train, station)` triples are the unit, because
that is what the comparison consumes — one prediction, one operator ETA, one actual. Raw
polled rows are ~18x that and would inflate every count. Both are printed so the ratio is
visible.

**A useful incidental finding.** For most groups `has ETA` and `has actual` are identical
counts — DART 1,068/1,068, Dublin hubs 961/961, Cork corridor 326/326. Both probably
derive from the same signalling detection: when the system sees the train it produces
both, when it cannot it produces neither. If that holds, operator-ETA availability is a
usable proxy for label availability. Not confirmed.

**Date.** 2026-08-25 (recording work done 2026-08-23)

---

## D48 — Line keywords are matched on word boundaries, not substrings

**Small but it would have silently corrupted the label-quality analysis.**

**Decision.** `coverage_by_location.py` matches the weak-coverage line keywords with
`\b(cork|cobh|...|ballina|athlone)\b`, never as plain substrings, and prints every
location name each keyword matched so the heuristic is checkable against reality.

**Why.** `Ballina` is a prefix of **Ballinasloe**, which is on the Dublin–Galway line and
is not flagged. `Ennis` is a prefix of **Enniscorthy**. A substring match would have
labelled both as weak-coverage lines, inflating the flagged group with well-covered
stations and corrupting the comparison that D20 was built to test. The bug would not have
raised anything; it would have produced a plausible number.

**The deeper caveat, which survives the fix.** data-dictionary 5.1 lists weakly-covered
**lines**; the data has **locations**. Intermediate stops on a flagged line — Little Island
or Carrigtwohill on the Cobh branch — never match any keyword, so the flagged group
understates the affected set in the other direction. This is part of why the line-keyword
approach was abandoned entirely for `AutoArrival` (D20, D21): it is a proxy that fails
both ways, and no amount of regex care fixes that.

**Date.** 2026-08-25 (recording work done 2026-07-28)

---

## D49 — The prediction log is filled by a scheduled sampler, not by traffic

**Why this matters:** an accuracy page needs predictions to grade, and nobody was
visiting the site to make any, so the system had to generate its own work.

**Decision.** A separate Lambda takes a uniform random sample of the in-service fleet
every five minutes, predicts at most one stop per lead band per train, and logs the lot in
one S3 object per cycle. It ships with its EventBridge rule DISABLED and is enabled after
the 30 August cutover.

**What forced it.** On 27 August, two days after the API went live, the prediction log held
exactly one row: the smoke test. The nightly scorer was about to be built against an empty
table, and would have stayed empty for ever — a portfolio service has no organic traffic.
The scoreboard's input is demand, and there is no demand.

**Why this is better than organic traffic, not a substitute for it.** Real visitors would
have typed whatever they happened to care about: Dublin, rush hour, the specific train
someone was waiting for. That is a biased and unstateable sampling frame. A uniform random
draw over the fleet is one that can be described in a sentence and checked. The accuracy
page says these are scheduled sampled predictions rather than implying they are user
queries — which is honest and is also the stronger claim.

**Alternatives considered.**

- *Full sweep, every in-service train every cycle.* ~22,600 predictions/day but ~4x the
  current request volume against a free, unsupported API, permanently. Rejected: the extra
  data buys nothing — the offline comparison drew its conclusion from 9,077 comparisons —
  and the politeness cost is real and never goes away.
- *Fold it into the poller after 30 August* (D42's original framing). Rejected for now: it
  couples prediction to collection, and a separate function can be built and deployed
  during the parallel run without touching either poller.
- *Generator as an HTTP client of `/predict`.* Rejected. One HTTP request is one Lambda
  invocation is one S3 object, so 7,500+ predictions a day would be 225k PUTs a month
  against ~190 batched. Worse, the serving version would be pinned in one stack and
  consumed in another. Importing the prediction core directly keeps one code path, one
  baked artifact and one CloudFormation parameter.
- *No generator; publish the offline numbers only.* Rejected: the retraining policy's
  trigger is "the first 30 days of live scored predictions after launch", which without
  live predictions never acquires a baseline.

**Sampling details, and why each is deliberate.** The draw is over the *sorted* fleet, so
the randomness comes from the RNG rather than from the feed's own ordering, which we
neither control nor understand; taking the first N off the board would have pinned the
scoreboard to whichever routes sort first. It is unseeded and redrawn every cycle, because
a fixed seed reproduces a similar sample every time, which is the same bias in slower
motion. `TrainStatus == R` filters to the product's stated scope — a train that has not
departed is out of scope, not a hard case. Target stations are drawn uniformly within each
band with **no** preference for the 30 stations the poller watches: preferring them would
raise the matched-event count for the head-to-head at the cost of drawing that population
differently from the accuracy population, and two populations is two things to explain. In
the first measured cycle 25 of 122 rows (20%) landed on polled stations anyway.

**What is deliberately NOT filtered.** Targets are not screened for whether a prediction is
possible. A train with no upstream report produces a `no_upstream_report` decline, and that
decline *is* the coverage measurement. Screening them out would delete the denominator and
turn "answers ~44% of queries" into "answers 100% of the queries we knew we could answer".

**A denominator trap this creates.** The first cycle declined 13 of 122, about 11%. The
offline figure is ~56% unanswerable. These are not in conflict and must never be shown
together: 56% is a share of *station board polls*, which include trains that have not
departed; 11% is a share of *sampled in-service trains*. Different populations. The
scorer's summary carries a note saying so, because the two numbers side by side would
otherwise read as a dramatic improvement that did not happen.

**Why DISABLED on arrival.** Doubling request volume against Irish Rail during the last
three days of the parallel run would confound the diff that decides the cutover — the same
argument that kept precompute out of the poller (D42) and that added cycle metadata to
LocalSink rather than changing the control mid-experiment. It is a stack parameter rather
than an `aws events enable-rule`, because CloudFormation reverts an out-of-band change on
the next deploy, and a parameter leaves the enabling in CloudTrail.

**Measured.** One cycle, 27 August: fleet 43, sampled 40, 41 requests, 122 predictions,
20.6 seconds.

**Correction, 2026-08-28 — the prediction counts above were wrong by 3.7x.** This entry
said batching saved "225k PUTs a month against ~190". Both figures conflated requests with
predictions. At 122 predictions per cycle and ~228 cycles a day the real numbers are
**~27,800 predictions/day**: ~228 objects/day or **~6,900 PUTs/month batched**, against
**~834k PUTs/month** and about **$4.17/month** unbatched, on a EUR 5 budget. Batching is
therefore load-bearing for cost, not merely tidy.

The politeness figure that actually justified sampling was **not** affected, because it
counts requests rather than predictions: 1 + 40 per cycle, ~9,350/day, roughly 2.3x the
poller's own volume. That number was right and the sampling decision stands on it. The
prediction count is higher than stated because one journey fetch answers several horizons,
which is the point of the design and was simply not carried through the arithmetic.

**Date.** 2026-08-27

---

## D50 — The scorer reuses the offline methodology rather than approximating it

**Why this matters:** if the live score and the headline 27% are worked out differently,
putting them on the same page tells a reader nothing about whether anything improved.

**Decision.** `src/score.py` reads logged predictions, refetches arrivals, and applies all
four D46 fairness traps. The lead-time bands moved into `feedtime.py` so the offline
comparison, the generator and the scorer cannot disagree about them.

**Why not a simpler live metric.** A live number computed differently from the published
27% is not comparable to it, and an accuracy page showing two incomparable numbers is worse
than one showing neither — a reader has no way to tell which difference is the model and
which is the arithmetic. Specifically: trap 3 (the operator is minute-precision, actuals
are 6-second) hands us up to 30 seconds of free accuracy per event if ignored, so both raw
and minute-rounded are reported and the rounded one is the headline. Trap 4 (one
comparison per event and lead band, the last statement made in that band, on *both* sides)
is not optional either — a stop 20 minutes out stays in the 15-30 band for several cycles,
so without deduplication every count inflates and every interval narrows.

Trap 1 is satisfied more strongly here than offline, and this is the point of D39: the
prediction was made live from stops that had actually reported, and written down before the
outcome existed. Nothing reconstructs a vantage point.

**Outcomes come from `getTrainMovementsXML`, not the archived boards.** Boards carry
`Exparrival` and `Duein`, which are the operator's prediction, not a confirmed arrival.
Because `TrainDate` is honoured back to 2007, a night the scorer did not run is recoverable
by running it later, so the job scans for dates that have predictions and no summary rather
than assuming last night succeeded. The boards are still read, for the operator baseline,
because `ExpectedArrival` exists only live and cannot be backfilled.

**Six states, all reported, none dropped.** `scored`, `echo_suspect`, `no_actual_arrival`,
`not_on_route`, `train_not_found`, `declined`. ~31% of movement records never receive an
actual time and the flagged lines echo scheduled times, so quietly keeping only the rows
that scored cleanly would bias the board toward trains that behaved. The failure mode is a
*better*-looking number, which is exactly why it would never have been questioned.
`echo_suspect` is reported separately and never blended, and the summary also carries an
including-echo variant, per D23's "flag and keep, exclusion is an evaluation-time decision,
report both ways".

**Enforced by credentials, not discipline.** The scorer's role may read predictions and
write scores; it has no `PutObject` on the prediction prefix, the exact mirror of the API's
role. Its package also contains no model, and `build_scorer.ps1` asserts the absence, so
"never regenerate historical predictions" is something the deployment *cannot* do.

**Two traps found while building it, both silent.**

1. Board rows carry their own `Traindate`, which is the service date and differs from the
   partition they sit in: a train that departs on the 26th and is still running at 00:30
   appears in the 27th's partition under 26 Aug. Keying on the partition would have lost
   every late-evening comparison with nothing reporting a loss.
2. Reconstructing a lead time from the raw wall-clock `scheduled_arrival` turns a 23:50
   prediction about a 00:20 arrival into a lead of minus 23 hours, which yields no band,
   matches no operator poll, and drops the row from the head-to-head silently. `api.py`
   now records `lead_sec` at prediction time, where the schedule is already unwrapped; the
   scorer keeps a reconstruction with a wrap correction for rows written before that.

**Scoring a date is once-only**, because `unscored_dates` skips anything with a summary.
So a run made while trains are still running would freeze a page full of
`no_actual_arrival` and never revisit it. `is_complete()` refuses unless forced. This was
found by running the scorer against the same day's predictions and seeing 93 of 109 come
back unarrived.

**Date.** 2026-08-27

---

## D51 — Three custom CloudWatch metrics for the generator, not eight

**Why this matters:** AWS gives away ten of these and charges for the eleventh, so an
unthinking eight would have multiplied the project's running cost by eighteen.

**Decision.** The generator publishes `PredictionsLogged`, `Declined` and `TrainsFailed`.
Everything else worth knowing — fleet size, sample size, per-reason decline counts, cycle
duration — goes into the handler's return value, which lands in CloudWatch Logs.

**Why.** The deployment already used 8 of the 10 free custom metrics. The first draft added
eight more, which would have crossed into $0.30 per metric per month against a total bill
of about $0.10 — an 18x increase in the running cost of the project, for telemetry nobody
alarms on. Logs are free and queryable in Logs Insights; metrics cost and should be
reserved for things worth alarming on.

Cycle duration is deliberately absent even though it is operationally interesting: Lambda
publishes `Duration` itself in the `AWS/Lambda` namespace, so a custom copy would be a paid
duplicate of a free metric.

**Consequence to watch.** This takes the account to 11 custom metrics, one over the free
allowance, so roughly $0.30/month. CLAUDE.md flags these thresholds as ones that "move
quietly"; this is the first crossing and it was deliberate.

**Date.** 2026-08-27

---

## D52 — Delay is anchored to the stop's own schedule, everywhere

**Why this matters:** the model was taught to measure lateness one way and then asked to
work in a system that measured it a different way, and on most trains the two agree so
nothing looked wrong.

**In plain terms.** "How late is this train" sounds like one question with one answer. It
is not. If a train is scheduled at 23:50 and arrives at 00:05, is that fifteen minutes
late or twenty-three hours and forty-five minutes early? You need a rule. The code that
built the training data used one rule; the code running live had drifted into a second
rule. They give the same answer for almost every train, and a wildly different answer for
a handful — which is the worst possible way for two rules to disagree, because the
disagreement is invisible until you look at exactly the right row.

**How it was caught.** The first scored day reported an average error of **1371 seconds**
(22.8 minutes) with a **median of 48 seconds**. A mean twenty-eight times the median is
not a bad model; it is a few absurd rows. Two of 81 held the entire error. If only the
mean had been published — the obvious single number to show — it would have looked like a
broken model, and the real cause would have been hunted in the wrong place entirely.

**Decision.** `feedtime.delay_seconds` is the one rule: `arrival - scheduled`, folded back
by a day if the result exceeds ±12 hours. `api.py` and `score.py` now use it. Journeys
whose anchored arrivals move backwards along the route are refused whole, as
`journey_inconsistent`, rather than scored.

**The bug it fixes was a train/serve skew, not a scoring artefact.** `parse_raw.py`
produced the training data using the anchored rule. `api.py` and `score.py` had instead
grown a second version: unwrap the arrival series and the schedule series independently
across the journey, then subtract. The two rules agree on every well-behaved journey and
disagree by a whole day on the pathological ones — so the model was being served
`current_delay_sec`, its dominant feature, computed by a different rule than it was
trained on, and nothing surfaced it.

**The case that exposed it.** A728 to Galway, 27 August:

```
26 BSLOE  sched 76770  arr 76962   (21:22)
27 WLAWN  sched 77430  arr  6912   (01:55)   <- backwards
28 ATMON  sched 78030  arr  2844   (00:47)   <- backwards
29 ATHRY  sched 78360  arr 81810   (22:43)
34 GALWY  sched 79620  arr 79920   (22:12)   <- earlier than ATHRY, which precedes it
```

The sequential unwrap sees `6912` after `76962`, correctly infers a midnight crossing by
its own logic, and adds a day to everything after. Athenry comes out at 89850s — 24.96
hours late — where the anchored rule gives the correct 3450s.

**`AutoArrival` does not protect against this.** All four bad stops carry `AutoArrival=1`.
D20–D23 established that label quality follows `AutoArrival` rather than line identity;
this is a separate failure mode that flag says nothing about, and assuming otherwise is
how it survived.

**Why the whole journey is refused rather than the bad stop dropped.** From magnitude alone
there is no way to say which reported time is wrong: Woodlawn at 01:55 and Athenry at 22:43
are mutually inconsistent, and the schedule cannot arbitrate. Monotonicity is the test
because it is a property the data must have for any reason at all; a threshold like "more
than N hours late is impossible" would be a number invented to fit one example.

**The offline archive is unaffected**, which was checked rather than assumed: zero of
334,984 non-null delays exceed 12 hours, because `parse_raw` already folded them. 0.164%
exceed one hour. The published 27% is not contaminated. The live path met this immediately
because it predicts at *any* station on a route, whereas the offline comparison only ever
looked at the 30 polled stations — the live system has wider station exposure than the
evaluation that validated it, and will keep meeting data problems the offline work never
saw.

**Measured effect.** Rescoring 27 August: MAE **1371.2s → 102.9s**, a 13x improvement in
the reported number with **no change to the model at all**. The median stayed at 48s
throughout, because the median never saw the bad rows — which is the whole point of having
looked at both. 9 rows were reclassified as `journey_inconsistent`. Two rows out of 81 had
been carrying the entire error.

The head-to-head for that day also flipped, from 6.4% ahead of the operator to **8.6%
behind**, on 13 matched events. Both numbers are too small a sample to mean anything; the
honest one is the second.

**Still outstanding.** `parse_raw.py` keeps its own copy of the rule. It is correct and
covered by its own checks, and converging it is a follow-up rather than something to do to
working code days before a cutover — the same call D-feedtime's docstring already records
for `hms`.

**Date.** 2026-08-28

---

## D53 — Two coverage numbers, and the visitor-facing one is the headline

**Why this matters:** "we answer 89% of questions" is true of a population no real
visitor ever picks from; the number they actually experience is 37.8%.

**Decision.** The accuracy page publishes both, each labelled with its population, and the
headline is the visitor-facing one:

- **conditional:** of trains sampled while in service, we answered **89.3%**
- **visitor-facing:** of entries on a station board, we answered **37.8%**

**Why a note was not enough.** D49 recorded that the generator's ~11% decline rate and the
offline ~56% unanswerable figure have different denominators, and left a note in the
summary saying so. A note protects whoever reads the JSON. It does not protect a visitor,
and it does not decide which number goes on the page — which was the actual open question.

**How the visitor number is computed.** Two factors, both measured live from data already
collected:

```
P(in scope)          share of board entries whose train has departed   42.3%
P(answered | scope)  the generator's own answer rate                   89.3%
                                                                      -----
visitor-facing coverage                                                37.8%
```

The first factor comes from the archived station boards, comparing `Origintime` against
`polled_at` — no extra request, no extra endpoint. Over 125,835 board rows spanning the
service date and the following morning, 42.3% were trains that had already departed.

**Why this is the honest one.** A visitor picks a train off a station board. The board
lists trains that have not departed yet, and the visitor cannot tell which those are by
looking. The product cannot answer for them at all — the features derive from upstream
reported delays. Publishing "we answer 89% of queries" would be true of a population the
visitor never selects from. It is the same shape of error CLAUDE.md's reporting rules
already forbid for accuracy: "27% better" without "answers ~44% of queries".

**The assumption, stated on the page rather than buried.** The second factor is measured on
sampled in-service trains and applied to in-service trains appearing on boards. The sample
is uniform over the fleet, so this is reasonable, but it is an inference and not a direct
measurement, and the summary carries it as a field rather than a footnote.

**What this retires.** The offline "~56% of polls unanswerable" stops being quoted as a
coverage headline. It measured a different population in a different period, and side by
side with the generator's rate it reads as an improvement that never happened. It stays in
the record as history, not as a published figure.

**Date.** 2026-08-28

---

## D54 — Cutover: the Lambda poller is the only poller

**Why this matters:** the laptop is out of the loop now — the thing that collects the data
runs in AWS, and if it stops, nothing on the site is right.

**In plain terms.** For eight days two copies of the same collector ran side by side: one on
the laptop, one in AWS. The point was to prove the AWS one saw the same railway as the
laptop one before trusting it alone. It did, so the laptop one is switched off. There is now
one collector and no fallback.

**Decision.** The parallel run (D36) is over and the bar was met. The local poller was
stopped after 00:30 on 31 August, during quiet hours and outside the measurement window, so
the control data for 23–30 August is complete. The Lambda poller continues and is now the
sole source.

**The measurement**, over 132.9 covered hours across all eight days including both weekend
days:

| Bar (D36) | Measured |
|---|---|
| schema identical, no tolerance | **identical**, 25 fields |
| ≥99.5% event overlap, every miss explained | **99.9%** — 21,183 both, 5 local-only, 6 lambda-only |
| per-station-per-hour volume within a few percent | **2.8%** mean deviation |
| `Exparrival` agrees on paired cycles | **89.6%** identical over 372,688 shared events |

**Every miss explained, which was the half of the bar that needed work.** All eleven have
one signature: seen in **exactly one cycle**, visible for **0 minutes**, with the other
poller's nearest cycle 0.1–2.5 minutes away. These are events that appeared on a board and
were gone before the next sweep, so whichever poller sampled inside the window caught them.

It is symmetric, which is what rules out a systematic defect: `A203 CORK` is lambda-only on
the 25th, 26th, 27th and 28th and **local-only on the 29th**; `P200 PTRTN` the same. All at
04:38–04:41 UTC — 05:38 Irish, minutes after quiet hours end at 05:30. They are brief
early-morning board entries and the winner is whichever poller's first cycle of the day
lands inside their visibility. This is D38's phase-offset artefact, now with a measured
median offset of 105 seconds.

**The forced-failure test cost zero events**, which was checked rather than assumed. Test A
killed the Lambda 18:24–18:43 UTC on 26 August (19 minutes) and Test B 23:39–00:15 (36
minutes). The local-only misses fall on 23 Aug 23:21, 24 Aug 11:26 and 29 Aug 04:39 —
none inside either window. 55 minutes of deliberate downtime lost nothing, because an event
is polled about 18 times across a 90-minute lookahead. The 28 August DNS outage, which
failed 15 of 30 stations in one cycle, likewise appears nowhere in the miss list.

**What was NOT renamed, and why.** The Lambda still writes to `parallel/lambda`. The name is
now historical and slightly wrong, and it was left alone deliberately: renaming an S3 prefix
mid-flight splits the archive across two locations, so scoring any date spanning the change
would have to read both. The scorer's `PollerPrefix` therefore needed no change — it already
pointed where the poller writes. Worth stating explicitly because "set the prefix after
cutover" was a planned action that turned out to be a no-op, and a future reader finding a
prefix called `parallel` on a system with no parallel run deserves the explanation.

**What is now single-homed.** There is no second collector. The poller's own alarms —
not-running, function-errors, partial-cycle, throttled-by-operator, invocation-dropped — are
the whole safety net, and their SNS topic is confirmed. `harvest_codes.py --from-snapshots`
reads the local archive and is now reading a frozen directory; see the note in CLAUDE.md.

**Date.** 2026-08-31

---

## D55 — harvest_codes gets a staleness guard, not a port to S3

**Why this matters:** a script that reads a folder nothing writes to any more will keep
saying "nothing new" forever, and that is word-for-word what a healthy quiet network looks
like.

**In plain terms.** One script's job is to notice new train codes appearing on the network.
It did that by reading a folder of saved responses. Since the cutover nothing writes to that
folder, so from now on the honest answer is "I cannot tell you" and the answer it actually
gave was "no new trains". Those are very different statements and the output could not tell
them apart. It now refuses instead.

**Decision.**

1. `--from-snapshots` takes a default: `data/raw/live/current`, the poller's archive. It
   previously required the path be typed from memory, and there are two plausible archives.
2. A staleness guard. If the newest snapshot is older than 24 hours the script prints what
   it found and the date, and exits 3 without merging. `--allow-stale` overrides it for a
   deliberate one-off merge of a frozen archive.
3. The age is read from the FILENAME's UTC stamp, never the file's mtime. An `aws s3 sync`,
   a copy between drives or a restore from backup all rewrite mtimes, and the question is
   when the data was captured, not when the bytes last moved.
4. **No port to S3.** The Lambda archives `current.xml` inside each cycle's tarball and
   reading those is real work, deliberately not done.

**A correction to what was reported first.** This was originally diagnosed as a wrong
default: that `--from-snapshots` fell back to `--snapshot-dir`, whose default points at this
script's own archive, frozen since 28 July, and had therefore been silently reporting "0 new
codes" for a month. That was wrong. `--from-snapshots` takes its own path argument and never
consults `--snapshot-dir`; the two are independent, and the help text already named the
right directory. There was no wrong default — there was a *missing* one, and no guard.
Acting on the original diagnosis would have changed where live harvesting writes, which has
nothing to do with the bug. Recorded because the wrong version was convincing and briefly
believed.

**Why no S3 port**, which is the part worth defending:

- **Nothing in the live path reads `codes.json`.** The generator gets its trains from
  `getCurrentTrainsXML` directly and the poller works from station boards. The file feeds
  `backfill.py` and `validate_join.py`, and nothing else.
- **The backfill is complete and is not repeating** (CLAUDE.md: "Done once, not repeated").
  28,706 files across 1,087 codes and 34 dates already exist.
- **The capability is not lost.** harvest_codes' live mode polls `getCurrentTrainsXML`
  itself and never depended on any local archive. If a future backfill is needed — thesis
  work, or retraining on a longer window — running it live for a day still produces exactly
  what it always did.

So the S3 port is work for a job that may never run again, and the alternative is not
"lose the ability" but "spend a day polling if the day ever comes". If a backfill is
actually scheduled, port it then, when the requirement is real rather than anticipated.

**The 56 codes.** Merging the poller's archive added 56 codes, taking `codes.json` from
1,087 to 1,143. An earlier estimate of 39 came from sampling the last 400 snapshots rather
than all 2,088.

**`data/codes.json` is now tracked in git, against the `data/` rule.** The reason for
merging was that these codes existed only in a frozen archive on one laptop — but `data/` is
gitignored, so merging alone did not fix that. A `!data/codes.json` negation makes the
exception explicit. It is defensible because the file is not bulk: 228 KB, a derived index
of every code ever observed, built by a harvest that cannot be repeated for the past.
Versioning it also timestamps when the set changed, so figures computed against the earlier
1,087 stay attributable.

**Amended 2026-08-31.** The `!data/codes.json` negation above was **inert**. Git does not
descend into an excluded directory, so under a bare `data/` that line could never be
reached; the file was in the repo only because `git add -f` had forced it there. Both the
fix and its verification were accepted because the file was visibly present — real
evidence for a different claim. `.gitignore` now uses `data/*`, which excludes only direct
children and so leaves negation reachable, and the rule covers `data/live/stations.json`
and `data/models/` as well. See the Conventions section of CLAUDE.md.

Verified rather than reasoned, because the same doubt applies to the replacement: a dummy
file at `data/models/<new-version>/model.txt` and at `<new-version>/sub/nested.txt` both
appear as untracked, so a future retrain will not silently produce an artifact outside the
repo while the API is pinned to it. `data/live/*`, `data/raw/*` and `data/parsed/*` stay
ignored. The whole thing was then confirmed end to end by cloning to a temp directory and
running all three build scripts, which is the only test that means anything here — reading
the file had already satisfied two readers that it was correct.

**Date.** 2026-08-31

---

## D56 — A third class of bad label: machine-captured arrivals attributed to the wrong train

**Why this matters:** four stations on the Galway line taught the model that trains arrive
there three-quarters of an hour late as a matter of course, and it believed them.

**In plain terms.** The echo problem (D20–D23) is the timetable reflected back as if it
were an observation. This is different: a *real* observation, captured by the signalling
system, filed against the *wrong* train. At Athenry on 7 July, A700 and A740 — two
different trains — both "arrived" at 10:33. A710, scheduled 13:29, "arrived" at 18:19.
A718, scheduled 17:43, "arrived" at 05:54 the next morning. Every eastbound train that day
got no arrival at all. `AutoArrival=1` on every one of them, because the time *was*
machine-captured — for some train. The flag that resolves the echo problem says nothing
about this one.

**Status: finding recorded, decision pending.** The retrain this implies is a model change
and belongs to the retraining policy in CLAUDE.md, not to a scoring investigation.

**How it surfaced.** Three days of live scoring (31 Aug, 1–2 Sep) with the generator
running. Overall MAE moved 84.4s → 102.0s on 2 September while the median stayed at 48s
all three days — the D52 signature, so the ten largest errors were pulled rather than
explained away. They split into two kinds:

1. **Real.** Three Sligo-line trains (D925, D930, A914) stepping from about five minutes
   late at Mullingar to 75–89 minutes late at Edgeworthstown, holding that delay to the
   end of the journey, `AutoArrival=1` throughout. A disruption, not a data defect. Nine of
   the ten largest errors.
2. **Contaminated.** Sixteen rows where the model predicted 35–45 minutes late at Athenry
   from a *normal* vantage delay of 90–534 seconds, with q90s up to 3.3 hours. The model
   itself, not its input.

**The training labels at the four stations**, 27 Jun – 12 Jul, `AutoArrival=1` only:

| station | n | median delay | q90 | max |
|---|---|---|---|---|
| ATHRY | 152 | **2,610s** | 9,960s | 32,730s |
| GL368 | 121 | **3,936s** | 16,356s | 40,890s |
| WLAWN | 164 | 810s | 9,768s | 35,424s |
| ATMON | 163 | 162s | 8,988s | 18,474s |

Only 48 of Athenry's 152 records (32%) are within half an hour of schedule; among those the
median is 60s. These four stations hold **222 of the 295** training records more than an
hour late, and **81% of all training examples** with a label beyond an hour. The offline
evaluator puts `target_location` at 18.35% of gain, second only to current delay — which is
exactly the mechanism by which a per-station garbage offset gets learned.

**`journey_consistent` (D52) removes it.** Applied to the training window it rejects 425
of 11,485 journeys (3.70%). After the filter: ATHRY n=44, median **60s**, q90 444s. GL368
median 378s. The distributions become sane. It is aggressive — Athenry loses 71% of its
records — but 0.45% of training examples target these stations, and a model with little
data at a station is honest where a model with wrong data is not.

**What this does to the published numbers.** Removing inconsistent journeys from validation
(158 of 5,291 journeys, 8,717 rows, 3.85%) moves offline MAE **76.1s → 60.6s** with
coverage unchanged at 80.3%. So about fifteen seconds of the published validation MAE is
contamination. The head-to-head is **not** affected: it runs over the 30 polled stations,
none of which are the four above, and the polled-30 MAE is 65.8s before the filter and
65.7s after.

**A second, smaller defect found in the same rows: the feed is mutable within a day.** At
05:34 on 2 September Irish Rail's fleet feed listed A731 — the 20:50 Galway to Heuston —
as `TrainStatus=R` with the message *"(-1013 mins late) Arrived Athlone next stop Clara"*,
and the movements record showed Athlone `Arrival 05:07:24`. The generator predicted from
that vantage: a 7.1-hour delay, propagated as ~70 minutes late at every downstream stop,
for the next hour and a half. By the time the scorer refetched, Athlone read 22:01 (+30s).
The arrival had been overwritten. Thirteen scored rows on 2 September carry a vantage delay
over an hour; none on 1 September. `journey_consistent` cannot catch this at scoring time
because the journey has been corrected by then — it would have to run at *prediction*
time, on what the generator actually sees.

**Also found: the generator has no ceiling on lead time.** Those A731 predictions were made
16–17 hours ahead of the scheduled arrival, well outside anything the model was trained on
(1–10 observed stops). `lead_band` labelled them "60+ min" and nothing refused them.

**What the two mechanisms explain, and what they do not.** On 2 September, excluding the
four stations and the phantom-vantage rows moves MAE 102.0s → 97.9s — about four seconds of
a seventeen-second jump. Excluding all 51 rows over thirty minutes moves it to 87.3s
against 1 September's 83.4s. The jump is the tail; most of the tail is real.

**Interval coverage, which prompted the look.** Live coverage was 77.3% and 75.5% on the
two full days against a nominal 80%. The hypothesis was that quantiles were calibrated on a
well-covered population and live evaluation spans everything. **Not supported**: polled 30
stations 76.1% / 75.3%, everywhere else 77.8% / 75.5%, and offline the same split is
80.4% / 80.2%. The structure is by corridor, not by polled-ness:

| group | offline val | live 1 Sep | live 2 Sep |
|---|---|---|---|
| dart | 80.7% | 81.2% | 78.5% |
| dublin_hubs | 80.6% | 79.2% | 81.7% |
| commuter_maynooth | 82.3% | 79.3% | 79.6% |
| **commuter_kildare** | 79.3% | **68.8%** | **64.0%** |
| **intercity_cork_corridor** | 79.1% | **63.1%** | **66.7%** |

The model was calibrated at 79% on the Cork corridor and the Kildare line in July, and is
covering 63–69% there now. Those two groups share track (D29). That is a change in the
railway between July and September, not in the model's training population, and two days
is not enough to say what. Misses are skewed upward — 12.9% / 14.3% above q90 against 9.8%
/ 10.2% below, where offline was balanced at 9.6 / 10.2 — so trains are running later than
the interval's top more often than they did in July.

**One population difference that cannot be checked from the logs.** Offline evaluation
requires `AutoArrival=1` at the vantage *and* the target. Live scoring requires it only at
the target; the generator's vantage selection does not look at the flag, and vantage
`AutoArrival` is not logged. Worth logging.

**Options, for the retraining decision.**

- *Retrain with `journey_consistent` applied to the training examples.* The clean fix.
  Costs 3.7% of journeys, mostly on the Galway line. Goes through the champion/challenger
  gate like any other model. The trigger is not the documented one — rolling MAE has not
  risen for a week — it is a label-quality finding, which is a different and stronger
  reason.
- *Leave the model; exclude the four stations from what is published.* Cheaper, honest
  about the numbers, dishonest about the service: the API still serves 40-minute-late
  predictions at Athenry to anyone who asks.
- *Both, in that order.* Exclusion is a one-line evaluation-time decision that can be made
  today; the retrain takes a validation week.

**Generator changes proposed alongside, not yet made.** A lead ceiling, so a prediction
more than a few hours ahead is refused rather than logged; `journey_consistent` at
prediction time, so a phantom vantage declines instead of predicting; and vantage
`AutoArrival` on every logged row. All three change what the generator logs, which is the
live population mid-measurement — so they are proposed here rather than applied quietly.

**Date.** 2026-09-03

---

## D57 — The retrain on consistent journeys: what it fixed, what it revealed, and a gate that cannot pass

**Why this matters:** the model no longer tells people at Athenry that their train is
forty minutes late; and it turns out no version of it has ever been able to see a
severe delay coming.

**In plain terms.** The model was retrained with the wrong-train arrivals (D56) removed
from what it learns from. At the four Galway-line stations its answers went from absurd to
sensible. Everywhere else it is the same model to within noise. And when asked "does it
now handle genuinely severe delays better?", the honest answer is that it never handled
them at all — the old model only looked as if it did, because its garbage-wide intervals
happened to swallow the garbage labels.

**Status.** Challenger trained and saved as `20260903T173007Z-5ebf03f`. **Not promoted.**
The gate as written in CLAUDE.md failed on one group by an amount inside its own noise,
and promotion is the model owner's decision, not the retrain's.

**The two verifications asked for before retraining.**

*Is the criterion concentrated on the Galway line?* No. Over train+validation, 583 of
16,776 journeys (3.48%) fail `journey_consistent`, and only 32% of those touch a
Galway-line station. The station where the inversion is first detected: ATMON 101,
MLLOW 61, CNLLY 53, GL368 41, BRAY 37, HWTHJ 29, GALWY 28. Routes: Heuston–Galway 167,
Tralee–Cork 61, then DART and Maynooth-line services. But it is not catching *something
else* — it is catching the same defect at lower density. A131 (Belfast–Connolly) on
27 June: Clontarf Road "arrived" at 23:49:18, twenty-seven minutes after the train
terminated at Connolly at 23:22:18. An arrival filed against the wrong train, at a Dublin
station on no warning list. The Galway line is where the defect is dense enough to move a
station's median; elsewhere it is a tail. So the criterion is targeted at the defect
class, not at a geography, which is the better property.

*Is it computable from labels alone?* Yes, and checkably so. `feedtime.py` has no imports
at all. `journey_consistent` reads three fields per stop — `order`, `sched`, `delay` —
all straight from the feed, and asks whether `sched + delay` is non-decreasing in route
order. No model, no prediction, no residual. It encodes a physical fact: one train cannot
reach a later stop before an earlier one. That is what makes this label cleaning rather
than removing hard cases — a hard case is a true label the model gets wrong; these are
labels that cannot be true.

**The rebuild.** Applied in `build_examples.py` before any example is cut, so train,
validation and test are filtered identically and the criterion never sees a model output.
744 journeys dropped: train 425, validation 158, test 161. Examples: train 474,996 (was
500,243), validation 221,943 (was 230,966), test 221,365 (was 230,397). **The test week
was filtered and remains unopened.** `--keep-inconsistent` reproduces the previous set.

**Both validation figures, as required.**

| population | champion MAE | coverage | n |
|---|---|---|---|
| all journeys (previous example set) | **76.1s** | 80.2% | 226,370 |
| consistent journeys only (96.1% of rows) | **60.6s** | 80.3% | 217,653 |

The champion is the same artifact in both rows; only the evaluation set changed. About
fifteen seconds of the published validation MAE was contamination. The head-to-head
against the operator is unaffected: it runs over the 30 polled stations, none of which is
on the Galway line, and the polled-30 MAE is 65.8s before the filter and 65.7s after.

**Champion versus challenger, identical cleaned validation, `AutoArrival=1` both ends,
n=217,653.**

| subset | n | MAE ch → cl | median ch → cl | coverage ch → cl |
|---|---|---|---|---|
| overall | 217,653 | 60.6 → **59.7** | 29.0 → 29.0 | 80.3 → 80.2 |
| polled 30 | 55,323 | 65.7 → 65.8 | 33.2 → 33.0 | 80.5 → 80.3 |
| everywhere else | 162,330 | 58.9 → 57.6 | 27.7 → 27.8 | 80.2 → 80.1 |
| **4 Galway stations** | 324 | **1,055.6 → 413.1** | **483.2 → 97.0** | 76.9 → 65.1 |
| weak_coverage | 424 | 111.3 → 112.1 | 68.4 → 69.3 | 86.3 → 84.9 |

Interval width at the Galway stations: **6,795s → 394s**. That is the fix, visible. The
coverage there *falls*, and that is correct: the 324 surviving Galway validation labels
still include garbage that happened to be monotonic, and the champion "covered" it with
two-hour intervals. The challenger's honest interval does not cover a wrong label.

**The gate, literally applied.** MAE passes. `weak_coverage` fails on both median (+0.8s)
and coverage (−1.4 points). n=424. A bootstrap 95% interval on the difference: median
−5.4s to +8.7s, coverage −3.8 to +0.9 points. Both contain zero. The gate as written has no
tolerance and no minimum group size, so a 424-row group will fail it on noise for any
retrain, including a retrain of the identical model with a different seed.

**Proposed amendment to the gate, not yet adopted:** compare with a bootstrap interval
rather than a point, and require a minimum of ~1,000 rows before a group can veto. A
group below that is reported, not enforced.

**Severe delays — the answer to "does it improve too?"** It does not, and the reason is
the finding.

| actual delay | rows | champion cov | challenger cov | note |
|---|---|---|---|---|
| > 60 min, all | 76 | 27.6% | 2.6% | |
| > 60 min, Galway only | 23 | 91.3% | 8.7% | garbage labels, garbage-wide intervals |
| **> 60 min, excluding Galway** | **53** | **0.0%** | **0.0%** | real severe delays |

The champion's apparent competence on severe delays was 23 Galway rows. On the 53 real
ones neither model covers a single case, and the medians are ~3,500s apart from the truth
for both. Training labels over an hour: 1,032 before the filter, 191 after; at Galway 839
→ 12; everywhere else 193 → 179. The cleaning removed a fiction and left 179 real examples
of severe lateness in 475,000 — nowhere near enough to learn from.

**The Sligo test.** D925, D930 and A914 on 2 September, re-run with both models at the
exact vantages the generator used, sixteen predictions. Champion MAE 4,564s, challenger
4,557s, interval hits **0 of 16** for each. At every vantage the train was two to seven
minutes late; it then lost 75–89 minutes between Mullingar and Edgeworthstown. The
features — delay so far, stops remaining, time of day, route — carry no information about
a disruption that has not started. This is a limitation of the approach, not of either
model, and it goes on the page as such: **the intervals are calibrated for ordinary
lateness and do not cover disruptions.**

**What this does to the theme.** The 27.6% severe-delay coverage was the ninth silent
failure: a number that looked like a modest capability and was garbage matching garbage.
It was not caught by any check; it was caught by asking, after the cleaning, why the
number had *fallen*.

**Process notes worth keeping.** Two retrains were discarded before the one above. The
first ran against the old examples because the rebuild had crashed on an import-order
error and `| tail` masked the non-zero exit; the second carried a `-dirty` suffix because a
`printf` restore of `LATEST` did not byte-match the committed file. D32's dirty flag did
its job both times — an artifact from an unclean tree announced itself in its own name —
and both were deleted before anything read them.

**Date.** 2026-09-03

---

## D58 — The generator refuses out-of-envelope questions, from 17:25 UTC on 3 September

**Why this matters:** the service was answering "seventy minutes late" for a train the
feed said had arrived somewhere it could not have been yet; now it says it cannot answer.

**Decision.** Three changes to what the generator — and the API behind it — will predict
from. Live from **2026-09-03 17:25:05 UTC** (deploy complete); first cycle under the new
rules at 17:29:37 UTC. The scoreboard has a discontinuity at that instant and
`decline_reasons` gains two values from it.

1. **A lead ceiling of four hours** (`feedtime.MAX_LEAD_SEC`), declined as
   `lead_out_of_range`. The longest scheduled journey on the network is Heuston–Tralee at
   3.89 hours; the 95th percentile of journey spans is 2.66. A lead past four hours cannot
   belong to a real journey in progress, whatever the feed says. Read off the timetable,
   not chosen. The generator had predicted 16–17 hours ahead for A731 on the strength of a
   fleet-feed entry reading "(-1013 mins late) Arrived Athlone" at dawn.
2. **`journey_consistent` at prediction time**, declined as `journey_inconsistent`. The
   scorer already applied it; applying it where the prediction is made prevents the wrong
   answer instead of refusing to grade it. Declining with a stated reason is the
   established pattern (D39), not a new one.
3. **Vantage `AutoArrival` logged** on every predicted row. Offline evaluation requires the
   flag at both ends; live scoring could only check the target because the vantage flag
   was never written down. Changes no output.

**What the ceiling does and does not catch.** Of 37 phantom-vantage rows on 2 September,
18 had leads beyond four hours and are caught. 19 — A461, A517, A519, with vantage delays
of +1.7h, −3.3h and −2.2h at plausible leads — are not. A single-stop phantom with a
believable lead passes both checks. A vantage-delay envelope would catch it and is not
added, because that would be a threshold on delay size, which D52 deliberately avoided.
Left as a known gap; the rows are visible in scoring as large over-predictions.

**Verified on the first cycle:** 122 rows, 104 predicted, every one carrying
`vantage_auto`, 18 declined `no_upstream_report`. `/health` 200. Generator errors alarm
OK.

**Date.** 2026-09-03
