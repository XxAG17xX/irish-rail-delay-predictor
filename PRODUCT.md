# PRODUCT.md — RailCast

Durable product truth. Visual decisions live in DESIGN.md, written from the built world at
finish. Engineering decisions live in docs/decisions.md.

## The mechanism, in one sentence

For a train already running, RailCast predicts how late it will arrive at a stop ahead as
an 80% range rather than a single time, logs every prediction before the outcome exists,
and scores itself against the operator's own estimate every night in public.

## Who it is for, and the real scene

Two audiences, and they are not the same person.

**The engineer or hiring manager**, at a desk, on a laptop, in daylight, giving the site
about ninety seconds before deciding whether the person who built it can think. They are
looking for evidence of judgement, not features. They will read the accuracy page and the
limitations before they read anything else, and a claim without a denominator will lose
them instantly.

**The passenger**, on a phone, on a platform, in bad light, in a hurry. They want to know
whether they will make the connection. They will never read a word of the method.

The engineer is the primary audience: this is a portfolio artifact with a deadline. The
passenger is who the product must still genuinely serve, because a portfolio piece that
does not work as a product proves nothing.

## What it does and refuses

- Answers for a train **already in service** that has reported at an upstream stop.
- Refuses future-dated services, and says why rather than guessing.
- Publishes coverage beside accuracy, always. "27% better" without "answers this often" is
  the misleading version.
- Publishes where it loses. The intervals cover 0% of delays over an hour, and that goes on
  the page next to the headline rather than in a footnote.

## What is true, and must never be dressed up

- Head to head against the operator's own `ExpectedArrival`, on matched events only:
  currently about 26% closer over roughly 20,000 matched predictions in a rolling week.
- Interval coverage is **75.1% against a claimed 80%**, and on two corridors it is nearer
  65%. That shortfall is a finding the site reports, not a number to be tuned away.
- The predictions being scored are **scheduled samples from a generator**, not visitor
  traffic. Board views from the site are logged separately as `api_board`.
- One model, trained July 2026, serving since 3 September. Nothing is ever regenerated
  after the fact.

## Brand commitments

- Called **RailCast**. Not affiliated with Iarnród Éireann, and must never imply it is.
  No use of the Irish Rail wordmark, logo, or their exact identity palette.
- Data credited to the public Irish Rail realtime feed.
- **No em dashes anywhere in the copy.** House rule.
- Plain language before the technical version, everywhere, including on the site.

## Surfaces

| Surface | Mode | The visitor's success |
|---|---|---|
| Landing | Persuade | Understands what this is and why the range matters, in one screen |
| Predictions | Operate | Reads a board and knows whether to run for the train |
| Accuracy | Read | Understands how good it is, including where it is not |
| How it works | Read | Understands the method and the four or five things that went wrong |

## Constraints

- Plain HTML, CSS and vanilla JavaScript. No build step, no framework, no npm (D41).
- Static files on S3 behind CloudFront, plus one Lambda behind `/api`.
- Deadline: mid September 2026. Design is the last phase before deploy.
- Must hold up on a mid range Android phone in daylight.
