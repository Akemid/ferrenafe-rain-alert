---
date: 2026-09-28
title: Two tests checked both ends of the coupling, and the middle was missing
kind: technical
tags: [astro, css, testing, verification, frontend, life-safety]
hook: One test asserted the class was in the stylesheet. Another asserted the class was on the element. Both passed. The page rendered an imminent flood warning in plain black text.
status: raw
---

## What I expected

The page's loudest signal is colour. `sin riesgo` green, `prepárate` amber,
`riesgo inminente` red. A whole-branch review had already found that a blind
cycle rendered as a green all-clear, and part of that fix was a fourth rule —
`.status-degraded.level-none` in amber — so a `level: "none"` from a cycle that
read nothing could never be painted like an all-clear.

That coupling was called out as unguarded, and guarded. Two tests, at both
ends:

```
web/test/page.test.ts     the rendered element carries class `level-imminent`
web/test/markup.test.ts   `.level-imminent` appears in the page's stylesheet
```

Both were mutation-proved. Change `levelClass` to return `lvl-${level}` and
three tests go red. Rename the CSS class and two more go red. The other end
holds, in both directions.

## What actually happened

The deployed page rendered a real SENAMHI orange warning — `riesgo inminente`,
live, during an actual advisory — in plain black 16-pixel text, visually
identical to `sin riesgo`.

```js
const lvl = document.querySelector('#status .status-level');
getComputedStyle(lvl)   // color: rgb(0, 0, 0)   fontSize: 16px   fontWeight: 400
                        // expected: rgb(207, 34, 46)  1.5rem  700
```

Astro **scopes** styles written in a component's `<style>` block by rewriting
every selector to require a generated attribute:

```css
.status-level[data-astro-cid-lcdefpme] { font-size: 1.5rem; font-weight: 700 }
.level-imminent[data-astro-cid-lcdefpme] .status-level[data-astro-cid-lcdefpme] { color: #cf222e }
```

It adds that attribute at build time to elements **written in the template**.
Every element the page builds with `document.createElement` has no such
attribute:

```js
[...section.attributes].map(a => a.name)   // ["data-astro-cid-lcdefpme"]  — in the template
[...levelEl.attributes].map(a => a.name)   // []                           — created by page.ts
```

Probing detached elements for the three dynamic classes — `status-level`,
`degraded-notice`, `stale-warning` — returned **zero matched rules each**.

The error box rendered correctly the whole time, which is why nothing looked
wrong until a page had data. `.status-error` and `.status-stale` sit on the
`<section>`, and the section *is* in the template. Which half of the stylesheet
worked was decided by which element each class happened to live on.

## Why it matters

The class name was in the stylesheet. The class name was on the element. Both
assertions were true, both were mutation-proved, and **neither knew about the
third thing sitting between them.** We verified both ends of the coupling. The
coupling still did not connect.

That is a different failure from the ones this repository has logged before. A
vacuous test asserts nothing. An unreached branch is never visited. These tests
assert something real and are visited every run — they just describe a
two-party relationship that turned out to have three parties. No amount of
strengthening either end would have found it, because neither end was wrong.

The other reason it survived: **the test environment cannot reproduce the
build.** The assertions run in jsdom against source, where the scoping
transform has not happened yet. The transform is applied by `astro build`, and
nothing in the suite looked at the built output. The tests were checking a
different artifact from the one residents load.

The damage is bounded and worth stating precisely, because overstating it would
be its own kind of dishonesty. The fix for the blind-cycle finding was mostly
*textual* — the heading says "No se pudo evaluar el riesgo" and the notice names
which source failed — and all of that still works. What was lost is the visual
hierarchy: the notice reads as a footnote again, and today a genuine imminent
warning has no red on it. Not a false all-clear. A mute alarm.

## The part I would get wrong again

I would have shipped this. The gate was green — 63 tests, typecheck, build, and
a whole-branch review that explicitly mutation-tested this exact coupling from
both directions and found it sound.

What caught it was opening the deployed page in a browser and reading the
computed style instead of the markup. Not a cleverer test. A different kind of
looking: at the real artifact, in the real runtime, after the real build.

There is a rule in this repository's own instructions — *verify against the real
thing, not against memory* — and I had been applying it to facts. Does this file
exist, does this command return what the report claims, does this branch red
when deleted. It applies just as hard to **artifacts**: the thing a test
examines is not always the thing a user loads, and the gap between them is
invisible from inside the test.

The narrow lesson is about Astro's scoping and dynamically created elements.
The general one is worth more: when a test and a user look at two different
build outputs, the test can be perfectly correct about a file nobody reads.

## Evidence

```js
// On https://main.<id>.amplifyapp.com/ , with a live SENAMHI orange warning
const sec = document.getElementById('status');
const lvl = sec.querySelector('.status-level');
({
  sectionClass: sec.className,                                    // "level-imminent"
  sectionAttrs: [...sec.attributes].map(a => a.name),             // ["data-astro-cid-lcdefpme", ...]
  levelAttrs:   [...lvl.attributes].map(a => a.name),             // []
  computed:     getComputedStyle(lvl).color                       // "rgb(0, 0, 0)"
})
```

Found by opening the live page after the Amplify rewrite went in — the first
time any of this code had been looked at in a browser rather than in jsdom.
