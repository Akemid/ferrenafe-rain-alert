# Findings log

Raw material for write-ups, kept as it is discovered rather than reconstructed
afterwards. Each finding is one file. Nothing here is finished prose — it is
notes with enough evidence attached that a post can be written from them
without re-doing the work.

## Why this exists

The interesting part of building this system has not been the code. It has been
the moments where a reasonable assumption turned out to be wrong, and the
measurement that showed it. Those moments are invisible in a diff and evaporate
from a chat log. Writing them down when they happen is the only way to keep
them.

## File format

`docs/blog/YYYY-MM-DD-slug.md`, with YAML front matter:

```yaml
---
date: 2026-09-18
title: A short, concrete claim — not a topic
kind: technical | process | domain
tags: [aws, verification, testing]
hook: One sentence. The thing that makes someone keep reading.
status: raw | drafted | published
---
```

Then the body, in this order:

1. **What I expected** — the assumption, stated plainly enough to be wrong.
2. **What actually happened** — the measurement, with the real numbers or output.
3. **Why it matters** — the cost of not having found it.
4. **Evidence** — the command, the diff, the test. Reproducible where possible.

## Rules

- **English.** These feed an English-language write-up.
- **A finding is a surprise, not a summary.** "Implemented the invoker" is not a
  finding. "The session id has a 33-character minimum and `uuid4().hex` is 32"
  is.
- **Attach the evidence.** A claim without the command that produced it is a
  memory, and memories drift.
- **Record being wrong.** The entries where the first guess was wrong are the
  ones worth reading. Do not tidy them into hindsight.
- **Write it when it happens.** A finding reconstructed a week later loses the
  detail that made it useful.

## Harvesting

To collect everything into one document, newest last:

```bash
for f in docs/blog/[0-9]*.md; do printf '\n\n---\n\n'; cat "$f"; done > /tmp/findings.md
```

To list what exists, with hooks:

```bash
grep -H '^hook:' docs/blog/[0-9]*.md
```

## Related

Decisions that were *made* live in [`docs/decisions/`](../decisions/) as ADRs.
The two are different: an ADR records a choice and its consequences, a finding
records a surprise. A finding often becomes the evidence section of an ADR.
