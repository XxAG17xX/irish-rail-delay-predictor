# What I should be able to explain

Questions only. No answers — the point is to find the gaps, not to be handed a script.

If you can answer one in two or three plain sentences without opening the repo, it is
solid. If you can only answer it by reading the code back, it is not yet yours. If you
cannot answer it at all, that is the useful outcome: it is a gap found now rather than in
a room with someone who does this for a living.

**★★★** almost certain to come up · **★★** likely · **★** if they go deep

Where an answer lives in the decision log, the entry is named. Read it *after* trying.

---

## A. What this is

- ★★★ What does the service actually do, in one sentence, to someone who does not know what a prediction interval is?
- ★★★ Why predict a range instead of a single time?
- ★★★ Who would use this, and what decision would they make differently because of it?
- ★★ What question does it deliberately refuse to answer, and why is that a design choice rather than a limitation?
- ★★ Why one model for the whole network instead of one per train or per line?
- ★ Why Irish Rail rather than any other transport network?
- ★ What would you build next if you had another month, and what would you refuse to build?

## B. The data, and what is wrong with it

- ★★★ Where does the data come from, and what does it cost to use?
- ★★★ Roughly what fraction of movement records never receive an actual arrival time, and what did you do about it? (D23)
- ★★ What is the difference between `ScheduledArrival`, `ExpectedArrival` and `Arrival`, and which of the three must never touch the model?
- ★★ What is `AutoArrival` and why does it matter more than which line a station is on? (D20–D23)
- ★★ What is the "echo" problem in this dataset?
- ★★ You tested a line-based explanation for label quality, it appeared to work, and you threw it out. What happened? (D20–D21)
- ★ What is Simpson's paradox and where did it show up here?
- ★ All feed times are quantised. To what, and how do you know? (D22)
- ★ What does `LocationType=C` appear to indicate, and why was it parked rather than modelled?
- ★ How did you establish that the API serves genuine history rather than replaying a timetable?

## C. Collecting the data

- ★★★ The hard problem in the backfill was enumerating which trains ran on a past date. Why is that hard, and how did you solve it?
- ★★ What rate do you request at, and how did you choose it?
- ★★ What happens when the API returns a 429 or a 503, and how is that different from what happens on a connection error? (D8)
- ★★ Why does a failed request go into a log rather than raise an exception? (D14)
- ★ Why archive the raw XML before parsing it? (D1)
- ★ What is a token bucket, and why did you not use one? (D6)
- ★ What is AIMD and where does it appear in this system? (D7)
- ★ Why full jitter on backoff rather than plain exponential? (D13)
- ★ You check the *bytes* of a response before trusting it. Why was that necessary? (D9)
- ★ Which 30 stations does the live poller watch, and why those rather than a random 30? (D29)
- ★ Why does a scheduled job ask "what am I missing" instead of assuming the last run worked?

## D. Storage and shape

- ★★ Why gzipped XML on disk, then Parquet, and what does each step buy?
- ★★ Why is there no database? (D40)
- ★★ What would have to change for a database to become the right answer?
- ★ What is Parquet and why is it a better fit than CSV here?
- ★ What does "atomic write" mean and why does every writer here do it? (D4)
- ★ Why one S3 object per poll cycle rather than one per station?

## E. Features

- ★★★ What is the single rule that decides whether something is allowed to be a feature?
- ★★★ Why is the train code not a feature?
- ★★★ Which feature do you expect to dominate, and why?
- ★★ You excluded `horizon_observed_stops`. What was wrong with it, and what did excluding it cost? (D35, features.py)
- ★★ What does "computable at prediction time" mean, and what breaks if you get it wrong?
- ★★ There is no "line" field in the feed. What do you use instead, and what does that proxy miss?
- ★ Why is `ExpectedArrival` banned as an input?
- ★ How are categorical features encoded, and what happens when a live request contains a category the model never saw?
- ★ The feature list used to exist in two files and they drifted. What was the consequence, and what stops it now? (D35)

## F. The model

- ★★★ Why quantile regression rather than predicting a mean and adding error bars?
- ★★★ What is a quantile loss function, in words?
- ★★★ Why gradient boosting rather than a neural network or a linear model?
- ★★ You train three separate models. Why three, and what could go wrong between them? (D27)
- ★★ Quantile crossing — what is it, why does it happen, and what did you do? (D27)
- ★★ What is the 80% interval actually claiming, and how would you check whether that claim is true?
- ★★ Interval coverage degrades as the horizon lengthens. By how much, and is that a bug? (D28)
- ★ What did you not tune, and why is that defensible?
- ★ What is in a saved model artifact besides the model? (D31)
- ★ How does the serving code know it has the right model, and what happens if it does not?

## G. Evaluation — the part they will push hardest on

- ★★★ What is the headline claim, in one sentence, with its sample size?
- ★★★ What exactly is the baseline you are beating, and why is it the right one?
- ★★★ How do you know the comparison is fair?
- ★★★ Why is the test week still unopened, and what would opening it early have cost you? (D25)
- ★★★ Why is the split by time rather than random?
- ★★ Your prediction is second-precision, the operator's is minute-precision. Why does that matter and what did you do? (D46)
- ★★ A single event gets polled about eighteen times. Why can you not treat those as eighteen results? (D46)
- ★★ What is the "vantage stop", and why must it be chosen by the clock rather than by position in the journey? (D46)
- ★★ Where does the model *lose*, and why is that on the page rather than in a footnote?
- ★★ Why must coverage always be published next to accuracy?
- ★ What is a persistence baseline and why is it the honest floor to compare against? (D26)
- ★ What would you need to see before you believed the model had genuinely got worse?

## H. Serving

- ★★★ Walk me through what happens between someone asking for a prediction and getting an answer.
- ★★★ Roughly 56% of queries cannot be answered. Why, and how does the service behave when it cannot answer?
- ★★ Why is a decline a first-class response with a machine-readable reason rather than an error?
- ★★ Why is the model version pinned as a deployment parameter rather than "use the latest"? (D43)
- ★★ Why is the model baked into the deployment package rather than downloaded at startup? (D43)
- ★ Why FastAPI, and what does Mangum do?
- ★ Why a Lambda Function URL rather than API Gateway?
- ★ Cold start versus warm request — what are the numbers and what explains the gap?
- ★ Three things about the deployment package could not be discovered without failing first. What were they? (D44)

## I. Leakage and trust

- ★★★ What is data leakage, and what is the specific form of it this project has to defend against?
- ★★★ Why is every prediction written down *before* its outcome exists? (D39)
- ★★★ Why can you never regenerate a historical prediction, even though it would be easy?
- ★★ A prediction that cannot be logged is not served. Why is failing closed the right call here? (D39)
- ★★ How would someone check that your accuracy page is not just marking its own homework?
- ★★ The scorer's permissions are the mirror image of the API's. What does that buy that a written rule does not? (D50)
- ★ The log is tamper-*evident*, not tamper-*proof*. What is the difference, and what would it take to close the gap?
- ★ Each prediction is written to two places. Why, and what does the second copy prove?

## J. The live scoreboard

- ★★★ Nobody visits the site, so where do the predictions being scored come from? (D49)
- ★★★ Is a self-generated sample weaker evidence than real user traffic, or stronger? Defend your answer.
- ★★ How are trains chosen for sampling, and what would go wrong with the obvious simpler choice? (D49)
- ★★ You deliberately do *not* filter out trains you know you cannot answer for. Why? (D49)
- ★★ You publish two coverage numbers. What are the two populations, and which one leads? (D53)
- ★★ What are the possible outcomes for a scored prediction, and why is none of them "discard"? (D50)
- ★ Why does the scorer refetch arrivals rather than read the station boards already archived?
- ★ A date is scored only once. What does that make dangerous, and what guards it?
- ★ What does the scorer do if a night's run is missed?
- ★ Zero operator comparisons on a day is treated as a failure, not a zero. Why? (D50)

## K. Running it

- ★★ What breaks if the laptop is closed overnight, and what does not?
- ★★ You ran a seven-day parallel run before switching over. What were you actually testing, and what was the pass mark? (D36)
- ★★ Why did the parallel run have a hard expiry date rather than running until it passed?
- ★★ You deliberately broke the system to test the alarms. What did that find? (D36)
- ★ What is the difference between a `degraded` cycle and a `failed` one, and why does the distinction matter to the cutover decision?
- ★ Byte-for-byte equality was explicitly *not* the pass mark for the parallel run. Why not?
- ★ What is OIDC and why are there no AWS keys in the repo?
- ★ What is the retraining policy, and why is "no scheduled retraining" a decision rather than laziness?
- ★ What is a champion/challenger gate and what does it protect against?

## L. Cost

- ★★ What does this cost to run per month, and what is the single largest line item?
- ★★ Which three AWS services did you refuse to use, and what do they have in common?
- ★ Two free-tier thresholds in this deployment move quietly. Which, and what happens past them? (D51)
- ★ Batching predictions into one object per cycle — what does that actually save? (D49)

## M. Judgement, and things that went wrong

- ★★★ Tell me about a bug you found. *(The training/serving skew — D52. Know the numbers: 1371s mean against a 48s median, the A728 Woodlawn record, 0 of 334,984 in the offline archive.)*
- ★★★ How did you find it, and what would have hidden it?
- ★★★ What did you change so that class of bug is harder to reintroduce?
- ★★ Tell me about something you built and then deleted, or a result you had to withdraw.
- ★★ What is the weakest part of this project?
- ★★ What did you get wrong in your own documentation, and how did you catch it?
- ★ Why is `AutoArrival=1` not sufficient protection against bad label data? (D52)
- ★ When two rules disagree and you cannot tell which is right, what does this system do?
- ★ What is in scope and what is explicitly out, and who decided?
- ★ Which decisions were reversed during the project, and what changed your mind?

---

## N. The theme — five failures with one shape

**This is the spine of the how-it-works page, not another section of it.**

Five separate failures in this project share a shape. None raised an exception. None
appeared in a log as an error. Every one produced output that looked exactly like a
correct, unremarkable result, and every one was caught the same way: by taking a number
and asking what it *should* have been.

| # | What it looked like | What it was |
|---|---|---|
| 1 | Flagged lines reporting arrival times | Scheduled times echoed back as actuals (D20-D23) |
| 2 | 420 successful fetches, HTTP 200 | An ISP captive portal returning 1,369 identical bytes |
| 3 | `CREATE_COMPLETE` on an alarm topic | A subscription nobody confirmed, reaped 48h later |
| 4 | A model with a 22-minute average error | Two ways of computing "late" disagreeing by a day (D52) |
| 5 | `0 new codes` from the harvest | A folder nothing had written to since July (D55) |

- ★★★ What do these five have in common, and why is that more interesting than any one of them?
- ★★★ Why is a silent wrong answer more dangerous than a crash?
- ★★★ In each case, what was the number you compared against, and where did the expectation come from?
- ★★★ Which of the five would still happen today, and what specifically stops the others?
- ★★ Why did none of these produce an error, given that the code has error handling throughout?
- ★★ Three of the five were caught by a *distribution* rather than a single value. Which three, and what does that suggest about what to monitor?
- ★★ In case 4, what would have happened if only the headline number had been published?
- ★★ In case 3, the deploy reported success. What is the general lesson about trusting a tool's own report that it worked?
- ★★ Two of the five were introduced by *me*, after the system was working. What does that say about when to be most careful?
- ★★ For each, what is the cheapest check that would have caught it on day one?
- ★ Case 1 was "confirmed" by a first analysis before being overturned. What made the first analysis convincing?
- ★ Case 5 was misdiagnosed before it was diagnosed. What was the wrong explanation, and why was it plausible?
- ★ Which of these would a test suite have caught, and which would it not?
- ★ What is the difference between a system that works and a system you can tell is working?
- ★ If you had to add exactly one automated check to this project tomorrow, which would it be and why?

The uncomfortable version of the question, which is the one worth being ready for:

- ★★★ How many failures of this shape are still in the project, undetected, right now?

---

## Gaps to close before the page is written

Things nobody can currently answer, including me:

- The live head-to-head number, on a sample large enough to mean anything. Right now it is 13 matched events from one cycle, and it says the model *loses*.
- Whether interval coverage holds up live at the 80% it claims. First reading is 67%, on 73 rows.
- Whether the weak-coverage lines behave differently live than they did offline.
- What the model does on a day with real disruption, which has not happened yet during the observed window.
