---
date: 2026-10-02
title: Switching from the scraper to the official API would not have fixed it — both sit behind the same wall
kind: technical
tags: [aws, lambda, networking, senamhi, verification, data-sources]
hook: The scraper timed out from Lambda, so I reached for the official API. Both hosts live in the same /24, and from AWS that /24 does not answer at all.
status: raw
---

## What I expected

The deployed cycle reported `senamhi: timeout — ConnectTimeout: [Errno 110]`.
The scraper is the brittle part of this system, so the obvious reading was
that the scraper was the problem. The obvious fix was the OGC/GeoServer service
already investigated in [ADR 0001](../decisions/0001-senamhi-source-strategy.md),
which is a different host, `idesep.senamhi.gob.pe`, with a documented
interface.

The second guess was the region. `us-east-2` is a long way from Peru, so
São Paulo might be treated differently.

## What actually happened

A throwaway Lambda tested each host from both regions. It made a raw TCP
connection first and an HTTPS GET second, with Open-Meteo as the control:

| | us-east-2 | sa-east-1 | outside AWS |
|---|---|---|---|
| `www.senamhi.gob.pe` (190.119.131.3) | TCP timeout | TCP timeout | 200 in 1.6 s |
| `idesep.senamhi.gob.pe` (190.119.131.56) | TCP timeout | TCP timeout | 200 in 1.9 s |
| `api.open-meteo.com` (control) | 200 in 615 ms | 200 in 547 ms | — |

The API is not a way around the block. Both hosts are in the same
`190.119.131.0/24`. The connection never completes: no RST, no 403, the packets
are dropped silently. Changing region changes nothing either.

## Why it matters

"Scraping or API" had been the wrong question once already, back when the API
turned out to lack the warning's text. This is the second way it was wrong.
Both sources depend on the same network path, so moving to the API would have
cost a day of adapter work and still timed out.

The order of the checks was what found it. The raw TCP connection came before
any HTTP request, and a control target ran alongside. Without the TCP step, a
timeout could pass for a slow server. Without the control, it could pass for a
Lambda with no egress.

The system did the right thing while blind. It reported `unavailable`, used
Open-Meteo only, and told residents so in the message. That is the outage path
from ADR 0001 working in production. It is also the reason not to accept "the
alert went out" as proof that SENAMHI was read.

## Open

- Is it AWS address space, cloud providers, or everything outside Peru? One
  machine outside AWS cannot tell these apart.
- Is it deliberate? If it is, the right fix is to ask SENAMHI for access, not
  to route around the block.

## Evidence

Full commands, raw output and verified cleanup:
[`docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md`](../evidence/2026-10-02-senamhi-unreachable-from-aws.md).
