# Irish Rail Realtime API — Data Dictionary

Working notes for the rail-delay project. Records what the official documentation says,
what we established empirically, and what remains open.

**Provenance is marked on every claim:**

- **[DOC]** stated in Irish Rail's own documentation at `https://api.irishrail.ie/realtime/`
- **[VERIFIED]** observed directly in API responses by us
- **[INFERRED]** our interpretation, consistent with observation but not proven
- **[UNKNOWN]** open question

Last updated: 2026-07-25

---

## 1. Service overview

Base URL: `http://api.irishrail.ie/realtime/realtime.asmx`
XML namespace: `http://api.irishrail.ie/realtime/`
Auth: none. No key, no registration. **[VERIFIED]**
Protocol: ASMX / SOAP service, also accepts plain HTTP GET. **[DOC]**

**Endpoint and parameter names are case sensitive.** **[DOC]**

Irish Rail provides the data "as is" and offers no support. **[DOC]**
No published rate limit. Be conservative — throttle to ~2 requests/second. **[INFERRED]**

---

## 2. Endpoints in use

| Endpoint | Purpose |
|---|---|
| `getAllStationsXML` | station list: StationDesc, StationCode, StationId, StationAlias, StationLatitude, StationLongitude **[DOC]** |
| `getCurrentTrainsXML` | trains between origin and destination, or starting within 10 minutes **[DOC]** |
| `getStationDataByCodeXML_WithNumMins?StationCode=&NumMins=` | trains serving a station in the next N minutes; N must be 5–90 **[DOC]** |
| `getTrainMovementsXML?TrainId=&TrainDate=` | per-stop schedule and actuals for one train — **our label source** |

`getAllStationsXML` returned 171 stations. **[VERIFIED]**
`getCurrentTrainsXML` returned 68 records (51 R, 13 N, 4 T) at ~16:50 on a Saturday. **[VERIFIED]**

---

## 3. `getTrainMovementsXML` — field reference

| Field | Meaning | Provenance |
|---|---|---|
| `TrainCode` | Irish Rail's unique code for one train service on one date. Trailing whitespace present — trim it. | **[DOC]** / whitespace **[VERIFIED]** |
| `TrainDate` | Date the service *started* its journey. Some services run past midnight. | **[DOC]** |
| `LocationCode` | 4–5 char location abbreviation | **[DOC]** |
| `LocationFullName` | Long name. **Sometimes empty** (e.g. codes PL277, LJ352). | empty case **[VERIFIED]** |
| `LocationOrder` | Sequence position along the journey | **[DOC]** |
| `LocationType` | `O` Origin, `S` Stop, `T` TimingPoint (non-stopping), `D` Destination | **[DOC]** |
| `ScheduledArrival` / `ScheduledDeparture` | Timetabled times. `00:00:00` at origin arrival and destination departure — structurally absent, not missing. | **[DOC]** / nulls **[VERIFIED]** |
| `ExpectedArrival` / `ExpectedDeparture` | Live prediction, updated as the train progresses | **[DOC]** |
| `Arrival` / `Departure` | **Actual** times. Empty element when absent. | **[DOC]** |
| `AutoArrival` / `AutoDepart` | Whether the time was automatically generated. Values `0` and `1` seen. Empty when the corresponding actual is empty. | meaning **[DOC]**, values **[VERIFIED]** |
| `StopType` | `C` Current, `N` Next. We have also observed `-`. | **[DOC]** / `-` **[VERIFIED]** |

### `TrainStatus` (in `getCurrentTrainsXML`)

| Value | Meaning | Provenance |
|---|---|---|
| `N` | not yet running | **[DOC]** |
| `R` | running | **[DOC]** |
| `T` | terminated — **observed but NOT documented** | **[VERIFIED]** |

Delay in `getCurrentTrainsXML` appears only as free text inside `PublicMessage`, e.g.
`A220\n16:00 - Dublin Heuston to Cork (-2 mins late)\nDeparted PL102 next stop Thurles`.
`\n` is a literal line break marker. **[DOC]**

---

## 4. Historical availability — CORRECTED 2026-07-25

**`TrainDate` is honoured and real history is served.** **[VERIFIED]**

An earlier assumption that only the current day worked was wrong. Evidence: requesting
train A218 for four past dates returned bodies whose own `TrainDate` matched the request,
with all 27 recorded arrival times differing between dates, and completed journeys
(destination arrival populated) for past dates while the same day's train was still
mid-journey.

| Requested | Records | Actual arrivals | Cork scheduled | Cork actual |
|---|---|---|---|---|
| 25 Jul 2026 (today) | 44 | 26 | 17:32:00 | – (in progress) |
| 15 Jan 2026 | 44 | 27 | 17:32:00 | 17:32:00 |
| 25 Jun 2026 | 44 | 27 | 17:32:00 | 17:32:00 |
| 25 Jun 2025 | 44 | 27 | 17:32:00 | 17:34:00 |
| 25 Jun 2024 | 44 | 28 | 17:32:00 | 17:32:00 |
| 25 Jun 2020 | **41** | 27 | **17:34:00** | 17:42:00 |

The 2020 response has three fewer timing points and a different scheduled arrival —
i.e. the 2020 timetable and 2020 infrastructure. Strong evidence of genuine archival
data rather than replayed current data. **[VERIFIED]**

**Horizon not yet established.** **[UNKNOWN]** — Irish Rail's own documentation
demonstrates the endpoint with `TrainDate=21 dec 2011`, suggesting the archive may reach
back ~15 years. **Test `TrainId=e109&TrainDate=21 dec 2011`.**

Caveat when probing: train codes are reassigned over time, so an empty response for an
old date may mean that code did not operate, not that history ends there. Test multiple
codes and weekday dates before concluding. **[INFERRED]**

---

## 5. Label quality — the central risk

### 5.1 Documented geographic coverage gaps

Irish Rail states real-time coverage is weaker on these lines, and that **"your query
will return the scheduled time only"**: **[DOC]**

- Athlone
- Westport / Ballina line
- Cork Station
- Cork – Cobh / Midleton line
- Mallow – Tralee line
- Ballybrophy – Limerick line
- Limerick – Ennis line
- Limerick Junction – Waterford line
- Greystones – Rosslare line
- Dundalk – Belfast line

**Implication: on these lines, `Arrival` may contain the scheduled time presented as an
actual observation.** This is far more dangerous than a null, because the field is
populated, the value is plausible, and no naive validity check will flag it. Left
unhandled it teaches the model that these lines are perfectly punctual.

Supporting observation: Cork (flagged) shows `Arrival` exactly equal to
`ScheduledArrival` on 25 Jun 2026 and 25 Jun 2024, but differing on 25 Jun 2025 and
25 Jun 2020 — so some records are genuine and some are likely echoes. **[VERIFIED]**

**OPEN — highest priority analysis:** measure the rate of exact
`Arrival == ScheduledArrival` matches on flagged lines versus well-covered lines
(e.g. Dublin – Portlaoise). If materially higher on flagged lines, treat exact matches
there as missing rather than observed. **[UNKNOWN]**

Also worth raising with Bidisha Ghosh — if internal Iarnród Éireann data is available,
it may not have this limitation.

### 5.2 Missing actuals at passed locations

Roughly 40% of movement records never receive an actual time: 26–28 of 44 records had
arrivals, and 14 of 40 already-passed locations had neither arrival nor departure.
**[VERIFIED]**

Three distinct kinds of null, which must not be conflated:

1. **Structural** — origin has no arrival, destination has no departure
2. **Future** — train has not reached that location yet
3. **Passed but unreported** — train demonstrably went by, nothing recorded

Category 3 locations observed on the Heuston–Cork route: `IBJCT` (Islandbridge Junction),
`HK101`, `HK151`, `HK157`, `HK177`, `CURAH` (Curragh), `CY112`, `PL277`, `LDUFF`
(Lisduff), `TS462`, `TS460`, `LJ352`, `RC894`. **[VERIFIED]**

Mostly cryptic-coded timing points, but some named stations too, so code format alone
does not predict silence. **[INFERRED]**

**OPEN:** are these locations *always* silent across all trains? If yes, exclude them
from the network graph rather than imputing values that will never exist. If only
sometimes, this is genuine sporadic missingness requiring explicit modelling. **[UNKNOWN]**

### 5.3 Anomalous individual records

Inchicore, 25 Jul 2026, train A218: `ScheduledArrival 15:05:00`, `Arrival 14:54:48` —
an arrival before the train's 15:00 scheduled departure from its origin at Heuston.
Every other timing point on the same journey shows arrival-to-departure gaps of seconds.

Status: **unresolved.** Not proven corrupt; not explained. **[UNKNOWN]**

**Policy: quarantine, do not delete.** Store the record, flag it, exclude from training
until understood. Resolve distributionally once thousands of journeys are available —
is an 11-minute arrival-to-departure gap at a timing point rare-but-real, or a bug?

Live feed has also shown trains flagged 319 and 499 minutes late, and services with
future scheduled departures already marked as having passed intermediate points. Sanity
bounds needed. **[VERIFIED]**

---

## 6. Operational notes

- Network is quiet roughly 00:30–05:30; near-empty responses are expected, not an outage. **[VERIFIED]**
- `TrainDate` format that works: `25 jul 2026` (lowercase month abbreviation). **[VERIFIED]**
- A train's set of timing points changes over the years — 41 records in 2020 vs 44 in 2026
  for the same service. Any location-keyed graph must be versioned by era, or restricted
  to a recent date range. **[VERIFIED]**
- Volume estimate: ~600 trains/day × ~44 records ≈ 26k records/day, ~10M/year. **[INFERRED]**

---

## 7. Modelling implications

1. **Recency vs volume.** More history is not automatically better. The 2020 data reflects
   COVID-era service patterns, a different timetable, and different infrastructure.
   Decide empirically — train on 1, 2 and 4 years and compare generalisation. Do not
   assume.
2. **Labels are cleanest at `O`/`S`/`D` records.** Timing points (`T`) give a far denser
   trajectory, valuable for delay-propagation modelling, but with worse coverage.
3. **Missingness is not random.** It is geographically clustered and documented. Any
   honest evaluation must report per-line coverage, and any claim about national accuracy
   must account for the fact that some lines are barely observed.
4. **Baseline to beat:** `ExpectedArrival` from the station-board endpoint is Irish Rail's
   own live prediction. Beating that is a much stronger claim than beating the timetable.
