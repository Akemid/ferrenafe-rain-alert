---
date: 2026-09-21
title: Three docstrings cited a contract file that was never written
kind: process
tags: [verification, documentation, contracts, drift]
hook: I was about to copy an established pattern. I checked whether the pattern existed. It did not, and three files in the repository said it did.
status: raw
---

## What I expected

A task on the status-page branch calls for a golden document that pins the
shape of the published JSON, so the Python publisher and a TypeScript page
cannot drift apart without a test going red. The plan justified it by pointing
at precedent:

> This project already chose a pattern for that — `contracts/agent-composition.json`.

I believed that, because I had read it more than once. The repository says so
in three places:

```
agent/models.py:9                        The drift guard is `contracts/agent-composition.json`, asserted from both …
agent/app.py:86                          `contracts/agent-composition.json` pins it for both sides.
adapters/agentcore_invoker.py:111        `contracts/agent-composition.json` does the same job for the prompt payload …
```

So my only question was a small one: how do the existing tests locate that
file, so the new test resolves its path the same way.

## What actually happened

No test locates it. No test mentions it.

```console
$ grep -rn "contracts" tests/ | head
tests/unit/domain/test_values.py:24:    """Two enums whose *string values* are contracts, not implementation.

$ ls contracts/
ls: contracts/: No such file or directory

$ git log --oneline --all -- contracts/
$
```

The third command is the one that settles it. Not "the file is untracked",
not "it moved" — **no commit on any branch has ever touched that path.** The
drift guard three files describe as protecting the agent seam from both sides
has never existed.

What exists is the sentence describing it. Written, I assume, at the moment
the pattern was decided and before it was built, and then never revisited,
because a docstring has no way to fail.

## Why it matters

The claim is load-bearing in a specific way. `agentcore_invoker.py` pins
`PROMPT_PAYLOAD_KEY = "prompt"` and `agent/app.py` reads `PROMPT_KEY` — two
constants in two Python projects with separate test suites and separate
interpreters. The comment beside one of them explains that a change to either
side cannot drift silently *because the contract file catches it*. Rename that
key on one side today and both suites stay green, exactly the failure the
comment promises is impossible.

That is worse than no comment. No comment leaves a reader suspicious of an
unguarded seam. This one spends the suspicion and returns nothing — the next
person to touch either constant reads that the seam is covered and moves on.

It is also the same shape as every other defect this project has logged: a
check that looks like it ran and did not. `all([])`. A regex that never
matched so its rule was skipped. Hygiene run before `git add`, where `git grep`
sees no untracked files. A `Protocol` nothing type-checked. Each one reads,
at a glance, exactly like the working version.

The variant here is the cheapest to produce and the hardest to see, because
the artifact is *prose*. A wrong test fails. A wrong type annotation gets
flagged. A sentence asserting that a guard exists is inert: nothing in
`pytest`, `ruff`, or `mypy` has any opinion about whether a path named in a
comment is a real path.

## The part worth keeping

I only found it because I wanted to copy the pattern and went looking for the
file to copy. Had the plan said "create a golden contract" instead of "follow
the existing one", I would have written the new one and left the phantom
standing.

So the useful habit is smaller than a lesson about documentation: **when a plan
justifies a decision by citing precedent, open the precedent.** Not to
evaluate it — just to confirm it is there. It costs one `ls`. Here the citation
was the only reason a false claim in three shipped files got checked at all.

The cheap mechanical version, worth having in the hygiene suite: every path
mentioned inside backticks in a docstring either exists or is a lie. That is a
test a repository can run.

## Evidence

```console
$ grep -rn "agent-composition" tests src agent --include=*.py
src/rain_alert/adapters/agentcore_invoker.py:111
agent/models.py:9
agent/app.py:86

$ ls contracts/
ls: contracts/: No such file or directory

$ git log --oneline --all -- contracts/
$
```

Parked, not fixed here: writing a real `contracts/agent-composition.json`
means deciding which fields it pins and where each side asserts it, and that
belongs to the composer change rather than to a branch about a status page.
`contracts/public-snapshot.json`, the status page's own contract, is built on
this branch — and unlike its cited precedent, a test reads it.

## What happened next, on this same branch

The three docstrings were parked, and the branch then added **seven more
phantom citations** before review caught them: six pointing at an uncommitted
report file, one naming a `SnapshotBuilder.build()` that does not exist. Three
occurrences of one defect on one branch is where a habit stops being enough.

So the mechanical version proposed above is now written, as
`tests/hygiene/test_docstring_citations.py`. It reads every docstring and
comment in every tracked Python and TypeScript source, and fails on a
backticked repository path that git does not have — either because it was
never written, or because it exists only on the author's machine. That second
shape is the one that got past review six times: the author can open it, so it
reads as correct, and it is invisible to everyone who clones.

The parked docstrings now say plainly that the guard is planned and that
nothing catches the drift today. That is the only honest way to make the check
green without writing a contract that belongs to another change.
