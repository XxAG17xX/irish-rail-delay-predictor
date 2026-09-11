# The story of RailCast

Written for someone who does not code and does not know trains. Every technical term is
explained the first time it appears. The system is live at
https://dc9icf7494up8.cloudfront.net.

About twenty minutes end to end. Sections 1 and 2 are what the thing is and where the data
comes from. **If time is short, read section 3, section 9 and section 10**: a result that
confirmed the official documentation and was wrong anyway, what the live numbers say
including where they are bad, and a week of data sealed at the start and opened once at the
end. The last section is the thread running through all of them.

---

## 1. The question

Irish Rail's station boards give one expected time per train and no indication of how firm it
is. A train that has been steadily two minutes late for five stops and a train that has just
picked up an unexplained delay get the same kind of estimate.

RailCast answers with a range. Not "17:09" but "most likely 17:09, probably between 17:07 and
17:14". The range is the useful part: for a four-minute connection at the far end, a
one-minute-wide range means it is safe and an eight-minute-wide range means it is not. The
technical name is a **prediction interval**, a low bound and a high bound chosen so the real
answer lands between them a stated fraction of the time. This system claims 80%: four times in
five, the train arrives inside the range. That claim is checkable, which matters later.

The system had to be finished and running publicly before mid-September 2026. That deadline
shaped every decision below.

## 2. Where the numbers come from

Irish Rail run a public data feed, an **API** (a web address that returns data rather than a
web page, meant for programs instead of people). It needs no sign-up and no key and is offered
as-is with no support. It reports what is due at a station in the next ninety minutes,
including Irish Rail's own expected arrival for each train; which trains are running now; the
list of stations; and one train's full journey on one date.

That last one is the project. Ask for train A220 on 25 June and the answer is every stop on its
route with "was supposed to arrive 17:06:30, actually arrived 17:08:12". Those actual times are
what a model learns from. Whether the feed serves real history or replays the timetable for old
dates needed checking early, and probing by year settles it: 2007 returns data, 2006 does not,
2027 does not, and the same train in an earlier year comes back with different recorded times
and a different scheduled arrival at Cork, the timetable of that era. So a missed collection run
can be re-fetched. Irish Rail's own expected arrival is the exception: it exists only in the
live moment and cannot be backfilled.

Getting the data out was the first real problem. The feed serves one train on one date per
request, and no endpoint lists which trains ran on a given day, so a train's code (A220, D930,
E817) has to be known before it can be asked about. The fix: ask "which trains are running
right now?" every five minutes across a full service day, on a weekday and a weekend, collect
the codes as they appear, then replay that list against past dates. Held to one or two requests
a second out of manners on a free service, the full download came to **28,706** saved responses
across **1,087** train codes and **34** dates (25 June to 2 August 2026), about **504,810**
stop-level records. It ran once. Every reply is saved exactly as received before anything is
done with it, so when the interpretation of a field later changed, the archive could be
reprocessed rather than re-downloaded: keeping only the interpretation makes every mistake in
it permanent.

## 3. Times that were not real

This is the problem the project turned out to be about. Training a model on lateness requires
knowing how late trains actually were: the **label**, the right answer the model is learning
to predict. The feed has a field for it. On some parts of the network the operator does not
observe arrivals, and the field comes back holding the timetable, presented as an observation.
The value looks entirely normal and nothing marks it, so a model trained on it learns that
those lines are perfectly punctual. Call it an **echo**: the schedule reflected back dressed
as a measurement.

It is detectable because a real arrival almost never lands on exactly the scheduled second.
Irish Rail document the underlying issue themselves, listing around ten lines where a query
returns the scheduled time only, so the obvious fix was to distrust those lines. Testing it
looked like clean confirmation: the flagged lines matched the schedule many times more often
than the rest of the network.

That interpretation was wrong, and the correct version is more interesting than "the
documentation is wrong". Every arrival record also carries a flag (`AutoArrival`) saying
whether the time was captured automatically by the signalling equipment or entered some other
way. Split by that flag rather than by line and the comparison reverses in the subgroup
holding almost all the data: among machine-captured records the flagged lines echo **less**
than unflagged ones. Among hand-entered records they echo considerably more, so there the
documentation is right. The aggregate gap is composition. Far more of the flagged lines'
records are hand-entered, and hand-entered records are where echoes live. A comparison that
reverses when the data is split this way is **Simpson's paradox**: the groups have different
internal mixes, and the mix is doing the work being attributed to the groups.

So the finding is not that the flagged lines are fine. Meeting one of their records cold,
knowing nothing about how it was captured, the echo risk really is raised. The finding is that
**the line name is a lossy proxy for the capture method**, so filtering by line deletes good
machine-captured records and keeps bad hand-entered ones on lines the documentation never
mentions. The rule is the per-record capture flag. Four-cell table and full argument in
`docs/label-quality.md`, D20 to D23.

An exact match is suspicious, not proof. Some trains do arrive on the scheduled second, and
every time in the feed is rounded to the nearest **6 seconds**, so there are only ten possible
second-values in a minute and coincidences are commoner than they look. Nothing is deleted at
ingestion: the flag is carried through, exclusion is an evaluation-time decision, and results
are reported both ways. A result that agrees with the documentation is the one that gets
checked least carefully.

## 4. Teaching a model

A **model** here is a program that has learned, from many examples, to map a situation to a
prediction. The situation is described by a handful of numbers, the **features**, and two
rules decided what could be one.

**It must be knowable at the moment of asking.** The timetable is known, and so is how late
the train was at stops it has already reported from. Nothing about stops it has not reached is
known. A feature that quietly uses the finished journey looks excellent in testing and is
useless in practice, because in practice the journey is not finished. One such feature, a
count of how many stops would eventually report, was caught and cut at a negligible cost in
accuracy.

**Describe the situation, not the identity.** The train's code is not a feature, because a
model that learns "A218 runs two minutes late" has nothing to say about a service launched
next spring. **12 features** survived: how late it is now, how late it was at the two stops
before, how many stops and scheduled minutes remain, the time of day, the day of the week,
where it started and where it is going.

The model is **gradient boosting** (LightGBM), which builds a prediction from hundreds of
small decision rules, each correcting the errors of the ones before, and is the standard
choice for tabular data. Three are trained rather than one, aimed at the 10th, 50th and 90th
**percentile** of lateness (a percentile is a cut point, so the 90th is the value 90% of
outcomes fall below). That is what produces a range: the 50th is the most likely value, the
other two are the ends of the interval. Three separate models can disagree and put the low
estimate above the high one; the fix is to sort them. Nothing was tuned, because a tuned
number arrived at before an untuned one cannot be interpreted.

## 5. Better than what, and how anyone would know

The obvious baseline is "assume it arrives on time", and beating it proves nothing. The honest
baseline is **persistence**: assume the train stays exactly as late as it is now. That is
already a decent predictor and it is what a sensible person would guess. The real target is
Irish Rail's own expected arrival, the number on the station board, and beating that means
beating the operator using the operator's own data. On matched events (same train, same
station, same moment, both producing a prediction), the offline measurement was **80.1s**
average error against **109.7s**, a **27%** improvement over **9,077** comparisons spanning
**2,654** distinct events.

Four ways that comparison could have been rigged, each handled. The operator estimated at a
specific instant, so the model may use only what was knowable then, never a stop that reported
five minutes later. No feature may need the finished journey, which is the one that was cut.
The operator's estimate is to the minute and the model's is to the second, which would hand
the model up to thirty seconds of free accuracy, so the model is rounded to the minute. And a
single event is polled roughly eighteen times as the train approaches, so it counts once per
event per time band rather than eighteen times.

The largest guard is that the data is split by **time**, not at random. The model trained on
**27 June to 12 July**, was checked against **13 to 19 July**, and a further week, **20 to 26
July**, was sealed at the start and left alone. Section 10 is what happened when it was opened.

## 6. Putting it on the internet

The host is **AWS**, Amazon's cloud. Four pieces, each a **Lambda** (code that runs on demand
and costs nothing while idle, so there is no server to keep switched on): a **poller** reading
station boards every five minutes, which is the only way to capture Irish Rail's expected
arrivals; the **prediction service**; a **generator**, in section 7; and a **scorer** that runs
nightly and checks the previous day's predictions against what actually happened. Storage is
**S3**, with public access blocked. There is no database and no always-on server, the whole
thing costs about **$0.10 a month**, and the public endpoint is capped at **5 simultaneous
executions**, so a flood is rejected before any code runs and cannot starve the poller or the
scorer. The infrastructure is declared in text files and rebuilt from the repository rather
than clicked together, and the site deploys itself using short-lived credentials, so **no
long-lived AWS key exists in the repository or in GitHub's secrets**.

Before the cloud poller was trusted alone it ran for a week alongside the laptop one, both
polling the same feed. Every disagreement was a train visible on a board for a single
five-minute cycle, caught by whichever poller sampled inside that window, and the misses were
symmetric between the two. Symmetry is what rules out a real difference.

**The alarms were tested by deliberately breaking things.** The poller was pointed at a
storage bucket that did not exist and its schedule was switched off. Both alarms fired. The
test also found that one alarm topic had **no subscribers**: the confirmation email sent days
earlier had never been clicked and Amazon had discarded the subscription, so every alarm on
that topic would have gone to nobody. An alarm that has never fired is not a verified alarm.

## 7. Keeping it honest, with nobody visiting

The accuracy page is worth nothing unless it cannot be gamed. The specific problem to prevent
is **leakage**: letting knowledge of the outcome influence the prediction, which produces
spectacular numbers that mean nothing. Three rules.

**Every prediction is written down before its outcome exists.** The service saves the answer,
with the time, train, station and model version, and only then replies. If the save fails, the
request fails. Logging failures are not random, they cluster when the infrastructure is
struggling, which is exactly when behaviour is unusual, so unlogged predictions would bias the
scoreboard. That guard was itself broken for two months: a failed write was caught and
returned a tidy 503, which Lambda records as a *successful* invocation, so the error alarm
could never fire, and a comment in the template claimed it worked. The failure now escapes the
handler and the existing alarm catches it (D76).

**Never regenerate a historical prediction**, because recomputing what the model "would have
said" last Tuesday means running today's model against a known outcome. Any code that does
this is a bug.

**The rules are physical, not just written.** The scoring job's permissions let it read
predictions and write scores; it cannot write predictions, and the prediction service can write
predictions and nothing else. "The scorer never touches predictions" is not a rule anyone has
to remember, it is something the credentials refuse to do. The log is tamper-evident, not
tamper-proof: an administrator could still rewrite it.

Two days after launch the prediction log held one entry, a smoke test. A portfolio site gets no
traffic, and the scoreboard's input is demand, so the fourth Lambda manufactures it: every five
minutes it picks trains at random from whatever is running and asks the service about stops
ahead on each, logging the answers as a visitor's would be logged. The accuracy page says
plainly that these are scheduled samples rather than user traffic. Random selection avoids
favouring whichever routes sort first, and it is honestly random: trains that cannot be
answered, because they have not reported anywhere yet, are asked anyway and logged as declined,
since screening them out would delete the denominator. **92.9%** of sampled in-service trains
get an answer (124,216 answered against 9,557 declined). The share a visitor meets on a station
board is lower, because a board also lists trains that have not departed and those cannot be
answered at all. Both populations are named wherever coverage is quoted.

## 8. Labels that belonged to other trains

Two defects surfaced once the system was live and scoring itself.

The first was a **training and serving mismatch**. The average error and the median were far
apart, which cannot both describe a healthy model, and a handful of absurd rows were carrying
the average. The live code and the training code disagreed about how to compute "how late":
the training code measured each stop against its own schedule, the live code did something
subtly different that agreed on every ordinary journey and produced a spurious extra day on
broken ones. The model had been taught one definition and served another. One definition is
now used everywhere, and the offline archive was unaffected because the training side had
always done it right.

The second was **Athenry**. With that fixed, the model still predicted forty minutes late at
one station on the Galway line for a train running two minutes down. The input was not bad,
the model was: in the training data, Athenry's typical recorded delay was enormous. On one day
two different trains both "arrived" there at the same second, another arrived at dawn the
following morning, and no train in the opposite direction ever received an arrival there at
all. These were real machine-captured times, flagged as verified, and they belonged to
different trains. The signalling equipment had seen a train and the feed had filed it against
the wrong service. That is a different defect from the echo: the echo is the timetable
pretending to be an observation, this is a genuine observation pretending to be about the
right train. The capture flag that resolves the echo says nothing about it, and Athenry is not
on Irish Rail's list of weak lines, which caught what the vendor knew about rather than what
is wrong.

The check that catches it is physical: a train visits its stops in order, so recorded arrivals
must not go backwards along the route, and if they do, at least one belongs to another train.
It reads nothing but the feed's own fields, no model and no prediction, which is what makes it
label cleaning rather than the discarding of hard cases. Applied to the training data it
rejects **3.7%** of journeys.

The model was retrained on the cleaned data and compared with the incumbent on identical
cleaned examples, so the comparison measures the model rather than the cleaning. Both
validation figures are published: the previous champion scored **76.1s** average error across
all journeys and **60.6s** across the **96.1%** with internally consistent records, and the
model now serving scores **59.7s** on that same consistent set.

Promotion was not automatic. The rule said a new model must not be worse on any station group,
and on one small group the candidate came out fractionally worse, well inside what chance
produces at that sample size. The rule as written had no allowance for chance, which made it a
rule no retrain could ever pass. It was changed, and changed with a failing candidate in hand,
which is exactly when a rule is most likely to be bent to fit, so D57 records that openly.
"Worse" now means the resampled range lies wholly on the wrong side of zero rather than the
single number doing so, and a group can block promotion only above a sample size derived from
statistical power, not from the group that happened to fail.

## 9. What the live numbers say

Against the operator's own expected arrival, since launch: **86.8s** average error against
**116.8s**, **25.7%** better over **27,984** matched events. On the rolling seven days,
**88.5s** against **117.7s**, **24.8%** over **20,761** matched events, of which the model
wins 11,154, the operator wins 5,705 and 3,902 are ties. The advantage holds across horizons:
**27.8%** at 0 to 5 minutes ahead (58.3s against 80.7s over 5,923 matched) and **23.6%** at 15
to 30 minutes (100.7s against 131.8s over 4,695). The offline claim was 27% over 9,077
comparisons. Live, on three times the sample and against a live baseline, it held.

The intervals did not. Rolling seven-day coverage is **75.0%** against a nominal 80%, and the
spread is the finding:

| station group | coverage | nominal |
|---|---|---|
| `commuter_maynooth` | 81.4% | 80% |
| `dublin_hubs` | 77.6% | 80% |
| `dart` | 77.2% | 80% |
| `weak_coverage` | 73.1% | 80% |
| `intercity_cork_corridor` | **66.5%** | 80% |
| `commuter_kildare` | **62.9%** | 80% |
| `intercity_other` | **57.0%** | 80% |

A retraining rule written months in advance said that if coverage fell below 70% on any
station group with enough scored events, and stayed there for a week, something had to be
done. On 8 September it fired, on the Cork corridor and the Kildare line and nowhere else.
Those two share track. A change confined to shared rails, six weeks after training, with the
DART and the Dublin hubs steady, reads as something that happened on that stretch of railway
rather than as a model that has gone bad.

The choice at that point is the most revealing one in the project. The ranges could have been
widened on those two lines until the number returned to 80%. That would produce a better
scoreboard and destroy the evidence: the point of writing the rule in advance was to find out
whether it would ever fire, and quietly patching it away answers the question by deleting it.
So coverage is published per group, beside the 80% it claims, and the reasoning is in D64.
Recovery, or its absence, then became a measurement in itself. The reading taken on 11
September found no recovery, and daily coverage has sat between **73.9%** and **76.2%** every
day since 2 September, so the shift is persistent and flat rather than noise.

Two further limits belong beside those numbers. **The intervals cover 0% of real delays over
an hour**, for the serving model and the one before it (53 validation rows, plus 16 Sligo-line
predictions on 2 September). At the moment of asking, each of those trains was a few minutes
late and then lost over an hour in the next stretch, and nothing in "how late is it now, how
far to go, what time is it" can see that coming. The ranges are calibrated for ordinary
lateness, not for disruption. Separately, average error across every scored row is **94.8s**
with a median of **48s** over **88,672** rows, a much wider population than the matched
head-to-head above and not comparable to the validation figure.

## 10. The sealed week

The data was cut three ways in June: one part to learn from, one to check every decision
against, and one week, 20 to 26 July, sealed and never opened. Every choice in the project was
checked against the validation week, and each look leaks a little of that week into the model,
so after dozens of decisions a validation figure partly measures how well the model was tuned
to that particular week. The sealed week had none of that, and it could settle one question
nothing else could. Validation said coverage was 80.2%, live September said 75.0%. Either the
railway changed or the validation figure was flattering itself, and those have opposite
meanings.

Before it was opened, the analysis was written down and committed: which model artifact, which
data, the exact command, what would be reported whatever the result, and explicitly no pass
mark and one run (D73). Deciding afterwards what counts as a good result is how people fool
themselves. It was opened once, on 10 September, on **217,290** predictions the model had
never seen.

| | validation (13–19 Jul) | test (20–26 Jul) |
|---|---|---|
| average error | 59.7s | **58.3s** |
| median error | 29.0s | **29.1s** |
| interval coverage | 80.19% | **80.0%** |

Coverage of **80.0%** against a claimed 80.0%. The misses split **10.4%** below the low bound
and **9.6%** above the high bound, against 10% and 10% expected, so the tails are not
lopsided, and the model is **32.1%** better than persistence, the honest floor from section 5.

Test came out marginally better than validation, which is the opposite of the overfitting
signature. That answers the question: the calibration was genuine, so the live shortfall is
the railway changing and not a model that was never as good as advertised. D64 had argued the
same thing from the shape of the evidence, two shared-track corridors moving while the rest of
the network held; the sealed week confirms it from a direction D64 could not reach.

The week is now spent. It cannot be used again, and no number in this project will ever be
tuned against it. Full result in D74, raw output kept verbatim in
`docs/test-week-2026-09-10.txt`.

## 11. A separate question: where the slack goes

The same data answers a second question that is not a prediction at all: given how late a train
has actually been, day after day, is the timetable's padding in the right places? **Nothing in
the deployed service depends on this.** It is here because formulating a problem and solving it
is a different skill from fitting a model to data.

Every timetable carries slack so a small delay is absorbed instead of passed down the line, and
the total is fixed, so giving one stretch more means taking it from another. That is written
out as a **linear program**: the objective and every constraint are straight-line relationships
in the quantities being chosen, which is the case where a solver finds the genuinely best
answer rather than a good one. The quantities are the slack on each stretch, the objective is
total passenger lateness over real recorded days, and the constraints hold total slack fixed,
keep each stretch above the fastest the train has been observed to run it, and carry lateness
forward from stop to stop.

Two things went wrong and both are recorded. Lateness is "however late, but never negative",
because an early train waits, and that bend is not a linear relationship; the standard
replacement works only if every downstream stop counts for something, and under one of the two
weightings it does not, so the condition is stated rather than waved at (D60). And the first
answer was worse than the timetable it was improving on, fitting the sampled days and doing
worse on new ones, which is ordinary overfitting in an unexpected place. Charging it for every
second it moves anything fixes that, after which all five trains improve, three of them by an
amount that survives resampling the days.

The finding nobody went looking for: weight only arrival at the final stop and there is nothing
to gain, because the timetable is already tuned for terminal punctuality, which is what
operators are measured on. All the available improvement is at intermediate stops. It remains a
simulation of what would have happened under different padding, unverifiable without running
the trains.

---

## The thread through all of it

Eleven separate failures in this project shared one shape. None raised an error. Every one
produced output that looked exactly like a correct result: arrival times identical to the
schedule; a run of successful downloads that were all an internet provider's login page; an
alarm with no subscribers; an average error wildly out of line with its own median; a code
harvester reporting "0 new codes" from a folder nothing had written to; a file count reported
as complete from a fraction of the files; a configuration fix that was inert while the file
sat visibly in the repository; arrival times marked verified that belonged to other trains; a
model that appeared to cover a quarter of severe delays, every one of them a wrong label
matched by a wrong prediction; a rebuild that reported success because the command reporting it
was `tail` rather than the program that had crashed; and a nightly job that printed a complete,
healthy report and then died minutes later, leaving a frozen page that looked exactly like a
working one.

Each was caught the same way: taking a number and asking what it should have been. Four were
introduced after the system was already working, while writing up the others. The lesson is not
"be more careful". It is that a system which works and a system that can be *shown* to be
working are different things, and most of the effort here went into the second.

The reasoning behind every decision above is in `docs/decisions.md`, 78 entries, including the
ones that were wrong.
