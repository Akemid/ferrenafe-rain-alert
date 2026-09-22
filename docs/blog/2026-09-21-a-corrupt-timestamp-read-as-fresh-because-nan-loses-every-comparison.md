---
date: 2026-09-21
title: A corrupt timestamp read as fresh, because NaN loses every comparison
kind: technical
tags: [javascript, staleness, verification, life-safety, frontend]
hook: The staleness check was `age > STALE_AFTER_HOURS`. With an unparseable date, age is NaN, and NaN is not greater than anything — so the page called the reading fresh, forever.
status: raw
---

## What I expected

The public status page shows when the rain-alert system last evaluated. If the
last reading is old, the age goes in the foreground: *"Última evaluación hace 3
días"* is the most important thing on the page at that moment, not an
embarrassment to hide behind a green badge.

The check is the obvious one:

```ts
if (age > STALE_AFTER_HOURS) return 'stale';
return 'ok';
```

A reviewer had already confirmed the branch was load-bearing — remove it and
exactly one test goes red. That is a real check, and it passed.

## What actually happened

It is load-bearing for *valid* dates. `age` comes from subtracting two
`Date`s, and an unparseable `evaluated_at` makes it `NaN`:

```console
$ node -e 'const age = (new Date("not-a-date") - new Date("2026-09-21T14:00:00-05:00"))/3.6e6;
           console.log("age =", age);
           console.log("age > 12 :", age > 12);
           console.log("age < 12 :", age < 12);'
age = NaN
age > 12 : false
age < 12 : false
```

`NaN` is not greater than twelve. It is also not less than twelve, and not
equal to twelve. Every comparison against `NaN` is `false`, so the guard falls
through to its own `else` and the page reports the reading as current — with a
timestamp it could not parse — indefinitely.

The most likely way to produce that value is the one the page is built to
survive: a truncated or partially written `status.json`. The publisher writes
the object directly rather than atomically; that trade-off is recorded
elsewhere in this repository as a deferred minor. A reader arriving mid-write
gets a document whose `evaluated_at` is half a string, and the page tells them
everything is current.

It surfaced only because a fix round elsewhere carried an instruction to audit
every other branch for the same defect class, rather than to fix the one
instance that had been reported.

## Why it matters

The failure is silent in the specific way that matters for this project.
A thrown exception routes to the error state, which says plainly that the
status could not be loaded. A visibly broken string — `Invalid Date` on the
screen — is something a resident distrusts on sight. This is neither. The page
renders a normal, confident, current-looking status, and the only thing wrong
is that the timestamp behind it is garbage.

It is also a false *negative* on staleness, which is the direction that costs
something. Wrongly saying "this reading may be out of date" sends someone to
check another source. Wrongly saying a reading is current, when the system may
have stopped publishing days ago, is the page quietly substituting its own
confidence for information it does not have.

The general shape is the one this repository keeps finding: **a check that
looks like it ran and did not.** The branch exists, the test that covers it
passes, a reviewer confirmed by deleting it that it is load-bearing — and all
of that is true only for inputs that parse. `NaN` does not fail the check. It
declines to participate in it.

## The part I would get wrong again

The instinct on discovering this is to fix the comparison — invert it to
`!(age <= STALE_AFTER_HOURS)`, which *does* catch `NaN`:

```console
$ node -e 'const age = NaN; console.log(!(age <= 12));'
true
```

That is worse than the bug. It makes a corrupt document render as **stale**,
which is a lie in the safer direction but still a lie: the page would announce
an age it computed from an unparseable date. It also encodes the fix in the
least readable line in the file, where the next person to tidy the logic
removes it without knowing what it was for.

The correct place is the parser. `parseSnapshot` already refuses a document
whose `schema_version` it does not recognise, rather than rendering a blank —
a document with no version is not a version-1 document. A document with an
unparseable `evaluated_at` is not a readable document either, by the same
argument, and it should reach the error state through the same door. The
validation belongs where the shape is validated, not distributed into each
comparison that later consumes the value.

## Where the fix was wrong, which is the better half of the story

That fix went in, with a report stating it rejected every unparseable form.
The evidence behind that sentence was one garbage string. Two forms still
produced the original bug:

```
evaluated_at: 12345          no typeof check, so String(12345) is "12345",
                             and new Date("12345") is year 12345 AD — a finite
                             timestamp, rendered as fresh.

evaluated_at: "2026-02-30"   JavaScript rolls it forward to 2026-03-02 rather
                             than producing NaN. A date that does not exist,
                             accepted as one that does.
```

The asymmetry that allowed it was visible in the file the whole time:
`schema_version` had a type guard and `evaluated_at` did not.

So the shape repeats one level up. The first finding was a check that looked
like it ran and did not. The second was a *claim* with no check behind it —
and a claim is cheaper to produce, because writing "rejects every unparseable
form" costs one sentence and testing it costs five cases.

The real fix is a string type guard plus a round-trip: parse the value, then
confirm it reads back as the same calendar date. And because a validator now
stands between the publisher and the page, the thing worth checking hardest was
not whether it rejects bad input but whether it accepts good input — a
validator stricter than its emitter sends every genuine document to the error
state, which is worse than the bug and invisible to tests built on
hand-written fixtures. That was settled by running the real Python emitter
across whole-second, fractional-microsecond, year-boundary and negative-offset
cases rather than by reading the fixture.

Then one more layer. With the guard in place, deleting it left the suite
green: `RegExp.exec` calls `ToString` internally, so `String(12345)` fails the
shape regex whether or not the guard exists. The number test passed because of
the regex. The guard is load-bearing only for an input whose `toString()` does
match the shape — `["2026-09-03T07:00:00-05:00"]`, an array of one — and until
someone wrote that test, the line was covered by nothing but the belief that
the passing test above it was testing it.

## The question this leaves open

The other date-shaped fields (`window.start`, `window.end`,
`alert.valid_until`, `recent_alerts[].sent_at`) are still unvalidated, on the
argument that they only drive display and no computed decision. That argument
is correct today, and each now carries a comment saying it is an assumption.
It stops being correct the first time someone computes with one of them — and
a comment is a weaker guarantee than a check, which is the whole subject of
this entry.

## Evidence

```console
$ node -e 'console.log(NaN > 12, NaN < 12, NaN === NaN)'
false false false
```

Found by auditing every reveal/hide branch on the page after an unrelated fix,
under the rule this project adopted earlier: when a fix lands, look for the
same defect elsewhere rather than fixing only the instance that was reported.
That rule was adopted after the identical UTF-8 test defect turned up in two
sibling adapters because only the first one's brief was corrected. This is its
second catch, and the first one it found in a different language.
