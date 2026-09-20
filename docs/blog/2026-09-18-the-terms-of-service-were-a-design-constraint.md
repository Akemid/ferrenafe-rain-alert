---
date: 2026-09-18
title: A clause in the terms of service turned out to be a validator rule
kind: domain
tags: [compliance, llm, prompt-design, civic-tech, senamhi]
hook: I went to check whether scraping a government site was legal. Scraping was fine. The thing that was not fine was the language model I had just spent two weeks adding.
status: raw
---

## What I expected

The question was narrow and defensive: this project reads a Peruvian
government weather page by scraping it, and it is about to be submitted
publicly. Is that a problem?

I expected to find either a `robots.txt` forbidding it or a terms page
forbidding it, and to then have a boring conversation about rate limits.

## What actually happened

Neither host publishes a `robots.txt` — both return 404. The terms of service
say **nothing at all** about automated access, scraping, robots, or
programmatic extraction. The topic is simply absent. Acquisition was never the
risk.

This clause was:

> "Queda prohibido que la información a la que se accede pueda ser modificada
> en su contenido para luego ser presentada como información oficial emitida
> por el Servicio Nacional…"

*It is prohibited for the information accessed to be modified in its content
and then presented as official information issued by the Service.*

The current change to this system puts a language model in the message path,
whose entire job is to rephrase the official warning into something a
neighbour will actually read. That clause describes the feature.

## The over-correction I nearly made

My first reaction was "then we must not name SENAMHI." That is wrong, and
wrong in an expensive direction.

The clause is a **conjunction**: modified *and then* presented as official.
Naming the agency is not what it forbids. And naming the agency is exactly
what gives a community alert its weight — an early-warning message that cannot
say the national weather service issued an orange warning is a message asking
to be believed on no authority at all. Dropping the attribution to be safe
would have made the product worse in the name of a rule that never required
it.

Three shapes, one forbidden:

| | Allowed |
|---|---|
| A fact: "SENAMHI issued an orange warning for the area." | yes |
| A verbatim quote, attributed | yes |
| A paraphrase wearing the attribution | **no** |

## Why it matters

The useful part is where the rule ended up living.

The obvious place to put "do not put words in SENAMHI's mouth" is the prompt.
It is one sentence, it reads well, and it is worth nothing: a model can ignore
it, and you find out in production, in Spanish, on somebody's phone, during a
flood.

So it became a validator rule instead, enforced deterministically in the
domain, from data the system already holds:

> If the body attributes anything to SENAMHI, what is attributed must be either
> a structured fact the domain itself produced, or a complete and unaltered
> quotation of the warning title. Nothing else.

Both halves are checkable without asking the model anything. The level comes
from a typed enum the domain computed. The title is a string the domain is
holding. A body that attributes something which is neither is rejected, and
the deterministic template goes out instead.

This is the same lesson the prompt-injection work in this project kept
teaching, arriving from a completely different direction: **prompt wording is
not a control.** Anything that must be true has to be true on the other side
of the model, where it can be tested.

## The unexpected part

Checking the legal exposure of the *old* component produced a hard constraint
on the *new* one. I went in to defend the scraper and came out with six
acceptance criteria for the composer.

It also settled something I had been treating as a tiebreaker. I had been
weighing a migration from scraping to the official OGC API, partly on
"cleaner provenance" grounds. That argument evaporated: the constraint is
about what you do with the data, not how you got it, and it applies to both
sources identically.

Recorded as [ADR 0002](../decisions/0002-senamhi-attribution-rule.md).
There is a related gap it also exposed: the message is titled
`Alerta de lluvias — Ferreñafe — <level>` and never says who sent it. A
resident cannot tell a community project from a government bulletin. That is
the same problem as the clause, arriving as a UX omission rather than a legal
one, and it costs one line to fix.

## Disclaimer

This is an engineering constraint derived from a published terms document by a
developer reading it carefully. It is not legal advice.
