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
