---
date: 2026-09-18
title: I handed a subagent a false premise, and it verified instead of believing me
kind: process
tags: [ai-agents, delegation, verification, workflow]
hook: My instructions said two files were already merged into main. They were not. The agent checked, found the branch they actually lived on, and worked from there — then told me.
status: raw
---

## What I expected

I delegated an implementation slice to a subagent with a long, carefully
written brief: resolved API facts, hard constraints, a blocking design rule.
One line of it said:

> Two decision records were just merged to `main` and are on your branch —
> read both before writing the wiring.

I believed that when I wrote it. I had opened the pull request for those
records myself an hour earlier. Somewhere between opening it and writing the
brief, "I opened a PR" became "it is merged" in my head.

## What actually happened

From the agent's report:

> The prompt claimed ADR 0001/0002 were merged to `main` — verified via
> `git log --all` that they are not; they exist only on unmerged branch
> `docs/senamhi-source-decisions` (commit `11da812`). I implemented ADR 0002's
> rule from that commit's actual content but did not merge the branch (scope
> creep risk). The new validator docstring cites a file that doesn't exist in
> this repo yet — merge that branch before/alongside this PR.

Three separate good decisions in that paragraph, and only the first is the
obvious one:

1. It checked a premise it was given rather than acting on it.
2. Having found the premise false, it did not stop and did not guess — it
   located the real content and used it.
3. It refused to fix the situation by merging the branch itself, because that
   was outside what it had been asked to do, and escalated instead.

The consequence was real, not hypothetical: the code it wrote cites
`docs/decisions/0002-senamhi-attribution-rule.md` in a docstring, and on that
branch the file did not exist. A dangling reference in a validator that
implements a compliance rule is exactly the kind of thing nobody notices until
someone goes looking for the justification and finds nothing.

(The fix was one command — by the time the agent finished, the PR had in fact
been merged, so a rebase onto the updated `main` resolved it.)

## Why it matters

The failure was mine and it was a *context* failure, not a knowledge failure.
I knew the state of that pull request. I compressed it wrong while writing a
long brief, and long briefs are exactly where that compression happens.

Two things I am taking from it:

**State facts a delegate can check, and let it.** "These files are on your
branch" is checkable in one command. If I had written "these ADRs are at
commit `11da812` on branch `docs/senamhi-source-decisions`" the error would
have been impossible. Precision in a brief is not pedantry; it is what makes
verification cheap enough to actually happen.

**Judge delegated work by what it questioned, not only by what it produced.**
The three findings I trusted most from the review passes on this project were
all delivered as evidence rather than opinion — a surviving mutation, a failing
probe, a `git log` output. An agent that reports "done, all green" is giving
you less information than one that reports "done, and by the way this thing you
told me was wrong."

There is an uncomfortable corollary. I am the one who writes the briefs, and I
am the compression step. The agent caught this because the claim was
falsifiable with a command. The claims in my briefs that are *not* falsifiable
with a command — judgements, priorities, "this is the risky part" — get
followed without challenge. Those are the ones worth re-reading before I send
them.
