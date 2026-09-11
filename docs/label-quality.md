# Label quality: the echo problem

RailCast predicts how late an Irish Rail train that is already running will arrive further
along its route. Training a model to do that requires knowing how late trains actually were,
and on part of the network the feed reports the timetable instead of an observation. This
note identifies the affected records, and shows why the obvious way of finding them is wrong.

Measured 2026-07-28 against the archive as it then stood: 26,532 archived responses over 32
dates, 481,935 movement records, 319,980 of them comparable, meaning an `Arrival` is present
and there is a real `ScheduledArrival` to compare it against. The archive has since grown to
28,706 responses over 34 dates; the analysis has not been rerun, so every figure below is
the 2026-07-28 one.

## 1. The problem

The feed carries a reported arrival time for every stop. On parts of the network the
operator does not observe arrivals, and that field comes back holding **the timetable**
instead. The value looks normal. Nothing in the record marks it as unobserved. A model
trained on it learns that those parts of the network are perfectly punctual, and will
confidently predict on-time arrivals for trains that are routinely late.

Such a record is an **echo**: the schedule returned dressed as a measurement.

## 2. Three time fields

| Field | What it is | Role here |
|---|---|---|
| `ScheduledArrival` | The **timetable**. What was planned, published months ahead. | The thing an echo copies. |
| `ExpectedArrival` | Irish Rail's **own live prediction** while the train is running, revised as the journey progresses. | The **benchmark the model is measured against**. Never a label, never a model input. |
| `Arrival` | The **reported actual**: what the operator says happened. | The label. Also the field that can be an echo. |

The echo test compares `Arrival` against `ScheduledArrival` only. `ExpectedArrival` plays no
part in it, and conflating the two would either leak the benchmark into the model or destroy
the comparison the project is built on.

A genuine arrival almost never lands on exactly the scheduled second, so the test is: how
often does `Arrival` exactly equal `ScheduledArrival`? A high rate is the echo signature.
Across the archive that rate is **2.92%**, 9,356 exact matches in 319,980 comparable records.

## 3. The line-based answer, and why it was wrong

Irish Rail's documentation names ten lines with weak real-time coverage and warns that there
"your query will return the scheduled time only". The obvious test flags every location
whose name matches one of those lines, and compares.

| Group | Comparable records | Exact matches | Exact rate |
|---|---|---|---|
| Flagged lines | 3,439 | 730 | **21.23%** |
| Everywhere else | 316,541 | 8,626 | **2.73%** |

An eightfold gap, in the direction the documentation predicts. It reads as clean
confirmation, and what it suggests next is to start distrusting those lines.

The arithmetic is right. The interpretation is not.

## 4. The field that explains it

Every record with a reported arrival also carries `AutoArrival`, a flag saying whether the
time was **captured automatically** by the signalling system (`1`) or **entered by hand**
(`0`). That single field separates the data far more sharply than any line name:

| `AutoArrival` | Comparable records | Exact matches | Exact rate |
|---|---|---|---|
| `1`, machine-captured | 313,479 | 7,443 | **2.37%** |
| `0`, hand-entered | 6,501 | 1,913 | **29.43%** |

Machine-captured times almost never coincide with the timetable. Hand-entered times do so
twelve times more often. That is where the echoes are.

## 5. Both splits at once

Splitting by line and by capture method together gives four cells. Each shows the
exact-match rate with the counts behind it.

| Line group | Machine-captured (`AutoArrival=1`) | Hand-entered (`AutoArrival=0`) | All records |
|---|---|---|---|
| Flagged lines | **0.99%** (18 / 1,823) | 44.06% (712 / 1,616) | **21.23%** (730 / 3,439) |
| Everywhere else | **2.38%** (7,425 / 311,656) | 24.59% (1,201 / 4,885) | **2.73%** (8,626 / 316,541) |

Compare the two rows one column at a time.

**Machine-captured records, 313,479 of the 319,980 and so 98% of the data: flagged lines
echo at 0.99% against 2.38% everywhere else.** On the records that were actually measured,
the flagged lines are better than the network average, and the comparison runs in the
opposite direction to the aggregate.

Hand-entered records: flagged lines echo at 44.06% against 24.59%, so worse, but by a factor
of 1.8 rather than the 8 the aggregate advertises.

The eightfold gap exists only in the last column. It reverses against the stratum holding
98% of the records and shrinks to under a fifth of its size in the other. That is
**Simpson's paradox**: a comparison that changes direction when the data is split, because
the groups being compared have different internal mixes rather than different behaviour.

Echo risk on a flagged line is not imaginary. Meeting a flagged line's record cold, the risk
really is 21.23%. What the split shows is that the line name is a proxy for how the time was
captured, and a lossy one, while `AutoArrival` states the capture method outright.

## 6. The arithmetic

The mix is what does the work. What share of each group's records are hand-entered?

- Flagged lines: 1,616 of 3,439 comparable records, **46.99%**
- Everywhere else: 4,885 of 316,541, **1.54%**

Flagged lines carry roughly **thirty times** the proportion of hand-entered records. Their
headline number rebuilds from the two cells:

```
flagged aggregate = (712 + 18) / (1,616 + 1,823)
                  =     730    /     3,439
                  =        21.23%
```

**712 of those 730 exact matches, 97.53%, come from the hand-entered cell**, which is under
half the records.

The counterfactual makes the point concrete. Give the flagged lines the same split between
hand-entered and machine-captured as the rest of the network, 1.54% against 98.46%, and keep
their own echo rate within each cell:

```
0.0154 × 44.06%  +  0.9846 × 0.99%  =  0.68% + 0.97%  =  1.65%
```

At the network's normal mix the flagged lines come out at **1.65%**, better than the 2.73%
measured everywhere else. Their bad headline number is an artefact of how much of their data
is hand-entered.

## 7. Two ways a line-based filter fails

Counting the suspect records, the hand-entered ones, by group:

| Group | `AutoArrival=0` records |
|---|---|
| Flagged lines | 1,616 |
| Unflagged lines | **4,885** |

**75.14% of all suspect records sit on lines the documentation never flagged.** Three out of
every four. A filter built on line names therefore fails twice over:

- **It misses most of the problem.** Three quarters of hand-entered records are outside the
  flagged set entirely, and they echo at 24.59%.
- **It condemns good data.** It would discard 1,823 machine-captured records on flagged lines
  that echo at 0.99%, better than the network average.

Name matching is also only ever a proxy: the documentation lists *lines*, the data has
*locations*, and intermediate stops such as Carrigtwohill on the Cobh line never matched the
keyword list at all. `AutoArrival` has none of these failure modes. It is present on every
record carrying an arrival, needs no name matching, and works on the 41 locations whose
`LocationFullName` comes back empty. It is a per-record fact rather than a guess about which
line a station sits on.

## 8. The coincidence floor

Machine-captured records still match the schedule exactly **2.37%** of the time. Some trains
genuinely do arrive on the scheduled second. That 2.37% is the **coincidence floor**: the
rate expected from luck alone, with no echo involved. The floor is that high partly because
arrival and scheduled times are both recorded in **6-second steps**, so only ten distinct
second-values exist within a minute and collisions are commoner than to-the-second timing
would suggest (D22).

An exact match is therefore suspicious, not proof. Within the hand-entered group **70.57% of
records are not exact matches**, and those are almost certainly real observations that
happened to be typed in.

Deleting every `AutoArrival=0` record to remove 1,913 echoes would also delete about 4,588
genuine ones, and would fall hardest on the lines already worst served: hand-entered records
are 46.99% of the flagged lines' arrivals, so the cut would come close to erasing them from
the dataset. A model trained on what remained would have nothing to say about Cork, Tralee
or Westport, which are among the places a passenger most wants an honest answer.

**The policy is flag, keep, and decide later.** `AutoArrival` is carried through the pipeline
as a column. Whether to exclude hand-entered records is an evaluation-time decision, made
with numbers reported both ways, not an ingestion-time deletion (D23).

## 9. Summary

1. `Arrival` sometimes holds the timetable rather than an observation. The test for such an
   echo is `Arrival == ScheduledArrival`, exactly; archive-wide that is 2.92%.
2. Flagged lines look eight times worse in aggregate, 21.23% against 2.73%. That reverses on
   splitting by `AutoArrival`: within machine-captured records, 98% of the data, flagged
   lines are better at 0.99% against 2.38%. Simpson's paradox, driven by composition, since
   46.99% of flagged-line records are hand-entered against 1.54% elsewhere.
3. 75.14% of suspect records are on unflagged lines, so a line-name filter misses most of the
   problem while discarding good data. `AutoArrival` replaces the line list: per-record,
   universal, no proxy.
4. An exact match is suspicious, not proven fake. The coincidence floor is 2.37% and 70.57%
   of hand-entered records are not exact matches, so the policy is flag and keep, decide at
   evaluation time, and report both ways.

The line-based result went unchallenged for a full analysis cycle because it agreed with the
documentation. What overturned it was not a better idea but a per-record field that allowed
the aggregate to be decomposed (D20).

Design decisions behind this are logged in [decisions.md](decisions.md) as D20–D23. Field
definitions and provenance are in [data-dictionary.md](data-dictionary.md).
