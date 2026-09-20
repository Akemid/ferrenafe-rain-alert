---
date: 2026-09-18
title: Two official AWS docs pages define the same retry setting in opposite ways
kind: technical
tags: [aws, boto3, botocore, documentation, verification]
hook: One AWS page says max_attempts excludes the initial request. Another AWS page says it counts total attempts. Both are correct, for different SDKs, and only one of them is the SDK you are using.
status: raw
---

## What I expected

The task was small: disable retries on a boto3 client, because the caller
already has a deadline and a silent triple-retry turns a 10-second budget into
30. The planning document had flagged it as a to-verify item — `max_attempts`
vs `total_max_attempts` — which at the time looked like over-caution about a
one-line config.

## What actually happened

The first documentation hit, the boto3 guide:

> `max_attempts` — **Important**: the behavior differs depending on how it's
> configured:
> - When set in your AWS config file or using `AWS_MAX_ATTEMPTS`: includes the
>   initial request (total requests)
> - **When set in a `Config` object: excludes the initial request (retries only)**

The second hit, also on `docs.aws.amazon.com`:

> `max_attempts` **counts total attempts, not retries**, so setting it to `1`
> disables retries entirely while still making the original request.

Directly contradictory, both official. The second page is for a newer
async SDK, not for botocore — but nothing in a search result says so, and both
describe a parameter with the same name doing the opposite thing.

So I stopped reading documentation and asked the installed library:

```python
from botocore.config import Config
print(Config.__doc__)
```

```
* total_max_attempts -- An integer representing the maximum number of
  total attempts that will be made on a single request.  This includes
  the initial request, so a value of 1 indicates that no requests
  will be retried.  If total_max_attempts and max_attempts are both
  provided, total_max_attempts takes precedence. total_max_attempts is
  preferred over max_attempts because it maps to the AWS_MAX_ATTEMPTS
  environment variable...

* max_attempts -- An integer representing the maximum number of retry
  attempts... Setting this value to 0 will result in no retries ever
  being attempted after the initial request.
```

botocore 1.43.98. Definitive, versioned with the code that will run, and it
names its own preference.

## Why it matters

Both spellings *can* disable retries — `total_max_attempts=1` or
`max_attempts=0`. The danger is the off-by-one in the middle: someone reading
the second page writes `max_attempts=1` intending "no retries" and gets one
retry. In a system with a read timeout, that quietly doubles the worst-case
latency, and the only symptom is a deadline that occasionally gets missed under
load — the kind of thing that never reproduces locally.

The general lesson is cheaper than it sounds: **for a pinned dependency, the
installed package is better documentation than the documentation.** It cannot
be stale relative to itself, it cannot be about a different SDK, and
`print(Thing.__doc__)` costs three seconds.

A planning step that says "verify this before you write it" earns its place
exactly here. This looked like the most skippable item on the list.

## Evidence

```bash
uv init -q botoprobe && cd botoprobe && uv add -q boto3
uv run python -c "from botocore.config import Config; print(Config.__doc__)" | grep -A20 'type retries'
```

Resolved as: `Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1})`
