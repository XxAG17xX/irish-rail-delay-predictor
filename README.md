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

Ingestion works. Nothing is modelled yet.

- [x] API feasibility probes, field reference, known data-quality issues
- [x] Historical availability established — `TrainDate` is honoured back to at least 2007
- [x] `harvest_codes.py` — discovers which train codes exist
- [x] `backfill.py` — downloads raw journey history, resumable and throttled
- [x] `inspect_raw.py` — read-only survey of the archive
- [ ] Parser: raw XML → Parquet
- [ ] Label-quality check per line
- [ ] Baseline, then a LightGBM quantile model
- [ ] FastAPI service and deploy

Current archive: 30 dates × 36 train codes. The code list is thin — it came from a single
late-evening poll, against an expected ~600 codes for a full service day.

## Layout

```
src/         pipeline — writes data
  harvest_codes.py   poll getCurrentTrainsXML, accumulate train codes
  backfill.py        download raw getTrainMovementsXML per (date, code)
scripts/     one-off probes and surveys — read-only
  probe_irishrail.py           original feasibility check
  probe2_nulls_and_history.py  null semantics and historical availability
  inspect_raw.py               survey what is actually on disk
docs/
  data-dictionary.md   field reference, provenance-tagged, and the label-quality risks
  decisions.md         design decision log — what was chosen, what was rejected, why
  feature-ideas.md     candidate model inputs
data/        gitignored, never committed
```

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

Harvest train codes. Run this across a full service day (~05:30 to after midnight), on a
weekday and again on a weekend — the timetables differ.

```powershell
python src\harvest_codes.py
```

Download raw history for a date range. Safe to interrupt; rerun the same command to
resume.

```powershell
python src\backfill.py --start 2026-06-25 --end 2026-07-24 --dry-run
python src\backfill.py --start 2026-06-25 --end 2026-07-24
```

Survey what landed.

```powershell
python scripts\inspect_raw.py
python scripts\coverage_by_location.py
```

Parse the raw archive into Parquet, partitioned by date. Idempotent — already-written
partitions are skipped.

```powershell
python src\parse_raw.py
```

Build training examples, then the baselines a model must beat, then the model.

```powershell
python src\build_examples.py
python src\baseline.py
python src\train_quantile.py
```

Capture the operator's live `ExpectedArrival` — the benchmark, which cannot be
backfilled. A missed poll is lost permanently. Polls the 30 stratified stations in
[config/poll_stations.toml](config/poll_stations.toml); `--all-stations` for all 171.

```powershell
python src\poll_live.py
```

The three collectors take an exclusive lock and refuse to run concurrently — the
2 req/s budget is per host, not per script, so running two would silently double it.

The collectors will not run at the same time — `src/hostlock.py` enforces it, because the
2 requests/second budget is per host rather than per script.

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

- [CLAUDE.md](CLAUDE.md) — project scope, working agreement, what is deliberately out of scope
- [docs/data-dictionary.md](docs/data-dictionary.md) — every field, provenance-tagged, plus the label-quality risks that shape the whole project
- [docs/decisions.md](docs/decisions.md) — decision log
- [docs/feature-ideas.md](docs/feature-ideas.md) — candidate model inputs
