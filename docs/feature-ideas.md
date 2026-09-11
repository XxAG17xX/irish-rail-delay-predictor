# Features

The twelve inputs the served model takes, the rule that decides what is allowed to be one,
and the candidates that were rejected.

`src/features.py` is the definition: one list, imported by the training script and by the
live API so the two cannot drift. This file says why the list looks the way it does.

## The rule: describe the situation, not the identity

**Train code is not a feature.** A model that learned "A218 runs two minutes down" has
nothing to say about a service launched next March, which arrives as an unknown category,
and nothing to say about an existing service the day it is renumbered. Those are often
exactly the trains people want to ask about, and a model built on identity fails on them
without raising an error.

What is used instead is time of day, day of week, route, stops remaining, scheduled time
still to run, and how much delay has accumulated upstream today. Every one of those exists
for a train that launched yesterday, so new services work the moment they appear in the
feed. `features.EXCLUDED` records the exclusion next to the code, with its reason.

Station codes are in the model and are not an exception. `vantage_location` and
`target_location` identify pieces of railway, not services: an approach, a platform and a
junction behave the way they behave whichever train is standing on them. New stations are
rare, new and renumbered services are routine.

## The second rule: computable at the moment of the request

At prediction time two things are known: the published timetable, and every stop the train
has already reported. Nothing is known about stops it has not reached. A feature built from
the finished journey trains fine, validates well, and cannot be served. That is not
hypothetical: the training script and the offline comparison each kept their own copy of
the feature list, one carried a feature the other excluded, and the saved model could not
have answered a live request. Nothing detected it; a human read both files. The fix was to
collapse them into one (D35).

## The twelve

Numeric:

| Feature | What it is |
|---|---|
| `current_delay_sec` | delay at the vantage stop, the most recent stop the train reported. The dominant input. |
| `prev_delay_sec` | delay one reported stop earlier |
| `prev2_delay_sec` | delay two reported stops earlier. Together the three give a trend rather than a level: growing, flat or recovering. |
| `horizon_route_stops` | timetabled stops from the vantage to the target |
| `horizon_sched_sec` | scheduled seconds from the vantage to the target. Carries how much slack the timetable leaves on that stretch. |
| `vantage_hour` | hour of day at the vantage stop |
| `vantage_minute_of_day` | the same clock at minute resolution |

Categorical:

| Feature | What it is |
|---|---|
| `day_of_week` | weekday, Saturday and Sunday are three different railways |
| `vantage_location` | station the train reported from |
| `target_location` | station being predicted |
| `TrainOrigin` | where the service started |
| `TrainDestination` | where it ends. With `TrainOrigin`, a corridor. |

Three details are easy to read past:

- `prev_delay_sec` and `prev2_delay_sec` step back over stops that never reported. About
  **31%** of movement records never receive an actual time, so the previous reported stop
  is frequently not the previous stop on the route. `horizon_route_stops` counts timetabled
  stops, which is knowable in advance, so the two are measured on different scales
  deliberately.
- `vantage_hour` and `vantage_minute_of_day` come from the vantage stop's scheduled time,
  not from the wall clock at the moment of the request. The peak a train is running into is
  the one its timetable puts it in.
- Delay means `arrival - scheduled` against that stop's own scheduled time, folded back by
  a day when the result exceeds twelve hours (`feedtime.delay_seconds`). A second rule for
  the same quantity once grew in the serving path, so the dominant feature was computed one
  way in training and another way live. Every well-behaved journey agreed and a handful
  disagreed by a whole day (D52).

The columns are the vocabulary; LightGBM works out the sentences. Nothing in the code says
"if Friday and the hour is 17, add ninety seconds". Given `day_of_week` and `vantage_hour`
the model finds that split itself, by measuring which split reduces error most. What it
cannot do is invent a column that was never supplied.

## Two features the feed does not have

**`line` does not exist in the feed.** `TrainOrigin` to `TrainDestination` is the closest
available proxy: unlike a single station code it identifies a whole corridor. It is a
proxy, not the thing.

**`train_type` does not exist either.** `getTrainMovementsXML` carries no type field, so
DART, commuter and intercity are not distinguishable in the archive at all.
`getCurrentTrainsXML_WithTrainType` exposes a type live, which does not label historical
records. `TrainCode` prefixes (`E`, `A`, `D`, `P`, `B`) look like a class marker and would
probably work, and they are deliberately unused, because nobody on the project knows what
they mean and an input that cannot be defended does not go in the repo.

The gap is larger than one missing column. A DART is a short electric suburban hop with
stops a couple of minutes apart and many chances to recover; an intercity run can put an
hour between stops, so an early delay has a long time to grow. The relationship between
delay at the previous stop and delay at the target almost certainly has a different shape
in each case. Whether that is best handled by one model with a type feature or by separate
models per type is a real structural question, and it stays untested while the label does
not exist in the archive. **One model serves the whole network.**

## Rejected

| Candidate | Reason |
|---|---|
| `TrainCode` | Identity, not situation. The rule at the top. |
| `ExpectedArrival` | The operator baseline the model is compared against. Feeding it in makes the comparison meaningless. |
| Anything derived from the actual arrival at the stop being predicted | The label leaking into the inputs. Produces excellent validation scores and a service that does nothing. |
| `horizon_observed_stops` | Counts the stops that *did* report, knowable only after the journey finishes. Kept as a column in the examples Parquet for offline breakdowns, never a model input. `horizon_route_stops` carries the same information in a knowable form (D35). |
| `train_type`, `line` | No such field in the feed. See above. |
| `is_public_holiday` | Pooling every holiday into one flag is sound, and the archive is a single summer window (25 June to 2 August 2026), too short to fit the effect. |
| Weather | Requires joining a second data source into both the training set and the request path. Not built. |
| `month`, seasonality | Needs years of history. 34 dates cannot support it. |
| Rolling mean delay per segment, 7 or 28 day | Would answer "how has this stretch of track been behaving lately". Needs a per-segment history maintained at request time; the serving path holds the model artifact and the live feed and nothing else. |
| Scheduled dwell and run time at upcoming stops | Timetable slack is where recovery happens, and `horizon_sched_sec` already carries it between vantage and target. Not tested separately. |
| `direction` | Largely carried by `TrainOrigin` and `TrainDestination`. |
| Trains ahead on the same line | The most interesting one missing: delay propagates between trains, not only within one. Needs the live state of other services at request time and is the largest piece of work on this list. |
| `LocationType=C` | An undocumented fifth value that looks like a contiguous stretch of route not served on that run, where almost no record carries an actual time. If that reading holds it is a cancellation signal, which is a different prediction target from lateness. Meaning not established; see `docs/data-dictionary.md` section 7. |
