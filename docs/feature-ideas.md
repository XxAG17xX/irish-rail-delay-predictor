# Feature ideas

Candidate model inputs, with reasoning and priority. Nothing here is committed — these
become experiments once the evaluation harness exists.

## The rule everything follows

**Describe the situation, not the identity.**

No train code as a feature. No "this specific service tends to run late." A model built on
identity cannot handle a service launched next March, or one that gets renumbered — it
arrives as an unknown category and the model has nothing to say. Every feature below is
computable for a train running for the first time today.

## What I supply vs what the model works out

I supply the **vocabulary** — the columns. Gradient boosting works out the **sentences** —
which columns matter, in what combination, and where the thresholds fall.

I do not write "if Friday and hour is 17 then add 90 seconds." Given `day_of_week` and
`hour` as columns, the model finds that split itself, by measuring which split reduces
error most. So questions like "does day-of-week matter more than month?" are not mine to
answer — the model ranks them empirically, and I read the ranking off the feature
importances afterwards.

What the model **cannot** do is invent a column I never gave it. That is the whole job.

## Tier 1 — expected to dominate, build these first

| Feature | Why |
|---|---|
| `delay_at_previous_stop` | Delay is strongly autocorrelated within a journey. A train 5 min down at Portlaoise is very likely ~5 min down at Thurles. Expect this to carry most of the signal. |
| `delay_2_stops_back`, `delay_3_stops_back` | Trend, not just level. Is the delay growing or recovering? |
| `stops_remaining` / `distance_to_go` | Uncertainty grows with horizon. Also lets the model learn where slack sits in the timetable. |
| `hour`, `minute_of_day` | Peak congestion. Expect strong effects around commuter peaks. |
| `day_of_week` | Weekday vs Saturday vs Sunday are different railways. |
| `line` / `route` | Different infrastructure, different reliability. Also interacts with the coverage problem — see data dictionary section 5.1. |

## Tier 2 — plausible, test after a baseline exists

| Feature | Why |
|---|---|
| `rolling_mean_delay_this_segment_7d` | "How has this stretch of track been behaving lately?" Captures temporary engineering works or a persistent problem. |
| `rolling_mean_delay_this_segment_28d` | Longer-horizon version of the same. |
| `scheduled_dwell_time` at upcoming stops | Slack in the timetable is where recovery happens. |
| `scheduled_run_time` for the next segment | Some segments are padded, some are tight. |
| `is_public_holiday` | See note below. |
| `train_type` (DART / commuter / intercity) | Different stopping patterns and different delay dynamics. Identity-free, so allowed. **See the dedicated section below — this is a modelling-structure question, not just a column.** |
| `direction` | Northbound/southbound asymmetry, e.g. peak flow into Dublin. |

## Tier 3 — later, needs external data or long history

| Feature | Why |
|---|---|
| Weather (Open-Meteo: rain, wind, temperature) | Plausible effect, especially wind on coastal DART. Requires joining a second source. |
| `month` / seasonality | **Requires 2+ years of data.** Cannot be learned from a 30-day slice. |
| Network congestion — trains ahead on the same line | Conceptually the most interesting feature here: delay propagates between trains, not just within one. Expensive to compute. Closest thing to thesis territory. |
| `is_c_segment` — cancellation / not-served signal | `LocationType=C` marks a contiguous route segment, stable per service across weeks, where only 5.8% of records carry an actual arrival against 87.7% at ordinary stops. Reads as "not served on this run". If that holds it is a *cancellation* signal, which is a different prediction target from lateness — and possibly a more useful one to a passenger. Meaning not yet established; see data-dictionary.md section 6. Do not use as an input until it is. |

## Note on holidays, since it is a good illustration

The model cannot recognise "St Patrick's Day" from a date. With two years of data it sees
that date twice — nowhere near enough to learn anything.

But `is_public_holiday` as a flag pools **every** holiday together, and they share the
structure that matters: reduced service, different demand patterns. That generalises from
a handful of examples.

This is the clearest case of feature engineering beating "hope the model figures it out."
I am encoding knowledge that the raw data cannot supply on its own.

## Train type — one model with a feature, or separate models?

This is the one structural modelling question in this file. Everything else is a column;
this is a choice about how many models there are.

**Why it matters.** DART, commuter and intercity are barely the same problem. A DART is a
short, frequent, electric suburban hop — stops a couple of minutes apart, delays measured
in a minute or two, and plenty of chances to recover across a dense stopping pattern. An
intercity run is multi-hour with sometimes an hour between stops, where a delay picked up
early has a long time to grow or decay and far fewer opportunities to recover. The
relationship between "delay at previous stop" and "delay at the stop being predicted" —
the feature expected to dominate everything — almost certainly has a different shape in
each case.

**Two options, both testable once the evaluation harness exists.**

1. **One model, `train_type` as a feature.** Gradient boosting can split on it and learn
   different thresholds per branch. Keeps all the data in one place, so rare situations in
   one type still borrow strength from patterns shared across all of them. Simpler to
   train, serve and retrain.
2. **Separate models per type.** Guarantees the model cannot average across categories
   that behave differently, and lets each one have its own hyperparameters and its own
   quantile calibration. Costs sample size per model and triples the serving and
   retraining surface.

The honest expectation is that option 1 wins unless the interaction is very strong,
because trees already handle this and splitting the data is a real cost. But that is a
prediction, not a result. Decide it by measuring, per type, not by argument.

**Getting the label is the awkward part.** Three known problems, in order of how much they
matter:

- `getCurrentTrainsXML_WithTrainType` exposes the type as `A` / `M` / `S` / `D`. That is a
  fourth endpoint, and CLAUDE.md currently scopes the project to three — treat adding it
  as a scope decision, not a detail.
- The backfilled `getTrainMovementsXML` records **may not carry the type at all**. Check
  the parsed Parquet before assuming it does. If it is absent, none of the historical data
  is labelled.
- Fallbacks if it is absent: join it on from the harvested live snapshots in
  `data/raw/current/`, which cover only the dates the harvester ran; or infer it from the
  route — stopping pattern, stop spacing, and origin/destination are all strong proxies.
  Inference is cheap and covers the whole archive, but it is a derived label and any result
  that depends on it needs saying so out loud.

## What NOT to include

- **Train code** — see the rule at the top
- **Anything not available at prediction time.** The obvious trap: a feature computed from
  the train's *actual* arrival at the stop being predicted. That is the label leaking into
  the inputs. It produces spectacular validation scores and a service that does nothing.
- **Operator `ExpectedArrival` as an input.** It is the *baseline being compared against*.
  Feeding it in makes the comparison meaningless.

## Priority reminder

Feature ideas are cheap and there will be dozens. What is scarce is the harness that says
whether an idea helped — parse, split, train, measure.

Build that on 30 days with five crude Tier 1 features. After that, every idea in this file
is a twenty-minute experiment with a number attached, instead of an argument.
