# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: reviewers.** Recruiters, interviewers, and the author's MAI thesis supervisor (a
quantitative transport modeller), arriving from a CV link, LinkedIn or an email. Within about a
minute they decide whether this is serious, well-evidenced work; a minority then go deep into
the method, the accuracy record and the decision log.

**Secondary: people travelling in Ireland** checking a train that is already running, often on a
phone at a platform. The board must work for them as a real tool, but the site is not optimised
around them.

## Product Purpose

RailCast predicts how late an Irish Rail train that is already running will arrive at a station
further along its route, and answers with an **80% range** rather than a single time. Every
prediction is written down before the train arrives and scored against the real arrival the next
morning, and the record is published, including where the model loses.

Success: beat Irish Rail's own estimate on well-covered lines, and be explicit about the lines
where the data cannot support a claim. An honest loss is a better result than an unverifiable win.

## Positioning

- **The moment it serves.** A train is running, the board shows one time, and someone has to
  decide whether to hurry or walk. Apps and maps already say which train and roughly when;
  RailCast says how far to trust that time right now: the earliest the train plausibly arrives
  and the latest. It is not a journey planner and has no better data than the operator: it
  reads the same public feed and adds the range, checked every morning.
- **Headline (chosen 2026-11-19):** "The board says 17:10. RailCast says 17:07 to 17:14.",
  filled from a real running train; when none is running, from a real logged prediction
  labelled with its date. Stated once, on the first screen. "Not a time. A range." is retired
  as a slogan.
- **A range, not a time.** A station board gives one number with no sign of how sure it is.
  RailCast gives the span the arrival should land inside four times in five.
- **Checkable, not claimed.** Predictions are logged before their outcomes exist, the scorer
  cannot rewrite them (enforced by permissions), and the accuracy page recomputes every morning.
- **A sealed test week**, opened once against a plan written beforehand, and a head-to-head
  against Irish Rail's estimate on matched events only.

## Operating Context

- **Data:** Irish Rail's public realtime feed, keyless and offered as is with no support. It
  provides journeys (the labels), station boards with the operator's estimate (the baseline,
  live only), the trains running now, and the station list. The feed also carries coordinates
  for all 171 stations and a position for each running train.
- **Daily rhythm:** a poller and a prediction generator every five minutes; a nightly scorer at
  06:15 that publishes the accuracy record.
- **Trains are only answerable once running and reported somewhere.** Overnight the network is
  empty (zero running trains at 02:30 is normal), so every live surface has a real empty state.
- **The reviewer's path:** a link on a CV or LinkedIn, then the landing page, then the board, the
  accuracy page or how-it-works, and often the GitHub repository and its decision log.

## Capabilities and Constraints

- **Surfaces:** landing; board (`predictions.html?station=CODE` loads a station's board and is
  shareable); accuracy; how it works; 404.
- **Stack (existing, D41 amended by D66):** static HTML, Tailwind v4 compiled ahead of time,
  TypeScript as a `checkJs` check, no framework. Served from S3 through CloudFront.
- **Security policy allows same-origin resources only:** any library must be served by the site
  itself, never from a CDN. `/api/*` is forwarded to a rate-limited Lambda capped at five
  concurrent executions.
- **CI:** a Lighthouse audit runs after every deploy; accessibility and best-practice failures
  turn the run red.
- **Both hostnames must keep working:** `railcast.ag17.dev` and the original
  `dc9icf7494up8.cloudfront.net`, which is printed on CVs already sent.
- **Scope:** the portfolio scope lock was lifted on 2026-11-19 for a full redesign (D86). A map
  view of the network is permitted where it is accurate and genuinely useful.
- **Binding reporting rules:** head-to-head figures on matched events only; coverage published
  per line beside the 80% claimed, never as one blended number; live figures carry the period
  they measure; the zero coverage on delays over an hour is stated; the scored predictions are
  stated to be scheduled samples, not visitor traffic; the accuracy page shows when it was
  generated and the model version.
- **Terms in use:** range (prediction interval), coverage, matched events, sealed test week,
  echo, vantage stop, scheduled samples.
- **Undecided:** whether live train positions are precise enough to place trains on a
  geographic map, or only between their last and next stations. To verify against the feed in
  daytime before any map work.

## Brand Commitments

- The name is **RailCast**.
- **Quiet authorship:** the product leads; a footer credit to Kartik Gupta with links to GitHub
  and LinkedIn.
- **Not affiliated with Iarnród Éireann.** Nothing may imitate its identity or imply an official
  service.
- **Public copy house style:** no em dashes; no defensive phrasing; third person, no "we";
  every limitation keeps its number beside it.
- **Bilingual station names:** the official Irish form from logainm.ie beside the English one
  wherever a station is named. A station without a verified Irish form shows English only.
- The current mark is a placeholder; a logo is being created from the brand boards in
  `design/refs/` (generated in ChatGPT as references; the mark is redrawn as exact SVG).

## Evidence on Hand

- The live accuracy record, regenerated nightly (`accuracy.json`).
- The sealed test week result (`docs/test-week-2026-09-10.txt`) and the offline head-to-head
  (D46).
- The decision log, `docs/decisions.md` (85 entries), and the label-quality analysis.
- Real journeys and boards, including captured screenshots (`docs/img/`).

**Absent, and never to be fabricated:** user testimonials, usage or visitor numbers (the scored
predictions are scheduled samples), press coverage, or any partnership with Irish Rail.

## Product Principles

1. **Show the range.** The uncertainty is the product, so it is never hidden behind one number.
2. **Every claim is checkable:** a figure travels with its date and its population.
3. **Losses sit beside wins,** in the same place and at the same size.
4. **The real thing on screen:** real stations, journeys and figures; an example is labelled as one.
5. **Explicable over clever:** the author must be able to defend every element under questioning.

## Accessibility & Inclusion

- WCAG AA contrast, checked in CI; every motion has a reduced-motion form; keyboard and
  screen-reader use work across all surfaces.
- The board serves phones in outdoor light, at a glance.
- Irish-language station names, as on the network's own signage.
