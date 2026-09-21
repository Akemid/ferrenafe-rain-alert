---
date: 2026-09-21
title: Three bugs stacked, each hiding the next, and the dangerous one was last
kind: technical
tags: [aws, bedrock, agentcore, deployment, testing, observability]
hook: The first live invocation failed three times. Fixing each one did not make things work — it exposed the next failure, which had been invisible behind it.
status: raw
---

## What I expected

947 tests green. Eleven prompt-injection attacks contained. Mutation-checked
validators. An agent unit with 32 of its own tests, an output contract, a
deterministic fallback for every failure mode I could enumerate.

I expected the first real invocation to be a formality.

## What actually happened

It failed three times, and the shape of the failures is the interesting part:
**each fix did not make things work. It uncovered the next problem, which the
previous one had been hiding.**

### Failure 1 — a quota, in the wrong region

```
ModelThrottledException: (ThrottlingException) when calling ConverseStream
(reached max retries: 4): Too many tokens per day, please wait before trying again.
```

Not a code defect: the account's daily Bedrock token budget in `us-east-1` was
spent. Moved to `us-east-2`.

There was a free finding buried in that stack trace — **`reached max retries:
4`**. Our invoker sets `total_max_attempts: 1` and I had been pleased about
getting that right. But Strands retries *inside the container*, where our
client config has no reach. There is a retry layer I do not control, and it
multiplies elapsed time against a deadline I set assuming one attempt.

### Failure 2 — a parameter pair the SDK accepts and the service refuses

```
ValidationException: `temperature` and `top_p` cannot both be specified
for this model. Please use only one.
```

Ours. `BedrockModel(model_id=..., temperature=0.2, top_p=0.9)` constructs
without complaint; the rejection happens at `ConverseStream`, inside a
deployed container, with credentials. So the first symptom of a bad constant
is a 500 from production infrastructure.

And here is the part worth sitting with: **the quota problem had been hiding
this one.** The `us-east-1` attempt died at throttling before it ever reached
parameter validation. Changing region did not fix a problem. It revealed one.

### Failure 3 — the validator rejects a perfect message

The third invocation returned `200`. Cold start 9.71 s, 776 bytes, and a
genuinely good Spanish civil-protection message:

> Ferreñafe está en alerta por precipitaciones de moderada a fuerte
> intensidad. Se esperan 18.0 mm en 24 horas y 25.5 mm en 48 horas, con
> probabilidad del 75% de lluvia intensa el 3 de septiembre a las 16:00.

Then I ran it through our own validator:

```
UNKNOWN_NUMBER: '3' is stated in '%' and traces to no such value on the request
```

Every number in that draft is correct:

| In the message | On the request |
|---|---|
| 18.0 mm / 24 h | `mm_24h=18.0` |
| 25.5 mm / 48 h | `mm_48h=25.5` |
| 75% | `peak_probability_pct=75` |
| "el 3 de septiembre a las 16:00" | `peak_at=2026-09-03T21:00Z` = 16:00 in Lima |

The model even did the timezone conversion right. The `3` is a day of the
month. Our clause-scoped number scan sees a bare `3` near a `%` and demands a
matching percentage on the request.

## Why the third one is the dangerous one

The first two **shouted**. A throttling exception. A 500. You cannot miss them.

The third one behaves exactly like the system working correctly. The validator
rejects, the composer falls back to the deterministic template, the alert goes
out, the community is served. The only evidence is an operator notice that
looks identical to the notice you would get if the agent were genuinely
misbehaving.

So the agent works perfectly, its output is discarded every single time, and
the system reports a successful cycle. Forever.

This is the failure mode the project's own instructions name — *"a false
rejection costs phrasing quality"* — except the cost is not quality. It is the
entire feature, silently.

## The thing I keep relearning

Nothing offline could have found any of these three.

Every test in the agent's suite fakes the model, which is correct: they test
our code, not Anthropic's. `BedrockModel` accepts the invalid parameter pair
at construction. The validator has 947 tests and eleven contained attacks, and
not one of them had ever been given **a real model's actual output** — they
were all given text I wrote, and I write text that conforms to my own rules.

That is the blind spot, and it is structural rather than careless. A test
suite is a set of inputs someone imagined. The first real output is the first
input nobody imagined.

There is a good argument here for capturing that live output as a fixture and
keeping it — not to replace the fakes, but because it is the one sample in the
suite that no one on the project invented.

## The fix is not to loosen the rule

Tempting, and wrong. The rule says every number must trace to a value on the
request, and `3` **does** trace — to `peak_at`. The defect is that the scan
compares against numeric fields only, and never against the components of the
instants the request is already carrying.

Extending it to recognise date and time components is faithful to the rule's
intent. Adding an exception for "numbers near a date-ish word" would not be.
Those two fixes look similar from a distance and are not the same thing at all:
one widens what counts as provenance, the other widens what escapes the check.

## Evidence

Full deployment transcript, resource-by-resource, in
`docs/evidence/2026-09-21-first-live-deploy-and-invocation.md`.

One number from it deserves its own line, because it is a second problem
waiting: cold start measured at **9.71 s against a 10 s read timeout**, on a
path where every invocation is cold — the runtime's idle session timeout is
900 s and the cycle runs every six hours. 290 ms of headroom, measured once.
