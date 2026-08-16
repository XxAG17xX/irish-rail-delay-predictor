# Decision log

One entry per significant design choice: what was decided, what else was on the table,
why it lost, and when. The point is to be able to defend every choice later without
re-deriving the reasoning.

Append new entries at the bottom. Do not edit an old entry to reflect a changed mind —
add a new entry that supersedes it, and note the supersession in both.

Entries D1–D10 were written on 2026-07-25 and cover decisions made up to that point, a
few of which were settled slightly earlier in the same week. Dates from D11 on are the
date the decision was actually made.

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

**Dates.** Start: the first Lambda invocation (record the actual date here on the day).
**Hard stop: seven days later, no extension.**

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

**A forced-failure test runs inside the week, not after it.** The Lambda is deliberately
broken mid-week — bad permissions and an induced timeout — to confirm the alarms actually
fire. An alarm that has never fired is not a verified alarm. Doing this during the window
rather than extending to a second week gets the same evidence for no extra calendar time,
which matters against a mid-September deadline.

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
