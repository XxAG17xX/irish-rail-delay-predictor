# The story of this project

Written for someone who doesn't code and doesn't know trains. Every technical word gets a
one-sentence explanation the first time it appears. This isn't the how-it-works page; it's
what I read before writing it.

---

## 1. The question

Irish Rail's station boards tell you when a train is expected. They don't tell you how
sure they are. A train that's been steadily two minutes late for five stops and a train
that just picked up an unexplained delay both get the same kind of estimate: one time,
stated with the same confidence.

I wanted to build something that gives you a **range** instead. Not "17:09" but "most
likely 17:09, probably between 17:07 and 17:14." The range is the useful part. If you've
got a four-minute connection to make at the other end, a one-minute-wide range says
you're fine and an eight-minute-wide range says you might not be. The single number can't
tell you that.

The technical name for this is a **prediction interval**: instead of one guess, a low and
a high bound, chosen so that the real answer lands between them a stated fraction of the
time. I chose 80%. So the honest claim is "four times in five, the train arrives inside
this range." That's a claim you can check, which matters later.

The whole thing had to be finished and running on the internet before my final year
started in mid-September. That deadline shaped every decision that follows.

## 2. Where the numbers come from

Irish Rail run a public data feed. An **API** — application programming interface — is
just a web address that returns data instead of a web page, meant for programs rather
than people. Theirs needs no sign-up, no key, and comes with no promises: they say it's
provided as-is with no support.

Four things it can tell you:

- **The station board.** What's due at a given station in the next ninety minutes,
  including Irish Rail's own expected arrival time for each train.
- **Which trains are running right now.**
- **The list of all stations.**
- **One train's full journey on one date** — every stop, the scheduled time, and the
  actual time it arrived, if it was recorded.

That last one is the whole project. It's the record of what actually happened. If I ask
for train A220 on the 25th of June, I get back every stop on its route with "was supposed
to arrive 17:06:30, actually arrived 17:08:12." Those actual times are what a model learns
from.

One thing I had to verify early: does the feed serve real history, or does it just replay
the timetable for old dates? I asked for the same train on the same date in 2020, 2024,
2025 and 2026. The times all differed, and the 2020 version had three fewer stops and a
different scheduled arrival at Cork — the 2020 timetable on 2020 infrastructure. It's
genuine history, back to at least 2007.

## 3. Getting hold of the data

The feed gives you one train, one date, per request. There's no "download everything."
And there's no list of which trains ran on a given day — you have to already know the
train's code (A220, D930, E817) to ask about it.

That was the first real problem. I solved it by asking "which trains are running right
now?" every five minutes across a full day, on a weekday and a weekend, and collecting the
codes as they appeared. Then I replayed that list against past dates. A code that didn't
run on a given date just returns an empty answer, which is fine.

The other constraint was manners. This is a free service with no stated limits. I kept it
to two requests a second, backed off when the server complained, and ran the big jobs
overnight. The full download — 1,087 train codes across 34 dates — came to 28,706 files
and about half a million stop-level records. It took a few hours and I did it once.

Two rules I set at the start and have been glad of since:

**Keep the raw responses.** Every reply from the feed is saved exactly as received before
anything is done with it. When I later changed my mind about how to interpret a field, I
could reprocess the archive instead of downloading it again. Throwing away the original
and keeping only your interpretation means every mistake in the interpretation is
permanent.

**Assume everything will be interrupted.** Files are written in a way that can't leave a
half-finished one behind. Rerunning a download skips what's already there. A failed
request goes into a list to retry later rather than stopping the run. None of this is
clever; it's what stops one dropped connection on hour three costing you hour four.

## 4. The first thing that broke: times that weren't real

Here's the problem the whole project turned out to be about.

To teach a model how late trains are, you need to know how late trains *were* — the true
arrival time at each stop. That's the **label**: the right answer the model is learning to
predict. The feed has a field for it. But on some parts of the network the operator
doesn't actually observe arrivals, and the field comes back holding **the timetable**,
presented as if it were an observation. The value looks completely normal. Nothing marks
it. If you train on it, the model learns that those lines are perfectly punctual.

I called it an **echo**: the schedule reflected back at you dressed as a measurement.

You can spot it because a real arrival almost never lands on exactly the scheduled second.
So: how often is the recorded arrival *identical* to the scheduled time? Across the whole
archive, 2.92%.

Irish Rail actually document this. They list ten lines where "your query will return the
scheduled time only." So the obvious fix was to distrust those lines. I tested it, and it
looked like clean confirmation — the flagged lines matched the schedule exactly 21% of the
time against under 3% everywhere else. Eightfold difference. Case closed.

It was wrong. Not the arithmetic — the interpretation.

Every arrival record also carries a flag saying whether the time was captured
automatically by the signalling equipment or entered some other way. Split by *that*
instead of by line, and the picture reverses: among machine-captured records, the flagged
lines echo *less* than the rest of the network. What made them look bad was that far more
of their records were non-automatic — 47% against 1.5% elsewhere — and the non-automatic
ones are where echoes live. The lines weren't worse. Their data was captured differently.

There's a name for a comparison that reverses when you split it: **Simpson's paradox**. It
happens when the two groups you're comparing have different internal mixes, and the mix is
doing the work you're attributing to the groups. This one taught me something I've used
since: a result that agrees with the documentation is exactly the one you don't check
carefully enough.

So the rule became: use the per-record capture flag, not the line list. Three quarters of
the suspect records were on lines the documentation never mentioned. A line filter would
have thrown away good data and kept bad.

And one more thing that stops this being a delete button: an exact match is *suspicious*,
not proof. Some trains genuinely do arrive on the scheduled second — about 2.4% of the
records I trust most. Part of the reason is that every time in the feed is rounded to the
nearest six seconds, so there are only ten possible second-values in a minute, and
coincidences are commoner than you'd think. Nothing is deleted at ingestion. The flag is
carried through, and what to exclude is decided when evaluating, with the numbers reported
both ways.

## 5. Teaching a model

A **model** here is a program that has learned, from many examples, to map a situation to
a prediction. The examples are the half-million records. The situation is described by a
handful of numbers — the **features** — and the prediction is how late the train will be.

The single rule that decided what could be a feature: **it has to be something you'd know
at the moment of asking.** You know the timetable. You know how late the train was at the
stops it has already reported from. You don't know anything about stops it hasn't reached.
A feature that quietly uses the finished journey will look brilliant in testing and be
useless in practice, because in practice the journey isn't finished. I caught one of these
— a count of how many stops *would* report — and cut it. It cost a quarter of a percent
of accuracy. Cheap.

The other rule: **describe the situation, not the identity.** The train's code is not a
feature. A model that learns "A218 runs two minutes late" has nothing to say about a new
service launched next spring. Use what the train is doing, not what it's called.

The features that made the cut: how late it is now, how late it was at the two stops
before, how many stops and scheduled minutes remain, the time of day, the day of the week,
where it started and where it's going.

The model is **gradient boosting** — a technique that builds a prediction from hundreds
of small decision rules, each one correcting the errors of the ones before. It's the
standard choice for tabular data like this. I trained three of them, not one: one aims at
the 10th percentile of lateness, one at the 50th, one at the 90th. That's what gives you a
range. The 50th is the "most likely"; the 10th and 90th are the ends of the interval.
(A **percentile** is a cut point: the 90th percentile of lateness is the value that 90% of
outcomes fall below.)

Three separate models can disagree with each other — the "low" estimate can come out above
the "high" one. It happened on about one prediction in a hundred. The fix is to sort them,
which is standard and can only help.

I didn't tune anything. Every setting is the default. A tuned number arrived at before an
untuned one is a number you can't interpret.

## 6. Is it actually better?

Two questions. Better than what, and how do you know you're not fooling yourself?

**Better than what.** The obvious baseline is "assume it arrives on time," and beating
that proves nothing — the timetable isn't a good predictor and nobody thinks it is. The
honest baseline is **persistence**: assume the train stays exactly as late as it is now.
That's already a decent predictor, and it's what a sensible person would guess. The model
beats it by about 22% on average error.

The real target is Irish Rail's own expected arrival — the number on the station board.
Beating that means beating the operator with their own data. On matched events (same
train, same station, same moment, both of us making a prediction) the model's average
error was 80 seconds against their 110. About 27% better, over 9,077 comparisons.

**How do you know you're not fooling yourself.** Four ways this comparison could have been
rigged, each handled:

1. The operator made their estimate at a specific instant. Mine has to use only what was
   knowable then — not a stop that reported five minutes later.
2. No feature that needs the finished journey (the one I cut).
3. Their estimate is to the minute; mine is to the second. Comparing them raw hands me up to
   thirty seconds of free accuracy. I round mine to the minute for the headline.
4. Each event gets polled about eighteen times as the train approaches. Counting those as
   eighteen results inflates everything. One per event per time-band.

And the biggest one: the data is split by **time**, not at random. The model trained on
late June to mid-July, was checked on the week after, and a further week — 20 to 26 July —
has **never been opened**. If I'd tuned against it, it would stop being an honest test.
Every number quoted anywhere is from the checking week or from live data, never from that
sealed one.

Where it loses: on the weakly-covered lines, the median error is worse than the operator's.
That's published as a loss, not buried.

## 7. Putting it on the internet

"Deployed" means running on a server somewhere, answering requests, without me at a
laptop. I used **AWS** — Amazon's cloud, which rents you computers and storage by the
minute.

The pieces:

- A **poller** that asks the feed for thirty station boards every five minutes and saves
  the results. This captures Irish Rail's expected arrivals, which exist only in the moment
  — you can't ask for yesterday's.
- The **prediction service** — the thing you ask "how late will A220 be at Thurles?"
- A **scorer** that runs each night and checks yesterday's predictions against what
  actually happened.

Each of these is a **Lambda**: a piece of code that runs on demand and costs nothing while
it's idle. There's no server to keep switched on. Storage is **S3**, which is Amazon's
service for storing files. The whole thing costs about ten cents a month.

Everything is described in **CloudFormation** — text files that say "I want a function
with these settings, a storage bucket, an alarm that emails me if the function errors" —
so the infrastructure can be rebuilt from the repository rather than clicked together.

Two things from this phase worth telling.

**The poller ran twice, side by side, for eight days.** My laptop version and the cloud
version, both polling the same feed, so I could prove the cloud one saw the same railway
before trusting it alone. They agreed on 99.9% of events. Every one of the eleven
disagreements was a train visible on a board for a single five-minute cycle, caught by
whichever poller happened to sample inside that window — and it was symmetric, sometimes
one poller, sometimes the other. That's what rules out a real difference.

**I tested the alarms by deliberately breaking things.** Pointed the poller at a storage
bucket that didn't exist; switched off its schedule. Both alarms fired and emailed me.
That test found that one alarm topic had **no subscribers** — the confirmation email from
three days earlier had never been clicked and Amazon had quietly discarded the
subscription. Every alarm for three days would have gone to nobody. An alarm that has never
fired is not a verified alarm.

## 8. Keeping it honest

The accuracy page is only worth anything if you can't cheat. The specific cheat to
prevent is called **leakage**: letting knowledge of the outcome influence the prediction,
which produces spectacular accuracy numbers that mean nothing.

Three rules:

**Every prediction is written down before its outcome exists.** When the service answers,
it first saves the answer to storage — with the time, the train, the station, the model
version — and only then replies. If saving fails, it doesn't reply. An unlogged prediction
would bias the scoreboard, because logging failures aren't random: they cluster when the
infrastructure is struggling, which is exactly when behaviour is unusual.

**Never regenerate a historical prediction.** It'd be easy to recompute what the model
"would have said" last Tuesday. It'd also be running today's model against a known
outcome. Any code that does this is a bug.

**Make the rules physical, not just written.** The scoring job's cloud permissions let it
*read* predictions and *write* scores. It cannot write predictions. The prediction service
can write predictions and nothing else. "The scorer never touches predictions" isn't a rule
someone has to remember; it's something the credentials refuse to do. (These permissions
are **IAM** — Amazon's system for saying which piece of code is allowed to do what.)

The log is tamper-*evident*, not tamper-*proof*: an administrator could still rewrite it.
That limit is stated rather than hidden.

## 9. Nobody visited, so it asks itself questions

Two days after the service went live, the prediction log held exactly one entry — my own
smoke test. A portfolio site gets no traffic. The scoreboard's input is demand, and there
was none.

So there's a fourth Lambda that manufactures it. Every five minutes it picks forty trains
at random from whatever's currently running, and asks the service about a few stops ahead
on each. The predictions get logged exactly as a real user's would.

It's random on purpose. Taking the first forty off the feed would silently favour whichever
routes sort first. And it's *honestly* random: trains that can't be answered — because they
haven't reported anywhere yet — are asked anyway and logged as declined. Screening them
out would delete the denominator. "We answer 89% of questions" turned out to be true of
trains already running, and **38%** of what a visitor actually meets on a station board,
because a board also lists trains that haven't left yet. Both numbers get published, with
their populations named.

## 10. What the live numbers said

The first full day with the generator running: the model beat the operator by **25.9%**
on 1,079 matched events. The next two days, 29.1% and 28.8%. The offline claim of 27% held
up on live data, from predictions provably written before the trains arrived.

Then two things that weren't fine.

**The average error was 22 minutes and the median was 48 seconds.** Those can't both be
describing a healthy model. An average that far above the median means a handful of absurd
rows. Two rows out of eighty-one were carrying the whole thing, each with a delay of about
twenty-five hours. The cause: the live code and the training code disagreed about how to
compute "how late." The training code measured each stop against its own schedule; the
live code did something subtly different that agreed on every ordinary journey and
produced a spurious extra day on broken ones. The model had been taught one definition
and served another. Fixed by making one definition and using it everywhere. The offline
archive was unaffected — zero records out of 334,984 had the problem — because the training
side had always done it right.

**Athenry.** With that fixed, the model was still predicting "forty minutes late" at one
station on the Galway line from a train running two minutes late. Not a bad input — the
model itself. In the training data, Athenry's *typical* recorded delay was 43 minutes. On
one day, two different trains both "arrived" there at exactly 10:33. Another arrived at
05:54 the following morning. No train going the other direction ever got an arrival there
at all. These were real, machine-captured times, flagged as verified — and they belonged
to *different trains*. The signalling equipment had seen a train and the feed had filed it
against the wrong service.

This is a different defect from the echo. The echo is the timetable pretending to be an
observation. This is a genuine observation pretending to be about the right train. The
per-record flag that resolves the echo says nothing about it, and Athenry isn't on Irish
Rail's list of weak lines. The vendor's list was necessary — it caught what the vendor
knew about — and not sufficient, because it's a list of what the vendor noticed.

Four Galway-line stations held 81% of all training labels more than an hour late. The
model had learned what "severely late" looks like largely from garbage.

The check that catches it is physical: a train visits its stops in order, so recorded
arrivals must not go backwards along the route. If they do, at least one of them belongs
to someone else. That reads nothing but the feed's own fields — no model, no prediction —
which is what makes it label cleaning rather than throwing away hard cases. Applied to the
training data it rejects 3.7% of journeys and takes Athenry's typical delay from 43 minutes
to one.

## 11. Where it is now

The generator now refuses to predict from a journey whose arrivals go backwards, refuses
any question more than four hours ahead (no journey on the network is that long; it had
predicted seventeen hours ahead for a train the feed listed as running at dawn), and logs
whether the reference stop was machine-captured. Those went live on 3 September.

The model has been retrained on the cleaned data, and compared with the old one on
identical held-out examples — same cleaned set for both, so the comparison measures the
model and not the cleaning. At the four Galway stations the new one's answers went from
absurd to sensible: typical error there fell from over seventeen minutes to under seven,
and the width of its range from nearly two hours to six and a half minutes. Everywhere
else it is the same model to within noise. Both validation figures are published: 76
seconds average error across all journeys, 61 seconds across the 96% with internally
consistent records, with the exclusion stated.

It hasn't replaced the old model yet. The rule for promotion says a new model must not be
worse on any line, and on one line of 424 examples it came out 0.8 seconds worse — an
amount well inside what chance produces on 424 examples. The rule as written has no
allowance for that, which makes it a rule no retrain can pass. That's being fixed before
the decision is made, rather than the decision being made by ignoring the rule.

And one thing the retrain revealed rather than fixed. When asked whether the new model
handles *genuinely* severe delays better — the Sligo trains that lost seventy-five minutes
in one stretch — the answer was that neither model has ever handled them. The old one
looked as if it covered a quarter of hour-plus delays; every one of those was a Galway
garbage label swallowed by a garbage-wide range. On real severe delays, both models miss
every time. At the moment of asking, each of those Sligo trains was a few minutes late and
then lost over an hour in the next stretch. Nothing in "how late is it now, how far to
go, what time is it" can see that coming. That's a limit of the approach, and it goes on
the page as one: the ranges are calibrated for ordinary lateness, not for disruptions.

The retraining trigger now watches interval coverage as well as average error, because on
the Cork and Kildare corridors the intervals were calibrated at 79% in July and are
covering 63–69% now while the average error looks fine. For a model whose product is the
range, watching only the midpoint would miss exactly this.

Not built yet: the three web pages. A predictions page, a scoreboard, and the how-it-works
page this document is the notes for.

---

## The thread through all of it

Nine separate failures in this project shared a shape. None raised an error. Every one
produced output that looked exactly like a correct result: arrival times identical to the
schedule; 420 successful downloads that were all an internet provider's login page; an
alarm with no subscribers; a 22-minute average error next to a 48-second median; a
harvester reporting "0 new codes" from a folder nothing had written to; a count from 400 of
2,088 files reported as complete; a configuration fix that was inert while the file sat
visibly in the repository; arrival times marked verified that belonged to other trains;
and a model that appeared to cover a quarter of severe delays, every one of them a wrong
label matched by a wrong prediction.

Each was caught the same way: taking a number and asking what it should have been. Two of
them I introduced myself, after the system was working, while writing up the others. The
lesson isn't "be more careful." It's that a system which works and a system you can *tell*
is working are different things, and most of the effort here went into the second.
