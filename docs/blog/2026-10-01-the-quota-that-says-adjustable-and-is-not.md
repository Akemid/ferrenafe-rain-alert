---
date: 2026-10-01
title: The quota reports Adjustable, and the adjustment API refuses it
kind: technical
tags: [aws, lambda, quotas, deployment, correctness]
hook: The deploy failed on a one-line correctness guard. The fix is a quota increase. Service Quotas says the quota is adjustable, and then declines to adjust it.
status: raw
---

## What I expected

`cdk deploy` of the scheduled-cycle stack, with the schedule deliberately
disabled so nothing would run until it had been verified by hand. The stack
had been through three security reviews and two code reviews. I expected a
boring deploy.

```
CREATE_FAILED | AWS::Lambda::Function
Specified ReservedConcurrentExecutions for function decreases account's
UnreservedConcurrentExecution below its minimum value of [10].
```

The line it failed on:

```ts
// EventBridge Scheduler delivery is at-least-once; two concurrent cycles would both read an
// empty dedup history and both send. This throttles the second one so its retry sees what the
// first wrote (D35).
reservedConcurrentExecutions: 1,
```

A pre-deploy security review had predicted this exactly, and had said the
important half out loud:

> The tempting fix is to delete the line — which removes the only thing
> stopping two concurrent cycles, under at-least-once delivery, from both
> reading an empty dedup history and both sending.

So: do not delete the line. Raise the quota.

## What actually happened

The account's limit is not low. It is *ten*.

```console
$ aws lambda get-account-settings --query 'AccountLimit'
{ "ConcurrentExecutions": 10, "UnreservedConcurrentExecutions": 10 }
```

AWS requires ten to remain unreserved. The account has ten in total.
**No reservation is possible at any value** — not 1, not anything. This is
not a number to tune down; it is a door that is closed.

Fine. Raise it. The quota reports itself as adjustable:

```console
$ aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384
{ "QuotaName": "Concurrent executions", "Value": 10.0, "Adjustable": true }
```

`Adjustable: true`. So:

```console
$ aws service-quotas request-service-quota-increase \
    --service-code lambda --quota-code L-B99A9384 --desired-value 100

An error occurred (IllegalArgumentException):
You must provide a quota value greater than the default quota value of 1000.0
```

The default is **1000**. The account is at **10**. And the adjustment API
will only accept a request for a value *above the default* — so it will take
a request for 1001 and refuse a request for 100.

```
AWS default for this quota : 1000
Applied to this account    :   10
Adjustable                 : true
Smallest value the API will accept : 1001
```

The field is not lying about the quota. `Concurrent executions` genuinely is
adjustable — upward, past the default, for accounts that are at the default.
It just has no vocabulary for an account sitting *below* it. Being at 10 is
not a quota setting; it is an account-level restriction, the kind AWS applies
to a young account and lifts as billing history accumulates. Service Quotas
does not model that state, so it describes it with the closest field it has,
and the closest field it has is wrong in the direction that matters.

The only route is a support case, which is a human reading a paragraph.

## Why it matters

Three layers of this system agreed the reservation was correct. A design
decision argued it, a code comment recorded the reasoning, and a security
review predicted the exact failure *and* named the wrong fix before anyone hit
it. All of that held.

What none of it could tell me is that the environment would not permit the
correct thing — and that the API for making the environment permit it would
report itself as able, and then decline.

The failure mode this creates is specific and worth naming: you hit a wall,
you check whether the wall can move, the tool says yes, you try, and the tool
says no with an error about a different number entirely. At that point the
cheap move — the one a tired person makes the day before a deadline — is to
delete the line that caused the wall. The guard disappears, the deploy goes
green, and the thing the guard was protecting against becomes silently
possible. Nothing in the repository would have recorded why the line left.

That is the whole pattern this project keeps finding, arriving from a
direction I had not seen it come from before: not a check that did not run,
but a **capability that reports itself as present and is not**.

## What we did

Deployed without the reservation, with the exposure written down beside the
other finding it rhymes with, on the `Notifier` port — because the cost of a
duplicated cycle today is a duplicate row in the audit table and a repeated
entry on the page, and the cost on the day a real notifier exists is the same
neighbour being warned twice.

Both findings share one line: `ConsoleNotifier` being the only implementation
is what makes them survivable, and the first notifier that delivers is what
makes them matter. They belong in the same place for that reason.

And filed the support case, with the four-day-old deploy as the justification
rather than a hypothetical.

## The part I would get wrong again

I read `Adjustable: true` and believed it. It was right there in the API
response for the exact quota I was blocked on, and it answered the question I
was asking in the words I was asking it in.

What I should have done is what this repository's own first rule says, and
what I had already done twice that same afternoon for less important things:
check what the field means against the thing it describes, not against the
question in my head. `get-aws-default-service-quota` was one command away and
would have shown the 1000 immediately — and the gap between 10 and 1000 is the
whole story.

## Evidence

```console
$ aws service-quotas get-aws-default-service-quota \
    --service-code lambda --quota-code L-B99A9384 --query 'Quota.Value'
1000.0

$ aws service-quotas get-service-quota \
    --service-code lambda --quota-code L-B99A9384 --query 'Quota.Value'
10.0

$ aws service-quotas list-requested-service-quota-change-history-by-quota \
    --service-code lambda --quota-code L-B99A9384 --query 'RequestedQuotas'
[]
```

No prior request. The restriction has been there since the account was
created, and nothing about it is visible until the first thing that needs
concurrency tries to reserve some.
