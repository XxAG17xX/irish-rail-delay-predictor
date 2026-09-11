# The public web layer

RailCast serves four static pages and one live prediction endpoint to the open internet. This
is how that is secured on AWS and why each control is there. Ground truth is three
CloudFormation templates, `infra/site.yaml`, `infra/github-oidc.yaml` and `infra/api.yaml`;
where this document and a template disagree, the template is right.

## 1. Shape

```
visitor  ->  CloudFront  ->  S3 bucket (the pages)
                        \->  Lambda Function URL (/api/*, the predictions)
```

Two buckets exist, and keeping them apart is most of the security story.

| Bucket | Holds | Reachable from the internet |
|---|---|---|
| `rail-delay-poller-kg` | every prediction, score and raw capture | never |
| `railcast-site-kg` | four pages plus a 404, one stylesheet, the page scripts, two self-hosted fonts, `accuracy.json` | through CloudFront only |

The site gets its own bucket rather than a public prefix inside the data bucket. A prefix
policy puts one rule between the internet and the entire archive, and one typo in that rule
exposes all of it. With two buckets, the worst mistake possible in the site bucket's policy
exposes files that were already public. Data moves outward only: the nightly scorer writes
`accuracy.json` **into** the site bucket at one exact key with no wildcard (`SiteBucket` in
`infra/scorer.yaml`), and nothing on the site side reads the data bucket.

## 2. The site bucket is not public

Four properties of `infra/site.yaml` carry this, and the second is the one worth the time.

**No `Principal: "*"` anywhere.** The policy names one principal, `cloudfront.amazonaws.com`,
which is AWS's CDN service itself. A visitor cannot address the bucket at all, only ask
CloudFront, which fetches on their behalf.

**The `AWS:SourceArn` condition, which is the load-bearing line.** `cloudfront.amazonaws.com`
is a **shared service principal**: every CloudFront distribution in every AWS account acts as
that same principal. A policy saying "CloudFront may read this bucket" and stopping there is
saying "any CloudFront distribution in anybody's account may read this bucket", and someone
could point a distribution of their own at it and serve its contents. The condition pins the
statement to one distribution:

```yaml
Condition:
  StringEquals:
    AWS:SourceArn: arn:aws:cloudfront::<account>:distribution/<this distribution>
```

Read it as: allow this, but only when the request originates from that one distribution in
this account. Delete the condition and the bucket is readable by anyone who knows its name and
can create a distribution. This is the **confused deputy** problem, where a trusted service is
talked into using its authority on behalf of the wrong person, and the fix is always to check
not only who is asking but on whose behalf.

**`s3:GetObject` and nothing else.** `GetObject` reads one object whose exact name the caller
already knows; `ListBucket` returns an inventory. A caller who guesses `/index.html` gets a
file that was going to be public, and a caller who wants to know what else is there gets
nothing.

**All four public access blocks stay on, including here.** On the data bucket that is obvious;
on the site bucket it is not. CloudFront reads the bucket as a named principal over signed
requests, so it never needs public access, and a site can be served to the world from a bucket
that is not public in S3's sense at all. `BlockPublicPolicy: true` then makes AWS refuse any
future policy that would change that: a later `Principal: "*"` fails the deploy rather than
succeeding quietly.

**Origin Access Control** is the mechanism underneath. CloudFront signs each request to S3 with
SigV4, the same way the AWS CLI does, and S3 checks the signature and the policy, including the
`AWS:SourceArn` condition. It replaced Origin Access Identity, which used a special user rather
than a signature; a tutorial recommending OAI predates 2022.

## 3. Response headers

One response headers policy is attached to both cache behaviours.

| Header | Value | What it stops |
|---|---|---|
| `Strict-Transport-Security` | 2 years, `includeSubDomains`, no preload | a downgrade to plain http. Preload is a one-way door and this is a `cloudfront.net` subdomain |
| `X-Content-Type-Options` | `nosniff` | a browser guessing a `.json` is really HTML and running it |
| `X-Frame-Options` | `DENY` | clickjacking; nothing here should ever be framed |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | full URLs leaking to other sites |
| `Permissions-Policy` | camera, microphone, geolocation, payment, usb, interest-cohort all empty | a future compromised script reaching features the site never uses |
| `Cross-Origin-Opener-Policy` | `same-origin` | a cross-origin opener sharing a browsing context group |

The last two are custom headers, because CloudFront's `SecurityHeadersConfig` does not cover
them. Every Content Security Policy directive is `'self'` or `'none'`:

```
default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self';
img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none';
frame-ancestors 'none'
```

The page makes no cross-origin request of any kind, so nothing about a visitor reaches anyone
but this distribution. The last third party was Google Fonts; both typefaces now come from this
origin under their SIL Open Font Licence, which ships beside them (D75). Removing the
dependency is what let the policy tighten, which is the usual direction: the strictest policy
is the one with the fewest things to permit. `script-src` carries no `'unsafe-inline'` because
the site has no inline script, and `style-src` is equally strict because the page-local
stylesheet was folded into `app.css` and the two bar widths are set through the CSSOM rather
than as `style` attributes. `connect-src 'self'` covers `/api`, same-origin through the
distribution.

Both 403 and 404 from the origin map to `/404.html` with a 404 status. Without `ListBucket`, S3
answers 403 for a key that does not exist, so a mistyped URL would otherwise render raw XML.

## 4. The API behind the same distribution

The prediction service has a **Function URL**, a plain HTTPS address AWS gives a Lambda
function. Its `AuthType` is `NONE`: anyone holding the address can call it. CloudFront forwards
`/api/*` there, and a six-line CloudFront Function strips the `/api` prefix at the edge,
because a cache behaviour cannot rewrite a path on its own. `scripts/dev_site.py` does the same
locally, so the page calls `/api/board?station=KDARE` in both places and needs no branch for
which environment it is running in.

What this buys, stated precisely, because the Function URL stays reachable directly by anyone
who has it: the site does not publish the address, so it is not discoverable by reading the
page; one place now exists, the distribution, where a WAF or rate rule can go without touching
the application; and the browser sees a single origin. That is obscurity plus a future control
point, not a lock. Section 5 is the lock.

The `/api/*` behaviour uses the managed **CachingDisabled** policy: a cached board would serve
a prediction that was never written to the log, and "every prediction is logged before the
outcome exists" is the property the accuracy page rests on. It also uses
**AllViewerExceptHostHeader** rather than AllViewer, because forwarding the viewer's `Host`
header to a Function URL breaks SigV4 host matching.

## 5. The concurrency cap

`ApiFunction` in `infra/api.yaml` carries `ReservedConcurrentExecutions: 5`. Lambda rejects a
sixth simultaneous request with a 429 before any application code runs, so no volume of traffic
can turn the endpoint into a bill, or into a flood against Irish Rail's free feed.

**The order in which this became possible is counter-intuitive.** Reserved concurrency is two
things at once: a guarantee to the function and a ceiling on it. AWS refuses any reservation
that would leave fewer than 100 unreserved executions in the account, so at the old account
limit of 10 a cap was arithmetically impossible. The limit was raised from 10 to 1000 and the
reservation applied the same morning, in that order. Raising the ceiling is what made a floor
possible, and raising it without then capping would have been strictly worse than leaving it
alone (D78, 2026-09-11). 995 executions stay unreserved, so a flood on the public endpoint can
no longer starve the poller or the scorer, which was always the worse of the two outcomes: a
lost dollar comes back, a collection cycle that never ran does not.

Token buckets in `src/ratelimit.py` hold one caller down and a pacer holds each container to
two requests a second against the feed. Neither can limit the number of containers, which is
what the reservation does. A throttled real visitor would show as `AWS/Lambda Throttles` on
that function; raising the 5 then costs nothing until it happens.

## 6. Deploying without an AWS key

The old way to let CI deploy is an IAM user with an access key pasted into the CI system's
secret store: long lived, usable from anywhere, one leaked log line away from being someone
else's. **OIDC**, OpenID Connect, replaces it. When a workflow runs, GitHub mints a short lived
signed token stating which repository, branch and workflow is running; AWS trusts GitHub's
signing key and issues temporary credentials to tokens whose claims match a condition. Nothing
is stored, so there is nothing to leak and nothing to rotate.

**The subject claim is not the string the published guides show.** `infra/github-oidc.yaml`
trusts:

```
repo:<owner>@<ownerId>/<repo>@<repoId>:ref:refs/heads/<branch>
```

not the familiar `repo:<owner>/<repo>:ref:refs/heads/<branch>`. The numbers are the owner id
and the repository id, readable as `owner.id` and `id` from
`https://api.github.com/repos/<owner>/<repo>`. Trusting the name-only form produced
`Not authorized to perform sts:AssumeRoleWithWebIdentity` on four consecutive runs. That
message is returned for a mismatched subject, a mismatched audience, a role that does not exist
and an SCP denial alike, and distinguishes none of them, so each failure was consistent with
several theories and confirmed none. What settled it was asking the other side what it had
received: CloudTrail records failed `AssumeRoleWithWebIdentity` calls with the presented
subject in `userIdentity.userName`. One query, and the string was unambiguous. When an error
names no value, go and find the value (D72).

The id-bearing condition is also the better one to hold. Ids are immutable, so renaming the
repository or the account, or deleting it and someone else claiming the name, no longer
satisfies the trust; the name-based condition would have kept trusting all three.

Two further conditions carry weight. The audience `sts.amazonaws.com` stops the role accepting
tokens minted for other services. The branch stops anyone who can open a pull request from a
fork from running a workflow that deploys to production. Declaring an `environment:` in the
workflow would change the subject claim to `...:environment:<name>` and break the trust, which
is why `.github/workflows/deploy.yml` declares none.

The role can read the site stack's outputs, write objects to the site bucket, and create a
CloudFront invalidation. It cannot call `UpdateDistribution`, so a compromised workflow cannot
repoint the site at a different origin, and it has no access to the data bucket.

## 7. Cost

Measured cost for the whole project is about **$0.10 a month**, effectively all of it S3 PUT
requests from the poller. The web layer contributes close to nothing: under a megabyte of
static objects, CloudFront and its edge function inside permanent free tiers, and Lambda
invocations in the tens per day. A budget alarm predates the first deploy and is what will say
otherwise.
