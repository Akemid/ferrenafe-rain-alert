---
date: 2026-10-01
title: The alarm could not tell an attack from the weather, so we deleted it
kind: process
tags: [security, observability, alarms, detection, aws]
hook: A pre-deploy review found one unmitigated HIGH. We built the detection, tested it, and threw it away — because it fired on rain.
status: raw
---

## What I expected

A pre-deploy security review of the scheduled-cycle stack came back GO, with
one HIGH that had no mitigation.

The Lambda's role holds `dynamodb:PutItem` on the alerts table, narrowed by
action but not by item. An attacker with code execution in the function —
realistically through a parser fault over the scraped SENAMHI HTML, or a
compromised transitive dependency — writes a single `AlertRecord` for the
city's partition at `imminent`, with a window start inside the dedup lookback.
Every cycle after that reads it as prior state and declines to send: *"not an
escalation over prior level imminent."*

One write. Seventy-two hours of silence. It outlives the attacker's access,
and all three alarms stay green: no errors, invocations normal, snapshot
published cleanly.

Narrowing IAM does not help — the attacker writes the partition the function
legitimately owns. So the control had to be detection, and detection looked
easy, because the handler already prints one JSON document per invocation:

```json
{ "level": "none", "decision": { "send": false, "reason": "level none" } }
```

Two combinations are healthy: `none` and no send, or an elevated level and a
send. The anomalous one is an elevated level that did not send. A CloudWatch
metric filter expresses that in one line:

```
{ ($.decision.send IS FALSE) && ($.level != "none") }
```

I had it built with the CDK pattern builder rather than a hand-typed literal,
verified the syntax against AWS's own documentation, mutation-tested both the
pattern and the `treatMissingData`, and sent it to a security review.

## What actually happened

> The forged-record attack and legitimate sustained dedup are **structurally
> indistinguishable at the log-line level.**

`should_send` returns `send=False` with the real, elevated level every time a
cycle continues an already-alerted event without escalating. That is not an
edge case. That is what happens whenever it rains for more than six hours.

The filter matches the attack. It also matches the weather.

## Why that is worse than nothing

An alarm that fires during ordinary operation does not stay an alarm. It
becomes noise, someone mutes it in the first week of the rainy season, and the
day it finally means something there is nobody left reading it. The detection
would have been *removed by the people it was built to protect*, and the
removal would look like good hygiene.

Worse, it would have read as coverage. The HIGH would have been marked
mitigated in the review, the runbook would have carried an alarm name, and the
real exposure would have gone back to being invisible — this time behind a
green check instead of behind nothing.

The reviewer's own suggestion was to ship it with a note telling the operator
to triage each trigger. I do not believe that. A page that arrives routinely
is not triaged; it is dismissed. Asking a human to stay vigilant against a
signal that is usually meaningless is asking them to do the job the detector
was supposed to do.

## Why the obvious tuning does not rescue it

The tempting fix is to alarm on *duration* rather than on the state: require
the suppression to persist across enough evaluation periods that a normal rain
event cannot trip it.

It fails in both directions at once. A three-day storm — unremarkable in a
coastal El Niño year, which is exactly when this system matters most — still
trips it. And a shorter attack slips under it. The threshold that makes it
quiet enough to keep makes it blind enough to be pointless, and there is no
setting where both are true, because the quantity being measured is the same
in both cases.

## What it actually needs

The thing that separates the two is not the decision. It is **provenance**: a
legitimate suppression rests on a record this system wrote and logged; the
attack's rests on one that appeared beside it.

That is an integrity mechanism on the table, not a field in a log line — the
attacker picks a plausible `sent_at` as easily as a plausible level. It is
real work, and it is not work to do the day before a deadline.

## What we did instead

Deleted the branch, and wrote the finding onto the `Notifier` port — the one
place the next person will be standing when its severity changes.

Today the attack suppresses a record and an operator notice. `ConsoleNotifier`
is the only implementation that exists, so nothing was going to reach a
resident anyway, and the public page still renders the *assessment's* level
rather than the sent alert's — so the risk stays visible in red even while the
alert is suppressed. That is what makes the gap survivable.

The first `Notifier` that actually delivers changes that, and nothing else in
the codebase would have announced it. A docstring on the port will not stop
anyone, but it is standing in the doorway rather than filed in a folder.

## The part worth keeping

A detection that cannot distinguish the thing it detects from normal operation
is not a weak control. It is a negative one: it costs attention, it
manufactures false confidence, and it will be deleted by someone acting
reasonably.

The honest move when you find that out is to delete it yourself, while you
still remember why — and to leave the finding somewhere louder than the alarm
would have been.
