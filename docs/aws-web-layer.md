# The public web layer, explained from scratch

Written because I asked what four security checks meant and could not follow the answer.
This is the version to read later, slowly. Nothing here is required to use the site; it is
required to defend it in an interview.

No prior AWS knowledge assumed. Every term is explained the first time it appears.

---

## 1. The shape of the thing

Four static files need to reach the public internet, and one Lambda function needs to answer
live questions. That is the whole job.

```
visitor  ->  CloudFront  ->  S3 bucket (the four pages)
                        \->  Lambda function URL (/api/*, the predictions)
```

**S3** is Amazon's file storage. You put objects in a **bucket**, which is just a named
container. **CloudFront** is Amazon's CDN, a network of servers around the world that sit in
front of something slow or private and serve copies of it quickly. **Lambda** runs a function
when something calls it and charges nothing while nobody does.

There are two buckets in this project and keeping them apart is the whole security story.

| Bucket | Holds | Public? |
|---|---|---|
| `rail-delay-poller-kg` | every prediction, every score, every raw capture | **never** |
| `railcast-site-kg` | four HTML files, one CSS file, three JS files, `accuracy.json` | served through CloudFront only |

The site bucket is a separate bucket rather than a folder inside the first one. A folder
would mean one policy standing between the internet and the entire archive, and one typo in
that policy would expose everything. Two buckets means the worst possible mistake in the site
bucket's policy exposes eight files that were going to be public anyway.

`accuracy.json` moves outward: the nightly scorer writes it **into** the site bucket, at one
exact key, with no wildcard. The site never reaches into the data bucket.

---

## 2. The four checks, one at a time

These are the four things I said to verify in `infra/site.yaml`.

### Check 1. There is no `Principal: "*"` anywhere

A **policy** is a list of rules saying who may do what to which resource. The **principal**
is the "who".

`"*"` means everyone, including everyone on the internet. It is the correct answer in almost
no situation, and it is the single most common cause of the "company leaks customer data from
an open S3 bucket" story.

The site bucket's policy names exactly one principal, and it is not a person or a public. It
is `cloudfront.amazonaws.com`, which is AWS's own CDN service. So the only thing on earth
allowed to read that bucket is CloudFront. A visitor cannot address the bucket at all; they
can only ask CloudFront, which then fetches on their behalf.

### Check 2. The `AWS:SourceArn` condition, which is the load-bearing line

This one is subtle, and it is the interesting one.

`cloudfront.amazonaws.com` is a **shared service principal**. Every CloudFront distribution
in every AWS account on earth acts as that same principal. So a policy that says "CloudFront
may read this bucket" and stops there is saying "any CloudFront distribution, in anybody's
account, may read this bucket". Someone could create a distribution in their own account,
point it at your bucket, and serve your files.

An **ARN** is an Amazon Resource Name, a unique identifier for one specific thing. The
condition is:

```yaml
Condition:
  StringEquals:
    AWS:SourceArn: arn:aws:cloudfront::<account>:distribution/<this distribution>
```

Read it as: allow this, **but only when the request is coming from that one distribution in
my account**. That line is what turns "CloudFront may read this" into "my CloudFront may read
this". Delete it and the bucket is effectively readable by anyone who knows its name and can
create a distribution.

This is called the **confused deputy** problem. The deputy is a trusted service (CloudFront)
that can be talked into using its authority on behalf of the wrong person. The fix is always
the same: make the resource check not just *who* is asking but *on whose behalf*.

### Check 3. `s3:GetObject` and nothing else

**Actions** are the verbs in a policy. `s3:GetObject` means "read one object, if you already
know its exact name". `s3:ListBucket` means "tell me everything that is in here".

The policy grants the first and not the second. Someone who guesses `/index.html` gets it,
which is fine because it was going to be public. Someone who wants an inventory of the bucket
gets nothing. The habit generalises: **grant the verb you need, never the category it belongs
to.**

### Check 4. All four public access blocks stay on, even here

S3 has four switches, collectively the **public access block**, that sit above every policy:

| Switch | Refuses |
|---|---|
| `BlockPublicAcls` | new per-object public permissions |
| `IgnorePublicAcls` | existing per-object public permissions |
| `BlockPublicPolicy` | a bucket policy that would make the bucket public |
| `RestrictPublicBuckets` | public access through any policy already attached |

They are on for the data bucket, which is obvious. They are also on for the **site** bucket,
which is not obvious, and that is the point worth understanding.

Because CloudFront reads the bucket as a named principal with signed requests, it does not
need public access. So the site can be served to the world from a bucket that is not public
in S3's sense at all.

The benefit is that `BlockPublicPolicy: true` makes AWS itself refuse any future policy that
would make this bucket public. If someone later pastes in a `Principal: "*"` rule, the deploy
**fails** rather than succeeding quietly. It is a guard rail that catches a mistake nobody has
made yet.

---

## 3. Origin Access Control, in one paragraph

**OAC** is the mechanism behind check 1. CloudFront signs each request it makes to S3 with
AWS credentials, the same way the CLI does. S3 sees a signed request from a known service,
checks the policy including the `AWS:SourceArn` condition, and answers. It replaces an older
mechanism called Origin Access Identity, which used a special user rather than a signature.
If a tutorial tells you to use OAI, it was written before 2022.

---

## 4. Why the API sits behind the same distribution

The prediction service has a **Function URL**, which is a plain HTTPS address AWS gives a
Lambda function. Its auth type is `NONE`, meaning anyone with the address can call it, and
nothing rate limits it.

CloudFront forwards `/api/*` to that address, so the page calls `/api/board?station=KDARE`
and the Function URL never appears in the page source.

**Be precise about what this does and does not buy.** The Function URL is still reachable
directly by anyone who has it. What changes:

- the site does not publish it, so it is not discoverable by reading the page,
- there is now one place, the distribution, where a rate limit or a WAF can be added later
  without touching the application,
- the browser sees one origin, so there is no cross-origin request to configure.

That is obscurity plus a future control point. It is not a lock, and calling it one would be
the kind of claim this project exists to avoid making.

`/api/*` is set to **CachingDisabled** deliberately. A cached board would serve a prediction
that was never logged, and "every prediction is logged before the outcome exists" is the
single property the accuracy page rests on.

---

## 5. OIDC, and why there is no AWS key in the repository

The old way to let a CI system deploy is to create an AWS user, generate an access key, and
paste it into the CI system's secret store. That key is long lived, works from anywhere, and
is one leaked log line away from being someone else's.

**OIDC**, OpenID Connect, replaces it. When a workflow runs, GitHub mints a short lived,
signed token that states which repository, which branch and which workflow is running. AWS is
configured to trust GitHub's signing key, and to hand out temporary credentials when the
token's description matches a condition you set.

The condition in `infra/github-oidc.yaml` is:

```
token.actions.githubusercontent.com:sub = repo:<owner>/<repo>:ref:refs/heads/main
```

Both halves matter. Without the repository, any repository on GitHub could assume the role.
Without the branch, anyone who opens a pull request from a fork could run a workflow that
deploys to production.

Nothing is stored anywhere. There is no key to leak and nothing to rotate. The role can write
to the site bucket, read the stack's outputs, and create a CloudFront invalidation. It cannot
change the distribution, and it cannot see the data bucket at all.

---

## 6. What this costs

| Thing | Cost |
|---|---|
| CloudFront | free tier is **Always Free**: 1 TB out and 10 million requests a month |
| S3 storage for the site | under a megabyte, effectively nothing |
| The CloudFront function that strips `/api` | free to 2 million invocations a month |
| Lambda, per board view | inside the permanent free tier at this traffic |

The 12 month free tier that expires in December covers S3 storage and requests. CloudFront's
does not expire. The whole site is expected to stay inside the free tier indefinitely, and the
budget alarm that has existed since before anything was deployed is what will say otherwise.

---

## 7. The questions to be ready for

- Why two buckets rather than one bucket with a public prefix?
- The bucket is not public, so how does the internet read it?
- What is the confused deputy problem, and which line in your policy prevents it?
- Why `GetObject` without `ListBucket`?
- Your site bucket serves a public website. Why are all four public access blocks still on?
- Putting the API behind CloudFront: what does that actually protect against, and what does
  it not?
- Where is your AWS access key for CI, and why is that the wrong question?
- What happens if someone forks the repo and pushes to their own main branch?
