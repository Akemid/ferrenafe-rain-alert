---
date: 2026-09-19
title: The validator I wrote to satisfy a legal clause had four holes in it
kind: technical
tags: [security, unicode, validation, llm, code-review]
hook: A Cyrillic А inside "SENAMHI" is invisible to a reader and invisible to an ASCII regex. The rule never fired, so the forged evacuation order passed as compliant.
status: raw
---

## What I expected

Yesterday I turned a clause in a government agency's terms of service into a
domain rule: *if the message attributes anything to SENAMHI, what is attributed
must be either a structured fact the domain produced or a complete, unaltered
quotation of the warning title.* Written as a validator rather than as prompt
wording, precisely because a model can ignore prompt wording.

It was built carefully. The module around it already had real Unicode
hardening — NFC composition, accent decomposition folding, rejection of
control, bidi and invisible characters — from two earlier adversarial reviews.
Tests per acceptance criterion. Green.

## What actually happened

A review pass found four ways through. I reproduced all four before touching
anything.

**1 — The rule never fired.** The mention detector was
`re.compile(r"\bsenamhi\b", re.IGNORECASE)`. ASCII. Replace one letter with a
Cyrillic А (U+0410) and it does not match, so the entire rule is skipped:

```
"El SENАMHI informa que deben evacuar de inmediato."   →  0 violations
```

The existing hardening does not help and could not: a Cyrillic А is a perfectly
ordinary graphic letter. It is not invisible, not a control character, not a
bidi override. NFC does not touch it. Accent folding does not touch it. It is
simply a different letter that looks identical.

**2 — The vocabulary check was vacuously true.** Even spelling SENAMHI
correctly, writing the *claim* in fullwidth Latin defeated it. The tokenizer is
`[a-z]+|\d+|%`, which does not tokenize fullwidth characters at all — so the
check `all(token in allowed for token in tokens)` ran over an empty list and
returned `True`. An empty token list is a vacuous pass, and `all([])` is the
classic way to write one without noticing.

**3 — A newline severed the attribution.** The rule iterated
`text.split("\n")` and only inspected lines containing "SENAMHI":

```
"Aviso del SENAMHI
Evacuen de inmediato toda la zona costera."       →  0 violations
```

The first line is harmless. The second has no "SENAMHI" in it, so it was never
examined. A resident reads two consecutive lines as one statement, and
`"Según SENAMHI:\n<content>"` is among the most common shapes a language model
produces.

**4 — A blank line severed it again.** After fixing 3 with a scope that
extends an unterminated mention onto the following line, the scope correctly
stops at blank lines, section headers and bullets — reaching past those would
drag unrelated checklist items into the judgement. But that left the same
attack one blank line away.

That last one is the interesting fix, because the obvious repair is wrong.
Reaching *across* the blank line reintroduces exactly the false positives the
stops exist to prevent, and a false positive here is not cosmetic: the message
is rejected, the composer falls back to the deterministic template, and the
community silently loses the feature.

The actual defect was not the scope at all. `"Aviso del SENAMHI"` is built
entirely from allowed vocabulary, so it passes on its own — it is a **dangling
attribution**, a mention that promises content it never delivers, with the
invented order sitting just below it wearing the credibility. Refusing the
dangle closes the blank line, the section header, the bullet and the
end-of-body at once, because they are all the same exit.

Before writing it I checked it was safe:

```
SENAMHI lines across 13 V0 fixtures: 5   dangling: 0
```

The deterministic template always terminates its sentences. The rule costs
nothing a real message wants.

## Two tests that were green for the wrong reason

Both found while fixing the above, and they are the part I keep thinking about.

My first version of the end-of-body test used `"Segun el SENAMHI"`. It passed
immediately — and not because the dangling attribution was caught. It passed
because `segun` is not in the closed vocabulary. I had written a test that
confirmed my fix worked, and it would have been just as green without the fix.

Then my change broke an existing test named
`test_the_scope_stops_at_a_section_header`. Its fixture was a dangling
attribution followed by a header, asserting no violation. The boundary it
*named* was real. But what it was actually resting on was hole 4 going
unnoticed — the header was never the reason it was green. It now asserts on
the reported text instead: that the violation does not swallow the checklist.
Same intent, an observable that survives the bug being fixed.

## Why it matters

Every one of these is the same failure with a different costume: **the check
did not run, and not running looked exactly like passing.**

- An ASCII regex that does not match → rule skipped → green.
- A tokenizer that produces nothing → `all([])` → green.
- A line that is never visited → not judged → green.
- A test whose fixture trips a different rule → green.

None of them look like bugs in a diff. The code is correct; it is simply
never reached. That is why "the tests pass" carries so little information
about a validator, and why the only review currency worth much here is a
probe that demonstrates the bypass.

The final state, verified against eleven attack variants including Cyrillic
and Greek homoglyphs, fullwidth text, three split shapes and a partial quote:

```
11/11 contained
legitimate template message still validates: True
```

Both halves of that matter. A validator that rejects everything is not secure,
it is broken — and in this system a false rejection means the town gets the
template while everyone believes the agent is working.

## Evidence

The defence needed two mechanisms, and confirming that neither was sufficient
alone was the thing worth checking first:

- **NFKC** folds fullwidth Latin to ASCII but does **not** map Cyrillic А to
  Latin A.
- **A script restriction** catches Cyrillic and Greek but **not** fullwidth,
  because fullwidth Latin *is* Latin script.

Shipping either one alone would have produced a fix that closed half the hole
and read, in the commit message, as if it had closed all of it.
