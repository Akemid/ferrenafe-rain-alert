---
date: 2026-09-21
title: The test asserted the reason instead of the number, and caught my arithmetic
kind: process
tags: [testing, measurement, deadlines, aws]
hook: I wrote "three times the measured worst case" and then wrote 30. The worst case was 10.27. The test failed, and it was right.
status: raw
---

## What I expected

A deadline needed changing. The design had set a 10-second timeout on the
agent call and said so provisionally — the document records cold-start latency
as *"to verify at first live run"* and as *"the input that would move D19's
number"*. I had the measurements. This was bookkeeping.

Ten cold starts against the deployed runtime:

```
7.00  7.50  8.52  8.59  8.82  8.97  9.44  9.71  9.72  10.27
median 8.9   max 10.27
```

The worst already exceeded the deadline, and every production invocation is
cold — the runtime's idle session expires after 900 s and the cycle runs every
six hours. At 10 s a perfectly working agent would lose roughly one cycle in
ten, silently, because the composer falls back to a message that is already
correct and nothing reports the difference.

So: raise it. I reasoned that three times the measured worst case was the
right headroom, wrote the tests, and set the value to 30.

## What actually happened

The test failed.

```
assert required <= READ_TIMEOUT
```

Three times 10.27 is **30.81**. I had written "three times ten" in my head,
looked at a max of 10.27, and produced 30. The value contradicted the
reasoning I had written three lines above it, in the same commit.

The reason the test caught it is the only interesting part of this entry.
I had written:

```python
MEASURED_COLD_STARTS = (9.71, 7.50, 8.52, ..., 10.27, ...)
REQUIRED_HEADROOM_FACTOR = 3.0

required = REQUIRED_HEADROOM_FACTOR * max(MEASURED_COLD_STARTS)
assert required <= READ_TIMEOUT
```

Had I written the obvious test instead —

```python
assert READ_TIMEOUT == 30.0
```

— it would have passed. Cheerfully. Forever. Pinning a value that did not
satisfy the rule it was chosen by, in a file whose comment explains a factor
the number does not meet.

## Why it matters

A test that asserts a value can only tell you the value changed. A test that
asserts the *reason* can tell you the value was wrong when it was written.

Those sound like the same test and they are not. The difference shows up
exactly once per constant — at the moment someone derives it — and that is the
moment the error is cheapest to catch and hardest to notice, because the
person checking is the person who just did the arithmetic.

It also leaves something behind. `MEASURED_COLD_STARTS` is now ten real
numbers in the test file with the date and conditions attached, so the next
person to touch the deadline inherits the evidence rather than a value and a
paragraph of prose asserting that someone once measured something.

## The part I would get wrong again

The factor of three is not a statistic and I should not pretend otherwise. Ten
samples from one session, one region, one payload size and one hour of one day
do not describe a tail.

What justifies it is asymmetry, not confidence. Overshooting costs nothing:
nobody waits on a scheduled job, and if the deadline passes the community gets
a deterministic message that is already correct. Undershooting throws away a
working agent's output and tells no one. When one side of an error is free and
the other is invisible, you lean hard, and you say that you are leaning rather
than dressing it up as a percentile.

Three is also still bounded, which turned out to matter for a reason I only
found in a stack trace: Strands retries *inside* the container —
`reached max retries: 4` — where the `total_max_attempts: 1` I had carefully
set on the client has no reach at all. A generous deadline is fine. An
unbounded one would sit through a retry storm I do not control.
