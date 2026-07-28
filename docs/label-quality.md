# Label quality — the echo problem

Written to be read cold. No prior knowledge of this project assumed.

Measured 2026-07-28 against 26,532 archived responses covering 32 dates: 481,935
movement records, of which 333,193 have a reported arrival time.

---

## 1. The problem in one paragraph

This project predicts how late a train will be. To train a model on that, we need to know
how late trains actually *were* — the true arrival time at each stop. Irish Rail's API
gives us a field for it. But on some parts of the network the operator does not really
observe arrivals, and the field comes back holding **the timetable** instead of an
observation. The value looks completely normal. Nothing marks it as unobserved. If we
train on it, the model learns that those parts of the network are perfectly punctual, and
it will confidently predict on-time arrivals for trains that are routinely late.

We call this an **echo**: the schedule echoed back at us dressed as a measurement.

---

## 2. Three time fields, and why they must not be confused

Every stop record carries several times. Three matter, and they mean different things.

| Field | What it is | Role here |
|---|---|---|
| `ScheduledArrival` | The **timetable**. What was planned, published months ahead. | The thing an echo copies. |
| `ExpectedArrival` | Irish Rail's **own live prediction** while the train is running — their ETA, revised as the journey progresses. | The **benchmark we compete against** later. Beating it is the project's success criterion. It is not a label and must never be a model input. |
| `Arrival` | The **reported actual** — what the operator says happened. | Our label. Also the field that can be an echo. |

**The echo test compares `Arrival` against `ScheduledArrival` only.** `ExpectedArrival`
plays no part in it. It is listed here because the three are easy to conflate, and
conflating them would either leak the answer into the model or destroy the comparison the
whole project is built on.

The test itself is simple. A genuine arrival almost never lands on exactly the scheduled
second. So: **how often does `Arrival` exactly equal `ScheduledArrival`?** A high rate is
the echo signature.

Across the whole archive that rate is **2.92%** — 9,356 exact matches out of 319,980
records where both times were present and comparable.

---

## 3. The first answer, which was wrong

Irish Rail's documentation names ten lines with weak real-time coverage and warns that
there, "your query will return the scheduled time only". The obvious test: flag every
location whose name matches one of those lines, and compare.

| Group | Comparable records | Exact matches | Exact rate |
|---|---|---|---|
| Flagged lines | 3,439 | 730 | **21.23%** |
| Everywhere else | 316,541 | 8,626 | **2.73%** |

An eightfold difference. It looks like clean confirmation of the documentation, and the
obvious next step is to distrust those lines.

That conclusion is wrong. Not the arithmetic — the interpretation.

---

## 4. The field that actually explains it

Every record with a reported arrival also carries `AutoArrival`, a flag saying whether the
time was **captured automatically** by the signalling system (`1`) or **not** (`0`).

That single field separates the data far more sharply than any line name:

| `AutoArrival` | Comparable records | Exact matches | Exact rate |
|---|---|---|---|
| `1` — machine-captured | 313,479 | 7,443 | **2.37%** |
| `0` — not machine-captured | 6,501 | 1,913 | **29.43%** |

Machine-captured times almost never coincide with the timetable. Non-auto times do so
twelve times more often. That is where the echoes live.

---

## 5. The four-cell table — where the first answer falls apart

Split both ways at once:

| Line group | `AutoArrival` | Comparable | Exact | Exact rate |
|---|---|---|---|---|
| Flagged | `0` | 1,616 | 712 | **44.06%** |
| Flagged | `1` | 1,823 | 18 | **0.99%** |
| Unflagged | `0` | 4,885 | 1,201 | **24.59%** |
| Unflagged | `1` | 311,656 | 7,425 | **2.38%** |

Read the middle rows carefully.

**Among machine-captured records, flagged lines echo *less* than everywhere else —
0.99% against 2.38%.** The flagged lines are not worse. On the records that were actually
measured, they are slightly *better* than the network average.

The aggregate says flagged lines are eight times worse. Every subgroup says they are the
same or better. A comparison that reverses direction when you split it is
**Simpson's paradox**, and it happens when the groups being compared have different
internal mixes.

---

## 6. The arithmetic, spelled out

The mix is the whole story. What share of each group's records are non-auto?

- Flagged lines: 1,616 of 3,439 comparable records are `AutoArrival=0` → **46.99%**
- Unflagged lines: 4,885 of 316,541 → **1.54%**

Flagged lines have roughly **thirty times** the proportion of non-auto records. Now
rebuild their headline number from the parts:

```
flagged aggregate = (1,616 × 44.06%) + (1,823 × 0.99%)
                    ---------------------------------
                                  3,439

                  = (712 + 18) / 3,439  =  730 / 3,439  =  21.23%
```

**712 of those 730 exact matches — 97.53% — come from the non-auto cell**, which is under
half the records. The 21.23% is not a property of the lines. It is 44% echo diluted by
some clean data.

The counterfactual makes it concrete. Give flagged lines the *same* auto/non-auto mix as
everywhere else, keeping their own within-cell rates:

```
(1.54% × 44.06%) + (98.46% × 0.99%)  =  0.68% + 0.97%  =  1.65%
```

At the network's normal mix, flagged lines would come out at **1.65%** — *better* than the
2.73% measured everywhere else. Their bad headline number is entirely an artefact of how
much of their data is hand-entered.

---

## 7. Why a line-based filter fails in both directions

Count the suspect records — the non-auto ones — by group:

| Group | `AutoArrival=0` records |
|---|---|
| Flagged lines | 1,616 |
| Unflagged lines | **4,885** |

**75.14% of all suspect records sit on lines the documentation never flagged.** Three out
of every four.

So a filter built on line names fails twice over:

- **It misses most of the problem.** Three quarters of non-auto records are outside the
  flagged set entirely, and they echo at 24.59% — badly.
- **It condemns good data.** It would discard 1,823 machine-captured records on flagged
  lines that echo at 0.99%, which is better than the network average.

`AutoArrival` has none of these failure modes. It is present on every record with an
arrival, it needs no name matching, and it works on the 41 locations whose
`LocationFullName` comes back empty. It is a per-record fact rather than a guess about
which line a station sits on.

---

## 8. An exact match is suspicious, not proof

This is the part that stops the filter being a delete button.

Machine-captured records — the ones we have every reason to trust — still match the
schedule exactly **2.37%** of the time. Some trains genuinely do arrive on the scheduled
second. That 2.37% is the **coincidence floor**: the rate you would expect from luck
alone, with no echo involved.

The floor is that high partly because arrival and scheduled times are both recorded in
**6-second steps** — only ten distinct second-values exist within a minute, so collisions
are far commoner than to-the-second timing would suggest.

The consequence: within the non-auto group, **70.57% of records are *not* exact matches**.
Those are almost certainly real observations that happen to have been entered by hand.

Deleting every `AutoArrival=0` record to remove 1,913 echoes would also delete about 4,588
genuine ones. Worse, it would fall hardest on exactly the lines already worst served —
non-auto is 46.99% of the flagged lines' arrivals, so the cut would come close to erasing
them from the dataset. A model trained on what remained would have nothing to say about
Cork, Tralee or Westport, which are precisely the places a passenger most wants an honest
answer.

**So: flag, keep, and decide later.** `AutoArrival` is carried through the pipeline as a
column. Whether to exclude non-auto records is an evaluation-time decision, made with
numbers reported both ways, not an ingestion-time deletion.

---

## 9. Summary

1. `Arrival` sometimes holds the timetable rather than an observation. We call it an echo.
2. The test is `Arrival == ScheduledArrival`, exactly. Archive-wide that is 2.92%.
3. Flagged lines look eight times worse in aggregate — 21.23% against 2.73%.
4. That reverses on splitting by `AutoArrival`: within machine-captured records, flagged
   lines are *better*, 0.99% against 2.38%. Simpson's paradox.
5. The cause is composition — 46.99% of flagged-line records are non-auto, against 1.54%
   elsewhere — and 97.53% of their exact matches sit in that one cell.
6. 75.14% of suspect records are on unflagged lines, so a line-name filter misses most of
   the problem while discarding good data.
7. `AutoArrival` replaces the line list. It is per-record, universal, and needs no proxy.
8. An exact match is suspicious, not proven fake — the coincidence floor is 2.37%, and
   70.57% of non-auto records are not exact matches.
9. Therefore: flag and keep. Decide at evaluation time, and report both ways.

Design decisions behind this are logged in [decisions.md](decisions.md) as D20–D23. Field
definitions and provenance are in [data-dictionary.md](data-dictionary.md).
