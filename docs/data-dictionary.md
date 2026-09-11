# Irish Rail Realtime API field reference

Irish Rail publishes a free realtime feed for the Irish rail network: no key, no
registration, no documented rate limit, offered as-is with no support. The official
documentation lists the field names and stops there. This file records what that
documentation states, what direct observation of the responses established, and what is
still open, so that an engineer writing a parser against the feed meets the traps here
rather than in production.

It came out of RailCast, a delay predictor built on the feed
(https://dc9icf7494up8.cloudfront.net), but everything below is about the feed itself.

## Provenance marking

Every claim carries one of four tags, so a reader can tell a documented guarantee from an
observation from a guess without re-deriving any of it.

| Tag | Meaning |
|---|---|
| **[DOC]** | Stated in Irish Rail's documentation at `https://api.irishrail.ie/realtime/`. |
| **[VERIFIED]** | Observed directly in API responses. |
| **[INFERRED]** | An interpretation consistent with observation, not proven. |
| **[UNKNOWN]** | Open question, recorded so it is not mistaken for settled. |

A **[DOC]** tag is not a guarantee of truth. Section 5 is a case where the documentation
was accurate in outline and actively misleading as a filtering rule.

Measured figures come from an archive of 28,706 gzipped responses covering 34 dates in
summer 2026, 504,810 stop-level records. Record-level rates were computed on a 2026-07-28
snapshot of it: 481,935 records, 319,980 comparable for the echo test in section 5.

## 1. Service overview

Base URL `http://api.irishrail.ie/realtime/realtime.asmx`, XML namespace
`http://api.irishrail.ie/realtime/`. ASMX / SOAP service that also accepts plain HTTP GET.
**[DOC]** No auth of any kind. **[VERIFIED]**

**Endpoint and parameter names are case sensitive.** **[DOC]**

No published rate limit. Throttle to roughly 2 requests/second, back off exponentially,
honour 429 and 503, run bulk jobs overnight. **[INFERRED]** One request, in PowerShell:

```powershell
curl.exe "http://api.irishrail.ie/realtime/realtime.asmx/getTrainMovementsXML?TrainId=A220&TrainDate=25%20jul%202026"
```

## 2. Endpoints

| Endpoint | Purpose |
|---|---|
| `getAllStationsXML` | station list: `StationDesc`, `StationCode`, `StationId`, `StationAlias`, `StationLatitude`, `StationLongitude` **[DOC]** |
| `getCurrentTrainsXML` | trains between origin and destination, or starting within 10 minutes **[DOC]** |
| `getStationDataByCodeXML_WithNumMins?StationCode=&NumMins=` | trains serving a station in the next N minutes; N must be 5–90 **[DOC]** |
| `getTrainMovementsXML?TrainId=&TrainDate=` | per-stop schedule and actuals for one train. The only endpoint carrying ground-truth arrival times. |

`getAllStationsXML` returned 171 stations. **[VERIFIED]**

**No endpoint lists the trains that ran on a past date.** `getCurrentTrainsXML` shows only
what is moving now or starting within ten minutes, so building a per-date list of codes
means harvesting that endpoint across a service day and replaying the codes against past
dates. Codes are stable day to day, and since-discontinued services will be missing.

## 3. `getTrainMovementsXML` field reference

| Field | Meaning | Provenance |
|---|---|---|
| `TrainCode` | Unique code for one train service on one date. Trailing whitespace is present: trim it. | **[DOC]** / whitespace **[VERIFIED]** |
| `TrainDate` | Date the service *started*. Some services run past midnight, so a train running at 00:15 belongs to yesterday's date. | **[DOC]** |
| `LocationCode` | 4–5 character location abbreviation | **[DOC]** |
| `LocationFullName` | Long name. **Sometimes empty** (for example codes PL277, LJ352). | empty case **[VERIFIED]** |
| `LocationOrder` | Sequence position along the journey | **[DOC]** |
| `LocationType` | `O` Origin, `S` Stop, `T` TimingPoint (non-stopping), `D` Destination. **A fifth value `C` also occurs, undocumented: see section 7.** | **[DOC]** / `C` **[VERIFIED]** |
| `ScheduledArrival` / `ScheduledDeparture` | Timetabled times. `00:00:00` at origin arrival and destination departure, structurally absent rather than missing. | **[DOC]** / nulls **[VERIFIED]** |
| `ExpectedArrival` / `ExpectedDeparture` | The operator's own live prediction, revised as the train progresses. Exists only live, cannot be backfilled. | **[DOC]** |
| `Arrival` / `Departure` | **Actual** times. Empty element when absent. | **[DOC]** |
| `AutoArrival` / `AutoDepart` | Whether the time was captured automatically. Values `0` and `1` only. Empty when the corresponding actual is empty, and **never empty when `Arrival` is populated**. **The strongest label-quality signal in the feed: see section 5.** | meaning **[DOC]**, values and behaviour **[VERIFIED]** |
| `StopType` | `C` Current, `N` Next. The value `-` also occurs. | **[DOC]** / `-` **[VERIFIED]** |

`TrainStatus`, in `getCurrentTrainsXML`, takes `N` not yet running and `R` running
**[DOC]**, plus `T` terminated, which is observed but not documented. **[VERIFIED]**

Delay in `getCurrentTrainsXML` appears only as free text inside `PublicMessage`, for
example `A220\n16:00 - Dublin Heuston to Cork (-2 mins late)\nDeparted PL102 next stop
Thurles`. The `\n` is a literal two-character marker, not a newline. **[DOC]**

## 4. Historical data

**`TrainDate` is honoured, and real history is served back to at least 2007.**
**[VERIFIED]** This is the most useful undocumented property of the feed: a missed
collection run is recoverable, because the same date can be re-fetched later. It does not
apply to `ExpectedArrival`, which exists only while a train is running.

Verified by probing the boundaries rather than by trusting a successful response: 2007
returns data, 2006 does not, 2027 does not. A service generating from a timetable would
have answered for all three.

Confirmed genuine by comparing one train across dates. Every recorded time differs between
dates, past journeys are complete while the current day's is mid-flight, and the 2020
response carries 41 records against 2026's 44, with a different scheduled arrival at Cork:
the timetable and timing points of that era, not today's replayed. Weekend and weekday
patterns also appear correctly, with Sunday dates returning empty for most harvested codes
and Saturdays for almost none, consistently across four weeks. **[VERIFIED]**

Caveat when probing: train codes are reassigned over time, so an empty response for an old
date may mean that code did not operate then, not that history ends there. Test several
codes and several weekday dates before concluding anything about the horizon. **[INFERRED]**

## 5. Label quality

On parts of the network the operator does not observe arrivals directly, and `Arrival`
comes back holding **the timetable** presented as an observation. The value looks normal
and nothing marks it as unobserved. Train a model on it and the model learns that those
parts of the network are perfectly punctual.

Irish Rail's documentation names ten lines with weak realtime coverage and warns that
there, "your query will return the scheduled time only". **[DOC]** Filtering on that line
list is the obvious move, it appears to work, and it is wrong. Flagged lines echo eight
times more often in aggregate, but splitting by `AutoArrival` reverses it: among
machine-captured records the flagged lines echo *less* than everywhere else. The aggregate
gap is Simpson's paradox driven by composition, and three quarters of non-auto records sit
on lines the documentation never flagged. **[VERIFIED]**

**Use `AutoArrival`, not the line list.** It is per-record, so it survives into any split
or subgroup analysis, and a line-name filter fails in both directions.

**An exact match between `Arrival` and `ScheduledArrival` is suspicious, not proven fake.**
Machine-captured records still match exactly around 2% of the time, the coincidence floor.
The workable policy is flag and keep, with exclusion decided at evaluation time and results
reported both ways.

The full measurement, the four-cell table and the arithmetic are in
[label-quality.md](label-quality.md); the decisions are D20–D23 in
[decisions.md](decisions.md).

## 6. Missing actual times

**About 31% of movement records never receive an actual time.** 348,837 of 504,810 carry
an arrival, so 30.9% do not; excluding structural and future nulls, 28.5% are genuinely
unreported. **[VERIFIED]** Three kinds of null, which must not be conflated:

1. **Structural.** An origin has no arrival, a destination has no departure.
2. **Future.** The train has not reached that location yet.
3. **Passed but unreported.** The train demonstrably went by and nothing was recorded.

Category 3 locations observed on the Heuston–Cork route: `IBJCT` (Islandbridge Junction),
`HK101`, `HK151`, `HK157`, `HK177`, `CURAH` (Curragh), `CY112`, `PL277`, `LDUFF`
(Lisduff), `TS462`, `TS460`, `LJ352`, `RC894`. **[VERIFIED]** Mostly cryptic-coded timing
points, but named stations appear too, so code format alone does not predict silence.
**[INFERRED]**

**OPEN:** whether these locations are *always* silent across all trains. If they are, they
belong outside any network graph rather than being imputed. If only sometimes, this is
genuine sporadic missingness and needs modelling explicitly. **[UNKNOWN]**

## 7. `LocationType=C`

`C` occurs on 0.54% of records and is undocumented: the documentation lists only
`O`/`S`/`T`/`D` for `LocationType`, with `C` belonging to `StopType`. It appears on
ordinary named stations that are `S` elsewhere (Raheny, Harmonstown, Killester), on
consecutive `LocationOrder` values within one train, with `StopType` set to `-`.
**[VERIFIED]**

**It does not mark the train's position at capture time.** If it did, `C` would cluster on
the dates that were live when each response was fetched. It does not: `C` appears on 31 of
32 dates at rates between 0.07% and 1.86% with no trend, and the two most recently fetched
dates are among the lowest. **[VERIFIED]**

What it does look like is a contiguous segment of a route, stable per service across weeks.
Train D541 carries nine `C` records on every one of the 24 dates it ran, always the same
set (`CORK, CK78, CE453, LSLND, GHANE, FOTA, CGLOE, RBROK, COBH`, the Cork–Cobh line);
P541 is those nine reversed; P503 shows a different fixed set along Cork–Mallow. Only 5.8%
of `C` records carry a populated `Arrival`, against 87.7% for `S` stops. **[VERIFIED]**

**OPEN:** a stable contiguous segment where nine in ten records carry no actual time reads
as "not served on this run", a cancellation or curtailment marker. Not established, and
complicated by `C` appearing outside the weakly-covered lines: train E244's `C` records sit
on the DART Northern line. **[UNKNOWN]**

Whatever it means, **`LocationType` is not a stable property of a location.** The same
location is `S` on one record and `C` on another. A parser must preserve it as recorded and
must not key on it as location metadata.

## 8. Anomalous records

Inchicore, 25 July 2026, train A218: `ScheduledArrival 15:05:00` against `Arrival
14:54:48`, an arrival before the train's 15:00 scheduled departure from its origin at
Heuston. Every other timing point on that journey shows arrival-to-departure gaps of
seconds. Not proven corrupt, not explained. **[UNKNOWN]**

The live feed has also shown trains flagged 319 and 499 minutes late, and services with
future scheduled departures already marked as having passed intermediate points.
**[VERIFIED]** Sanity bounds are needed on any ingestion path.

Quarantine rather than delete: store the record, flag it, exclude it from training until
understood. Anomalies of this shape resolve distributionally once thousands of journeys are
available, and a record deleted at ingestion cannot be reconsidered.

## 9. Operational notes

- **All times are quantised to 6-second steps**, zero violations across 614,041 non-null
  delays, with only ten distinct second values within a minute (`:00 :06 :12 … :54`).
  **[VERIFIED]** Any histogram or density analysis of delay must divide by reachable
  6-second buckets rather than by seconds, or apparent spikes inflate sixfold. Six seconds
  being a tenth of a minute suggests the source stores decimal minutes and converts on
  output, which is a guess. **[INFERRED]**
- The network is quiet roughly 00:30–05:30. Near-empty responses then are expected, not an
  outage. **[VERIFIED]**
- `TrainDate` format that works: `25 jul 2026`, lowercase month abbreviation. Both padded
  and unpadded days are accepted (`05 jul 2026` and `5 jul 2026`). **[VERIFIED]**
- A train's set of timing points changes over the years, 41 records in 2020 against 44 in
  2026 for the same service. Any location-keyed graph must be versioned by era or
  restricted to a recent date range. **[VERIFIED]**
- Volume: roughly 600 trains/day at about 44 records each, so about 26k records/day and
  10M/year. **[INFERRED]**

## 10. Implications for modelling

1. **More history is not automatically better.** 2020 data reflects COVID-era service
   patterns, a different timetable and different infrastructure. Decide the training window
   empirically.
2. **Labels are cleanest at `O`/`S`/`D` records.** Timing points (`T`) give a far denser
   trajectory, useful for delay propagation, with worse coverage.
3. **Missingness is not random.** It is geographically clustered and partly documented, so
   an honest evaluation reports per-line coverage rather than a national figure that hides
   lines which are barely observed.
4. **`ExpectedArrival` is a benchmark, never an input.** It is the operator's own live
   prediction, which makes it the strongest thing to compare against and makes any model
   that consumes it impossible to evaluate.
5. **Carry `AutoArrival` through to training and evaluation**, and report accuracy both
   with and without non-auto records rather than silently choosing one. This cuts across
   point 3: null missingness is geographically clustered, echo risk is not, because it
   follows capture method instead of line.
